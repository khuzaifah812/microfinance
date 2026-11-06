from django.db import models

METRICS = [
    ("members_total", "Total members"), ("members_active", "Active members"), ("savings_total", "Member savings (UGX)"),
    ("shares_total", "Share capital (UGX)"), ("loans_outstanding", "Outstanding loan portfolio (UGX)"),
    ("loans_disbursed_ytd", "Loans disbursed this year (UGX)"), ("loans_count_active", "Active loans (count)"),
    ("arrears_amount", "Loans in arrears (UGX)"), ("par_percent", "Portfolio at risk %"), ("provision_held", "Loan-loss provision held (UGX)"),
    ("provision_required", "Loan-loss provision required (UGX)"), ("income_ytd", "Income this year (UGX)"), ("expense_ytd", "Expenditure this year (UGX)"),
    ("surplus_ytd", "Surplus / (deficit) this year (UGX)"), ("liquid_assets", "Cash, bank and mobile money (UGX)"), ("total_assets", "Total assets (UGX)"),
    ("total_liabilities", "Total liabilities (UGX)"), ("total_equity", "Total equity (UGX)"),
]


class UmraLine(models.Model):
    """Configurable management-summary lines so layouts can follow regulator template changes."""
    code = models.CharField(max_length=20, unique=True)
    label = models.CharField(max_length=160)
    metric = models.CharField(max_length=30, choices=METRICS)
    order = models.PositiveIntegerField(default=100)
    enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "code"]

    def __str__(self):
        return f"{self.code} {self.label}"
