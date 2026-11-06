from django import forms

from core.forms import StyledMixin, money_field
from members.models import Member

from .models import SavingsAccount


class OpenAccountForm(StyledMixin, forms.Form):
    member_no = forms.CharField(max_length=20, label="Member number")
    kind = forms.ChoiceField(choices=SavingsAccount.KINDS, label="Account type")
    name = forms.CharField(max_length=60, required=False, label="Account name", help_text="e.g. Ordinary savings, Education savings")
    account_no = forms.CharField(max_length=24, required=False, label="Account number",
                                 help_text="Leave blank to generate automatically. Must be unique.")
    min_balance = forms.DecimalField(required=False, min_value=0, max_digits=14, decimal_places=0, label="Minimum balance override (UGX)")

    def clean_member_no(self):
        m = Member.objects.filter(member_no__iexact=self.cleaned_data["member_no"].strip()).first()
        if not m:
            raise forms.ValidationError("No member with this number.")
        self.member = m
        return m.member_no


class MoneyForm(StyledMixin, forms.Form):
    amount = money_field("Amount (UGX)")
    channel = forms.ChoiceField(choices=[("cash", "Cash (my till)"), ("bank", "Bank transfer")], label="Paid through")
    narration = forms.CharField(max_length=200, required=False, label="Narration / reference")


class TransferForm(StyledMixin, forms.Form):
    to_account = forms.CharField(max_length=24, label="Destination account number")
    amount = money_field("Amount (UGX)")
    narration = forms.CharField(max_length=200, required=False)


class InterestForm(StyledMixin, forms.Form):
    kind = forms.ChoiceField(choices=[("interest", "Savings interest (rate p.a., applied on current balance / 12)"),
                                      ("dividend", "Share dividend (rate p.a. on share balance)")])
    period = forms.CharField(max_length=20, label="Period label", help_text="e.g. 2026-10 for monthly interest, FY2026 for dividends. Each period can be posted once.")
    rate = forms.DecimalField(min_value=0.01, max_value=100, max_digits=6, decimal_places=2, label="Annual rate %")
