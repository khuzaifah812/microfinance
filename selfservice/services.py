import re
import secrets

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.utils import timezone

from accounts_core.audit import audit
from core.utils import client_ip

from .models import PortalAccess

PIN_RE = re.compile(r"^\d{6}$")


def new_pin():
    return f"{secrets.randbelow(10**6):06d}"


def issue_pin(member, user):
    pin = new_pin()
    acc, _ = PortalAccess.objects.get_or_create(member=member, defaults={"pin_hash": "x", "created_by": user})
    acc.pin_hash, acc.must_change_pin, acc.failed_attempts, acc.locked_until, acc.is_active = make_password(pin), True, 0, None, True
    acc.save()
    audit("portal.pin_issue", "security", "Member", member.pk, member.member_no, detail="Portal PIN issued", user=user)
    return pin


def authenticate_member(request, member_no, pin):
    """Returns (PortalAccess|None, error|None). Rate-limited per IP and per account."""
    from members.models import Member
    ip = client_ip(request)
    key = f"portalfail:{ip}"
    if cache.get(key, 0) >= 15:
        return None, "Too many attempts. Please wait 10 minutes."
    m = Member.objects.filter(member_no__iexact=member_no.strip()).first()
    acc = getattr(m, "portal", None) if m else None
    if acc is None or not acc.is_active or m.status != "active":
        check_password(pin, make_password("dummy"))  # constant-ish time
        cache.set(key, cache.get(key, 0) + 1, 600)
        return None, "Invalid member number or PIN."
    if acc.is_locked:
        return None, "This access is temporarily locked. Try again later or visit the SACCO office."
    if not check_password(pin, acc.pin_hash):
        acc.failed_attempts += 1
        if acc.failed_attempts >= settings.LOCKOUT_THRESHOLD:
            from datetime import timedelta
            acc.locked_until = timezone.now() + timedelta(minutes=settings.LOCKOUT_MINUTES)
            audit("portal.lockout", "security", "Member", m.pk, m.member_no, detail="Portal PIN locked after failures")
        acc.save(update_fields=["failed_attempts", "locked_until"])
        cache.set(key, cache.get(key, 0) + 1, 600)
        return None, "Invalid member number or PIN."
    acc.failed_attempts, acc.locked_until, acc.last_login = 0, None, timezone.now()
    acc.save(update_fields=["failed_attempts", "locked_until", "last_login"])
    return acc, None
