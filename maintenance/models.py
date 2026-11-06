from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

CATEGORIES = [("overview", "Overview"), ("members", "Members & savings"), ("lending", "Lending"),
              ("finance", "Finance & accounting"), ("control", "Control & reporting"),
              ("channels", "Channels"), ("admin", "Administration"), ("extensions", "Extensions")]


class Module(models.Model):
    """Registry entry for a feature module. Sidebar is built from enabled modules + role access."""
    STATUS = [("draft", "Draft"), ("testing", "In developer testing"), ("security_review", "In security review"),
              ("ready", "Ready for activation"), ("active", "Active")]
    key = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=250, blank=True)
    icon = models.CharField(max_length=40, default="extension", help_text="Google Material Symbols icon name")
    category = models.CharField(max_length=12, choices=CATEGORIES, default="extensions")
    url_name = models.CharField(max_length=80, blank=True, help_text="Named URL for built-in modules")
    required_perm = models.CharField(max_length=40, blank=True)
    version = models.CharField(max_length=20, default="1.0.0")
    sort_order = models.PositiveIntegerField(default=100)
    builtin = models.BooleanField(default=False)
    enabled = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=STATUS, default="draft")
    # activation gates for custom modules (spec section 15)
    dev_tested = models.BooleanField("Developer testing passed", default=False)
    security_reviewed = models.BooleanField("Security review passed", default=False)
    migrated = models.BooleanField("Database migration applied", default=False)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    settings_json = models.JSONField("Module settings", default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category", "sort_order", "name"]

    def __str__(self):
        return self.name

    @property
    def can_activate(self):
        return self.builtin or (self.dev_tested and self.security_reviewed and self.migrated and self.approved_by_id)


class ModuleRole(models.Model):
    module = models.ForeignKey(Module, on_delete=models.CASCADE, related_name="roles")
    role = models.CharField(max_length=20)

    class Meta:
        unique_together = [("module", "role")]

    def __str__(self):
        return f"{self.module.key}:{self.role}"


class MaintenanceNotice(models.Model):
    title = models.CharField(max_length=120)
    message = models.TextField()
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-starts_at"]

    def __str__(self):
        return self.title

    @property
    def is_current(self):
        n = timezone.now()
        return self.is_active and self.starts_at - timedelta(days=2) <= n <= self.ends_at


class FailedOperation(models.Model):
    operation = models.CharField(max_length=80, db_index=True)
    error = models.TextField()
    context = models.JSONField(default=dict, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    ts = models.DateTimeField(default=timezone.now, db_index=True)
    resolved = models.BooleanField(default=False)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-ts"]

    def __str__(self):
        return f"{self.operation}: {self.error[:50]}"


class BackupRecord(models.Model):
    filename = models.CharField(max_length=160, unique=True)
    size_bytes = models.BigIntegerField(default=0)
    sha256 = models.CharField(max_length=64)
    encrypted = models.BooleanField(default=True)
    records = models.PositiveIntegerField(default=0)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_ok = models.BooleanField(null=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.filename


class DocPage(models.Model):
    title = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)
    body = models.TextField()
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title
