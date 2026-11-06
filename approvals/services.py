"""Maker-checker engine. Sensitive actions are parked as ApprovalRequests until a different user approves."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from accounts_core.audit import audit
from accounts_core.rbac import can
from core.models import next_ref
from core.utils import D

from .models import ApprovalRequest, ApprovalRule

HANDLERS = {}
DEFAULT_RULES = {"withdrawal": 1000000, "expense": 500000, "reschedule": 0, "reversal": 0,
                 "manual_journal": 0, "share_transfer": 1000000}


def register(kind):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn
    return deco


def ensure_rules():
    for k, th in DEFAULT_RULES.items():
        ApprovalRule.objects.get_or_create(kind=k, defaults={"threshold": th})


def needs_approval(kind, amount):
    rule = ApprovalRule.objects.filter(kind=kind).first()
    if rule is None:
        ensure_rules()
        rule = ApprovalRule.objects.get(kind=kind)
    return rule.enabled and D(amount) > rule.threshold


@transaction.atomic
def submit(kind, amount, summary, payload, user):
    req = ApprovalRequest.objects.create(ref=next_ref("AP"), kind=kind, summary=summary[:250], amount=D(amount),
                                         payload=payload, requested_by=user)
    audit("approval.request", "approval", "ApprovalRequest", req.pk, req.ref, new={"kind": kind, "amount": amount, "payload": payload},
          detail=summary[:200], user=user)
    from notifications.services import notify
    notify("approval", f"Approval needed: {req.get_kind_display()}", f"{summary} (UGX {D(amount):,.0f})",
           roles=["manager", "accountant"], link="/approvals/", level="warn")
    return req


@transaction.atomic
def decide(req_pk, user, approve, note=""):
    if not can(user, "approvals.decide"):
        raise PermissionDenied
    req = ApprovalRequest.objects.select_for_update().get(pk=req_pk)
    if req.status != "pending":
        raise ValidationError("This request has already been decided.")
    if req.requested_by_id == user.id:
        raise ValidationError("You cannot approve or reject your own request (maker-checker rule).")
    if not approve and not note.strip():
        raise ValidationError("Please give a reason for rejecting.")
    result = ""
    if approve:
        handler = HANDLERS.get(req.kind)
        if handler is None:
            raise ValidationError(f"No handler registered for {req.kind}.")
        result = handler(req, user) or ""
    if not approve and req.kind == "expense":
        from accounting.vouchers import reject_voucher_for_request
        reject_voucher_for_request(req)
    req.status = "approved" if approve else "rejected"
    req.decided_by, req.decided_at, req.decision_note, req.result_ref = user, timezone.now(), note[:250], result
    req.save()
    audit("approval.approve" if approve else "approval.reject", "approval", "ApprovalRequest", req.pk, req.ref,
          detail=f"{req.get_kind_display()} {req.status}. {note}", approval=f"{req.ref} {req.status} by {user.username}; requested by {req.requested_by.username}", user=user)
    from notifications.services import notify
    notify("approval", f"Request {req.ref} {req.status}", req.summary, users=[req.requested_by], link="/approvals/",
           level="ok" if approve else "bad")
    return req


def run_or_submit(kind, amount, summary, payload, user):
    """Queues for a second approver when the configured limit is exceeded; otherwise runs the handler now.
    Returns (result_ref, None) or (None, ApprovalRequest)."""
    if needs_approval(kind, amount):
        return None, submit(kind, amount, summary, payload, user)
    from types import SimpleNamespace
    with transaction.atomic():
        ref = HANDLERS[kind](SimpleNamespace(payload=payload, ref="DIRECT", kind=kind), user)
    audit(f"{kind}.direct", "approval", detail=f"{summary} (below approval limit, executed directly)", user=user)
    return ref, None
