"""Tamper-evident audit trail: HMAC hash chain, written in the same DB transaction as the action."""
import hashlib
from datetime import timezone as dt_timezone
import hmac
import json

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.middleware import get_current_request
from core.utils import client_ip

from .models import AuditHead, AuditLog

GENESIS = "0" * 64


def _clean(v):
    if v is None:
        return None
    return json.loads(json.dumps(v, default=str))


def _digest(prev_hash, row):
    payload = json.dumps(row, sort_keys=True, default=str, separators=(",", ":"))
    return hmac.new(settings.AUDIT_HMAC_KEY.encode(), (prev_hash + payload).encode(), hashlib.sha256).hexdigest()


def _ts(dt):
    """Millisecond precision on purpose: JSON backups serialise datetimes to milliseconds, and the chain must still verify after a restore."""
    u = dt.astimezone(dt_timezone.utc)
    return u.strftime("%Y-%m-%dT%H:%M:%S.") + f"{u.microsecond // 1000:03d}Z"


def _row(e):
    return {
        "ts": _ts(e.ts), "user_id": e.user_id, "username": e.username, "role": e.role,
        "action": e.action, "category": e.category, "entity": e.entity, "entity_id": e.entity_id,
        "reference": e.reference, "old": e.old_value, "new": e.new_value, "detail": e.detail,
        "approval": e.approval, "ip": e.ip, "device": e.device,
    }


def audit(action, category, entity="", entity_id="", reference="", old=None, new=None, detail="",
          approval="", user=None, request=None):
    request = request or get_current_request()
    if user is None and request is not None and getattr(request, "user", None) is not None \
            and request.user.is_authenticated:
        user = request.user
    role = ""
    if user is not None and getattr(user, "pk", None):
        p = getattr(user, "profile", None)
        role = p.role if p else ""
    else:
        user = None
    with transaction.atomic():
        AuditHead.objects.get_or_create(pk=1)
        head = AuditHead.objects.select_for_update().get(pk=1)
        entry = AuditLog(
            ts=timezone.now(), user=user, username=user.username if user else "", role=role, action=action,
            category=category, entity=entity, entity_id=str(entity_id or ""), reference=reference or "",
            old_value=_clean(old), new_value=_clean(new), detail=detail or "", approval=approval or "",
            ip=client_ip(request), device=(request.META.get("HTTP_USER_AGENT", "")[:255] if request else ""),
            prev_hash=head.last_hash,
        )
        entry.hash = _digest(entry.prev_hash, _row(entry))
        entry.save()
        head.last_id, head.last_hash = entry.pk, entry.hash
        head.save(update_fields=["last_id", "last_hash"])
    return entry


def verify_chain(limit=None):
    """Returns (ok, checked, problems[list of str]). Detects edits, deleted rows and tail truncation."""
    problems, prev, checked = [], GENESIS, 0
    qs = AuditLog.objects.order_by("id")
    if limit:
        qs = qs[:limit]
    for e in qs.iterator(chunk_size=500):
        checked += 1
        if e.prev_hash != prev:
            problems.append(f"Chain broken before entry #{e.pk} (a record was removed or re-ordered).")
        if _digest(e.prev_hash, _row(e)) != e.hash:
            problems.append(f"Entry #{e.pk} ({e.action}) was altered after it was written.")
        prev = e.hash
    if not limit:
        head = AuditHead.objects.filter(pk=1).first()
        if head and (head.last_hash != prev):
            problems.append("Latest audit entries are missing (chain head does not match the last record).")
    return (not problems), checked, problems[:50]
