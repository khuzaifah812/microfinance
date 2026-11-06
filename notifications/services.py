import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail

from .models import Notification

log = logging.getLogger("sacco")
User = get_user_model()


def notify(kind, title, message="", roles=None, users=None, link="", level="info", external=False):
    """In-app notification for role holders / specific users; optional email + SMS (queued) for urgent items."""
    targets = {}
    if roles:
        for u in User.objects.filter(is_active=True, profile__role__in=roles).select_related("profile"):
            targets[u.pk] = u
    for u in users or []:
        targets[u.pk] = u
    for u in targets.values():
        Notification.objects.create(user=u, kind=kind, level=level, title=title[:140], message=message[:400], link=link)
    if external:
        for u in targets.values():
            if u.email:
                try:
                    send_mail(f"[SACCO] {title}", message or title, settings.DEFAULT_FROM_EMAIL, [u.email], fail_silently=False)
                except Exception as e:  # recorded, never blocks the transaction
                    log.warning("email failed: %s", e)
                    _failed("notify.email", str(e))
            ph = getattr(getattr(u, "profile", None), "phone", "")
            if ph:
                try:
                    from integrations.sms import queue_sms
                    queue_sms(ph, f"{title}. {message}"[:160], kind=kind)
                except Exception as e:
                    _failed("notify.sms", str(e))
    return len(targets)


def _failed(op, err):
    try:
        from maintenance.services import log_failure
        log_failure(op, err)
    except Exception:
        pass
