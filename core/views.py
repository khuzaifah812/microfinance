from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect, render

from accounts_core.audit import audit
from accounts_core.rbac import can, perm_required

from . import dashboard as dash
from .forms import StyledMixin
from .models import DEFAULT_SETTINGS, SiteConfig, SystemSetting


@login_required
@perm_required("dashboard.view")
def dashboard(request):
    data = dash.build(request.user)
    from approvals.models import ApprovalRequest
    from loans.models import Loan
    todo = []
    if can(request.user, "approvals.decide"):
        n = ApprovalRequest.objects.filter(status="pending").exclude(requested_by=request.user).count()
        if n:
            todo.append(("task_alt", f"{n} approval request(s) waiting for you", "/approvals/"))
    stage = {"loans.officer": "pending_officer", "loans.committee": "pending_committee", "loans.manager": "pending_manager"}
    for perm, st in stage.items():
        if can(request.user, perm):
            n = Loan.objects.filter(status=st).exclude(applied_by=request.user).count()
            if n:
                todo.append(("request_quote", f"{n} loan application(s) awaiting your decision", f"/loans/?status={st}"))
    if can(request.user, "loans.disburse"):
        n = Loan.objects.filter(status="approved").count()
        if n:
            todo.append(("payments", f"{n} approved loan(s) ready to disburse", "/loans/?status=approved"))
    return render(request, "core/dashboard.html", {**data, "todo": todo})


def healthz(request):
    try:
        connection.ensure_connection()
        return HttpResponse("ok", content_type="text/plain")
    except Exception:
        return HttpResponse("db unavailable", status=503, content_type="text/plain")


def branding_logo(request):
    cfg = SiteConfig.get()
    if cfg.logo:
        return FileResponse(cfg.logo.open("rb"))
    from django.contrib.staticfiles import finders
    path = finders.find("img/logo.png")
    if not path:
        raise Http404
    return FileResponse(open(path, "rb"), content_type="image/png")


@login_required
def search(request):
    q = request.GET.get("q", "").strip()
    results = []
    if len(q) >= 2:
        from loans.models import Loan
        from members.models import Member
        from savings.models import SavingsAccount
        if can(request.user, "members.view"):
            for m in Member.objects.filter(Q(full_name__icontains=q) | Q(member_no__icontains=q) | Q(phone__icontains=q))[:10]:
                results.append(("Member", f"{m.member_no} - {m.full_name}", f"/members/{m.pk}/"))
        if can(request.user, "savings.view"):
            for a in SavingsAccount.objects.filter(account_no__icontains=q).select_related("member")[:10]:
                results.append(("Account", f"{a.account_no} - {a.member.full_name}", f"/savings/{a.pk}/"))
        if can(request.user, "loans.view"):
            for l in Loan.objects.filter(Q(loan_no__icontains=q) | Q(member__full_name__icontains=q)).select_related("member")[:10]:
                results.append(("Loan", f"{l.loan_no} - {l.member.full_name}", f"/loans/{l.pk}/"))
    return render(request, "core/search.html", {"q": q, "results": results})


class BrandingForm(StyledMixin, __import__("django").forms.ModelForm):
    class Meta:
        model = SiteConfig
        fields = ["name", "short_name", "tagline", "logo", "registration_no", "address", "phone", "email"]


@perm_required("maintenance.manage")
def settings_view(request):
    cfg = SiteConfig.get()
    form = BrandingForm(request.POST or None, request.FILES or None, instance=cfg)
    if request.method == "POST" and request.POST.get("form") == "branding" and form.is_valid():
        old = {"name": cfg.name}
        form.save()
        audit("config.branding", "maintenance", "SiteConfig", 1, "branding", old=old, new={"name": cfg.name}, detail="System name/logo updated")
        messages.success(request, "Branding updated.")
        return redirect("core:settings")
    if request.method == "POST" and request.POST.get("form") == "settings":
        changed = {}
        for key, (default, _) in DEFAULT_SETTINGS.items():
            val = request.POST.get(key)
            if val is not None and val.strip() != SystemSetting.get(key):
                try:
                    float(val)
                except ValueError:
                    messages.error(request, f"{key} must be a number.")
                    continue
                changed[key] = (SystemSetting.get(key), val.strip())
                SystemSetting.set(key, val.strip())
        if changed:
            audit("config.settings", "maintenance", "SystemSetting", "", "settings", old={k: v[0] for k, v in changed.items()}, new={k: v[1] for k, v in changed.items()})
            messages.success(request, "System settings saved.")
        return redirect("core:settings")
    rows = [(k, SystemSetting.get(k), d[1]) for k, d in DEFAULT_SETTINGS.items()]
    return render(request, "core/settings.html", {"form": form, "rows": rows})


def _err(request, code, title, msg):
    return render(request, "error.html", {"code": code, "title": title, "msg": msg}, status=code)


def error_403(request, exception=None):
    return _err(request, 403, "Access denied", "Your role does not allow this action. If you believe this is a mistake, contact your administrator.")


def error_404(request, exception=None):
    return _err(request, 404, "Page not found", "The page you are looking for does not exist or was moved.")


def error_500(request):
    try:
        from maintenance.services import log_failure
        log_failure("http.500", f"Server error at {request.path}", {"method": request.method})
    except Exception:
        pass
    return _err(request, 500, "Something went wrong", "The error was recorded. Please try again; if it persists contact your administrator.")
