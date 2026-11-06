from django.conf import settings
from django.db import models

from core.utils import today


class SavingsAccount(models.Model):
    KINDS = [("savings", "Savings"), ("shares", "Share capital")]
    STATUS = [("active", "Active"), ("dormant", "Dormant"), ("frozen", "Frozen"), ("closed", "Closed")]

    account_no = models.CharField(max_length=24, unique=True)
    member = models.ForeignKey("members.Member", on_delete=models.PROTECT, related_name="accounts")
    kind = models.CharField(max_length=10, choices=KINDS, db_index=True)
    name = models.CharField(max_length=60, default="Ordinary savings")
    status = models.CharField(max_length=10, choices=STATUS, default="active", db_index=True)
    balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    pledged = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    min_balance = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True,
                                      help_text="Overrides the system-wide minimum for this account")
    opened_on = models.DateField(default=today)
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["account_no"]
        indexes = [models.Index(fields=["member", "kind"])]
        constraints = [models.CheckConstraint(condition=models.Q(balance__gte=0), name="sav_balance_nonneg"),
                       models.CheckConstraint(condition=models.Q(pledged__gte=0), name="sav_pledged_nonneg")]

    def __str__(self):
        return f"{self.account_no} ({self.member.full_name})"

    @property
    def available(self):
        return self.balance - self.pledged


class SavingsTransaction(models.Model):
    KINDS = [("deposit", "Deposit"), ("withdrawal", "Withdrawal"), ("share_purchase", "Share purchase"),
             ("transfer_in", "Transfer in"), ("transfer_out", "Transfer out"), ("interest", "Interest"),
             ("dividend", "Dividend"), ("reversal", "Reversal")]
    CHANNELS = [("cash", "Cash (till)"), ("bank", "Bank"), ("momo", "Mobile money"), ("internal", "Internal")]
    CREDITS = ("deposit", "share_purchase", "transfer_in", "interest", "dividend")

    ref = models.CharField(max_length=24, unique=True)
    account = models.ForeignKey(SavingsAccount, on_delete=models.PROTECT, related_name="transactions")
    kind = models.CharField(max_length=16, choices=KINDS, db_index=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    balance_after = models.DecimalField(max_digits=14, decimal_places=2)
    date = models.DateField(default=today, db_index=True)
    channel = models.CharField(max_length=10, choices=CHANNELS, default="cash")
    narration = models.CharField(max_length=200, blank=True)
    journal = models.ForeignKey("accounting.Journal", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    till = models.ForeignKey("cashier.Till", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    group_ref = models.CharField(max_length=24, blank=True, db_index=True)
    reverses = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversed_by")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        indexes = [models.Index(fields=["account", "date"]), models.Index(fields=["kind", "date"])]

    def __str__(self):
        return f"{self.ref} {self.kind} {self.amount}"

    @property
    def is_credit(self):
        return self.kind in self.CREDITS or (self.kind == "reversal" and self.reverses and not self.reverses.is_credit)


class InterestRun(models.Model):
    KINDS = [("interest", "Savings interest"), ("dividend", "Share dividend")]
    kind = models.CharField(max_length=10, choices=KINDS)
    period = models.CharField(max_length=20)
    rate = models.DecimalField(max_digits=6, decimal_places=2)
    total = models.DecimalField(max_digits=14, decimal_places=2)
    accounts_count = models.PositiveIntegerField()
    journal = models.ForeignKey("accounting.Journal", on_delete=models.PROTECT, related_name="+")
    run_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    run_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-run_at"]
        unique_together = [("kind", "period")]

    def __str__(self):
        return f"{self.kind} {self.period}"
