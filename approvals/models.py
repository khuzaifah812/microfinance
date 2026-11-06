from django.conf import settings
from django.db import models

KINDS = [("withdrawal", "Large withdrawal"), ("expense", "Large expense"), ("reschedule", "Loan rescheduling"),
         ("reversal", "Financial correction (reversal)"), ("manual_journal", "Manual journal entry"),
         ("share_transfer", "Share transfer")]


class ApprovalRule(models.Model):
    kind = models.CharField(max_length=20, choices=KINDS, unique=True)
    threshold = models.DecimalField("Approval required above (UGX)", max_digits=14, decimal_places=2, default=0)
    enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["kind"]

    def __str__(self):
        return f"{self.kind} > {self.threshold}"


class ApprovalRequest(models.Model):
    STATUS = [("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected")]
    ref = models.CharField(max_length=20, unique=True)
    kind = models.CharField(max_length=20, choices=KINDS, db_index=True)
    summary = models.CharField(max_length=250)
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=10, choices=STATUS, default="pending", db_index=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    requested_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=250, blank=True)
    result_ref = models.CharField(max_length=40, blank=True)

    class Meta:
        ordering = ["-id"]
        indexes = [models.Index(fields=["status", "kind"])]

    def __str__(self):
        return f"{self.ref} {self.kind} {self.status}"
