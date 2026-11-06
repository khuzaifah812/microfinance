"""Six-digit email verification codes (hashed at rest, expiring, attempt-limited, resend-throttled)."""
import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from .audit import audit


def mask_email(addr):
    if not addr or "@" not in addr:
        return ""
    name, _, domain = addr.partition("@")
    return f"{name[:1]}{'*' * max(2, len(name) - 1)}@{domain}"


def _hash(user, code):
    return hmac.new(settings.SECRET_KEY.encode(), f"{user.pk}:{code}".encode(), hashlib.sha256).hexdigest()


def issue_code(user, force=False):
    """Send a fresh code. Returns (ok, message). Without force, nothing is sent while a live code exists."""
    p = user.profile
    now = timezone.now()
    live = bool(p.email_code_hash and p.email_code_expires and p.email_code_expires > now)
    if live and not force:
        return True, ""
    if force and p.email_code_sent_at and (now - p.email_code_sent_at).total_seconds() < settings.EMAIL_CODE_RESEND_SECONDS:
        wait = settings.EMAIL_CODE_RESEND_SECONDS - int((now - p.email_code_sent_at).total_seconds())
        return False, f"Please wait {wait} seconds before asking for another code."
    code = f"{secrets.randbelow(1_000_000):06d}"
    from core.models import SiteConfig
    name = SiteConfig.get().name
    who = user.get_full_name() or user.username
    body = (f"Hello {who},\n\nYour verification code for {name} is:\n\n    {code}\n\n"
            f"It expires in {settings.EMAIL_CODE_MINUTES} minutes. If you did not just sign in, ignore this email and tell your administrator.\n"
            f"Never share this code with anyone.\n\n{name}")
    try:
        send_mail(f"{code} is your {name} verification code", body, settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False)
    except Exception as exc:                      # the SMTP server is down or misconfigured
        audit("user.email_code_failed", "security", "User", user.pk, user.username, detail=f"{type(exc).__name__}", user=user)
        return False, "We could not send the email. Check the mail settings or ask your administrator."
    p.email_code_hash, p.email_code_sent_at, p.email_code_attempts = _hash(user, code), now, 0
    p.email_code_expires = now + timedelta(minutes=settings.EMAIL_CODE_MINUTES)
    p.save(update_fields=["email_code_hash", "email_code_sent_at", "email_code_expires", "email_code_attempts"])
    audit("user.email_code_sent", "security", "User", user.pk, user.username, detail="Verification code emailed", user=user)
    return True, f"A 6-digit code was sent to {mask_email(user.email)}."


def check_code(user, entered):
    """Returns (ok, message). Five wrong tries burn the code."""
    p = user.profile
    now = timezone.now()
    entered = "".join(ch for ch in (entered or "") if ch.isdigit())
    if not p.email_code_hash or not p.email_code_expires or p.email_code_expires <= now:
        return False, "That code has expired. Press Send a new code."
    if p.email_code_attempts >= settings.EMAIL_CODE_MAX_ATTEMPTS:
        p.email_code_hash = ""
        p.save(update_fields=["email_code_hash"])
        return False, "Too many wrong attempts. Press Send a new code."
    if len(entered) != 6 or not hmac.compare_digest(_hash(user, entered), p.email_code_hash):
        p.email_code_attempts += 1
        p.save(update_fields=["email_code_attempts"])
        left = settings.EMAIL_CODE_MAX_ATTEMPTS - p.email_code_attempts
        audit("user.email_code_wrong", "security", "User", user.pk, user.username, detail="Wrong verification code", user=user)
        return False, f"That code is not correct. {max(left, 0)} attempt(s) left."
    p.email_verified, p.email_code_hash, p.email_code_expires, p.email_code_attempts = True, "", None, 0
    p.save(update_fields=["email_verified", "email_code_hash", "email_code_expires", "email_code_attempts"])
    audit("user.email_verified", "security", "User", user.pk, user.username, detail="Email verified", user=user)
    return True, "Verified."
