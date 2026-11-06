from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts_core.rbac import perm_required
from core.ui import paginate

from .models import Notification


@perm_required("notifications.view")
def notification_list(request):
    qs = Notification.objects.filter(user=request.user)
    page, qstr = paginate(request, qs)
    return render(request, "notifications/list.html", {"page": page, "qs": qstr})


@perm_required("notifications.view")
def open_notification(request, pk):
    n = get_object_or_404(Notification, pk=pk, user=request.user)
    if not n.read_at:
        n.read_at = timezone.now()
        n.save(update_fields=["read_at"])
    link = n.link if n.link.startswith("/") and not n.link.startswith("//") else "/notifications/"
    return redirect(link)


@require_POST
@perm_required("notifications.view")
def mark_all_read(request):
    Notification.objects.filter(user=request.user, read_at__isnull=True).update(read_at=timezone.now())
    return redirect("notifications:list")
