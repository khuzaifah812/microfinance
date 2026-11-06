from django.core.exceptions import ValidationError
from django.db import transaction

from accounts_core.audit import audit
from approvals import services as appr
from cashier import services as cash
from core.models import next_ref
from core.utils import D, today

from . import services as gl
from .models import Voucher


def _amount(a):
    a = D(a)
    if a <= 0 or a != a.to_integral_value():
        raise ValidationError("Enter a positive whole-shilling amount.")
    return a


def _post(v, user):
    ch = v.mode
    if v.kind == "expense":
        credit = gl.PAYABLE if ch == "credit" else gl.CHANNEL_GL[ch]
        lines = [(v.account.code, v.amount, 0, v.party), (credit, 0, v.amount, v.party)]
    else:
        debit = gl.RECEIVABLE if ch == "credit" else gl.CHANNEL_GL[ch]
        lines = [(debit, v.amount, 0, v.party), (v.account.code, 0, v.amount, v.party)]
    till = None
    if ch == "cash":
        till = cash.require_till(user, v.amount if v.kind == "expense" else None)
    j = gl.post_journal(lines, f"{v.get_kind_display()}: {v.description} ({v.party})", v.date, "voucher", v.ref, user=user)
    if till:
        cash.record_cash(till, "out" if v.kind == "expense" else "in", "payment" if v.kind == "expense" else "receipt", v.amount, v.ref, v.description, j, user)
    v.journal, v.status = j, "posted"
    v.save(update_fields=["journal", "status"])
    audit("voucher.post", "finance", "Voucher", v.pk, v.ref, new={"amount": v.amount, "mode": ch, "journal": j.ref}, detail=v.description, user=user)
    return v


@transaction.atomic
def create_voucher(kind, account, amount, party, description, mode, date, user):
    amount = _amount(amount)
    if account.type != ("expense" if kind == "expense" else "income"):
        raise ValidationError("Choose an " + ("expense" if kind == "expense" else "income") + " account for this voucher.")
    v = Voucher.objects.create(ref=next_ref("VC"), kind=kind, date=date or today(), account=account, amount=amount, party=party,
                               description=description, mode=mode, status="pending_approval", created_by=user)
    if kind == "expense" and appr.needs_approval("expense", amount):
        req = appr.submit("expense", amount, f"Expense {v.ref}: {description} - {party}", {"voucher_id": v.pk, "maker_id": user.pk}, user)
        audit("voucher.queued", "finance", "Voucher", v.pk, v.ref, detail=f"Held for approval {req.ref}", user=user)
        return v, req
    return _post(v, user), None


@appr.register("expense")
def _approve_expense(req, approver):
    from django.contrib.auth import get_user_model
    v = Voucher.objects.select_for_update().get(pk=req.payload["voucher_id"])
    maker = get_user_model().objects.get(pk=req.payload["maker_id"])
    _post(v, maker)
    return v.ref


def reject_voucher_for_request(req):
    Voucher.objects.filter(pk=req.payload.get("voucher_id"), status="pending_approval").update(status="rejected")


@transaction.atomic
def settle_voucher(v, mode, user, date=None):
    v = Voucher.objects.select_for_update().get(pk=v.pk)
    if v.mode != "credit" or v.status != "posted" or v.settled_on:
        raise ValidationError("This voucher is not an open payable/receivable.")
    ch = gl.CHANNEL_GL[mode]
    till = cash.require_till(user, v.amount if v.kind == "expense" else None) if mode == "cash" else None
    if v.kind == "expense":
        lines = [(gl.PAYABLE, v.amount, 0, v.party), (ch, 0, v.amount, v.party)]
    else:
        lines = [(ch, v.amount, 0, v.party), (gl.RECEIVABLE, 0, v.amount, v.party)]
    j = gl.post_journal(lines, f"Settlement of {v.ref} ({v.party})", date, "voucher", v.ref, user=user)
    if till:
        cash.record_cash(till, "out" if v.kind == "expense" else "in", "settlement", v.amount, v.ref, f"Settle {v.ref}", j, user)
    v.settled_on, v.settle_journal = date or today(), j
    v.save(update_fields=["settled_on", "settle_journal"])
    audit("voucher.settle", "finance", "Voucher", v.pk, v.ref, new={"mode": mode, "journal": j.ref}, user=user)
    return v
