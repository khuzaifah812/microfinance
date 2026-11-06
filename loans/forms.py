from django import forms

from core.forms import StyledMixin, money_field
from members.models import Member

from .models import LoanProduct

CHANNELS = [("cash", "Cash (my till)"), ("bank", "Bank"), ("momo", "Mobile money"), ("savings", "Member savings account")]


class ProductForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = LoanProduct
        exclude = ["created_at"]
        widgets = {"eligibility": forms.Textarea(attrs={"rows": 3})}

    def clean(self):
        d = super().clean()
        if d.get("min_amount") and d.get("max_amount") and d["min_amount"] > d["max_amount"]:
            raise forms.ValidationError("Minimum amount cannot exceed maximum amount.")
        if d.get("min_term") and d.get("max_term") and d["min_term"] > d["max_term"]:
            raise forms.ValidationError("Minimum period cannot exceed maximum period.")
        r = d.get("interest_rate")
        if r is not None and not (0 <= r <= 100):
            raise forms.ValidationError("Interest rate must be between 0 and 100.")
        return d


class ApplyForm(StyledMixin, forms.Form):
    member_no = forms.CharField(max_length=20, label="Member number")
    product = forms.ModelChoiceField(queryset=LoanProduct.objects.filter(is_active=True), empty_label="Select product")
    amount = money_field("Amount requested (UGX)")
    term = forms.IntegerField(min_value=1, max_value=120, label="Repayment period (number of instalments)")
    purpose = forms.CharField(max_length=200, required=False)
    refinance_of = forms.IntegerField(required=False, widget=forms.HiddenInput())

    def clean_member_no(self):
        m = Member.objects.filter(member_no__iexact=self.cleaned_data["member_no"].strip()).first()
        if not m:
            raise forms.ValidationError("No member with this number.")
        self.member = m
        return m.member_no

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["product"].label_from_instance = lambda p: f"{p.name} - {'rate not set' if not p.rate_set else f'{p.interest_rate}% {p.get_interest_method_display().lower()}'}"


class GuarantorForm(StyledMixin, forms.Form):
    guarantor_no = forms.CharField(max_length=20, label="Guarantor member number")
    account_no = forms.CharField(max_length=24, label="Savings account to pledge")
    amount = money_field("Amount to pledge (UGX)")


class DecisionForm(StyledMixin, forms.Form):
    decision = forms.ChoiceField(choices=[("approve", "Approve / recommend"), ("reject", "Reject")])
    comment = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False, label="Comment (required when rejecting)")


class DisburseForm(StyledMixin, forms.Form):
    channel = forms.ChoiceField(choices=CHANNELS, label="Pay out through")
    savings_account_no = forms.CharField(max_length=24, required=False, label="Savings account (if crediting savings)")


class RepayForm(StyledMixin, forms.Form):
    amount = money_field("Amount (UGX)")
    channel = forms.ChoiceField(choices=CHANNELS, label="Paid through")
    savings_account_no = forms.CharField(max_length=24, required=False, label="Savings account (if paying from savings)")
    note = forms.CharField(max_length=160, required=False)


class SettleForm(StyledMixin, forms.Form):
    channel = forms.ChoiceField(choices=CHANNELS, label="Paid through")
    savings_account_no = forms.CharField(max_length=24, required=False, label="Savings account (if paying from savings)")


class RescheduleForm(StyledMixin, forms.Form):
    term = forms.IntegerField(min_value=1, max_value=120, label="New number of instalments (from today)")
    rate = forms.DecimalField(required=False, min_value=0, max_value=100, max_digits=6, decimal_places=2, label="New annual rate % (blank = keep current)")
    reason = forms.CharField(max_length=250, widget=forms.Textarea(attrs={"rows": 2}))
