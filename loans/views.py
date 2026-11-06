from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts_core.audit import audit
from accounts_core.rbac import can, perm_required
from core.ui import C, M, Table, paginate
from core.utils import D, today
from members.models import Member
from savings.models import SavingsAccount

from . import calc, services
from .forms import ApplyForm, DecisionForm, DisburseForm, GuarantorForm, ProductForm, RepayForm, RescheduleForm, SettleForm
from .models import Loan, LoanProduct, LoanRepayment


def _acct(form, loan):
    no = (form.cleaned_data.get("savings_account_no") or "").strip()
    if form.cleaned_data["channel"] != "savings":
        return None
    a = SavingsAccount.objects.filter(account_no__iexact=no, member=loan.member, kind="savings").first()
    if not a:
        raise ValidationError("Enter one of the borrower's own savings account numbers.")
    return a


# ------------------------------------------------------------------ products
@perm_required("loans.view")
def product_list(request):
    t = Table(["Code", "Product", "Rate", "Method", "Amount range (UGX)", "Periods", "Fee %", "Penalty %", "Status", ""])
    can_edit = can(request.user, "loans.products")
    for p in LoanProduct.objects.all():
        rate = C(f"{p.interest_rate}% p.a.") if p.rate_set else C("Rate not set", badge="warn")
        t.add([p.code, p.name, rate, p.get_interest_method_display(), f"{p.min_amount:,.0f} - {p.max_amount:,.0f}", f"{p.min_term}-{p.max_term} {p.frequency}",
               p.processing_fee_pct, p.penalty_pct, C("Active" if p.is_active else "Inactive", badge=True), C("Edit", f"/loans/products/{p.pk}/edit/") if can_edit else ""])
    return render(request, "generic_list.html", {"title": "Loan products", "subtitle": "Interest rates are entered manually by an authorised administrator. Approved loans keep the terms agreed at application.",
                                                 "table": t, "actions": [("New product", "/loans/products/new/", "primary")] if can_edit else []})


@perm_required("loans.products")
def product_edit(request, pk=None):
    p = get_object_or_404(LoanProduct, pk=pk) if pk else None
    form = ProductForm(request.POST or None, instance=p)
    if request.method == "POST" and form.is_valid():
        old = {"rate": str(p.interest_rate), "method": p.interest_method} if p else None
        obj = form.save()
        audit("loanproduct.save", "loan", "LoanProduct", obj.pk, obj.code, old=old, new={"rate": obj.interest_rate, "method": obj.interest_method, "min": obj.min_amount, "max": obj.max_amount})
        messages.success(request, f"Product '{obj.name}' saved." + ("" if obj.rate_set else " Remember to set the interest rate before applications can be taken."))
        return redirect("loans:products")
    return render(request, "generic_form.html", {"title": "Edit loan product" if p else "New loan product", "form": form, "submit": "Save product", "cancel": "/loans/products/",
                  "intro": "Changing a rate here never changes loans already applied for or disbursed."})


# ------------------------------------------------------------------ list
@perm_required("loans.view")
def loan_list(request):
    q = request.GET.get("q", "").strip()
    st = request.GET.get("status", "")
    qs = Loan.objects.select_related("member", "product")
    if q:
        qs = qs.filter(Q(loan_no__icontains=q) | Q(member__full_name__icontains=q) | Q(member__member_no__icontains=q))
    if st:
        qs = qs.filter(status=st)
    page, qstr = paginate(request, qs)
    t = Table(["Loan no.", "Member", "Product", "Principal (UGX)", "Terms", "Applied", "Status", ""])
    for l in page:
        t.add([C(l.loan_no, f"/loans/{l.pk}/"), f"{l.member.full_name} ({l.member.member_no})", l.product.name, M(l.principal),
               f"{l.interest_rate}% {l.get_interest_method_display().lower()}, {l.term} {l.frequency}", f"{l.applied_on:%d %b %Y}",
               C(l.get_status_display(), badge=True), C("Open", f"/loans/{l.pk}/")])
    actions = []
    if can(request.user, "loans.apply"):
        actions.append(("New application", "/loans/apply/", "primary"))
    actions += [("Arrears", "/loans/arrears/", "ghost"), ("Calculator", "/loans/calculator/", "ghost")]
    return render(request, "generic_list.html", {"title": "Loans", "subtitle": "Applications, approvals, disbursements and repayments.", "table": t, "page": page, "qs": qstr, "q": q,
                  "search": "Search loan no. or member", "actions": actions, "filters": [("status", "Status", [("", "All")] + Loan.STATUS, st)]})


# ------------------------------------------------------------------ apply
@perm_required("loans.apply")
def loan_apply(request):
    initial = {}
    refi = None
    if request.GET.get("topup"):
        refi = Loan.objects.filter(pk=request.GET["topup"], status="active").select_related("member").first()
        if refi:
            initial = {"member_no": refi.member.member_no, "product": refi.product_id, "refinance_of": refi.pk}
    elif request.GET.get("member"):
        m = Member.objects.filter(pk=request.GET["member"]).first()
        if m:
            initial["member_no"] = m.member_no
    form = ApplyForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        old = Loan.objects.filter(pk=d["refinance_of"]).first() if d.get("refinance_of") else None
        try:
            loan = services.apply_for_loan(form.member, d["product"], d["amount"], d["term"], d["purpose"], request.user, refinance_of=old)
        except ValidationError as e:
            form.add_error(None, e)
        else:
            messages.success(request, f"Application {loan.loan_no} captured. Add guarantors, then send for review.")
            return redirect("loans:detail", pk=loan.pk)
    return render(request, "loans/apply.html", {"form": form, "refi": refi})


@perm_required("loans.view")
def preview(request):
    """Live repayment preview for the application form / calculator (no data is saved)."""
    try:
        p = LoanProduct.objects.get(pk=request.GET["product"])
        if not p.rate_set:
            return JsonResponse({"error": "Rate not set for this product."}, status=200)
        amount, term = D(request.GET["amount"]), int(request.GET["term"])
        if not (p.min_amount <= amount <= p.max_amount) or not (p.min_term <= term <= p.max_term):
            return JsonResponse({"error": f"Amount must be {p.min_amount:,.0f}-{p.max_amount:,.0f} and period {p.min_term}-{p.max_term}."})
        rows = calc.build_schedule(amount, p.interest_rate, term, p.interest_method, p.frequency, services.date_next(p.frequency, today()))
        _, ti, tp = calc.totals(rows)
        fee = amount * p.processing_fee_pct / 100
        return JsonResponse({"rate": str(p.interest_rate), "method": p.get_interest_method_display(), "total_interest": str(ti), "total_payable": str(tp), "fee": str(round(fee)),
                             "first": str(rows[0]["principal"] + rows[0]["interest"]),
                             "rows": [{"no": r["no"], "due": r["due"].strftime("%d %b %Y"), "principal": str(r["principal"]), "interest": str(r["interest"]),
                                       "total": str(r["principal"] + r["interest"])} for r in rows]})
    except (KeyError, ValueError, ArithmeticError, LoanProduct.DoesNotExist):
        return JsonResponse({"error": "Enter a product, amount and period."})


@perm_required("loans.view")
def calculator(request):
    return render(request, "loans/calculator.html", {"products": LoanProduct.objects.filter(is_active=True)})


# ------------------------------------------------------------------ detail & workflow
def _flags(request, loan):
    u = request.user
    stage = services.STAGES.get(loan.status)
    prior = set(loan.approvals.values_list("user_id", flat=True))
    f = {}
    f["can_decide"] = bool(stage and can(u, stage[1]) and loan.applied_by_id != u.id and u.id not in prior)
    f["decide_blocked_self"] = bool(stage and can(u, stage[1]) and (loan.applied_by_id == u.id or u.id in prior))
    f["stage_label"] = dict(Loan.STATUS).get(loan.status)
    f["can_disburse"] = loan.status == "approved" and can(u, "loans.disburse") and loan.applied_by_id != u.id and u.id not in prior
    f["can_repay"] = loan.status == "active" and can(u, "loans.repay")
    f["can_settle"] = loan.status == "active" and can(u, "loans.settle")
    f["can_reschedule"] = loan.status == "active" and can(u, "loans.reschedule")
    f["can_topup"] = loan.status == "active" and can(u, "loans.apply")
    f["can_guarantor"] = loan.status in Loan.PENDING and can(u, "loans.apply")
    return f


@perm_required("loans.view")
def loan_detail(request, pk):
    loan = get_object_or_404(Loan.objects.select_related("member", "product", "applied_by", "refinance_of"), pk=pk)
    insts = list(loan.installments.all())
    overdue_amt, days, n = services.overdue_info(loan)
    ctx = {"loan": loan, "insts": insts, "guarantors": loan.guarantors.select_related("guarantor", "account"), "approvals": loan.approvals.select_related("user"),
           "repayments": loan.repayments.all()[:30], "overdue": overdue_amt, "days_overdue": days, "decision_form": DecisionForm(),
           "gform": GuarantorForm(), "pen_unpaid": sum((i.penalty_balance for i in insts if i.status != "superseded"), 0),
           "quote": services.settlement_quote(loan) if loan.status == "active" else None, **_flags(request, loan)}
    if loan.status == "active":
        ctx["next_due"] = next((i for i in insts if i.status in services.ACTIVE_INST), None)
    return render(request, "loans/detail.html", ctx)


@require_POST
@perm_required("loans.apply")
def add_guarantor(request, pk):
    loan = get_object_or_404(Loan, pk=pk)
    form = GuarantorForm(request.POST)
    if form.is_valid():
        d = form.cleaned_data
        g = Member.objects.filter(member_no__iexact=d["guarantor_no"].strip()).first()
        a = SavingsAccount.objects.filter(account_no__iexact=d["account_no"].strip()).first()
        try:
            if not g or not a:
                raise ValidationError("Guarantor member or savings account not found.")
            services.add_guarantor(loan, g, a, d["amount"], request.user)
            messages.success(request, f"UGX {d['amount']:,.0f} of {g.full_name}'s savings is now reserved against this loan.")
        except ValidationError as e:
            for m in e.messages:
                messages.error(request, m)
    else:
        messages.error(request, "Please complete all guarantor fields correctly.")
    return redirect("loans:detail", pk=pk)


@require_POST
@perm_required("loans.officer", "loans.committee", "loans.manager")
def decide(request, pk):
    loan = get_object_or_404(Loan, pk=pk)
    form = DecisionForm(request.POST)
    if form.is_valid():
        try:
            services.stage_action(loan, request.user, form.cleaned_data["decision"], form.cleaned_data["comment"])
            messages.success(request, "Decision recorded.")
        except ValidationError as e:
            for m in e.messages:
                messages.error(request, m)
        except PermissionDenied:
            messages.error(request, "Your role cannot act on this stage.")
    return redirect("loans:detail", pk=pk)


@perm_required("loans.disburse")
def disburse(request, pk):
    loan = get_object_or_404(Loan.objects.select_related("member"), pk=pk)
    form = DisburseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.disburse(loan, request.user, form.cleaned_data["channel"], savings_account=_acct(form, loan))
        except ValidationError as e:
            form.add_error(None, e)
        else:
            messages.success(request, f"Loan {loan.loan_no} disbursed. The repayment schedule has been generated.")
            return redirect("loans:detail", pk=pk)
    fee = loan.principal * loan.processing_fee_pct / 100
    return render(request, "generic_form.html", {"title": f"Disburse {loan.loan_no}", "form": form, "submit": "Confirm disbursement",  "confirm": "Release the loan funds now? This posts the disbursement to the ledger and starts the repayment schedule.", "confirm_title": "Confirm disbursement", "confirm_type": "warning", "cancel": f"/loans/{pk}/",
                  "intro": f"{loan.member.full_name}: principal UGX {loan.principal:,.0f}, processing fee UGX {fee:,.0f}." +
                           (f" This loan refinances {loan.refinance_of.loan_no}; its settlement is deducted from the proceeds." if loan.refinance_of_id else "")})


@perm_required("loans.repay")
def repay(request, pk):
    loan = get_object_or_404(Loan.objects.select_related("member"), pk=pk)
    form = RepayForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            rep = services.repay(loan, d["amount"], request.user, d["channel"], note=d["note"], savings_account=_acct(form, loan))
        except ValidationError as e:
            form.add_error(None, e)
        else:
            messages.success(request, f"Repayment {rep.ref} posted: principal {rep.principal:,.0f}, interest {rep.interest:,.0f}, penalty {rep.penalty:,.0f}.")
            return redirect("loans:detail", pk=pk)
    amt, days, n = services.overdue_info(loan)
    return render(request, "generic_form.html", {"title": f"Repayment - {loan.loan_no}", "form": form, "submit": "Post repayment",  "confirm": "Post a repayment of UGX {amount}?", "confirm_title": "Confirm repayment", "cancel": f"/loans/{pk}/",
                  "intro": f"{loan.member.full_name} | outstanding principal UGX {loan.outstanding_principal:,.0f} | overdue UGX {amt:,.0f}. Payments clear penalties first, then interest, then principal, oldest instalment first."})


@perm_required("loans.settle")
def settle(request, pk):
    loan = get_object_or_404(Loan.objects.select_related("member"), pk=pk)
    quote = services.settlement_quote(loan)
    form = SettleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.settle(loan, request.user, form.cleaned_data["channel"], savings_account=_acct(form, loan))
        except ValidationError as e:
            form.add_error(None, e)
        else:
            messages.success(request, f"Loan {loan.loan_no} settled early and cleared.")
            return redirect("loans:detail", pk=pk)
    return render(request, "generic_form.html", {"title": f"Early settlement - {loan.loan_no}", "form": form, "submit": f"Pay UGX {quote['total']:,.0f} and clear loan", "cancel": f"/loans/{pk}/",
                  "confirm": f"Collect UGX {quote['total']:,.0f} and close this loan? Future interest is waived and guarantor pledges are released.", "confirm_title": "Confirm early settlement", "confirm_type": "warning",
                  "intro": f"Principal {quote['principal']:,.0f} + interest due {quote['interest']:,.0f} + penalties {quote['penalty']:,.0f} = UGX {quote['total']:,.0f}. "
                           f"Interest not yet due (UGX {quote['waived']:,.0f}) is waived."})


@perm_required("loans.reschedule")
def reschedule(request, pk):
    loan = get_object_or_404(Loan.objects.select_related("member"), pk=pk)
    form = RescheduleForm(request.POST or None, initial={"term": loan.term})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            done, req = services.request_reschedule(loan, d["term"], d["rate"], d["reason"], request.user)
        except ValidationError as e:
            form.add_error(None, e)
        else:
            if req:
                messages.warning(request, f"Rescheduling needs a second approver. Request {req.ref} submitted; the schedule is unchanged until approved.")
                return redirect("approvals:queue")
            messages.success(request, "Loan rescheduled.")
            return redirect("loans:detail", pk=pk)
    return render(request, "generic_form.html", {"title": f"Reschedule {loan.loan_no}", "form": form, "submit": "Submit rescheduling", "cancel": f"/loans/{pk}/",
                  "intro": f"Outstanding principal UGX {loan.outstanding_principal:,.0f}. Unpaid interest already due is carried forward; the old schedule is kept as history."})


@perm_required("loans.view")
def statement(request, pk):
    loan = get_object_or_404(Loan.objects.select_related("member", "product"), pk=pk)
    return render(request, "loans/statement.html", {"loan": loan, "insts": loan.installments.all(), "repayments": loan.repayments.filter(is_reversed=False).order_by("date", "id")})


@perm_required("loans.view")
def certificate(request, pk):
    loan = get_object_or_404(Loan.objects.select_related("member", "product"), pk=pk)
    if loan.status != "cleared":
        messages.error(request, "A clearance certificate is only issued for fully repaid loans.")
        return redirect("loans:detail", pk=pk)
    audit("loan.certificate", "loan", "Loan", loan.pk, loan.loan_no, detail="Clearance certificate printed")
    return render(request, "loans/certificate.html", {"loan": loan})


@perm_required("loans.view")
def arrears(request):
    snap = services.portfolio_snapshot()
    rows = []
    for l in Loan.objects.filter(pk__in=list(snap["first_overdue"])).select_related("member").prefetch_related("installments"):
        amt, days, n = services.overdue_info(l)
        rows.append((days, l, amt, n))
    rows.sort(key=lambda r: -r[0])
    t = Table(["Loan", "Member", "Overdue instalments", "Arrears (UGX)", "Days overdue", "Outstanding principal (UGX)", "Penalties unpaid (UGX)"])
    from core.templatetags.sacco import mask_phone
    for days, l, amt, n in rows:
        pen = sum((i.penalty_balance for i in l.installments.all() if i.status != "superseded"), 0)
        t.add([C(l.loan_no, f"/loans/{l.pk}/"), f"{l.member.full_name} ({mask_phone(l.member.phone)})", n, M(amt), C(days, badge="bad" if days > 30 else "warn"), M(l.outstanding_principal), M(pen)])
    actions = [("Accrue penalties", "/loans/penalties/", "post")] if can(request.user, "loans.penalties") else []
    return render(request, "generic_list.html", {"title": "Loans in arrears", "table": t, "actions": actions,
                  "subtitle": f"Arrears UGX {snap['arrears']:,.0f} on {snap['loans_in_arrears']} loans. Portfolio at risk {snap['par']:.1f}%."})


@require_POST
@perm_required("loans.penalties")
def run_penalties(request):
    total, n = services.accrue_penalties(user=request.user)
    messages.success(request, f"Penalties accrued: UGX {total:,.0f} on {n} instalment(s). They are recognised as income when collected.")
    return redirect("loans:arrears")
