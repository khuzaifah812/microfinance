from django import forms
from django.contrib.auth import get_user_model

from core.forms import StyledMixin

from .rbac import ROLES

User = get_user_model()


class LoginForm(StyledMixin, forms.Form):
    username = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"autocomplete": "username", "autofocus": True}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))


class OTPForm(StyledMixin, forms.Form):
    token = forms.CharField(label="6-digit code", min_length=6, max_length=6,
                            widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code",
                                                          "pattern": "[0-9]{6}", "autofocus": True}))


class StaffCreateForm(StyledMixin, forms.Form):
    username = forms.CharField(max_length=150)
    first_name = forms.CharField(max_length=60)
    last_name = forms.CharField(max_length=60)
    email = forms.EmailField(required=False)
    phone = forms.CharField(max_length=20, required=False)
    role = forms.ChoiceField(choices=ROLES)

    def clean_username(self):
        u = self.cleaned_data["username"].strip().lower()
        if User.objects.filter(username__iexact=u).exists():
            raise forms.ValidationError("This username is already taken.")
        return u


class StaffEditForm(StyledMixin, forms.Form):
    first_name = forms.CharField(max_length=60)
    last_name = forms.CharField(max_length=60)
    email = forms.EmailField(required=False)
    phone = forms.CharField(max_length=20, required=False)
    role = forms.ChoiceField(choices=ROLES)
    is_active = forms.BooleanField(required=False, label="Account active")
