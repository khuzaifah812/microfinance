from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts_core.audit import audit
from accounts_core.rbac import can, perm_required
from approvals import services as appr
from core.models import SystemSetting
from core.ui import C, M, Table, paginate
from core.utils import D, today
from members.models import Member

from . import services
from .forms import InterestForm, MoneyForm, OpenAccountForm, TransferForm
from .models import InterestRun, SavingsAccount, SavingsTransaction


def _errors(request, e):
    for m in (e.messages if hasattr(e, "messages") else [str(e)]):
        messages.error(request, m)


@perm_required("savings.view")
def account_list(request):
    q = request.GET.get("q", "").strip()
    kind, st = request.GET.get("kind", ""), request.GET.get("status", "")
    qs = SavingsAccount.objects.select_related("member")
    if q:
        qs = qs.filter(Q(account_no__icontains=q) | Q(member__full_name__icontains=q) | Q(member__member_no__icontains=q))
    if kind:
        qs = qs.filter(kind=kind)
    if st:
        qs = qs.filter(status=st)
    totals = qs.aggregate(b=Sum("balance"), p=Sum("pledged"))
    page, qstr = paginate(request, qs)
    t = Table(["Account no.", "Member", "Type", "Balance (UGX)", "Pledged (UGX)", "Status", ""])
    for a in page:
        t.add([C(a.account_no, f"/savings/{a.pk}/"), f"{a.member.full_name} ({a.member.member_no})", a.get_kind_display(), M(a.balance), M(a.pledged),
               C(a.get_status_display(), badge=True), C("Open", f"/savings/{a.pk}/")])
    actions = []
    if can(request.user, "savings.open"):
        actions.append(("Open account", "/savings/open/", "primary"))
    if can(request.user, "savings.interest"):
        actions.append(("Interest & dividends", "/savings/interest/", "ghost"))
    return render(request, "generic_list.html", {
        "title": "Savings & share accounts", "subtitle": f"Showing balance UGX {D(totals['b']):,.0f}; pledged UGX {D(totals['p']):,.0f}.",
        "table": t, "page": page, "qs": qstr, "q": q, "search": "Search account no., member name or number", "actions": actions,
        "filters": [("kind", "Type", [("", "All")] + SavingsAccount.KINDS, kind), ("status", "Status", [("", "All")] + SavingsAccount.STATUS, st)]})


@perm_required("savings.open")
def account_open(request):
    initial = {}
    if request.GET.get("member"):
        m = Member.objects.filter(pk=request.GET["member"]).first()
        if m:
            initial["member_no"] = m.member_no
    form = OpenAccountForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            a = services.open_account(form.member, d["kind"], d["name"], request.user, d["account_no"], d["min_balance"])
        except ValidationError as e:
            form.add_error(None, e)
        else:
            messages.success(request, f"Account {a.account_no} opened.")
            return redirect("savings:detail", pk=a.pk)
    return render(request, "generic_form.html", {"title": "Open savings / share account", "form": form, "submit": "Open account", "cancel": "/savings/"})


@perm_required("savings.view")
def account_detail(request, pk):
    a = get_object_or_404(SavingsAccount.objects.select_related("member"), pk=pk)
    page, qstr = paginate(request, a.transactions.select_related("created_by"))
    return render(request, "savings/detail.html", {"a": a, "page": page, "qs": qstr, "min_balance": services.min_balance_for(a)})


@perm_required("savings.view")
def statement(request, pk):
    a = get_object_or_404(SavingsAccount.objects.select_related("member"), pk=pk)
    f, t = request.GET.get("from"), request.GET.get("to")
    qs = a.transactions.order_by("date", "id")
    if f:
        qs = qs.filter(date__gte=f)
    if t:
        qs = qs.filter(date__lte=t)
    return render(request, "savings/statement.html", {"a": a, "txs": qs, "f": f or "", "t": t or ""})


@perm_required("savings.transact")
def deposit(request, pk):
    a = get_object_or_404(SavingsAccount.objects.select_related("member"), pk=pk)
    form = MoneyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            tx = services.deposit(a, d["amount"], request.user, d["channel"], d["narration"])
        except ValidationError as e:
            form.add_error(None, e)
        else:
            messages.success(request, f"{'Share purchase' if a.kind == 'shares' else 'Deposit'} of UGX {tx.amount:,.0f} posted. Receipt {tx.ref}.")
            return redirect("savings:receipt", ref=tx.ref)
    return render(request, "generic_form.html", {"title": f"{'Buy shares' if a.kind == 'shares' else 'Deposit'} - {a.account_no}",
                  "intro": f"{a.member.full_name} | balance UGX {a.balance:,.0f}", "form": form, "submit": "Post transaction",  "confirm": "Post UGX {amount} to this account? A receipt is issued and the transaction cannot be edited afterwards, only reversed.", "confirm_title": "Confirm deposit", "cancel": f"/savings/{a.pk}/"})


@perm_required("savings.transact")
def withdraw(request, pk):
    a = get_object_or_404(SavingsAccount.objects.select_related("member"), pk=pk)
    form = MoneyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            tx, req = services.request_withdrawal(a, d["amount"], request.user, d["channel"], d["narration"])
        except ValidationError as e:
            form.add_error(None, e)
        else:
            if req:
                messages.warning(request, f"UGX {d['amount']:,.0f} exceeds the approval limit. Request {req.ref} was sent to a second approver; nothing has been posted yet.")
                return redirect("approvals:queue")
            messages.success(request, f"Withdrawal of UGX {tx.amount:,.0f} posted. Receipt {tx.ref}.")
            return redirect("savings:receipt", ref=tx.ref)
    return render(request, "generic_form.html", {"title": f"Withdraw - {a.account_no}", "form": form, "submit": "Post withdrawal",  "confirm": "Pay out UGX {amount} from this account?", "confirm_title": "Confirm withdrawal", "confirm_type": "warning", "cancel": f"/savings/{a.pk}/",
                  "intro": f"{a.member.full_name} | balance UGX {a.balance:,.0f} | pledged UGX {a.pledged:,.0f} | available UGX {max(0, a.balance - a.pledged - services.min_balance_for(a)):,.0f}"})


@perm_required("savings.transact")
def transfer(request, pk):
    a = get_object_or_404(SavingsAccount.objects.select_related("member"), pk=pk)
    form = TransferForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        dst = SavingsAccount.objects.filter(account_no__iexact=d["to_account"].strip()).first()
        try:
            if dst is None:
                raise ValidationError("Destination account not found.")
            if a.kind == "shares" and appr.needs_approval("share_transfer", d["amount"]):
                req = appr.submit("share_transfer", d["amount"], f"Share transfer UGX {d['amount']:,.0f} {a.account_no} -> {dst.account_no}",
                                  {"src": a.pk, "dst": dst.pk, "amount": str(d["amount"]), "narration": d["narration"], "maker_id": request.user.pk}, request.user)
                messages.warning(request, f"Share transfers above the limit need a second approver. Request {req.ref} submitted.")
                return redirect("approvals:queue")
            services.transfer(a, dst, d["amount"], request.user, d["narration"])
        except ValidationError as e:
            form.add_error(None, e)
        else:
            messages.success(request, "Transfer completed.")
            return redirect("savings:detail", pk=a.pk)
    return render(request, "generic_form.html", {"title": f"Transfer from {a.account_no}", "form": form, "submit": "Transfer",  "confirm": "Transfer UGX {amount}?", "confirm_title": "Confirm transfer", "cancel": f"/savings/{a.pk}/",
                  "intro": "Savings move between savings accounts; shares between share accounts (where permitted by policy)."})


@require_POST
@perm_required("savings.open")
def set_status(request, pk):
    a = get_object_or_404(SavingsAccount, pk=pk)
    new = request.POST.get("status")
    if new in dict(SavingsAccount.STATUS):
        if new == "closed" and (a.balance > 0 or a.pledged > 0):
            messages.error(request, "Only accounts with zero balance and no pledges can be closed.")
        else:
            old, a.status = a.status, new
            a.save(update_fields=["status"])
            audit("account.status", "savings", "SavingsAccount", a.pk, a.account_no, old={"status": old}, new={"status": new})
            messages.success(request, f"Account is now {a.get_status_display().lower()}.")
    return redirect("savings:detail", pk=pk)


@perm_required("savings.view")
def receipt(request, ref):
    tx = get_object_or_404(SavingsTransaction.objects.select_related("account__member", "created_by"), ref=ref)
    return render(request, "savings/receipt.html", {"tx": tx})


@perm_required("savings.interest")
def interest_run(request):
    form = InterestForm(request.POST or None, initial={"rate": SystemSetting.get("default_savings_interest")})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            run = services.post_interest(d["kind"], d["rate"], d["period"], request.user)
        except ValidationError as e:
            form.add_error(None, e)
        else:
            messages.success(request, f"Posted UGX {run.total:,.0f} to {run.accounts_count} accounts (journal {run.journal.ref}).")
            return redirect("savings:interest")
    t = Table(["Date", "Type", "Period", "Rate %", "Accounts", "Total (UGX)", "Journal", "By"])
    for r in InterestRun.objects.select_related("journal", "run_by")[:30]:
        t.add([f"{r.run_at:%d %b %Y}", r.get_kind_display(), r.period, r.rate, r.accounts_count, M(r.total), C(r.journal.ref, f"/accounting/journals/{r.journal_id}/"), r.run_by.username])
    return render(request, "generic_form.html", {"title": "Interest & dividend posting", "form": form, "submit": "Post to all eligible accounts",
                  "intro": "Creates one balanced journal and a credit on every eligible account. Cannot be repeated for the same period.",
                  "table": t, "table_title": "Previous runs", "cancel": "/savings/"})
