"""Approval handlers for financial corrections (reversals) and manual journals."""
from django.core.exceptions import ValidationError
from django.db import transaction

from approvals import services as appr
from core.utils import D

from . import services as gl
from .models import Journal, Voucher


def reversal_domain_for(journal):
    """Maps a journal to the sub-ledger object whose reversal must accompany the GL reversal."""
    src = journal.source
    if journal.reverses_id or journal.reversals.exists():
        return None
    if src == "savings":
        from savings.models import SavingsTransaction
        t = SavingsTransaction.objects.filter(journal=journal).first()
        return ("savings_tx", t.pk) if t else None
    if src == "loan_repay":
        from loans.models import LoanRepayment
        r = LoanRepayment.objects.filter(journal=journal, is_reversed=False).first()
        return ("loan_repay", r.pk) if r else None
    if src == "voucher":
        v = Voucher.objects.filter(journal=journal, status="posted").first()
        return ("voucher", v.pk) if v and v.mode != "cash" else None
    if src == "manual":
        return ("journal", journal.pk)
    return None


@appr.register("reversal")
def _approve_reversal(req, approver):
    p = req.payload
    dom, oid, reason = p["domain"], p["id"], p["reason"]
    if dom == "savings_tx":
        from savings.models import SavingsTransaction
        from savings.services import reverse_transaction
        return reverse_transaction(SavingsTransaction.objects.get(pk=oid), reason, approver).ref
    if dom == "loan_repay":
        from loans.models import LoanRepayment
        from loans.services import reverse_repayment
        return reverse_repayment(LoanRepayment.objects.get(pk=oid), reason, approver).ref
    if dom == "voucher":
        v = Voucher.objects.select_for_update().get(pk=oid)
        rj = gl.reverse_journal_gl(v.journal, reason, approver)
        v.status = "reversed"
        v.save(update_fields=["status"])
        return rj.ref
    if dom == "journal":
        return gl.reverse_journal_gl(Journal.objects.get(pk=oid), reason, approver).ref
    raise ValidationError("Unknown reversal type.")


@appr.register("manual_journal")
def _approve_manual(req, approver):
    from django.contrib.auth import get_user_model
    from datetime import date
    p = req.payload
    maker = get_user_model().objects.get(pk=p["maker_id"])
    lines = [(l[0], D(l[1]), D(l[2]), l[3] if len(l) > 3 else "") for l in p["lines"]]
    j = gl.post_journal(lines, p["description"], date.fromisoformat(p["date"]), "manual", req.ref, user=maker)
    return j.ref


@transaction.atomic
def post_provision(user):
    from loans.services import provision_required
    rows, required = provision_required()
    current = -gl.balance_of(gl.PROVISION)
    diff = required - current
    if diff == 0:
        raise ValidationError("The provision account already equals the required provision.")
    if diff > 0:
        lines = [(gl.PROV_EXP, diff, 0, "Provision top-up"), (gl.PROVISION, 0, diff, "Provision top-up")]
    else:
        lines = [(gl.PROVISION, -diff, 0, "Provision release"), (gl.PROV_EXP, 0, -diff, "Provision release")]
    return gl.post_journal(lines, f"Loan-loss provision adjustment to UGX {required:,.0f}", source="provision", user=user)
