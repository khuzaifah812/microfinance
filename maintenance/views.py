import json
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts_core.audit import audit
from accounts_core.models import AuditLog
from accounts_core.rbac import ROLES, perm_required
from core.forms import StyledMixin
from core.ui import C, Table, paginate

from . import services
from .models import BackupRecord, DocPage, FailedOperation, MaintenanceNotice, Module, ModuleRole


@perm_required("maintenance.manage")
def home(request):
    checks = services.health_report()
    bad = [c for c in checks if c["status"] == "bad"]
    warn = [c for c in checks if c["status"] == "warn"]
    return render(request, "maintenance/home.html", {
        "bad": len(bad), "warn": len(warn), "ok": len(checks) - len(bad) - len(warn), "info": services.app_info(),
        "modules": Module.objects.count(), "enabled": Module.objects.filter(enabled=True).count(),
        "notices": MaintenanceNotice.objects.filter(is_active=True, ends_at__gte=timezone.now())[:5],
        "last_backup": BackupRecord.objects.first(), "open_failures": FailedOperation.objects.filter(resolved=False).count(),
        "logs": AuditLog.objects.filter(category="maintenance")[:8]})


@perm_required("maintenance.manage")
def health(request):
    return render(request, "maintenance/health.html", {"checks": services.health_report(), "info": services.app_info()})


# ------------------------------------------------------------------ modules
class ModuleForm(StyledMixin, forms.ModelForm):
    roles = forms.MultipleChoiceField(choices=ROLES, required=False, widget=forms.CheckboxSelectMultiple, label="Roles that can see this module")
    settings_text = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 4}), label="Module settings (JSON)")

    class Meta:
        model = Module
        fields = ["key", "name", "description", "icon", "category", "version", "sort_order"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        if self.instance.pk:
            self.fields["key"].disabled = True
            self.fields["roles"].initial = list(self.instance.roles.values_list("role", flat=True))
            self.fields["settings_text"].initial = json.dumps(self.instance.settings_json, indent=2)
        self.fields["roles"].widget.attrs.pop("class", None)

    def clean_settings_text(self):
        t = self.cleaned_data.get("settings_text", "").strip()
        if not t:
            return {}
        try:
            v = json.loads(t)
            if not isinstance(v, dict):
                raise ValueError
            return v
        except ValueError:
            raise forms.ValidationError("Settings must be a valid JSON object.")


@perm_required("maintenance.manage")
def module_list(request):
    t = Table(["Module", "Category", "Version", "Type", "Status", "Active", "Roles", ""])
    for m in Module.objects.prefetch_related("roles"):
        t.add([C(m.name, f"/maintenance/modules/{m.pk}/"), m.get_category_display(), m.version, C("Built-in" if m.builtin else "Custom", badge="mute"), C(m.get_status_display(), badge=True),
               C("Enabled" if m.enabled else "Disabled", badge="ok" if m.enabled else "mute"), ", ".join(r.role for r in m.roles.all()) or "none", C("Manage", f"/maintenance/modules/{m.pk}/")])
    return render(request, "generic_list.html", {"title": "Modules", "subtitle": "The sidebar is built from this registry. Enabling a module never grants data access by itself - role access is set per module.",
                  "table": t, "actions": [("Add module", "/maintenance/modules/new/", "primary"), ("Export configuration", "/maintenance/modules/export/", "ghost"),
                                          ("Import configuration", "/maintenance/modules/import/", "ghost")]})


@perm_required("maintenance.manage")
def module_edit(request, pk=None):
    m = get_object_or_404(Module, pk=pk) if pk else None
    form = ModuleForm(request.POST or None, instance=m)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.settings_json = form.cleaned_data["settings_text"]
        if not m:
            obj.builtin, obj.enabled, obj.status = False, False, "draft"
        obj.save()
        obj.roles.exclude(role__in=form.cleaned_data["roles"]).delete()
        for r in form.cleaned_data["roles"]:
            ModuleRole.objects.get_or_create(module=obj, role=r)
        audit("module.save", "maintenance", "Module", obj.pk, obj.key, new={"name": obj.name, "roles": form.cleaned_data["roles"], "version": obj.version})
        messages.success(request, "Module saved." + ("" if obj.builtin else " It stays disabled until it passes the activation checklist."))
        return redirect("maintenance:module", pk=obj.pk)
    return render(request, "generic_form.html", {"title": "Edit module" if m else "Add module", "form": form, "submit": "Save module", "cancel": "/maintenance/modules/",
                  "intro": "Material Symbols icon names: https://fonts.google.com/icons. New modules start disabled."})


@perm_required("maintenance.manage")
def module_detail(request, pk):
    m = get_object_or_404(Module, pk=pk)
    return render(request, "maintenance/module.html", {"m": m, "roles": list(m.roles.values_list("role", flat=True)), "settings_pretty": json.dumps(m.settings_json, indent=2),
                  "logs": AuditLog.objects.filter(category="maintenance", entity="Module", entity_id=str(m.pk))[:10]})


@require_POST
@perm_required("maintenance.manage")
def module_action(request, pk, action):
    m = get_object_or_404(Module, pk=pk)
    if action in ("dev_tested", "security_reviewed", "migrated") and not m.builtin:
        setattr(m, action, not getattr(m, action))
        m.approved_by, m.approved_at = None, None   # any change to the checklist voids earlier approval
        m.status = "testing" if m.dev_tested and not m.security_reviewed else "security_review" if m.security_reviewed and not m.migrated else m.status
        m.save()
        audit(f"module.{action}", "maintenance", "Module", m.pk, m.key, new={action: getattr(m, action)})
    elif action == "approve" and not m.builtin:
        if not (m.dev_tested and m.security_reviewed and m.migrated):
            messages.error(request, "Complete developer testing, security review and database migration before approval.")
        else:
            m.approved_by, m.approved_at, m.status = request.user, timezone.now(), "ready"
            m.save()
            audit("module.approve", "maintenance", "Module", m.pk, m.key, detail="Administrator approved activation", approval=f"approved by {request.user.username}")
            messages.success(request, "Module approved for activation.")
    elif action == "enable":
        if not m.can_activate:
            messages.error(request, "This module cannot be activated yet: testing, security review, migration and administrator approval are all required.")
        else:
            m.enabled, m.status = True, "active"
            m.save()
            audit("module.enable", "maintenance", "Module", m.pk, m.key, detail="Module activated")
            messages.success(request, f"{m.name} is now active and appears in the sidebar for its assigned roles.")
    elif action == "disable":
        if m.key in ("maintenance", "dashboard", "users"):
            messages.error(request, "Core administration modules cannot be disabled.")
        else:
            m.enabled = False
            m.status = "ready" if not m.builtin else "active"
            m.save()
            audit("module.disable", "maintenance", "Module", m.pk, m.key, detail="Module disabled")
            messages.success(request, f"{m.name} disabled.")
    elif action == "delete":
        if m.builtin:
            messages.error(request, "Built-in modules cannot be removed.")
        elif m.enabled:
            messages.error(request, "Disable the module before removing it.")
        else:
            audit("module.delete", "maintenance", "Module", m.pk, m.key, old={"name": m.name}, detail="Custom module removed")
            m.delete()
            messages.success(request, "Module removed.")
            return redirect("maintenance:modules")
    return redirect("maintenance:module", pk=pk)


@perm_required("maintenance.manage")
def module_page(request, key):
    """Landing page for custom modules. A developer's code replaces this when the module ships its own views."""
    from accounts_core.rbac import get_role
    m = get_object_or_404(Module, key=key, enabled=True, builtin=False)
    if not m.roles.filter(role=get_role(request.user)).exists():
        raise Http404
    return render(request, "maintenance/module_page.html", {"m": m})


@perm_required("maintenance.manage")
def module_export(request):
    data = [{"key": m.key, "name": m.name, "description": m.description, "icon": m.icon, "category": m.category, "version": m.version, "sort_order": m.sort_order,
             "builtin": m.builtin, "enabled": m.enabled, "roles": list(m.roles.values_list("role", flat=True)), "settings": m.settings_json} for m in Module.objects.all()]
    audit("module.export", "maintenance", detail=f"{len(data)} modules exported")
    resp = HttpResponse(json.dumps({"format": "sfms-modules/1", "modules": data}, indent=2), content_type="application/json")
    resp["Content-Disposition"] = 'attachment; filename="sfms-modules.json"'
    return resp


class ImportForm(StyledMixin, forms.Form):
    file = forms.FileField(label="Module configuration (.json)")


@perm_required("maintenance.manage")
def module_import(request):
    form = ImportForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            raw = form.cleaned_data["file"].read(2_000_000)
            data = json.loads(raw)
            assert data.get("format") == "sfms-modules/1"
            n = 0
            for d in data["modules"]:
                existing = Module.objects.filter(key=d["key"]).first()
                if existing and existing.builtin:
                    existing.name, existing.description, existing.icon = d["name"][:80], d.get("description", "")[:250], d.get("icon", existing.icon)[:40]
                    existing.save()
                    existing.roles.all().delete()
                    for r in d.get("roles", []):
                        if r in dict(ROLES):
                            ModuleRole.objects.create(module=existing, role=r)
                else:
                    mod = existing or Module(key=d["key"][:40], builtin=False)
                    mod.name, mod.description, mod.icon = d["name"][:80], d.get("description", "")[:250], d.get("icon", "extension")[:40]
                    mod.category = d.get("category") if d.get("category") in dict(__import__("maintenance.models", fromlist=["x"]).CATEGORIES) else "extensions"
                    mod.version, mod.sort_order, mod.settings_json = d.get("version", "1.0.0")[:20], int(d.get("sort_order", 100)), d.get("settings", {})
                    if not existing:
                        mod.enabled, mod.status = False, "draft"     # imports never auto-activate
                    mod.save()
                    mod.roles.all().delete()
                    for r in d.get("roles", []):
                        if r in dict(ROLES):
                            ModuleRole.objects.create(module=mod, role=r)
                n += 1
            audit("module.import", "maintenance", detail=f"{n} modules imported (new modules arrive disabled)")
            messages.success(request, f"{n} module definitions imported. New custom modules are disabled until they pass the activation checklist.")
            return redirect("maintenance:modules")
        except Exception as e:
            messages.error(request, f"Invalid configuration file: {e.__class__.__name__}")
    return render(request, "generic_form.html", {"title": "Import module configuration", "form": form, "submit": "Import", "multipart": True, "cancel": "/maintenance/modules/",
                  "intro": "Imported modules are always created disabled. Code, tables and permissions of a module are never imported - only its registry entry."})


# ------------------------------------------------------------------ notices
class NoticeForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = MaintenanceNotice
        fields = ["title", "message", "starts_at", "ends_at", "is_active"]
        widgets = {"starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
                   "ends_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"), "message": forms.Textarea(attrs={"rows": 3})}

    def clean(self):
        d = super().clean()
        if d.get("starts_at") and d.get("ends_at") and d["ends_at"] <= d["starts_at"]:
            raise forms.ValidationError("End time must be after the start time.")
        return d


@perm_required("maintenance.manage")
def notices(request):
    form = NoticeForm(request.POST or None, initial={"is_active": True})
    if request.method == "POST":
        if request.POST.get("toggle"):
            n = get_object_or_404(MaintenanceNotice, pk=request.POST["toggle"])
            n.is_active = not n.is_active
            n.save()
            return redirect("maintenance:notices")
        if form.is_valid():
            n = form.save(commit=False)
            n.created_by = request.user
            n.save()
            audit("notice.create", "maintenance", "MaintenanceNotice", n.pk, n.title, detail=f"{n.starts_at} - {n.ends_at}")
            from notifications.services import notify
            notify("maintenance", f"Planned maintenance: {n.title}", n.message[:300], roles=[r for r, _ in ROLES], level="warn")
            messages.success(request, "Notice published. It shows as a banner from 2 days before it starts.")
            return redirect("maintenance:notices")
    return render(request, "maintenance/notices.html", {"form": form, "items": MaintenanceNotice.objects.all()[:30]})


@perm_required("maintenance.manage")
def activity(request):
    page, qstr = paginate(request, AuditLog.objects.filter(category="maintenance"))
    t = Table(["When", "User", "Action", "Reference", "Detail"])
    for a in page:
        t.add([f"{timezone.localtime(a.ts):%d %b %Y %H:%M}", a.username or "system", C(a.action, badge="mute"), a.reference, (a.detail or "")[:100]])
    return render(request, "generic_list.html", {"title": "Maintenance activity log", "table": t, "page": page, "qs": qstr})


# ------------------------------------------------------------------ backups
@perm_required("maintenance.manage")
def backups(request):
    t = Table(["Created", "File", "Size", "Records", "Encrypted", "Verified", "", ""])
    for b in BackupRecord.objects.all()[:30]:
        t.add([f"{timezone.localtime(b.created_at):%d %b %Y %H:%M}", b.filename, f"{b.size_bytes / 1024:.0f} KB", b.records, C("Yes" if b.encrypted else "No", badge="ok" if b.encrypted else "bad"),
               C("Verified" if b.verified_ok else ("Failed" if b.verified_ok is False else "Not yet"), badge="ok" if b.verified_ok else "bad" if b.verified_ok is False else "warn"),
               C("Download", f"/maintenance/backups/{b.pk}/download/"), C("Verify", f"/maintenance/backups/{b.pk}/verify/", cls="post")])
    return render(request, "maintenance/backups.html", {"table": t, "age": services.last_backup_age_hours(), "key_set": bool(settings.BACKUP_ENCRYPTION_KEY),
                                                        "dir": str(settings.BACKUP_DIR), "prod": settings.PRODUCTION_MODE})


@require_POST
@perm_required("maintenance.manage")
def backup_create(request):
    try:
        rec = services.create_backup(request.user)
        messages.success(request, f"Backup {rec.filename} created and {'verified' if rec.verified_ok else 'FAILED verification'}. Download it and keep a copy off this server.")
    except Exception as e:
        services.log_failure("backup.create", str(e), user=request.user)
        messages.error(request, f"Backup failed: {e}")
    return redirect("maintenance:backups")


@perm_required("maintenance.manage")
def backup_download(request, pk):
    b = get_object_or_404(BackupRecord, pk=pk)
    path = settings.BACKUP_DIR / b.filename
    if not path.exists():
        raise Http404
    audit("backup.download", "maintenance", "BackupRecord", b.pk, b.filename, detail="Backup downloaded")
    return FileResponse(open(path, "rb"), as_attachment=True, filename=b.filename)


@require_POST
@perm_required("maintenance.manage")
def backup_verify(request, pk):
    ok, err = services.verify_backup(get_object_or_404(BackupRecord, pk=pk), request.user)
    (messages.success if ok else messages.error)(request, "Backup verified: checksum, decryption and record count are correct." if ok else f"Verification failed: {err}")
    return redirect("maintenance:backups")


# ------------------------------------------------------------------ failures, docs, demo reset
@perm_required("maintenance.manage")
def failures(request):
    if request.method == "POST":
        FailedOperation.objects.filter(pk=request.POST.get("resolve")).update(resolved=True, resolved_by=request.user)
        return redirect("maintenance:failures")
    page, qstr = paginate(request, FailedOperation.objects.all())
    return render(request, "maintenance/failures.html", {"page": page, "qs": qstr})


class DocForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = DocPage
        fields = ["title", "slug", "body"]
        widgets = {"body": forms.Textarea(attrs={"rows": 12})}


@perm_required("maintenance.manage")
def docs(request):
    return render(request, "maintenance/docs.html", {"pages": DocPage.objects.all()})


@perm_required("maintenance.manage")
def doc_edit(request, pk=None):
    d = get_object_or_404(DocPage, pk=pk) if pk else None
    form = DocForm(request.POST or None, instance=d)
    if request.method == "POST" and form.is_valid():
        o = form.save(commit=False)
        o.updated_by = request.user
        o.save()
        audit("doc.save", "maintenance", "DocPage", o.pk, o.slug)
        return redirect("maintenance:docs")
    return render(request, "generic_form.html", {"title": "Edit document" if d else "New document", "form": form, "submit": "Save", "cancel": "/maintenance/docs/"})


@perm_required("maintenance.manage")
def doc_view(request, pk):
    return render(request, "maintenance/doc.html", {"d": get_object_or_404(DocPage, pk=pk)})


class ResetForm(StyledMixin, forms.Form):
    confirm = forms.CharField(label='Type RESET DEMO DATA to confirm')


@perm_required("maintenance.manage")
def demo_reset(request):
    form = ResetForm(request.POST or None)
    blocked = settings.PRODUCTION_MODE
    if request.method == "POST" and form.is_valid() and not blocked:
        if form.cleaned_data["confirm"] != "RESET DEMO DATA":
            form.add_error("confirm", "Confirmation text does not match.")
        else:
            audit("demo.reset", "maintenance", detail="Demonstration data reset requested")
            call_command("reset_demo", "--yes-i-am-sure")
            messages.success(request, "Demonstration data reset. Sign in again with the demo accounts.")
            return redirect("accounts:login")
    return render(request, "maintenance/demo_reset.html", {"form": form, "blocked": blocked})
