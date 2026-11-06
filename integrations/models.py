from django.conf import settings
from django.db import models
from django.utils import timezone


class ProviderConfig(models.Model):
    PROVIDERS = [("mtn_momo", "MTN Mobile Money Uganda"), ("airtel_money", "Airtel Money Uganda"),
                 ("sms", "SMS gateway"), ("email", "Email (SMTP)")]
    provider = models.CharField(max_length=20, choices=PROVIDERS, unique=True)
    enabled = models.BooleanField(default=False)
    environment = models.CharField(max_length=10, default="sandbox", choices=[("sandbox", "Sandbox"), ("live", "Live")])
    merchant_id = models.CharField("Merchant / short code", max_length=60, blank=True)
    callback_note = models.CharField(max_length=200, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["provider"]

    def __str__(self):
        return self.get_provider_display()


class MobileMoneyTransaction(models.Model):
    STATUS = [("confirmed", "Confirmed by provider"), ("failed", "Failed"), ("unmatched", "Unmatched"),
              ("applied", "Applied to account"), ("pending", "Pending")]
    provider = models.CharField(max_length=20)
    external_ref = models.CharField(max_length=80)
    phone = models.CharField(max_length=20, db_index=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    purpose_ref = models.CharField("Payment reference", max_length=40, blank=True)
    status = models.CharField(max_length=12, choices=STATUS, default="pending", db_index=True)
    member = models.ForeignKey("members.Member", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    applied_to = models.CharField(max_length=40, blank=True)
    raw = models.JSONField(default=dict)
    received_at = models.DateTimeField(default=timezone.now, db_index=True)
    applied_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-received_at"]
        unique_together = [("provider", "external_ref")]

    def __str__(self):
        return f"{self.provider} {self.external_ref} {self.amount}"


class SmsOutbox(models.Model):
    STATUS = [("queued", "Queued - not sent"), ("sent", "Sent (provider accepted)"), ("failed", "Failed")]
    phone = models.CharField(max_length=20)
    message = models.CharField(max_length=480)
    kind = models.CharField(max_length=30, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="queued", db_index=True)
    error = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.phone}: {self.message[:30]}"
