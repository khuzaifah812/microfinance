import time

from django import forms
from django.contrib import messages
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts_core.audit import audit
from accounts_core.rbac import perm_required
from core.forms import StyledMixin
from core.models import SiteConfig
from core.utils import today
from loans.models import Loan
from members.models import Member
from savings.models import SavingsAccount, SavingsTransaction

from . import services, ussd
from .models import PortalAccess

PORTAL_IDLE = 300


def portal_required(view):
    def wrapped(request, *a, **kw):
        mid = request.session.get("portal_member")
        last = request.session.get("portal_last", 0)
        if not mid or time.time() - last > PORTAL_IDLE:
            request.session.pop("portal_member", None)
            return redirect("portal:login")
        request.session["portal_last"] = time.time()
        member = Member.objects.filter(pk=mid, status="active").first()
        acc = PortalAccess.objects.filter(member=member, is_active=True).first() if member else None
        if not acc:
            request.session.pop("portal_member", None)
            return redirect("portal:login")
        if acc.must_change_pin and request.resolver_match.url_name != "pin":
            return redirect("portal:pin")
        request.member = member
        return view(request, *a, **kw)
    wrapped.__name__ = view.__name__
    return wrapped


class LoginForm(StyledMixin, forms.Form):
    member_no = forms.CharField(max_length=20, label="Member number")
    pin = forms.CharField(max_length=6, min_length=6, label="6-digit PIN", widget=forms.PasswordInput(attrs={"inputmode": "numeric", "pattern": "[0-9]{6}", "autocomplete": "current-password"}))


def login_view(request):
    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        acc, err = services.authenticate_member(request, form.cleaned_data["member_no"], form.cleaned_data["pin"])
        if err:
            messages.error(request, err)
        else:
            request.session.cycle_key()
            request.session["portal_member"] = acc.member_id
            request.session["portal_last"] = time.time()
            audit("portal.login", "security", "Member", acc.member_id, acc.member.member_no, detail="Member portal sign-in")
            return redirect("portal:home")
    return render(request, "selfservice/login.html", {"form": form, "site": SiteConfig.get()})


def logout_view(request):
    request.session.pop("portal_member", None)
    return redirect("portal:login")


class PinForm(StyledMixin, forms.Form):
    new_pin = forms.CharField(min_length=6, max_length=6, widget=forms.PasswordInput(attrs={"inputmode": "numeric", "pattern": "[0-9]{6}"}), label="New 6-digit PIN")
    confirm = forms.CharField(min_length=6, max_length=6, widget=forms.PasswordInput(attrs={"inputmode": "numeric", "pattern": "[0-9]{6}"}), label="Confirm PIN")

    def clean(self):
        d = super().clean()
        p = d.get("new_pin", "")
        if d.get("confirm") != p:
            raise forms.ValidationError("PINs do not match.")
        if not services.PIN_RE.match(p) or len(set(p)) == 1 or p in "0123456789" or p in "9876543210":
            raise forms.ValidationError("Choose a PIN of 6 digits that is not a simple sequence or repeated digit.")
        return d


@portal_required
def pin(request):
    form = PinForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        acc = request.member.portal
        acc.pin_hash, acc.must_change_pin = make_password(form.cleaned_data["new_pin"]), False
        acc.save()
        audit("portal.pin_change", "security", "Member", request.member.pk, request.member.member_no, detail="Member changed portal PIN")
        messages.success(request, "PIN updated.")
        return redirect("portal:home")
    return render(request, "selfservice/pin.html", {"form": form, "member": request.member, "site": SiteConfig.get()})


@portal_required
def home(request):
    m = request.member
    return render(request, "selfservice/home.html", {"member": m, "accounts": m.accounts.exclude(status="closed"), "loans": m.loans.filter(status="active"),
                  "recent": SavingsTransaction.objects.filter(account__member=m).select_related("account")[:8], "site": SiteConfig.get()})


@portal_required
def loan_view(request, pk):
    loan = get_object_or_404(Loan, pk=pk, member=request.member, status__in=("active", "cleared"))
    return render(request, "selfservice/loan.html", {"loan": loan, "insts": loan.installments.exclude(status="superseded"), "member": request.member, "site": SiteConfig.get()})


@portal_required
def statement(request, pk):
    a = get_object_or_404(SavingsAccount, pk=pk, member=request.member)
    f = request.GET.get("from") or str(today().replace(day=1))
    t = request.GET.get("to") or str(today())
    txs = a.transactions.filter(date__gte=f, date__lte=t).order_by("date", "id")
    audit("portal.statement", "member", "SavingsAccount", a.pk, a.account_no, detail=f"Statement viewed {f}..{t}")
    return render(request, "selfservice/statement.html", {"a": a, "txs": txs, "f": f, "t": t, "member": request.member, "site": SiteConfig.get()})


# ---------------------------------------------------------- staff pages (under /channels/, behind the normal staff gate)
@perm_required("portal.manage")
def portal_admin(request):
    q = request.GET.get("q", "").strip()
    pin_shown = request.session.pop("portal_pin", None)
    members = Member.objects.filter(full_name__icontains=q)[:15] if q else []
    access = PortalAccess.objects.select_related("member")[:30]
    return render(request, "selfservice/admin.html", {"q": q, "members": members, "access": access, "pin_shown": pin_shown})


@require_POST
@perm_required("portal.manage")
def issue_pin(request, pk):
    m = get_object_or_404(Member, pk=pk, status="active")
    request.session["portal_pin"] = {"member": m.member_no, "name": m.full_name, "pin": services.issue_pin(m, request.user)}
    messages.success(request, "One-time PIN generated. Give it to the member in person; they must change it at first sign-in.")
    return redirect("channels:portal_admin")


@require_POST
@perm_required("portal.manage")
def toggle_access(request, pk):
    acc = get_object_or_404(PortalAccess, pk=pk)
    acc.is_active = not acc.is_active
    acc.save(update_fields=["is_active"])
    audit("portal.access", "security", "Member", acc.member_id, acc.member.member_no, new={"active": acc.is_active})
    return redirect("channels:portal_admin")


@perm_required("portal.manage")
def ussd_preview(request):
    member = Member.objects.filter(pk=request.POST.get("member") or request.GET.get("member")).first()
    text = request.POST.get("text", "")
    screen, cont = "", False
    if member:
        entry = request.POST.get("entry", "").strip()
        if request.POST.get("reset"):
            text = ""
        elif entry and text != "END":
            text = f"{text}*{entry}" if text else entry
        msg, cont = ussd.respond(member, text)
        screen = msg.replace("{sacco}", SiteConfig.get().short_name)
    return render(request, "selfservice/ussd.html", {"members": Member.objects.filter(status="active")[:50], "member": member, "screen": screen, "text": text, "cont": cont})
