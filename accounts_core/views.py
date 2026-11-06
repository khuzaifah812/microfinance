import io
from datetime import timedelta
import time

import qrcode
import qrcode.image.svg
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.cache import cache
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_POST
from django_otp import login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice

from core.forms import StyledMixin
from core.ui import C, Table, paginate
from core.utils import client_ip

from .audit import audit, verify_chain
from .forms import LoginForm, OTPForm, StaffCreateForm, StaffEditForm
from .models import AuditLog, LoginHistory, Profile
from .rbac import ROLE_LABEL, perm_required
from .emailcode import check_code, issue_code, mask_email
from .services import create_staff, temp_password

User = get_user_model()


def _safe_next(request):
    nxt = request.POST.get("next") or request.GET.get("next") or "/"
    return nxt if nxt.startswith("/") and not nxt.startswith("//") else "/"


def _record(request, username, ok, reason="", user=None):
    LoginHistory.objects.create(user=user, username=username[:150], success=ok, reason=reason,
                                ip=client_ip(request), user_agent=request.META.get("HTTP_USER_AGENT", "")[:255])


def _notify_security(title, msg):
    try:
        from notifications.services import notify
        notify("security", title, msg, roles=["admin", "manager"], level="bad")
    except Exception:  # never block login on notification failure
        pass


def login_view(request):
    if request.user.is_authenticated:
        return redirect("/")
    form = LoginForm(request.POST or None)
    ip = client_ip(request)
    if request.method == "POST" and form.is_valid():
        uname = form.cleaned_data["username"].strip().lower()
        pw = form.cleaned_data["password"]
        throttle_key = f"loginfail:{ip}"
        if cache.get(throttle_key, 0) >= 25:
            messages.error(request, "Too many failed attempts from this network. Please wait 10 minutes.")
            return render(request, "accounts/login.html", {"form": form})
        user_obj = User.objects.filter(username=uname).select_related("profile").first()
        profile = getattr(user_obj, "profile", None) if user_obj else None
        if profile and profile.is_locked:
            _record(request, uname, False, "account locked", user_obj)
            messages.error(request, f"This account is locked until {timezone.localtime(profile.locked_until):%H:%M}. "
                                    "Contact your system administrator to unlock it sooner.")
        else:
            user = authenticate(request, username=uname, password=pw)
            if user is None or not hasattr(user, "profile"):
                cache.set(throttle_key, cache.get(throttle_key, 0) + 1, 600)
                if profile:
                    if profile.locked_until and profile.locked_until <= timezone.now():
                        profile.failed_attempts, profile.locked_until = 0, None
                    profile.failed_attempts += 1
                    if profile.failed_attempts >= settings.LOCKOUT_THRESHOLD:
                        profile.locked_until = timezone.now() + timedelta(minutes=settings.LOCKOUT_MINUTES)
                        audit("auth.lockout", "security", "User", user_obj.pk, uname,
                              detail=f"Locked after {profile.failed_attempts} failed attempts", user=user_obj)
                        _notify_security("Account locked", f"{uname} was locked after repeated failed sign-ins (IP {ip}).")
                    profile.save(update_fields=["failed_attempts", "locked_until"])
                _record(request, uname, False, "bad credentials", user_obj)
                messages.error(request, "Invalid username or password.")
            else:
                p = user.profile
                p.failed_attempts, p.locked_until, p.last_login_ip = 0, None, ip
                p.save(update_fields=["failed_attempts", "locked_until", "last_login_ip"])
                login(request, user)
                request.session["last_activity"] = int(time.time())
                _record(request, uname, True, "", user)
                audit("auth.login", "security", "User", user.pk, uname, detail="Signed in", user=user)
                return redirect(_safe_next(request))
    return render(request, "accounts/login.html", {"form": form, "next": request.GET.get("next", "")})


@require_POST
def logout_view(request):
    if request.user.is_authenticated:
        audit("auth.logout", "security", "User", request.user.pk, request.user.username, detail="Signed out")
    logout(request)
    messages.success(request, "You have been signed out securely.")
    return redirect("accounts:login")


@require_POST
@login_required
def keepalive(request):
    return JsonResponse({"ok": True, "ts": int(time.time())})


@login_required
def password_change(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    for f in form.fields.values():
        f.widget.attrs["class"] = "inp"
    forced = request.user.profile.must_change_password
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        user.profile.must_change_password = False
        user.profile.save(update_fields=["must_change_password"])
        audit("user.password_change", "security", "User", user.pk, user.username, detail="Password changed")
        messages.success(request, "Password updated.")
        return redirect("/")
    return render(request, "generic_form.html", {
        "title": "Change your password" if not forced else "Set a new password to continue",
        "intro": "Your account uses a temporary password. Choose a strong password of at least 10 characters." if forced else
                 "Use at least 10 characters. Avoid common or numeric-only passwords.",
        "form": form, "submit": "Update password", "bare": forced})


@login_required
def verify_email(request):
    """Step after the first password change: confirm the registered email with a 6-digit code."""
    p = request.user.profile
    if p.email_verified or not settings.EMAIL_VERIFICATION:
        return redirect("/")
    u = request.user
    error = ""
    action = request.POST.get("action") if request.method == "POST" else ""
    if action == "set_email" and not u.email:
        addr = (request.POST.get("email") or "").strip().lower()
        try:
            from django.core.validators import validate_email
            validate_email(addr)
        except Exception:
            error = "Enter a valid email address."
        else:
            u.email = addr
            u.save(update_fields=["email"])
            audit("user.email_set", "security", "User", u.pk, u.username, detail="Email address added during verification")
            ok, msg = issue_code(u)
            (messages.success if ok else messages.error)(request, msg)
            return redirect("accounts:verify_email")
    elif action == "resend" and u.email:
        ok, msg = issue_code(u, force=True)
        (messages.success if ok else messages.error)(request, msg)
        return redirect("accounts:verify_email")
    elif action == "verify" and u.email:
        ok, msg = check_code(u, request.POST.get("code", ""))
        if ok:
            messages.success(request, "Email verified. Welcome.")
            return redirect("/")
        error = msg
    elif request.method == "GET" and u.email:
        ok, msg = issue_code(u)             # sends only when there is no live code, so a page refresh does not spam
        if msg:
            (messages.success if ok else messages.error)(request, msg)
    return render(request, "accounts/verify_email.html", {"masked": mask_email(u.email), "has_email": bool(u.email), "error": error,
                                                          "minutes": settings.EMAIL_CODE_MINUTES})


def _qr_svg(data):
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=7, border=2)
    buf = io.BytesIO()
    img.save(buf)
    s = buf.getvalue().decode()
    return mark_safe(s[s.index("<svg"):])


@login_required
def twofa_setup(request):
    u = request.user
    if TOTPDevice.objects.filter(user=u, confirmed=True).exists():
        return redirect("accounts:2fa_verify")
    device = TOTPDevice.objects.filter(user=u, confirmed=False).first() or TOTPDevice.objects.create(user=u, name="Authenticator", confirmed=False)
    form = OTPForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if device.verify_token(form.cleaned_data["token"]):
            device.confirmed = True
            device.save()
            otp_login(request, device)
            audit("auth.2fa_enrol", "security", "User", u.pk, u.username, detail="Authenticator app enrolled")
            messages.success(request, "Two-factor authentication is now active.")
            return redirect("/")
        messages.error(request, "That code is not valid. Check the time on your phone and try again.")
    import base64
    secret = base64.b32encode(bytes.fromhex(device.key)).decode()
    return render(request, "accounts/twofa_setup.html", {"form": form, "qr": _qr_svg(device.config_url), "secret": secret})


@login_required
def twofa_verify(request):
    u = request.user
    device = TOTPDevice.objects.filter(user=u, confirmed=True).first()
    if device is None:
        return redirect("accounts:2fa_setup")
    if u.is_verified():
        return redirect("/")
    form = OTPForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if device.verify_token(form.cleaned_data["token"]):
            otp_login(request, device)
            audit("auth.2fa_ok", "security", "User", u.pk, u.username, detail="Second factor verified")
            return redirect(_safe_next(request))
        p = u.profile
        p.failed_attempts += 1
        if p.failed_attempts >= settings.LOCKOUT_THRESHOLD:
            p.locked_until = timezone.now() + timedelta(minutes=settings.LOCKOUT_MINUTES)
            audit("auth.lockout", "security", "User", u.pk, u.username, detail="Locked after failed 2FA codes")
            p.save(update_fields=["failed_attempts", "locked_until"])
            _record(request, u.username, False, "2fa lockout", u)
            logout(request)
            messages.error(request, "Too many wrong codes. Account locked.")
            return redirect("accounts:login")
        p.save(update_fields=["failed_attempts"])
        _record(request, u.username, False, "bad 2fa code", u)
        messages.error(request, "Invalid code. Try again.")
    return render(request, "accounts/twofa_verify.html", {"form": form})


# ------------------------------------------------------------------ staff management
@perm_required("users.manage")
def user_list(request):
    q = request.GET.get("q", "").strip()
    qs = Profile.objects.select_related("user")
    if q:
        qs = qs.filter(Q(user__username__icontains=q) | Q(user__first_name__icontains=q) | Q(user__last_name__icontains=q))
    page, qstr = paginate(request, qs)
    t = Table(["Username", "Name", "Role", "2FA", "Status", "Last sign-in", ""])
    for p in page:
        has2fa = TOTPDevice.objects.filter(user=p.user, confirmed=True).exists()
        status = "Locked" if p.is_locked else ("Active" if p.user.is_active else "Disabled")
        t.add([C(p.user.username, f"/users/{p.pk}/"), p.user.get_full_name(), p.role_label,
               C("Enrolled" if has2fa else ("Required" if p.needs_2fa else "Optional"), badge="ok" if has2fa else ("warn" if p.needs_2fa else "mute")),
               C(status, badge=True), f"{timezone.localtime(p.user.last_login):%d %b %Y %H:%M}" if p.user.last_login else "Never",
               C("Manage", f"/users/{p.pk}/")])
    return render(request, "generic_list.html", {
        "title": "Staff accounts", "subtitle": "Individual logins for every employee. Roles control what each person can see and do.",
        "table": t, "page": page, "qs": qstr, "q": q, "search": "Search username or name",
        "actions": [("Add staff member", "/users/new/", "primary")]})


@perm_required("users.manage")
def user_create(request):
    form = StaffCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        user, pw = create_staff(d["username"], d["first_name"], d["last_name"], d["role"], d["email"], d["phone"], created_by=request.user)
        request.session["temp_pw"] = {"u": user.username, "p": pw}
        return redirect("accounts:user_detail", pk=user.profile.pk)
    return render(request, "generic_form.html", {"title": "Add staff member", "form": form, "submit": "Create account",
                  "intro": "A one-time temporary password is generated. The employee must change it at first sign-in.", "cancel": "/users/"})


@perm_required("users.manage")
def user_detail(request, pk):
    p = get_object_or_404(Profile.objects.select_related("user"), pk=pk)
    temp = request.session.pop("temp_pw", None)
    logins = LoginHistory.objects.filter(username=p.user.username)[:15]
    return render(request, "accounts/user_detail.html", {
        "p": p, "temp": temp, "logins": logins, "has2fa": TOTPDevice.objects.filter(user=p.user, confirmed=True).exists()})


@perm_required("users.manage")
def user_edit(request, pk):
    p = get_object_or_404(Profile.objects.select_related("user"), pk=pk)
    initial = {"first_name": p.user.first_name, "last_name": p.user.last_name, "email": p.user.email, "phone": p.phone,
               "role": p.role, "is_active": p.user.is_active}
    form = StaffEditForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        if p.user_id == request.user.id and (not d["is_active"] or d["role"] != p.role):
            messages.error(request, "You cannot deactivate or change the role of your own account.")
        else:
            old = {"role": p.role, "active": p.user.is_active}
            u = p.user
            if (d["email"] or "").lower() != (u.email or "").lower():
                p.email_verified = False          # a new address must be confirmed with a code
            u.first_name, u.last_name, u.email, u.is_active = d["first_name"], d["last_name"], d["email"], d["is_active"]
            u.save()
            p.role, p.phone = d["role"], d["phone"]
            p.save()
            audit("user.update", "security", "User", u.pk, u.username, old=old, new={"role": p.role, "active": u.is_active})
            messages.success(request, "Staff account updated.")
            return redirect("accounts:user_detail", pk=p.pk)
    return render(request, "generic_form.html", {"title": f"Edit {p.user.username}", "form": form, "submit": "Save changes",
                                                 "cancel": f"/users/{p.pk}/"})


@require_POST
@perm_required("users.manage")
def user_action(request, pk, action):
    p = get_object_or_404(Profile.objects.select_related("user"), pk=pk)
    u = p.user
    if action == "reset_password":
        pw = temp_password()
        u.set_password(pw)
        u.save()
        p.must_change_password, p.failed_attempts, p.locked_until = True, 0, None
        p.save()
        request.session["temp_pw"] = {"u": u.username, "p": pw}
        audit("user.password_reset", "security", "User", u.pk, u.username, detail="Temporary password issued by administrator")
        messages.success(request, "Temporary password issued. Share it securely with the employee.")
    elif action == "unlock":
        p.failed_attempts, p.locked_until = 0, None
        p.save()
        audit("user.unlock", "security", "User", u.pk, u.username, detail="Account unlocked")
        messages.success(request, "Account unlocked.")
    elif action == "reset_2fa":
        TOTPDevice.objects.filter(user=u).delete()
        audit("user.2fa_reset", "security", "User", u.pk, u.username, detail="Authenticator removed; re-enrolment required")
        messages.success(request, "Authenticator removed. The user will enrol a new device at next sign-in.")
    return redirect("accounts:user_detail", pk=p.pk)


@perm_required("users.manage", "audit.view")
def login_history(request):
    q = request.GET.get("q", "").strip()
    only_fail = request.GET.get("failed") == "1"
    qs = LoginHistory.objects.all()
    if q:
        qs = qs.filter(Q(username__icontains=q) | Q(ip__icontains=q))
    if only_fail:
        qs = qs.filter(success=False)
    page, qstr = paginate(request, qs)
    t = Table(["When", "User", "Result", "Reason", "IP address", "Device"])
    for h in page:
        t.add([f"{timezone.localtime(h.ts):%d %b %Y %H:%M:%S}", h.username, C("Success" if h.success else "Failed", badge=True),
               h.reason, h.ip or "", (h.user_agent or "")[:60]])
    return render(request, "generic_list.html", {
        "title": "Login history", "subtitle": "Every sign-in attempt, including failures, with network address and device.",
        "table": t, "page": page, "qs": qstr, "q": q, "search": "Search user or IP",
        "actions": [("Show failures only" if not only_fail else "Show all", "?failed=" + ("1" if not only_fail else "0"), "ghost")]})


@perm_required("audit.view")
def audit_list(request):
    q = request.GET.get("q", "").strip()
    cat = request.GET.get("cat", "")
    qs = AuditLog.objects.all()
    if q:
        qs = qs.filter(Q(username__icontains=q) | Q(action__icontains=q) | Q(reference__icontains=q) | Q(detail__icontains=q))
    if cat:
        qs = qs.filter(category=cat)
    page, qstr = paginate(request, qs)
    t = Table(["#", "When", "User", "Action", "Reference", "Detail", "IP"])
    for a in page:
        t.add([a.pk, f"{timezone.localtime(a.ts):%d %b %Y %H:%M:%S}", f"{a.username or 'system'} ({ROLE_LABEL.get(a.role, a.role) or '-'})",
               C(a.action, badge="mute"), a.reference, (a.detail or "")[:90], a.ip or ""])
    cats = AuditLog.objects.order_by().values_list("category", flat=True).distinct()
    return render(request, "accounts/audit_list.html", {
        "title": "Audit trail", "table": t, "page": page, "qs": qstr, "q": q, "search": "Search user, action, reference",
        "cats": sorted(cats), "cat": cat})


@require_POST
@perm_required("audit.view")
def audit_verify(request):
    ok, n, problems = verify_chain()
    audit("audit.verify", "security", detail=f"Integrity check: {'PASS' if ok else 'FAIL'} ({n} records)")
    if ok:
        messages.success(request, f"Audit trail verified: {n} records, hash chain intact.")
    else:
        for pr in problems[:5]:
            messages.error(request, "INTEGRITY FAILURE: " + pr)
        _notify_security("Audit integrity failure", problems[0])
    return redirect("accounts:audit")
