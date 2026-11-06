from django.conf import settings
from django.db import models

from core.utils import today


class ImmutableQuerySet(models.QuerySet):
    def update(self, **kw):
        raise PermissionError("Posted accounting records cannot be modified. Post a reversal instead.")

    def delete(self):
        raise PermissionError("Posted accounting records cannot be deleted. Post a reversal instead.")


class Account(models.Model):
    TYPES = [("asset", "Asset"), ("liability", "Liability"), ("equity", "Equity"), ("income", "Income"), ("expense", "Expense")]
    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=100)
    type = models.CharField(max_length=10, choices=TYPES, db_index=True)
    description = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)
    is_system = models.BooleanField(default=False, help_text="System accounts are used by automatic postings and cannot be removed.")

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.name}"

    @property
    def debit_normal(self):
        return self.type in ("asset", "expense")


class Journal(models.Model):
    ref = models.CharField(max_length=24, unique=True)
    date = models.DateField(default=today, db_index=True)
    description = models.CharField(max_length=250)
    source = models.CharField(max_length=20, db_index=True, default="manual")
    source_ref = models.CharField(max_length=30, blank=True, db_index=True)
    reverses = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversals")
    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    posted_at = models.DateTimeField(auto_now_add=True)

    objects = ImmutableQuerySet.as_manager()

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.ref} {self.description}"

    def save(self, *a, **kw):
        if not self._state.adding:
            raise PermissionError("Posted journals are immutable. Post a reversal entry instead.")
        super().save(*a, **kw)

    def delete(self, *a, **kw):
        raise PermissionError("Posted journals cannot be deleted. Post a reversal entry instead.")

    @property
    def is_reversed(self):
        return self.reversals.exists()

    @property
    def total(self):
        return sum((l.debit for l in self.lines.all()), 0)


class JournalLine(models.Model):
    journal = models.ForeignKey(Journal, on_delete=models.PROTECT, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="lines")
    debit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    memo = models.CharField(max_length=200, blank=True)

    objects = ImmutableQuerySet.as_manager()

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["account", "journal"])]
        constraints = [models.CheckConstraint(
            condition=(models.Q(debit__gte=0) & models.Q(credit__gte=0)), name="jl_nonneg")]

    def __str__(self):
        return f"{self.account.code} Dr{self.debit} Cr{self.credit}"

    def save(self, *a, **kw):
        if not self._state.adding:
            raise PermissionError("Journal lines are immutable.")
        super().save(*a, **kw)

    def delete(self, *a, **kw):
        raise PermissionError("Journal lines cannot be deleted.")


class Voucher(models.Model):
    """Expenses (payments) and other income (receipts). Large expenses wait for a second approver."""
    KINDS = [("expense", "Expense / payment"), ("income", "Other income / receipt")]
    MODES = [("cash", "Cash (till)"), ("bank", "Bank"), ("momo", "Mobile money"), ("credit", "On credit (payable / receivable)")]
    STATUS = [("pending_approval", "Pending approval"), ("posted", "Posted"), ("rejected", "Rejected"), ("reversed", "Reversed")]

    ref = models.CharField(max_length=24, unique=True)
    kind = models.CharField(max_length=10, choices=KINDS, db_index=True)
    date = models.DateField(default=today, db_index=True)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="vouchers")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    party = models.CharField("Payee / payer", max_length=120)
    description = models.CharField(max_length=250)
    mode = models.CharField(max_length=8, choices=MODES, default="cash")
    status = models.CharField(max_length=20, choices=STATUS, default="posted", db_index=True)
    journal = models.ForeignKey(Journal, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    settled_on = models.DateField(null=True, blank=True)
    settle_journal = models.ForeignKey(Journal, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [models.Index(fields=["kind", "status"])]

    def __str__(self):
        return f"{self.ref} {self.kind} {self.amount}"


class BankAccount(models.Model):
    name = models.CharField(max_length=80)
    bank_name = models.CharField(max_length=80)
    account_number = models.CharField(max_length=40, unique=True)
    gl_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.bank_name})"


class BankReconciliation(models.Model):
    bank_account = models.ForeignKey(BankAccount, on_delete=models.PROTECT, related_name="reconciliations")
    as_of = models.DateField(db_index=True)
    statement_balance = models.DecimalField(max_digits=14, decimal_places=2)
    deposits_in_transit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    outstanding_payments = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    gl_balance = models.DecimalField(max_digits=14, decimal_places=2)
    difference = models.DecimalField(max_digits=14, decimal_places=2)
    notes = models.TextField(blank=True)
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-as_of", "-id"]

    def __str__(self):
        return f"{self.bank_account} {self.as_of}"

    @property
    def reconciled(self):
        return self.difference == 0
