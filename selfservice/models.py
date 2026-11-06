from django.conf import settings
from django.db import models
from django.utils import timezone


class PortalAccess(models.Model):
    member = models.OneToOneField("members.Member", on_delete=models.PROTECT, related_name="portal")
    pin_hash = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)
    must_change_pin = models.BooleanField(default=True)
    failed_attempts = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    last_login = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Portal: {self.member.member_no}"

    @property
    def is_locked(self):
        return bool(self.locked_until and self.locked_until > timezone.now())


class StatementRequest(models.Model):
    member = models.ForeignKey("members.Member", on_delete=models.PROTECT, related_name="statement_requests")
    account = models.ForeignKey("savings.SavingsAccount", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    date_from = models.DateField()
    date_to = models.DateField()
    status = models.CharField(max_length=10, default="open")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
