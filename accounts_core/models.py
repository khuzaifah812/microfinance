from django.conf import settings
from django.db import models
from django.db.models.signals import pre_save
from django.dispatch import receiver
from django.utils import timezone

from .rbac import ROLES, ROLE_LABEL, TWO_FA_ROLES


class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=20, choices=ROLES, db_index=True)
    phone = models.CharField(max_length=20, blank=True)
    must_change_password = models.BooleanField(default=True)
    failed_attempts = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    last_login_ip = models.GenericIPAddressField(null=True, blank=True)
    email_verified = models.BooleanField(default=False)
    email_code_hash = models.CharField(max_length=64, blank=True)
    email_code_expires = models.DateTimeField(null=True, blank=True)
    email_code_sent_at = models.DateTimeField(null=True, blank=True)
    email_code_attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["user__username"]

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} ({self.role_label})"

    @property
    def role_label(self):
        return ROLE_LABEL.get(self.role, self.role)

    @property
    def needs_2fa(self):
        return self.role in TWO_FA_ROLES

    @property
    def is_locked(self):
        return bool(self.locked_until and self.locked_until > timezone.now())


class LoginHistory(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="logins")
    username = models.CharField(max_length=150, db_index=True)
    success = models.BooleanField(default=False, db_index=True)
    reason = models.CharField(max_length=80, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    ts = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-ts"]
        verbose_name_plural = "Login history"

    def __str__(self):
        return f"{self.username} {'OK' if self.success else 'FAIL'} {self.ts:%Y-%m-%d %H:%M}"


class AuditQuerySet(models.QuerySet):
    def update(self, **kw):
        raise PermissionError("Audit log is append-only.")

    def delete(self):
        raise PermissionError("Audit log is append-only.")

    def bulk_update(self, *a, **kw):
        raise PermissionError("Audit log is append-only.")


class AuditLog(models.Model):
    ts = models.DateTimeField(default=timezone.now, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    username = models.CharField(max_length=150, blank=True, db_index=True)
    role = models.CharField(max_length=20, blank=True)
    action = models.CharField(max_length=60, db_index=True)
    category = models.CharField(max_length=30, db_index=True)
    entity = models.CharField(max_length=60, blank=True)
    entity_id = models.CharField(max_length=40, blank=True)
    reference = models.CharField(max_length=40, blank=True, db_index=True)
    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    detail = models.TextField(blank=True)
    approval = models.CharField(max_length=200, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    device = models.CharField(max_length=255, blank=True)
    prev_hash = models.CharField(max_length=64)
    hash = models.CharField(max_length=64)

    objects = AuditQuerySet.as_manager()

    class Meta:
        ordering = ["-id"]
        indexes = [models.Index(fields=["category", "ts"])]

    def __str__(self):
        return f"#{self.pk} {self.action} {self.reference}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError("Audit log rows cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Audit log rows cannot be deleted.")


@receiver(pre_save, sender=AuditLog)
def _audit_append_only(sender, instance, **kwargs):
    # belt and braces: refuse any save of an already persisted audit row
    if instance.pk and AuditLog.objects.filter(pk=instance.pk).exists():
        raise PermissionError("Audit log is append-only.")


class AuditHead(models.Model):
    """Single row that serialises appends to the hash chain (locked with SELECT ... FOR UPDATE)."""
    last_id = models.BigIntegerField(default=0)
    last_hash = models.CharField(max_length=64, default="0" * 64)
