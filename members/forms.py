import re

from django import forms
from django.utils import timezone

from core import crypto
from core.forms import DateInput, StyledMixin

from .models import Member, MemberDocument

PHONE_RE = re.compile(r"^\+?\d{10,13}$")
NIN_RE = re.compile(r"^[A-Z0-9]{14}$")


class MemberForm(StyledMixin, forms.ModelForm):
    member_no = forms.CharField(max_length=20, required=False, label="Member number",
                                help_text="Leave blank to generate automatically. Must be unique.")
    nin = forms.CharField(max_length=14, required=False, label="National ID number (NIN)",
                          help_text="14 characters. Stored encrypted; masked for staff without PII permission.")
    consent = forms.BooleanField(required=True, label="The member has consented to the SACCO processing their personal data (Data Protection and Privacy Act, 2019).")

    class Meta:
        model = Member
        fields = ["member_no", "full_name", "gender", "date_of_birth", "nin", "phone", "alt_phone", "email", "address", "occupation",
                  "next_of_kin_name", "next_of_kin_relation", "next_of_kin_phone", "photo", "joined_on"]
        widgets = {"date_of_birth": DateInput(), "joined_on": DateInput()}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        if self.instance.pk:
            self.fields["member_no"].disabled = True
            self.fields["nin"].initial = self.instance.nin
            self.fields["consent"].initial = self.instance.consent_given
            self.fields["consent"].required = False
        self.order_fields(["member_no", "full_name", "gender", "date_of_birth", "nin", "phone", "alt_phone", "email", "address", "occupation",
                           "next_of_kin_name", "next_of_kin_relation", "next_of_kin_phone", "photo", "joined_on", "consent"])

    def clean_member_no(self):
        v = (self.cleaned_data.get("member_no") or "").strip().upper()
        if v and Member.objects.filter(member_no__iexact=v).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(f"Member number {v} already exists. Member numbers must be unique.")
        return v

    def clean_phone(self):
        v = re.sub(r"[\s-]", "", self.cleaned_data["phone"])
        if not PHONE_RE.match(v):
            raise forms.ValidationError("Enter a valid phone number, e.g. 0772123456 or +256772123456.")
        return v

    def clean_next_of_kin_phone(self):
        v = re.sub(r"[\s-]", "", self.cleaned_data["next_of_kin_phone"])
        if not PHONE_RE.match(v):
            raise forms.ValidationError("Enter a valid phone number.")
        return v

    def clean_nin(self):
        v = (self.cleaned_data.get("nin") or "").strip().upper()
        if not v:
            return ""
        if not NIN_RE.match(v):
            raise forms.ValidationError("A national ID number has 14 letters/digits.")
        h = crypto.blind_hash(v)
        if Member.objects.filter(nin_hash=h).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("A member with this National ID is already registered.")
        return v


class DocumentForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = MemberDocument
        fields = ["kind", "file", "note"]
