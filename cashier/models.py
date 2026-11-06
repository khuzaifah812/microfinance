from django.conf import settings
from django.db import models

from core.utils import today

DENOMINATIONS = [50000, 20000, 10000, 5000, 2000, 1000, 500, 200, 100, 50]


class Till(models.Model):
    STATUS = [("open", "Open"), ("closed", "Closed")]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tills")
    business_date = models.DateField(default=today, db_index=True)
    opening_float = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=8, choices=STATUS, default="open", db_index=True)
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    counted = models.JSONField(default=dict, blank=True)
    expected_cash = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    actual_cash = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    variance = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    variance_reason = models.CharField(max_length=250, blank=True)
    variance_journal = models.ForeignKey("accounting.Journal", null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-id"]
        indexes = [models.Index(fields=["user", "status"])]

    def __str__(self):
        return f"Till #{self.pk} {self.user.username} {self.business_date}"

    @property
    def cash_in(self):
        return self.entries.filter(direction="in").aggregate(s=models.Sum("amount"))["s"] or 0

    @property
    def cash_out(self):
        return self.entries.filter(direction="out").aggregate(s=models.Sum("amount"))["s"] or 0

    @property
    def expected(self):
        return self.opening_float + self.cash_in - self.cash_out


class TillEntry(models.Model):
    till = models.ForeignKey(Till, on_delete=models.PROTECT, related_name="entries")
    ts = models.DateTimeField(auto_now_add=True)
    direction = models.CharField(max_length=3, choices=[("in", "Cash in"), ("out", "Cash out")])
    kind = models.CharField(max_length=20)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    reference = models.CharField(max_length=30, blank=True)
    description = models.CharField(max_length=200, blank=True)
    journal = models.ForeignKey("accounting.Journal", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["id"]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name="tillentry_pos")]

    def __str__(self):
        return f"{self.direction} {self.amount} {self.kind}"
