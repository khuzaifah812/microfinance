import math
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Q, Sum

from accounts_core.audit import audit
from accounts_core.rbac import can
from accounting import services as gl
from approvals import services as appr
from cashier import services as cash
from core.models import next_ref
from core.utils import ZERO, D, add_months, q0, today
from savings.models import SavingsAccount, SavingsTransaction

from . import calc
from .models import Loan, LoanApproval, LoanGuarantor, LoanInstallment, LoanProduct, LoanRepayment

ACTIVE_INST = ("pending", "partial")


# ----------------------------------------------------------------------- application
def member_savings_total(member):
    return SavingsAccount.objects.filter(member=member, kind="savings", status__in=("active", "dormant")).aggregate(s=Sum("balance"))["s"] or ZERO


@transaction.atomic
def apply_for_loan(member, product, amount, term, purpose, user, refinance_of=None):
    amount, term = D(amount), int(term)
    if member.status != "active":
        raise ValidationError("Only active members can apply for loans.")
    if not product.is_active:
        raise ValidationError("This loan product is not currently offered.")
    if not product.rate_set:
        raise ValidationError(f"Rate not set for '{product.name}'. An administrator must enter the interest rate before applications can be taken.")
    if not (product.min_amount <= amount <= product.max_amount):
        raise ValidationError(f"Amount must be between UGX {product.min_amount:,.0f} and UGX {product.max_amount:,.0f}.")
    if amount != amount.to_integral_value():
        raise ValidationError("Loan amount must be in whole shillings.")
    if not (product.min_term <= term <= product.max_term):
        raise ValidationError(f"Repayment period must be between {product.min_term} and {product.max_term} {product.frequency} periods.")
    if product.savings_multiple:
        limit = member_savings_total(member) * product.savings_multiple
        if amount > limit:
            raise ValidationError(f"Eligibility: loan cannot exceed {product.savings_multiple}x savings (UGX {limit:,.0f}).")
    if refinance_of is not None:
        if refinance_of.member_id != member.pk or refinance_of.status != "active":
            raise ValidationError("Only the member's own active loan can be topped-up / refinanced.")
    loan = Loan.objects.create(
        loan_no=next_ref("LN"), member=member, product=product, principal=amount, interest_rate=product.interest_rate,
        interest_method=product.interest_method, frequency=product.frequency, term=term,
        processing_fee_pct=product.processing_fee_pct, penalty_pct=product.penalty_pct, grace_periods=product.grace_periods,
        purpose=purpose[:200], applied_by=user, refinance_of=refinance_of)
    audit("loan.apply", "loan", "Loan", loan.pk, loan.loan_no,
          new={"member": member.member_no, "amount": amount, "rate": loan.interest_rate, "method": loan.interest_method, "term": term},
          detail=f"Application by {user.username}", user=user)
    from notifications.services import notify
    notify("loan_application", f"New loan application {loan.loan_no}", f"{member.full_name} - UGX {amount:,.0f}", roles=["loan_officer"],
           link=f"/loans/{loan.pk}/", level="info")
    return loan


@transaction.atomic
def add_guarantor(loan, guarantor, account, amount, user):
    loan = Loan.objects.select_for_update().get(pk=loan.pk)
    amount = D(amount)
    if loan.status not in Loan.PENDING:
        raise ValidationError("Guarantors can only be added while the application is pending.")
    if guarantor.pk == loan.member_id:
        raise ValidationError("A member cannot guarantee their own loan.")
    if guarantor.status != "active":
        raise ValidationError("The guarantor must be an active member.")
    if amount <= 0:
        raise ValidationError("Pledge must be greater than zero.")
    acct = SavingsAccount.objects.select_for_update().get(pk=account.pk)
    if acct.member_id != guarantor.pk or acct.kind != "savings" or acct.status != "active":
        raise ValidationError("Choose an active savings account belonging to the guarantor.")
    if loan.guarantors.filter(guarantor=guarantor, status="active").exists():
        raise ValidationError("This member already guarantees this loan.")
    eligible = D(acct.balance) - D(acct.pledged)
    if amount > eligible:
        raise ValidationError(f"Only UGX {eligible:,.0f} of this account is free to pledge; the rest is already reserved. "
                              "The same savings cannot back several loans.")
    acct.pledged += amount
    acct.save(update_fields=["pledged"])
    g = LoanGuarantor.objects.create(loan=loan, guarantor=guarantor, account=acct, amount=amount)
    audit("loan.guarantor_add", "loan", "Loan", loan.pk, loan.loan_no, new={"guarantor": guarantor.member_no, "pledge": amount, "account": acct.account_no}, user=user)
    return g


def release_pledges(loan):
    from django.utils import timezone
    for g in loan.guarantors.select_related("account").filter(status="active"):
        acct = SavingsAccount.objects.select_for_update().get(pk=g.account_id)
        acct.pledged = max(ZERO, D(acct.pledged) - g.amount)
        acct.save(update_fields=["pledged"])
        g.status, g.released_at = "released", timezone.now()
        g.save(update_fields=["status", "released_at"])


STAGES = {
    "pending_officer": ("officer", "loans.officer", "pending_committee"),
    "pending_committee": ("committee", "loans.committee", "pending_manager"),
    "pending_manager": ("manager", "loans.manager", "approved"),
}
NEXT_ROLES = {"pending_committee": ["committee"], "pending_manager": ["manager"], "approved": ["cashier", "accountant", "manager"]}


@transaction.atomic
def stage_action(loan, user, decision, comment=""):
    """Three-stage workflow. Maker (applicant) can never approve; each stage needs a different person."""
    loan = Loan.objects.select_for_update().get(pk=loan.pk)
    if loan.status not in STAGES:
        raise ValidationError("This application is not awaiting a decision.")
    stage, perm, nxt = STAGES[loan.status]
    if not can(user, perm):
        raise PermissionDenied
    if loan.applied_by_id == user.id:
        raise ValidationError("You cannot review or approve a loan application that you captured yourself.")
    if loan.approvals.filter(user=user).exists():
        raise ValidationError("Each approval stage must be completed by a different person.")
    if decision not in ("approve", "reject"):
        raise ValidationError("Invalid decision.")
    comment = (comment or "").strip()
    if decision == "reject":
        if not comment:
            raise ValidationError("Please record the reason for rejecting.")
        loan.status = "rejected"
        release_pledges(loan)
    else:
        if stage == "officer":
            need = loan.product.guarantors_required
            have = loan.guarantors.filter(status="active").count()
            if have < need:
                raise ValidationError(f"This product requires {need} guarantor(s); {have} recorded. Add guarantors before recommending.")
        loan.status = nxt
    loan.save(update_fields=["status"])
    LoanApproval.objects.create(loan=loan, stage=stage, decision="reject" if decision == "reject" else ("recommend" if stage == "officer" else "approve"),
                                user=user, comment=comment)
    audit(f"loan.{stage}_{decision}", "loan", "Loan", loan.pk, loan.loan_no, new={"status": loan.status}, detail=comment[:200], user=user, approval=f"{stage} by {user.username}")
    from notifications.services import notify
    if loan.status in NEXT_ROLES:
        notify("loan_workflow", f"{loan.loan_no} awaiting next step", f"{loan.member.full_name} UGX {loan.principal:,.0f}",
               roles=NEXT_ROLES[loan.status], link=f"/loans/{loan.pk}/", level="warn")
    return loan


# ----------------------------------------------------------------------- allocation
def allocate(loan, amount, as_of=None):
    """Applies a payment oldest-instalment-first: penalty -> interest -> principal. Returns (p, i, pen, allocations)."""
    amount = D(amount)
    insts = list(LoanInstallment.objects.select_for_update().filter(loan=loan, status__in=ACTIVE_INST).order_by("no"))
    remaining_total = sum((i.balance + i.penalty_balance for i in insts), ZERO)
    if amount > remaining_total:
        raise ValidationError(f"Payment exceeds the amount outstanding on this loan (UGX {remaining_total:,.0f}). Use early settlement for a full payoff.")
    left, sp, si, spen, allocs = amount, ZERO, ZERO, ZERO, []
    for inst in insts:
        if left <= 0:
            break
        pen = min(left, inst.penalty_balance)
        left -= pen
        inte = min(left, inst.interest - inst.interest_paid)
        left -= inte
        prin = min(left, inst.principal - inst.principal_paid)
        left -= prin
        if pen or inte or prin:
            inst.penalty_paid += pen
            inst.interest_paid += inte
            inst.principal_paid += prin
            inst.status = "paid" if inst.balance == 0 and inst.penalty_balance == 0 else "partial"
            inst.save()
            allocs.append([inst.pk, str(prin), str(inte), str(pen)])
            sp, si, spen = sp + prin, si + inte, spen + pen
    loan.principal_paid += sp
    loan.interest_paid += si
    loan.penalty_paid += spen
    return sp, si, spen, allocs


def _maybe_clear(loan, as_of, user):
    open_left = loan.installments.filter(status__in=ACTIVE_INST).exists()
    if not open_left and loan.principal_paid >= loan.principal:
        loan.status, loan.cleared_on = "cleared", as_of
        release_pledges(loan)
        audit("loan.cleared", "loan", "Loan", loan.pk, loan.loan_no, detail="Loan fully repaid", user=user)
        from notifications.services import notify
        notify("loan_cleared", f"Loan {loan.loan_no} cleared", loan.member.full_name, roles=["loan_officer", "manager"], link=f"/loans/{loan.pk}/", level="ok")


# ----------------------------------------------------------------------- repayment
def _payment_lines(channel, amount, sp, si, spen, label, savings_account=None):
    src = gl.SAVINGS if channel == "savings" else gl.CHANNEL_GL[channel]
    lines = [(src, amount, 0, label)]
    if sp:
        lines.append((gl.LOANS, 0, sp, "Principal"))
    if si:
        lines.append((gl.INT_INCOME, 0, si, "Interest"))
    if spen:
        lines.append((gl.PEN_INCOME, 0, spen, "Penalty"))
    return lines


@transaction.atomic
def repay(loan, amount, user, channel="cash", date=None, note="", savings_account=None, external_ref=""):
    amount = D(amount)
    loan = Loan.objects.select_for_update().get(pk=loan.pk)
    if loan.status != "active":
        raise ValidationError("Repayments can only be posted to active loans.")
    if amount <= 0 or amount != amount.to_integral_value():
        raise ValidationError("Enter a positive whole-shilling amount.")
    date = date or today()
    till = cash.require_till(user) if channel == "cash" else None
    acct = None
    if channel == "savings":
        if savings_account is None or savings_account.member_id != loan.member_id:
            raise ValidationError("Choose one of the borrower's own savings accounts.")
        acct = SavingsAccount.objects.select_for_update().get(pk=savings_account.pk)
        from savings.services import validate_withdrawal
        validate_withdrawal(acct, amount)
    sp, si, spen, allocs = allocate(loan, amount, date)
    ref = next_ref("RP")
    j = gl.post_journal(_payment_lines(channel, amount, sp, si, spen, loan.loan_no), f"Loan repayment {loan.loan_no} {note}".strip(), date, "loan_repay", ref, user=user)
    if acct:
        acct.balance -= amount
        acct.save(update_fields=["balance"])
        SavingsTransaction.objects.create(ref=next_ref("TX"), account=acct, kind="withdrawal", amount=amount, balance_after=acct.balance,
                                          date=date, channel="internal", narration=f"Loan repayment {loan.loan_no}", journal=j, created_by=user)
    rep = LoanRepayment.objects.create(ref=ref, loan=loan, date=date, amount=amount, principal=sp, interest=si, penalty=spen, channel=channel,
                                       allocations=allocs, journal=j, till=till, note=(note or external_ref)[:160], created_by=user)
    if till:
        cash.record_cash(till, "in", "loan_repayment", amount, ref, f"Repayment {loan.loan_no}", j, user)
    loan.save()
    _maybe_clear(loan, date, user)
    loan.save()
    audit("loan.repay", "finance", "Loan", loan.pk, ref, new={"amount": amount, "principal": sp, "interest": si, "penalty": spen, "journal": j.ref},
          detail=f"Repayment {loan.loan_no}", user=user)
    return rep


@transaction.atomic
def reverse_repayment(rep, reason, user):
    rep = LoanRepayment.objects.select_for_update().get(pk=rep.pk)
    if rep.is_reversed:
        raise ValidationError("This repayment was already reversed.")
    loan = Loan.objects.select_for_update().get(pk=rep.loan_id)
    till = None
    if rep.till_id:
        till = cash.Till.objects.select_for_update().get(pk=rep.till_id)
        if till.status != "open":
            raise ValidationError("The original till is closed. Post a manual correcting journal instead.")
    for iid, p, i, pen in rep.allocations:
        inst = LoanInstallment.objects.select_for_update().get(pk=iid)
        inst.principal_paid -= D(p)
        inst.interest_paid -= D(i)
        inst.penalty_paid -= D(pen)
        if inst.status != "superseded":
            inst.status = "pending" if (inst.principal_paid + inst.interest_paid + inst.penalty_paid) == 0 else "partial"
        inst.save()
    loan.principal_paid -= rep.principal
    loan.interest_paid -= rep.interest
    loan.penalty_paid -= rep.penalty
    if loan.status == "cleared":
        loan.status, loan.cleared_on = "active", None
    loan.save()
    gl.reverse_journal_gl(rep.journal, reason, user=user)
    rep.is_reversed = True
    rep.save(update_fields=["is_reversed"])
    if till:
        cash.record_cash(till, "out", "reversal", rep.amount, rep.ref, f"Reversal of {rep.ref}", None, user)
    audit("loan.repay_reverse", "finance", "LoanRepayment", rep.pk, rep.ref, old={"amount": rep.amount}, detail=reason, user=user)
    return rep


# ----------------------------------------------------------------------- penalties, arrears
def overdue_info(loan, as_of=None):
    as_of = as_of or today()
    rows = [i for i in loan.installments.all() if i.status in ACTIVE_INST and i.due_date < as_of and i.balance > 0]
    if not rows:
        return ZERO, 0, 0
    amt = sum((i.balance for i in rows), ZERO)
    days = (as_of - min(i.due_date for i in rows)).days
    return amt, days, len(rows)


@transaction.atomic
def accrue_penalties(as_of=None, user=None):
    """Idempotent: charges penalty_pct of the unpaid instalment for every full/part 30-day period overdue."""
    as_of = as_of or today()
    total, n = ZERO, 0
    qs = (LoanInstallment.objects.select_for_update().select_related("loan")
          .filter(loan__status="active", status__in=ACTIVE_INST, due_date__lt=as_of))
    for inst in qs:
        if inst.loan.penalty_pct <= 0 or inst.balance <= 0:
            continue
        periods = ((as_of - inst.due_date).days + 29) // 30
        new = periods - inst.penalty_periods
        if new > 0:
            pen = q0(inst.loan.penalty_pct / 100 * inst.balance * new)
            inst.penalty_accrued += pen
            inst.penalty_periods = periods
            inst.save(update_fields=["penalty_accrued", "penalty_periods"])
            total += pen
            n += 1
    if n:
        audit("loan.penalties", "loan", detail=f"Accrued UGX {total:,.0f} penalties on {n} instalments as of {as_of}", new={"total": total, "count": n}, user=user)
    return total, n


# ----------------------------------------------------------------------- settlement
def settlement_quote(loan, as_of=None):
    """Outstanding principal + interest on instalments already due + unpaid penalties. Future interest is waived."""
    as_of = as_of or today()
    insts = loan.installments.filter(status__in=ACTIVE_INST)
    principal = sum((i.principal - i.principal_paid for i in insts), ZERO)
    interest_due = sum((i.interest - i.interest_paid for i in insts if i.due_date <= as_of), ZERO)
    penalty = sum((i.penalty_balance for i in insts), ZERO)
    waived = sum((i.interest - i.interest_paid for i in insts if i.due_date > as_of), ZERO)
    return {"principal": principal, "interest": interest_due, "penalty": penalty, "waived": waived,
            "total": principal + interest_due + penalty}


def _settle_core(loan, as_of):
    quote = settlement_quote(loan, as_of)
    for inst in LoanInstallment.objects.select_for_update().filter(loan=loan, status__in=ACTIVE_INST, due_date__gt=as_of):
        w = inst.interest - inst.interest_paid
        if w > 0:
            inst.interest = inst.interest_paid
            inst.save(update_fields=["interest"])
    loan.interest_waived += quote["waived"]
    loan.total_interest -= quote["waived"]
    loan.total_payable -= quote["waived"]
    sp, si, spen, allocs = allocate(loan, quote["total"], as_of) if quote["total"] > 0 else (ZERO, ZERO, ZERO, [])
    return quote, sp, si, spen, allocs


@transaction.atomic
def settle(loan, user, channel="cash", date=None, savings_account=None):
    loan = Loan.objects.select_for_update().get(pk=loan.pk)
    if loan.status != "active":
        raise ValidationError("Only active loans can be settled early.")
    date = date or today()
    till = None
    quote = settlement_quote(loan, date)
    if channel == "cash":
        till = cash.require_till(user)
    acct = None
    if channel == "savings":
        acct = SavingsAccount.objects.select_for_update().get(pk=savings_account.pk) if savings_account else None
        if acct is None or acct.member_id != loan.member_id:
            raise ValidationError("Choose one of the borrower's own savings accounts.")
        from savings.services import validate_withdrawal
        validate_withdrawal(acct, quote["total"])
    quote, sp, si, spen, allocs = _settle_core(loan, date)
    ref = next_ref("RP")
    j = gl.post_journal(_payment_lines(channel, quote["total"], sp, si, spen, loan.loan_no), f"Early settlement {loan.loan_no}", date, "loan_repay", ref, user=user)
    if acct:
        acct.balance -= quote["total"]
        acct.save(update_fields=["balance"])
        SavingsTransaction.objects.create(ref=next_ref("TX"), account=acct, kind="withdrawal", amount=quote["total"], balance_after=acct.balance,
                                          date=date, channel="internal", narration=f"Early settlement {loan.loan_no}", journal=j, created_by=user)
    rep = LoanRepayment.objects.create(ref=ref, loan=loan, date=date, amount=quote["total"], principal=sp, interest=si, penalty=spen, channel=channel,
                                       allocations=allocs, journal=j, till=till, note="Early settlement", created_by=user)
    if till:
        cash.record_cash(till, "in", "loan_settlement", quote["total"], ref, f"Settlement {loan.loan_no}", j, user)
    loan.save()
    _maybe_clear(loan, date, user)
    loan.save()
    audit("loan.settle", "finance", "Loan", loan.pk, ref, new={"paid": quote["total"], "interest_waived": quote["waived"]}, detail="Early settlement", user=user)
    return rep


# ----------------------------------------------------------------------- disbursement
def first_due_after(loan, date):
    n = 1 + loan.grace_periods
    return date + timedelta(days=7 * n) if loan.frequency == "weekly" else add_months(date, n)


@transaction.atomic
def disburse(loan, user, channel="cash", date=None, savings_account=None):
    loan = Loan.objects.select_for_update().get(pk=loan.pk)
    if loan.status != "approved":
        raise ValidationError("Only fully approved loans can be disbursed.")
    if loan.applied_by_id == user.id:
        raise ValidationError("You cannot disburse a loan you applied for.")
    if loan.approvals.filter(user=user).exists():
        raise ValidationError("Disbursement must be carried out by someone who did not approve the loan.")
    date = date or today()
    fee = q0(loan.principal * loan.processing_fee_pct / 100)
    old = None
    old_parts = None
    if loan.refinance_of_id:
        old = Loan.objects.select_for_update().get(pk=loan.refinance_of_id)
        if old.status != "active":
            raise ValidationError("The loan being refinanced is no longer active.")
        old_q = settlement_quote(old, date)
        payoff = old_q["total"]
    else:
        payoff = ZERO
    net = loan.principal - fee - payoff
    if net < 0:
        raise ValidationError(f"Proceeds (UGX {loan.principal:,.0f}) do not cover fees and the payoff of the old loan (UGX {fee + payoff:,.0f}).")
    till = None
    if channel == "cash" and net > 0:
        till = cash.require_till(user, net)
    acct = None
    if channel == "savings":
        acct = SavingsAccount.objects.select_for_update().get(pk=savings_account.pk) if savings_account else None
        if acct is None or acct.member_id != loan.member_id or acct.kind != "savings":
            raise ValidationError("Choose one of the borrower's own savings accounts to credit.")
    rows = calc.build_schedule(loan.principal, loan.interest_rate, loan.term, loan.interest_method, loan.frequency, first_due_after(loan, date))
    _, total_int, total_pay = calc.totals(rows)
    for r in rows:
        LoanInstallment.objects.create(loan=loan, no=r["no"], due_date=r["due"], principal=r["principal"], interest=r["interest"])
    # accounting
    lines = [(gl.LOANS, loan.principal, 0, f"Disbursement {loan.loan_no}")]
    if fee:
        lines.append((gl.FEE_INCOME, 0, fee, "Processing fee"))
    if old is not None:
        old_q, osp, osi, ospen, oallocs = _settle_core(old, date)
        if osp:
            lines.append((gl.LOANS, 0, osp, f"Refinance {old.loan_no} principal"))
        if osi:
            lines.append((gl.INT_INCOME, 0, osi, f"Refinance {old.loan_no} interest"))
        if ospen:
            lines.append((gl.PEN_INCOME, 0, ospen, f"Refinance {old.loan_no} penalty"))
    if net > 0:
        dest = gl.SAVINGS if channel == "savings" else gl.CHANNEL_GL[channel]
        lines.append((dest, 0, net, "Net proceeds"))
    j = gl.post_journal(lines, f"Loan disbursement {loan.loan_no} to {loan.member.full_name}", date, "loan_disb", loan.loan_no, user=user)
    if old is not None:
        LoanRepayment.objects.create(ref=next_ref("RP"), loan=old, date=date, amount=payoff, principal=osp, interest=osi, penalty=ospen,
                                     channel="refinance", allocations=oallocs, journal=j, note=f"Refinanced by {loan.loan_no}", created_by=user)
        old.save()
        _maybe_clear(old, date, user)
        old.save()
    if acct:
        acct.balance += net
        acct.save(update_fields=["balance"])
        SavingsTransaction.objects.create(ref=next_ref("TX"), account=acct, kind="deposit", amount=net, balance_after=acct.balance, date=date,
                                          channel="internal", narration=f"Loan proceeds {loan.loan_no}", journal=j, created_by=user)
    if till:
        cash.record_cash(till, "out", "loan_disbursement", net, loan.loan_no, f"Disbursement {loan.loan_no}", j, user)
    loan.status, loan.disbursed_on, loan.disbursed_by = "active", date, user
    loan.first_due_date, loan.total_interest, loan.total_payable = rows[0]["due"], total_int, total_pay
    loan.fee_charged, loan.disbursement_channel, loan.disbursement_journal = fee, channel, j
    loan.save()
    audit("loan.disburse", "finance", "Loan", loan.pk, loan.loan_no,
          new={"principal": loan.principal, "fee": fee, "net": net, "payoff": payoff, "journal": j.ref, "rate": loan.interest_rate, "method": loan.interest_method},
          detail=f"Disbursed to {loan.member.full_name}", user=user)
    return loan


# ----------------------------------------------------------------------- reschedule
def request_reschedule(loan, new_term, new_rate, reason, user):
    if loan.status != "active":
        raise ValidationError("Only active loans can be rescheduled.")
    new_term = int(new_term)
    if new_term < 1 or new_term > 120:
        raise ValidationError("Enter a valid number of periods.")
    if not reason.strip():
        raise ValidationError("A reason for rescheduling is required.")
    new_rate = D(new_rate) if new_rate not in (None, "") else loan.interest_rate
    amount = loan.outstanding_principal
    payload = {"loan_id": loan.pk, "term": new_term, "rate": str(new_rate), "reason": reason, "maker_id": user.pk}
    if appr.needs_approval("reschedule", amount):
        return None, appr.submit("reschedule", amount, f"Reschedule {loan.loan_no} over {new_term} periods @ {new_rate}%: {reason}", payload, user)
    return execute_reschedule(loan.pk, new_term, new_rate, reason, user), None


@transaction.atomic
def execute_reschedule(loan_id, new_term, new_rate, reason, user):
    loan = Loan.objects.select_for_update().get(pk=loan_id)
    if loan.status != "active":
        raise ValidationError("Only active loans can be rescheduled.")
    as_of = today()
    old = list(LoanInstallment.objects.select_for_update().filter(loan=loan, status__in=ACTIVE_INST))
    carried_int = sum((i.interest - i.interest_paid for i in old if i.due_date <= as_of), ZERO)
    carried_pen = sum((i.penalty_balance for i in old), ZERO)
    for i in old:
        i.status, i.penalty_accrued = "superseded", i.penalty_paid
        i.save()
    base = loan.principal - loan.principal_paid
    first_due = date_next(loan.frequency, as_of)
    rows = calc.build_schedule(base, new_rate, new_term, loan.interest_method, loan.frequency, first_due)
    no = (loan.installments.order_by("-no").values_list("no", flat=True).first() or 0) + 1
    new_interest = ZERO
    if carried_int > 0 or carried_pen > 0:
        LoanInstallment.objects.create(loan=loan, no=no, due_date=first_due, principal=0, interest=carried_int, penalty_accrued=carried_pen)
        new_interest += carried_int
        no += 1
    for r in rows:
        LoanInstallment.objects.create(loan=loan, no=no, due_date=r["due"], principal=r["principal"], interest=r["interest"])
        new_interest += r["interest"]
        no += 1
    old_total = loan.total_payable
    loan.interest_rate, loan.term = new_rate, new_term
    loan.total_interest = loan.interest_paid + new_interest
    loan.total_payable = loan.principal + loan.total_interest
    loan.save()
    audit("loan.reschedule", "loan", "Loan", loan.pk, loan.loan_no, old={"total_payable": old_total}, new={"term": new_term, "rate": new_rate, "total_payable": loan.total_payable},
          detail=reason, user=user)
    return loan


def date_next(frequency, d):
    return d + timedelta(days=7) if frequency == "weekly" else add_months(d, 1)


@appr.register("reschedule")
def _approve_reschedule(req, approver):
    p = req.payload
    from django.contrib.auth import get_user_model
    maker = get_user_model().objects.get(pk=p["maker_id"])
    execute_reschedule(p["loan_id"], int(p["term"]), D(p["rate"]), p["reason"], maker)
    return Loan.objects.get(pk=p["loan_id"]).loan_no


# ----------------------------------------------------------------------- portfolio analytics
def portfolio_snapshot(as_of=None):
    """Arrears, PAR and ageing buckets computed from instalments (single pass)."""
    as_of = as_of or today()
    active = Loan.objects.filter(status="active")
    outstanding = active.aggregate(s=Sum(F("principal") - F("principal_paid")))["s"] or ZERO
    overdue = LoanInstallment.objects.filter(loan__status="active", status__in=ACTIVE_INST, due_date__lt=as_of)
    arrears = overdue.aggregate(s=Sum(F("principal") + F("interest") - F("principal_paid") - F("interest_paid")))["s"] or ZERO
    first_overdue = {}
    for lid, due in overdue.values_list("loan_id", "due_date").order_by("due_date"):
        first_overdue.setdefault(lid, due)
    buckets = {"1-30": [0, ZERO], "31-60": [0, ZERO], "61-90": [0, ZERO], "90+": [0, ZERO]}
    par_amt = ZERO
    for l in active.filter(pk__in=list(first_overdue)):
        days = (as_of - first_overdue[l.pk]).days
        key = "1-30" if days <= 30 else "31-60" if days <= 60 else "61-90" if days <= 90 else "90+"
        buckets[key][0] += 1
        buckets[key][1] += l.outstanding_principal
        par_amt += l.outstanding_principal
    par = (par_amt / outstanding * 100) if outstanding else ZERO
    return {"outstanding": outstanding, "arrears": arrears, "loans_in_arrears": len(first_overdue), "par_amount": par_amt, "par": par, "buckets": buckets,
            "first_overdue": first_overdue}


def provision_required(as_of=None):
    from core.models import SystemSetting
    snap = portfolio_snapshot(as_of)
    rates = {"1-30": D(SystemSetting.get("prov_1_30")), "31-60": D(SystemSetting.get("prov_31_60")),
             "61-90": D(SystemSetting.get("prov_61_90")), "90+": D(SystemSetting.get("prov_90_plus"))}
    cur_rate = D(SystemSetting.get("prov_current"))
    performing = snap["outstanding"] - snap["par_amount"]
    rows = [("Performing (0 days)", performing, cur_rate, q0(performing * cur_rate / 100))]
    for k, (n, amt) in snap["buckets"].items():
        rows.append((f"{k} days overdue", amt, rates[k], q0(amt * rates[k] / 100)))
    return rows, sum((r[3] for r in rows), ZERO)
