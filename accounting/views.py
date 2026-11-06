from datetime import date

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts_core.audit import audit
from accounts_core.rbac import can, perm_required
from approvals import services as appr
from core.forms import DateInput, StyledMixin, money_field
from core.ui import C, M, Table, paginate
from core.utils import D, ZERO, month_start, today

from . import handlers, services as gl, vouchers
from .models import Account, BankAccount, BankReconciliation, Journal, JournalLine, Voucher


def _errs(request, e):
    for m in (e.messages if hasattr(e, "messages") else [str(e)]):
        messages.error(request, m)


# ------------------------------------------------------------------ chart of accounts
class AccountForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = Account
        fields = ["code", "name", "type", "description", "is_active"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        if self.instance.pk:
            self.fields["code"].disabled = True
            self.fields["type"].disabled = True


@perm_required("accounting.view")
def coa(request):
    t = Table(["Code", "Account", "Type", "Balance (UGX)", "Status", ""])
    edit = can(request.user, "accounting.manage_coa")
    for a in Account.objects.all():
        t.add([C(a.code, f"/accounting/ledger/?account={a.pk}"), a.name, a.get_type_display(), M(gl.balance_of(a.code)),
               C("System" if a.is_system else ("Active" if a.is_active else "Inactive"), badge="mute" if a.is_system else True),
               C("Edit", f"/accounting/coa/{a.pk}/edit/") if edit else ""])
    return render(request, "generic_list.html", {"title": "Chart of accounts", "table": t, "subtitle": "Accounts used by the double-entry ledger. System accounts power automatic postings.",
                  "actions": [("New account", "/accounting/coa/new/", "primary")] if edit else []})


@perm_required("accounting.manage_coa")
def coa_edit(request, pk=None):
    a = get_object_or_404(Account, pk=pk) if pk else None
    form = AccountForm(request.POST or None, instance=a)
    if request.method == "POST" and form.is_valid():
        if a and a.is_system and not form.cleaned_data["is_active"]:
            form.add_error(None, "System accounts cannot be deactivated.")
        else:
            obj = form.save()
            audit("coa.save", "finance", "Account", obj.pk, obj.code, new={"name": obj.name, "type": obj.type, "active": obj.is_active})
            messages.success(request, "Account saved.")
            return redirect("accounting:coa")
    return render(request, "generic_form.html", {"title": "Edit account" if a else "New account", "form": form, "submit": "Save account", "cancel": "/accounting/coa/",
                  "intro": "Codes starting 1=assets, 2=liabilities, 3=equity, 4=income, 5=expenses."})


# ------------------------------------------------------------------ journals
@perm_required("accounting.view")
def journals(request):
    q, src = request.GET.get("q", "").strip(), request.GET.get("source", "")
    qs = Journal.objects.select_related("posted_by")
    if q:
        qs = qs.filter(Q(ref__icontains=q) | Q(description__icontains=q) | Q(source_ref__icontains=q))
    if src:
        qs = qs.filter(source=src)
    page, qstr = paginate(request, qs)
    t = Table(["Journal", "Date", "Description", "Source", "Amount (UGX)", "Posted by", ""])
    for j in page:
        t.add([C(j.ref, f"/accounting/journals/{j.pk}/"), f"{j.date:%d %b %Y}", j.description[:80], C(j.source, badge="mute"), M(j.total), j.posted_by.username,
               C("Reversal", badge="warn") if j.reverses_id else (C("Reversed", badge="bad") if j.reversals.exists() else "")])
    sources = Journal.objects.order_by().values_list("source", flat=True).distinct()
    actions = [("Manual journal", "/accounting/journals/new/", "primary")] if can(request.user, "accounting.journal") else []
    return render(request, "generic_list.html", {"title": "Journal entries", "subtitle": "Posted journals are permanent. Corrections are made by approved reversal entries.",
                  "table": t, "page": page, "qs": qstr, "q": q, "search": "Search reference or description", "actions": actions,
                  "filters": [("source", "Source", [("", "All")] + [(s, s) for s in sorted(sources)], src)]})


@perm_required("accounting.view")
def journal_detail(request, pk):
    j = get_object_or_404(Journal.objects.select_related("posted_by", "reverses"), pk=pk)
    lines = j.lines.select_related("account")
    reversible = bool(can(request.user, "accounting.reverse") and handlers.reversal_domain_for(j))
    return render(request, "accounting/journal.html", {"j": j, "lines": lines, "reversible": reversible, "reversals": j.reversals.all(),
                  "total_dr": sum(l.debit for l in lines), "total_cr": sum(l.credit for l in lines)})


@require_POST
@perm_required("accounting.reverse")
def journal_reverse(request, pk):
    j = get_object_or_404(Journal, pk=pk)
    reason = request.POST.get("reason", "").strip()
    dom = handlers.reversal_domain_for(j)
    if not reason:
        messages.error(request, "A reason is required for every reversal.")
    elif not dom:
        messages.error(request, "This journal type cannot be reversed automatically. Post a manual correcting journal instead.")
    else:
        try:
            ref, req = appr.run_or_submit("reversal", j.total, f"Reverse {j.ref}: {reason}", {"domain": dom[0], "id": dom[1], "reason": reason, "journal": j.ref}, request.user)
        except ValidationError as e:
            _errs(request, e)
        else:
            if req:
                messages.warning(request, f"Reversal request {req.ref} sent to a second approver. Nothing changes until it is approved.")
                return redirect("approvals:queue")
            messages.success(request, f"Reversed ({ref}).")
    return redirect("accounting:journal", pk=pk)


@perm_required("accounting.journal")
def journal_new(request):
    accounts = list(Account.objects.filter(is_active=True))
    rows = []
    if request.method == "POST":
        codes, drs, crs, memos = (request.POST.getlist(k) for k in ("code", "debit", "credit", "memo"))
        lines = []
        for c, d, cr, m in zip(codes, drs, crs, memos):
            if c and (d or cr):
                rows.append({"code": c, "debit": d, "credit": cr, "memo": m})
                try:
                    lines.append((c, D(d or 0), D(cr or 0), m))
                except Exception:
                    messages.error(request, "Amounts must be numbers.")
                    lines = None
                    break
        desc = request.POST.get("description", "").strip()
        try:
            if lines is None:
                raise ValidationError("Fix the amounts.")
            if not desc:
                raise ValidationError("Description is required.")
            valid = {a.code for a in accounts}
            if any(l[0] not in valid for l in lines):
                raise ValidationError("Unknown account code.")
            dr, cr = sum(l[1] for l in lines), sum(l[2] for l in lines)
            if dr != cr or dr <= 0:
                raise ValidationError(f"Debits (UGX {dr:,.0f}) must equal credits (UGX {cr:,.0f}).")
            d = date.fromisoformat(request.POST.get("date") or str(today()))
            ref, req = appr.run_or_submit("manual_journal", dr, f"Manual journal: {desc}",
                                          {"lines": [[c, str(a), str(b), m] for c, a, b, m in lines], "description": desc, "date": str(d), "maker_id": request.user.pk}, request.user)
        except (ValidationError, ValueError) as e:
            _errs(request, e if isinstance(e, ValidationError) else ValidationError(str(e)))
        else:
            if req:
                messages.warning(request, f"Manual journals need a second approver. Request {req.ref} submitted.")
                return redirect("approvals:queue")
            messages.success(request, f"Journal {ref} posted.")
            return redirect("accounting:journals")
    return render(request, "accounting/journal_new.html", {"accounts": accounts, "rows": rows or [{}, {}], "today": today(), "desc": request.POST.get("description", "")})


@perm_required("accounting.view")
def ledger(request):
    accounts = Account.objects.all()
    acc = Account.objects.filter(pk=request.GET.get("account")).first() or accounts.first()
    f = request.GET.get("from") or str(month_start(today()))
    t = request.GET.get("to") or str(today())
    lines, opening, running, rows = [], ZERO, ZERO, []
    if acc:
        sign = 1 if acc.debit_normal else -1
        agg = JournalLine.objects.filter(account=acc, journal__date__lt=f).aggregate(d=Sum("debit"), c=Sum("credit"))
        opening = sign * (D(agg["d"]) - D(agg["c"]))
        running = opening
        for l in JournalLine.objects.filter(account=acc, journal__date__gte=f, journal__date__lte=t).select_related("journal").order_by("journal__date", "journal_id", "id"):
            running += sign * (l.debit - l.credit)
            rows.append((l, running))
    return render(request, "accounting/ledger.html", {"accounts": accounts, "acc": acc, "f": f, "t": t, "rows": rows, "opening": opening, "closing": running})


# ------------------------------------------------------------------ vouchers
class VoucherForm(StyledMixin, forms.Form):
    kind = forms.ChoiceField(choices=Voucher.KINDS, label="Type")
    account = forms.ModelChoiceField(queryset=Account.objects.filter(type__in=("expense", "income"), is_active=True), label="Expense / income account")
    amount = money_field("Amount (UGX)")
    party = forms.CharField(max_length=120, label="Payee / payer")
    description = forms.CharField(max_length=250)
    mode = forms.ChoiceField(choices=Voucher.MODES, label="Settled through")


@perm_required("accounting.view")
def voucher_list(request):
    q, kind = request.GET.get("q", "").strip(), request.GET.get("kind", "")
    qs = Voucher.objects.select_related("account", "created_by")
    if q:
        qs = qs.filter(Q(ref__icontains=q) | Q(party__icontains=q) | Q(description__icontains=q))
    if kind:
        qs = qs.filter(kind=kind)
    page, qstr = paginate(request, qs)
    t = Table(["Voucher", "Date", "Type", "Account", "Party", "Amount (UGX)", "Mode", "Status", ""])
    canpost = can(request.user, "accounting.post")
    for v in page:
        act = ""
        if canpost and v.mode == "credit" and v.status == "posted" and not v.settled_on:
            act = C("Settle", f"/accounting/vouchers/{v.pk}/settle/")
        elif v.mode == "credit" and v.settled_on:
            act = C("Settled", badge="ok")
        t.add([v.ref, f"{v.date:%d %b %Y}", v.get_kind_display(), v.account.name, v.party, M(v.amount), v.get_mode_display(), C(v.get_status_display(), badge=True), act])
    open_pay = Voucher.objects.filter(kind="expense", mode="credit", status="posted", settled_on__isnull=True).aggregate(s=Sum("amount"))["s"] or 0
    open_rec = Voucher.objects.filter(kind="income", mode="credit", status="posted", settled_on__isnull=True).aggregate(s=Sum("amount"))["s"] or 0
    return render(request, "generic_list.html", {"title": "Income & expenses", "subtitle": f"Accounts payable UGX {open_pay:,.0f} | Accounts receivable UGX {open_rec:,.0f}",
                  "table": t, "page": page, "qs": qstr, "q": q, "search": "Search voucher, party or description",
                  "actions": [("New voucher", "/accounting/vouchers/new/", "primary")] if canpost else [],
                  "filters": [("kind", "Type", [("", "All")] + Voucher.KINDS, kind)]})


@perm_required("accounting.post")
def voucher_new(request):
    form = VoucherForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            v, req = vouchers.create_voucher(d["kind"], d["account"], d["amount"], d["party"], d["description"], d["mode"], today(), request.user)
        except ValidationError as e:
            form.add_error(None, e)
        else:
            if req:
                messages.warning(request, f"Expense of UGX {v.amount:,.0f} is above the approval limit. It is held as {req.ref} and will NOT post until a second user approves.")
                return redirect("approvals:queue")
            messages.success(request, f"Voucher {v.ref} posted (journal {v.journal.ref}).")
            return redirect("accounting:vouchers")
    return render(request, "generic_form.html", {"title": "New income / expense voucher", "form": form, "submit": "Submit voucher", "cancel": "/accounting/vouchers/",
                  "intro": "Cash vouchers use your open till. 'On credit' creates a payable (expense) or receivable (income) to settle later."})


class SettleForm(StyledMixin, forms.Form):
    mode = forms.ChoiceField(choices=[("cash", "Cash (my till)"), ("bank", "Bank"), ("momo", "Mobile money")])


@perm_required("accounting.post")
def voucher_settle(request, pk):
    v = get_object_or_404(Voucher, pk=pk)
    form = SettleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            vouchers.settle_voucher(v, form.cleaned_data["mode"], request.user)
        except ValidationError as e:
            form.add_error(None, e)
        else:
            messages.success(request, f"{v.ref} settled.")
            return redirect("accounting:vouchers")
    return render(request, "generic_form.html", {"title": f"Settle {v.ref}", "form": form, "submit": "Record settlement", "cancel": "/accounting/vouchers/",
                  "intro": f"{v.party}: UGX {v.amount:,.0f}"})


# ------------------------------------------------------------------ banks & reconciliation
class BankForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = BankAccount
        fields = ["name", "bank_name", "account_number", "gl_account"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["gl_account"].queryset = Account.objects.filter(type="asset", code__in=("1010", "1020"))


class ReconForm(StyledMixin, forms.Form):
    as_of = forms.DateField(widget=DateInput(), initial=today)
    statement_balance = forms.DecimalField(max_digits=14, decimal_places=0, label="Closing balance per bank statement (UGX)")
    deposits_in_transit = forms.DecimalField(max_digits=14, decimal_places=0, required=False, initial=0, label="Deposits in transit (UGX)")
    outstanding_payments = forms.DecimalField(max_digits=14, decimal_places=0, required=False, initial=0, label="Outstanding payments (UGX)")
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)


@perm_required("accounting.view")
def banks(request):
    t = Table(["Account", "Bank", "Number", "Ledger balance (UGX)", ""])
    for b in BankAccount.objects.select_related("gl_account"):
        t.add([b.name, b.bank_name, b.account_number, M(gl.balance_of(b.gl_account.code)), C("Reconcile", f"/accounting/banks/{b.pk}/reconcile/") if can(request.user, "accounting.reconcile") else ""])
    r = Table(["Date", "Bank account", "Statement", "Ledger", "Difference", "Result", "Prepared by"])
    for x in BankReconciliation.objects.select_related("bank_account", "prepared_by")[:20]:
        r.add([f"{x.as_of:%d %b %Y}", x.bank_account.name, M(x.statement_balance), M(x.gl_balance), M(x.difference), C("Reconciled" if x.reconciled else "Unreconciled", badge="ok" if x.reconciled else "bad"), x.prepared_by.username])
    return render(request, "generic_list.html", {"title": "Bank accounts & reconciliation", "table": t, "table2": r, "table2_title": "Recent reconciliations",
                  "actions": [("Add bank account", "/accounting/banks/new/", "primary")] if can(request.user, "accounting.manage_coa") else []})


@perm_required("accounting.manage_coa")
def bank_new(request):
    form = BankForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        b = form.save()
        audit("bank.add", "finance", "BankAccount", b.pk, b.account_number, new={"name": b.name})
        messages.success(request, "Bank account added.")
        return redirect("accounting:banks")
    return render(request, "generic_form.html", {"title": "Add bank account", "form": form, "submit": "Save", "cancel": "/accounting/banks/"})


@perm_required("accounting.reconcile")
def reconcile(request, pk):
    b = get_object_or_404(BankAccount, pk=pk)
    form = ReconForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        ledger_bal = gl.balance_of(b.gl_account.code, d["as_of"])
        adjusted = d["statement_balance"] + D(d["deposits_in_transit"]) - D(d["outstanding_payments"])
        diff = adjusted - ledger_bal
        r = BankReconciliation.objects.create(bank_account=b, as_of=d["as_of"], statement_balance=d["statement_balance"], deposits_in_transit=D(d["deposits_in_transit"]),
                                              outstanding_payments=D(d["outstanding_payments"]), gl_balance=ledger_bal, difference=diff, notes=d["notes"], prepared_by=request.user)
        audit("bank.reconcile", "finance", "BankReconciliation", r.pk, b.account_number, new={"difference": diff, "statement": d["statement_balance"], "ledger": ledger_bal})
        (messages.success if diff == 0 else messages.warning)(request, "Reconciled - statement agrees with the ledger." if diff == 0 else f"Difference of UGX {diff:,.0f} remains. Investigate unrecorded items.")
        return redirect("accounting:banks")
    return render(request, "generic_form.html", {"title": f"Reconcile {b.name}", "form": form, "submit": "Run reconciliation", "cancel": "/accounting/banks/",
                  "intro": f"Ledger balance now: UGX {gl.balance_of(b.gl_account.code):,.0f}. Adjusted bank balance = statement + deposits in transit - outstanding payments."})


# ------------------------------------------------------------------ provisioning
@perm_required("accounting.provision", "accounting.view")
def provision(request):
    from loans.services import provision_required
    rows, required = provision_required()
    current = -gl.balance_of(gl.PROVISION)
    if request.method == "POST":
        if not can(request.user, "accounting.provision"):
            messages.error(request, "Not permitted.")
        else:
            try:
                j = handlers.post_provision(request.user)
                messages.success(request, f"Provision adjusted (journal {j.ref}).")
            except ValidationError as e:
                _errs(request, e)
        return redirect("accounting:provision")
    return render(request, "accounting/provision.html", {"rows": rows, "required": required, "current": current, "diff": required - current,
                                                         "can_post": can(request.user, "accounting.provision")})
