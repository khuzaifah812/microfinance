import hashlib
import hmac
import os
import re

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from accounts_core.audit import audit
from core.utils import D

from .models import MobileMoneyTransaction

User = get_user_model()
SECRET_ENV = {"mtn_momo": "MTN_MOMO_WEBHOOK_SECRET", "airtel_money": "AIRTEL_MONEY_WEBHOOK_SECRET"}


def system_user():
    u, created = User.objects.get_or_create(username="system", defaults={"first_name": "System", "last_name": "Automation", "is_active": True})
    if created:
        u.set_unusable_password()
        u.save()
    return u


def verify_signature(provider, raw_body, signature):
    secret = os.environ.get(SECRET_ENV.get(provider, ""), "")
    if not secret:
        return None  # not configured
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, (signature or "").strip().lower())


def normalise_phone(p):
    d = re.sub(r"\D", "", p or "")
    return d[-9:]


def find_member(phone):
    from members.models import Member
    n = normalise_phone(phone)
    if len(n) < 9:
        return None
    hits = [m for m in Member.objects.filter(phone__contains=n[-7:], status="active") if normalise_phone(m.phone) == n]
    return hits[0] if len(hits) == 1 else None


@transaction.atomic
def record_confirmed(provider, external_ref, phone, amount, reference, raw):
    """Stores a provider-CONFIRMED payment. Never called for unconfirmed/simulated payments."""
    try:
        with transaction.atomic():
            tx = MobileMoneyTransaction.objects.create(provider=provider, external_ref=external_ref[:80], phone=phone[:20], amount=D(amount),
                                                       purpose_ref=(reference or "")[:40], status="confirmed", raw=raw)
    except IntegrityError:
        return MobileMoneyTransaction.objects.get(provider=provider, external_ref=external_ref), False
    audit("momo.confirmed", "integration", "MobileMoneyTransaction", tx.pk, external_ref, new={"amount": tx.amount, "phone": phone[-4:], "ref": reference}, user=system_user())
    member = find_member(phone)
    if member:
        tx.member = member
        tx.save(update_fields=["member"])
        try:
            auto_apply(tx)
        except ValidationError as e:
            tx.status = "unmatched"
            tx.save(update_fields=["status"])
            audit("momo.apply_failed", "integration", "MobileMoneyTransaction", tx.pk, external_ref, detail="; ".join(e.messages), user=system_user())
    else:
        tx.status = "unmatched"
        tx.save(update_fields=["status"])
        from notifications.services import notify
        notify("integration", "Unmatched mobile money payment", f"{provider} {external_ref} UGX {tx.amount:,.0f}", roles=["accountant"], link="/integrations/", level="warn")
    return tx, True


def auto_apply(tx):
    """Reference decides the target: a loan number repays that loan; otherwise deposit to the member's main savings account."""
    from loans.models import Loan
    from loans.services import repay
    from savings.models import SavingsAccount
    from savings.services import deposit
    user = system_user()
    ref = (tx.purpose_ref or "").upper().strip()
    loan = Loan.objects.filter(loan_no=ref, member=tx.member, status="active").first() if ref.startswith("LN") else None
    if loan:
        repay(loan, tx.amount, user, channel="momo", note=f"MoMo {tx.external_ref}")
        tx.applied_to = f"loan:{loan.loan_no}"
    else:
        acct = SavingsAccount.objects.filter(member=tx.member, kind="savings", status="active").order_by("id").first()
        if acct is None:
            raise ValidationError("Member has no active savings account.")
        deposit(acct, tx.amount, user, channel="momo", narration=f"MoMo {tx.external_ref}")
        tx.applied_to = f"savings:{acct.account_no}"
    tx.status = "applied"
    tx.save(update_fields=["status", "applied_to"])
