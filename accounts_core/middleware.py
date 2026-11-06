import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect

EXEMPT_PREFIXES = ("/login/", "/logout/", "/static/", "/2fa/", "/account/password/", "/account/verify-email/", "/session/", "/healthz",
                   "/portal/", "/integrations/webhook/", "/branding/")


class IdleTimeoutMiddleware:
    """Logs out staff after 10 minutes without a request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            now = int(time.time())
            last = request.session.get("last_activity")
            if last and now - last > settings.IDLE_TIMEOUT_SECONDS:
                from .audit import audit
                audit("session.timeout", "security", detail="Idle timeout", user=request.user)
                logout(request)
                messages.warning(request, "You were signed out after 10 minutes of inactivity.")
                return redirect(f"/login/?next={request.path}")
            request.session["last_activity"] = now
        return self.get_response(request)


class SecurityGateMiddleware:
    """Forces password change, email verification and TOTP for admin/manager/accountant."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        u = request.user
        if u.is_authenticated and not request.path.startswith(EXEMPT_PREFIXES):
            p = getattr(u, "profile", None)
            if p is None and not u.is_superuser:
                logout(request)
                return redirect("/login/")
            if p is not None:
                if p.must_change_password:
                    return redirect("accounts:password_change")
                if settings.EMAIL_VERIFICATION and not p.email_verified:
                    return redirect("accounts:verify_email")
                if p.needs_2fa and not u.is_verified():
                    from django_otp.plugins.otp_totp.models import TOTPDevice
                    has = TOTPDevice.objects.filter(user=u, confirmed=True).exists()
                    return redirect("accounts:2fa_verify" if has else "accounts:2fa_setup")
        return self.get_response(request)
