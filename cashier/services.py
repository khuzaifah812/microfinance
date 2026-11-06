from django.core.exceptions import ValidationError
from django.db import transaction

from accounts_core.audit import audit
from accounting import services as gl
from core.utils import D, ZERO, today

from .models import DENOMINATIONS, Till, TillEntry


def get_open_till(user):
    return Till.objects.filter(user=user, status="open").first()


def require_till(user, amount_out=None):
    t = Till.objects.select_for_update().filter(user=user, status="open").first()
    if t is None:
        raise ValidationError("You must open your till before handling cash (Cashier > My till).")
    if amount_out is not None and t.expected < D(amount_out):
        raise ValidationError(f"Insufficient cash in your till (expected balance UGX {t.expected:,.0f}).")
    return t


def record_cash(till, direction, kind, amount, reference, description, journal, user):
    return TillEntry.objects.create(till=till, direction=direction, kind=kind, amount=D(amount), reference=reference,
                                    description=description[:200], journal=journal, created_by=user)


@transaction.atomic
def open_till(user, opening_float):
    if Till.objects.select_for_update().filter(user=user, status="open").exists():
        raise ValidationError("You already have an open till. Close it before opening another.")
    opening_float = D(opening_float)
    if opening_float < 0:
        raise ValidationError("Opening float cannot be negative.")
    t = Till.objects.create(user=user, opening_float=opening_float, business_date=today())
    audit("till.open", "cashier", "Till", t.pk, f"TILL{t.pk}", new={"opening_float": opening_float}, detail="Till opened", user=user)
    return t


@transaction.atomic
def close_till(till, counted, reason, user):
    """counted: {denomination: count}. Records shortage/overage journals with the reason."""
    till = Till.objects.select_for_update().get(pk=till.pk)
    if till.status != "open":
        raise ValidationError("This till is already closed.")
    if till.user_id != user.id:
        raise ValidationError("Only the cashier who opened the till can close it.")
    clean = {}
    actual = ZERO
    for d in DENOMINATIONS:
        n = int(counted.get(d, counted.get(str(d), 0)) or 0)
        if n < 0:
            raise ValidationError("Denomination counts cannot be negative.")
        clean[str(d)] = n
        actual += d * n
    expected = till.expected
    variance = actual - expected
    reason = (reason or "").strip()
    if variance != 0 and not reason:
        raise ValidationError("A cash difference was found. You must explain the shortage/overage before closing.")
    j = None
    if variance < 0:
        j = gl.post_journal([(gl.SHORTAGE, -variance, 0, "Till shortage"), (gl.CASH, 0, -variance, "Till shortage")],
                            f"Cash shortage on till #{till.pk}: {reason}", source="till", source_ref=f"TILL{till.pk}", user=user)
    elif variance > 0:
        j = gl.post_journal([(gl.CASH, variance, 0, "Till overage"), (gl.OVERAGE, 0, variance, "Till overage")],
                            f"Cash overage on till #{till.pk}: {reason}", source="till", source_ref=f"TILL{till.pk}", user=user)
    from django.utils import timezone
    till.status, till.closed_at, till.counted = "closed", timezone.now(), clean
    till.expected_cash, till.actual_cash, till.variance = expected, actual, variance
    till.variance_reason, till.variance_journal = reason, j
    till.save()
    audit("till.close", "cashier", "Till", till.pk, f"TILL{till.pk}",
          new={"expected": expected, "actual": actual, "variance": variance, "reason": reason},
          detail=("Shortage" if variance < 0 else "Overage" if variance > 0 else "Balanced"), user=user)
    if variance != 0:
        from notifications.services import notify
        notify("cash_difference", f"Till {'shortage' if variance < 0 else 'overage'}: UGX {abs(variance):,.0f}",
               f"{user.get_full_name() or user.username} closed till #{till.pk}. Reason: {reason}",
               roles=["manager", "accountant"], level="bad" if variance < 0 else "warn", link=f"/cashier/till/{till.pk}/", external=True)
    return till


@transaction.atomic
def transfer_cash(from_till, to_till_id, amount, user):
    amount = D(amount)
    if amount <= 0:
        raise ValidationError("Amount must be positive.")
    src = Till.objects.select_for_update().get(pk=from_till.pk)
    dst = Till.objects.select_for_update().filter(pk=to_till_id, status="open").first()
    if dst is None or dst.pk == src.pk:
        raise ValidationError("Choose a different open till to transfer to.")
    if src.expected < amount:
        raise ValidationError("Insufficient cash in your till for this transfer.")
    ref = f"TT{src.pk}-{dst.pk}"
    record_cash(src, "out", "till_transfer", amount, ref, f"Transfer to till #{dst.pk}", None, user)
    record_cash(dst, "in", "till_transfer", amount, ref, f"Transfer from till #{src.pk}", None, user)
    audit("till.transfer", "cashier", "Till", src.pk, ref, new={"from": src.pk, "to": dst.pk, "amount": amount}, user=user)
    return ref
