import json
import os

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from accounts_core.audit import audit
from accounts_core.rbac import can, perm_required
from core.forms import StyledMixin
from core.ui import C, M, Table, paginate
from members.models import Member

from . import services, sms
from .models import MobileMoneyTransaction, ProviderConfig, SmsOutbox

ENV_NEEDS = {"mtn_momo": ["MTN_MOMO_API_KEY", "MTN_MOMO_WEBHOOK_SECRET"], "airtel_money": ["AIRTEL_MONEY_API_KEY", "AIRTEL_MONEY_WEBHOOK_SECRET"],
             "sms": ["SMS_PROVIDER_API_KEY"], "email": ["EMAIL_HOST", "EMAIL_HOST_USER", "EMAIL_HOST_PASSWORD"]}


class ConfigForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = ProviderConfig
        fields = ["enabled", "environment", "merchant_id", "callback_note"]


@perm_required("integrations.view", "integrations.manage")
def home(request):
    cards = []
    manage = can(request.user, "integrations.manage")
    for p, label in ProviderConfig.PROVIDERS:
        cfg = ProviderConfig.objects.get_or_create(provider=p)[0]
        form = ConfigForm(request.POST if request.method == "POST" and request.POST.get("provider") == p else None, instance=cfg, prefix=p)
        if request.method == "POST" and request.POST.get("provider") == p and manage and form.is_valid():
            old = {"enabled": ProviderConfig.objects.get(pk=cfg.pk).enabled}
            form.save()
            audit("integration.config", "maintenance", "ProviderConfig", cfg.pk, p, old=old, new={"enabled": cfg.enabled, "env": cfg.environment})
            messages.success(request, f"{label} settings saved.")
            return redirect("integrations:home")
        envs = [(k, bool(os.environ.get(k))) for k in ENV_NEEDS[p]]
        ready = all(v for _, v in envs) and cfg.enabled
        cards.append({"cfg": cfg, "label": label, "form": form, "envs": envs, "ready": ready})
    t = Table(["Received", "Provider", "Reference", "Phone", "Amount (UGX)", "Status", "Applied to", ""])
    from core.templatetags.sacco import mask_phone
    for x in MobileMoneyTransaction.objects.all()[:15]:
        t.add([f"{x.received_at:%d %b %H:%M}", x.provider, x.external_ref, mask_phone(x.phone), M(x.amount), C(x.get_status_display(), badge=True), x.applied_to,
               C("Match", f"/integrations/match/{x.pk}/") if x.status == "unmatched" and can(request.user, "savings.transact") else ""])
    return render(request, "integrations/home.html", {"cards": cards, "table": t, "manage": manage, "queued": SmsOutbox.objects.filter(status="queued").count(),
                                                      "outbox": SmsOutbox.objects.all()[:8], "webhook_base": request.build_absolute_uri("/integrations/webhook/")})


@require_POST
@perm_required("integrations.manage")
def sms_dispatch(request):
    try:
        n = sms.dispatch_queued()
        messages.success(request, f"{n} message(s) handed to the provider.")
    except sms.NotConfigured as e:
        messages.warning(request, f"Nothing sent: {e} Messages stay queued - they are never marked as sent without provider confirmation.")
    return redirect("integrations:home")


class MatchForm(StyledMixin, forms.Form):
    member_no = forms.CharField(max_length=20, label="Member number")
    target = forms.CharField(max_length=40, required=False, label="Loan number to repay (blank = deposit to savings)")


@perm_required("savings.transact")
def match(request, pk):
    tx = get_object_or_404(MobileMoneyTransaction, pk=pk, status="unmatched")
    form = MatchForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        m = Member.objects.filter(member_no__iexact=form.cleaned_data["member_no"].strip()).first()
        if not m:
            form.add_error("member_no", "No such member.")
        else:
            tx.member, tx.purpose_ref = m, form.cleaned_data["target"].strip()
            try:
                services.auto_apply(tx)
                tx.applied_by = request.user
                tx.save(update_fields=["applied_by", "member", "purpose_ref"])
                audit("momo.manual_match", "integration", "MobileMoneyTransaction", tx.pk, tx.external_ref, new={"member": m.member_no, "to": tx.applied_to})
                messages.success(request, f"Payment applied to {tx.applied_to}.")
                return redirect("integrations:home")
            except ValidationError as e:
                form.add_error(None, e)
    return render(request, "generic_form.html", {"title": "Match mobile money payment", "form": form, "submit": "Apply payment", "cancel": "/integrations/",
                  "intro": f"{tx.provider} ref {tx.external_ref} - UGX {tx.amount:,.0f} from {tx.phone[-4:].rjust(len(tx.phone), '*')}. The provider has CONFIRMED this payment."})


@csrf_exempt
@require_POST
def webhook(request, provider):
    """Provider callback. Accepted ONLY with a valid HMAC signature; unsigned or simulated calls are rejected.

    TODO: adapt `parse_payload` to the official MTN MoMo / Airtel Money callback schema for your account
    (field names and signature scheme differ per provider and environment).
    """
    if provider not in services.SECRET_ENV:
        return HttpResponse(status=404)
    ok = services.verify_signature(provider, request.body, request.headers.get("X-Signature", ""))
    if ok is None:
        return JsonResponse({"error": "provider not configured"}, status=503)
    if not ok:
        audit("momo.bad_signature", "security", detail=f"Rejected {provider} callback with invalid signature")
        return JsonResponse({"error": "invalid signature"}, status=403)
    try:
        d = json.loads(request.body)
        status = str(d["status"]).upper()
        ext, phone, amount, ref = str(d["external_ref"]), str(d["phone"]), d["amount"], str(d.get("reference", ""))
    except (ValueError, KeyError):
        return JsonResponse({"error": "bad payload"}, status=400)
    if status not in ("SUCCESSFUL", "SUCCESS", "COMPLETED"):
        audit("momo.not_successful", "integration", detail=f"{provider} {ext} status {status}; nothing posted")
        return JsonResponse({"ok": True, "posted": False})
    try:
        tx, created = services.record_confirmed(provider, ext, phone, amount, ref, d)
    except Exception as e:
        from maintenance.services import log_failure
        log_failure("momo.webhook", str(e), {"provider": provider, "ref": ext})
        return JsonResponse({"error": "processing failed"}, status=500)
    return JsonResponse({"ok": True, "posted": created, "status": tx.status})
