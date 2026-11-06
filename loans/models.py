from decimal import Decimal

from django.conf import settings
from django.db import models

from core.utils import today


class LoanProduct(models.Model):
    METHODS = [("flat", "Flat rate"), ("reducing", "Reducing balance")]
    FREQ = [("monthly", "Monthly"), ("weekly", "Weekly")]

    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=250, blank=True)
    interest_rate = models.DecimalField("Annual interest rate %", max_digits=6, decimal_places=2, null=True, blank=True,
                                        help_text="Left blank until an authorised administrator enters it.")
    interest_method = models.CharField(max_length=10, choices=METHODS, default="reducing")
    min_amount = models.DecimalField(max_digits=14, decimal_places=2, default=50000)
    max_amount = models.DecimalField(max_digits=14, decimal_places=2, default=20000000)
    min_term = models.PositiveIntegerField("Minimum repayment periods", default=1)
    max_term = models.PositiveIntegerField("Maximum repayment periods", default=36)
    frequency = models.CharField(max_length=10, choices=FREQ, default="monthly")
    processing_fee_pct = models.DecimalField("Processing fee %", max_digits=5, decimal_places=2, default=0)
    penalty_pct = models.DecimalField("Late penalty % per 30 days overdue", max_digits=5, decimal_places=2, default=0)
    grace_periods = models.PositiveIntegerField("Grace periods before first instalment", default=0)
    guarantors_required = models.PositiveIntegerField(default=1)
    savings_multiple = models.DecimalField("Max loan as multiple of savings", max_digits=5, decimal_places=2, null=True, blank=True,
                                           help_text="e.g. 3 means the loan cannot exceed 3x the member's savings. Blank = no limit.")
    eligibility = models.TextField("Eligibility requirements", blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def rate_set(self):
        return self.interest_rate is not None


class Loan(models.Model):
    STATUS = [("pending_officer", "Pending loan officer"), ("pending_committee", "Pending credit committee"),
              ("pending_manager", "Pending manager"), ("approved", "Approved - awaiting disbursement"),
              ("active", "Active"), ("cleared", "Cleared"), ("rejected", "Rejected")]
    PENDING = ("pending_officer", "pending_committee", "pending_manager")

    loan_no = models.CharField(max_length=20, unique=True)
    member = models.ForeignKey("members.Member", on_delete=models.PROTECT, related_name="loans")
    product = models.ForeignKey(LoanProduct, on_delete=models.PROTECT, related_name="loans")
    principal = models.DecimalField(max_digits=14, decimal_places=2)
    # Terms are frozen at application so later product changes never alter an agreed loan
    interest_rate = models.DecimalField(max_digits=6, decimal_places=2)
    interest_method = models.CharField(max_length=10, choices=LoanProduct.METHODS)
    frequency = models.CharField(max_length=10, choices=LoanProduct.FREQ)
    term = models.PositiveIntegerField()
    processing_fee_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    penalty_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    grace_periods = models.PositiveIntegerField(default=0)
    purpose = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=20, choices=STATUS, default="pending_officer", db_index=True)
    applied_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    applied_on = models.DateField(default=today, db_index=True)
    refinance_of = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="refinanced_by")
    disbursed_on = models.DateField(null=True, blank=True, db_index=True)
    disbursed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    disbursement_channel = models.CharField(max_length=10, blank=True)
    fee_charged = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    first_due_date = models.DateField(null=True, blank=True)
    total_interest = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_payable = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    principal_paid = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    interest_paid = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    penalty_paid = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    interest_waived = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    cleared_on = models.DateField(null=True, blank=True)
    disbursement_journal = models.ForeignKey("accounting.Journal", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        indexes = [models.Index(fields=["member", "status"]), models.Index(fields=["status", "disbursed_on"])]

    def __str__(self):
        return f"{self.loan_no} - {self.member.full_name}"

    @property
    def outstanding_principal(self):
        return self.principal - self.principal_paid

    @property
    def is_pending(self):
        return self.status in self.PENDING


class LoanInstallment(models.Model):
    STATUS = [("pending", "Pending"), ("partial", "Partial"), ("paid", "Paid"), ("superseded", "Superseded")]
    loan = models.ForeignKey(Loan, on_delete=models.PROTECT, related_name="installments")
    no = models.PositiveIntegerField()
    due_date = models.DateField(db_index=True)
    principal = models.DecimalField(max_digits=14, decimal_places=2)
    interest = models.DecimalField(max_digits=14, decimal_places=2)
    principal_paid = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    interest_paid = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    penalty_accrued = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    penalty_paid = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    penalty_periods = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=12, choices=STATUS, default="pending", db_index=True)

    class Meta:
        ordering = ["loan_id", "no"]
        unique_together = [("loan", "no")]
        indexes = [models.Index(fields=["status", "due_date"])]

    def __str__(self):
        return f"{self.loan.loan_no} #{self.no}"

    @property
    def total(self):
        return self.principal + self.interest

    @property
    def balance(self):
        return self.principal + self.interest - self.principal_paid - self.interest_paid

    @property
    def penalty_balance(self):
        return self.penalty_accrued - self.penalty_paid


class LoanApproval(models.Model):
    STAGES = [("officer", "Loan officer"), ("committee", "Credit committee"), ("manager", "Manager")]
    DECISIONS = [("recommend", "Recommended"), ("approve", "Approved"), ("reject", "Rejected")]
    loan = models.ForeignKey(Loan, on_delete=models.PROTECT, related_name="approvals")
    stage = models.CharField(max_length=10, choices=STAGES)
    decision = models.CharField(max_length=10, choices=DECISIONS)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    comment = models.TextField(blank=True)
    ts = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["ts", "id"]

    def __str__(self):
        return f"{self.loan.loan_no} {self.stage} {self.decision}"


class LoanGuarantor(models.Model):
    STATUS = [("active", "Pledge active"), ("released", "Released")]
    loan = models.ForeignKey(Loan, on_delete=models.PROTECT, related_name="guarantors")
    guarantor = models.ForeignKey("members.Member", on_delete=models.PROTECT, related_name="guarantees")
    account = models.ForeignKey("savings.SavingsAccount", on_delete=models.PROTECT, related_name="+")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=10, choices=STATUS, default="active")
    created_at = models.DateTimeField(auto_now_add=True)
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.guarantor.full_name} pledges {self.amount}"


class LoanRepayment(models.Model):
    ref = models.CharField(max_length=24, unique=True)
    loan = models.ForeignKey(Loan, on_delete=models.PROTECT, related_name="repayments")
    date = models.DateField(default=today, db_index=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    principal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    interest = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    penalty = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    channel = models.CharField(max_length=10, default="cash")
    allocations = models.JSONField(default=list)
    journal = models.ForeignKey("accounting.Journal", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    till = models.ForeignKey("cashier.Till", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    is_reversed = models.BooleanField(default=False)
    note = models.CharField(max_length=160, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"{self.ref} {self.amount}"
