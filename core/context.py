from django.conf import settings
from django.utils import timezone

from accounts_core.rbac import ROLE_LABEL, get_role, perms_for


def site_context(request):
    from .models import SiteConfig
    ctx = {"site": SiteConfig.get(), "theme": request.COOKIES.get("theme", ""), "now": timezone.localtime(),
           "idle_seconds": settings.IDLE_TIMEOUT_SECONDS, "idle_warn": settings.IDLE_WARNING_SECONDS}
    u = request.user
    if u.is_authenticated and hasattr(u, "profile"):
        role = get_role(u)
        ctx["perms"] = perms_for(u)
        ctx["role_label"] = ROLE_LABEL.get(role, role)
        if request.path.startswith(("/portal/", "/static/")):
            return ctx
        from maintenance.models import MaintenanceNotice
        from maintenance.services import last_backup_age_hours, nav_for
        from notifications.models import Notification
        ctx["nav"] = nav_for(u, role)
        ctx["unread"] = Notification.objects.filter(user=u, read_at__isnull=True).count()
        ctx["notices"] = [n for n in MaintenanceNotice.objects.filter(is_active=True, ends_at__gte=timezone.now())[:3] if n.is_current]
        if "maintenance.manage" in ctx["perms"]:
            from core.models import SystemSetting
            age = last_backup_age_hours()
            if age is None or age > float(SystemSetting.get("backup_reminder_hours") or 24):
                ctx["backup_due"] = True
    return ctx
