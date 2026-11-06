from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts_core.audit import audit
from accounts_core.rbac import can, perm_required
from core.forms import StyledMixin
from core.ui import C, M, Table, paginate

from . import services
from .models import ApprovalRequest, ApprovalRule


@perm_required("approvals.view")
def queue(request):
    status = request.GET.get("status", "pending")
    qs = ApprovalRequest.objects.select_related("requested_by", "decided_by")
    if status != "all":
        qs = qs.filter(status=status)
    page, qstr = paginate(request, qs)
    return render(request, "approvals/queue.html", {"page": page, "qs": qstr, "status": status, "can_decide": can(request.user, "approvals.decide")})


@require_POST
@perm_required("approvals.decide")
def decide(request, pk):
    approve = request.POST.get("action") == "approve"
    try:
        req = services.decide(pk, request.user, approve, request.POST.get("note", ""))
    except ValidationError as e:
        for m in e.messages:
            messages.error(request, m)
    except PermissionDenied:
        messages.error(request, "You are not allowed to decide approvals.")
    else:
        messages.success(request, f"Request {req.ref} {req.status}." + (f" Result: {req.result_ref}" if req.result_ref else ""))
    return redirect("approvals:queue")


class RuleForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = ApprovalRule
        fields = ["threshold", "enabled"]


@perm_required("approvals.view")
def rules(request):
    services.ensure_rules()
    can_edit = can(request.user, "approvals.configure")
    items = []
    for r in ApprovalRule.objects.all():
        prefix = f"r{r.pk}"
        form = RuleForm(request.POST or None, instance=r, prefix=prefix) if can_edit else None
        items.append((r, form))
    if request.method == "POST" and can_edit:
        ok = True
        for r, form in items:
            if form.has_changed():
                if form.is_valid():
                    old = {"threshold": str(ApprovalRule.objects.get(pk=r.pk).threshold), "enabled": ApprovalRule.objects.get(pk=r.pk).enabled}
                    form.save()
                    audit("approval.rule", "approval", "ApprovalRule", r.pk, r.kind, old=old, new={"threshold": form.instance.threshold, "enabled": form.instance.enabled})
                else:
                    ok = False
        messages.success(request, "Approval limits saved.") if ok else messages.error(request, "Please correct the limits.")
        return redirect("approvals:rules")
    return render(request, "approvals/rules.html", {"items": items, "can_edit": can_edit})
