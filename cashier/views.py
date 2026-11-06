from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts_core.rbac import can, perm_required
from core.forms import StyledMixin, money_field
from core.ui import C, M, Table, paginate
from core.utils import D, today

from . import services
from .models import DENOMINATIONS, Till


class OpenForm(StyledMixin, forms.Form):
    opening_float = forms.DecimalField(min_value=0, max_digits=14, decimal_places=0, label="Opening cash float (UGX)")


class TransferForm(StyledMixin, forms.Form):
    to_till = forms.ModelChoiceField(queryset=Till.objects.none(), label="Transfer to till")
    amount = money_field("Amount (UGX)")

    def __init__(self, *a, user=None, **kw):
        super().__init__(*a, **kw)
        self.fields["to_till"].queryset = Till.objects.filter(status="open").exclude(user=user).select_related("user")
        self.fields["to_till"].label_from_instance = lambda t: f"Till #{t.pk} - {t.user.get_full_name() or t.user.username}"


@perm_required("cashier.till", "cashier.view_all")
def home(request):
    till = services.get_open_till(request.user)
    form = OpenForm()
    recent = Till.objects.filter(user=request.user)[:8]
    t = Table(["Till", "Date", "Float", "Expected", "Actual", "Difference", "Status", ""])
    for x in recent:
        t.add([f"#{x.pk}", f"{x.business_date:%d %b %Y}", M(x.opening_float), M(x.expected_cash if x.status == "closed" else x.expected),
               M(x.actual_cash) if x.actual_cash is not None else "-", C(f"{x.variance:,.0f}", badge="bad" if x.variance and x.variance < 0 else "warn" if x.variance else "ok", num=True) if x.variance is not None else "-",
               C(x.get_status_display(), badge=True), C("View", f"/cashier/till/{x.pk}/")])
    return render(request, "cashier/home.html", {"till": till, "form": form, "table": t, "can_all": can(request.user, "cashier.view_all"), "can_till": can(request.user, "cashier.till")})


@require_POST
@perm_required("cashier.till")
def open_till(request):
    form = OpenForm(request.POST)
    if form.is_valid():
        try:
            t = services.open_till(request.user, form.cleaned_data["opening_float"])
            messages.success(request, f"Till opened with float UGX {t.opening_float:,.0f}.")
        except ValidationError as e:
            for m in e.messages:
                messages.error(request, m)
    else:
        messages.error(request, "Enter a valid opening float.")
    return redirect("cashier:home")


@perm_required("cashier.till")
def close_till(request):
    till = services.get_open_till(request.user)
    if not till:
        messages.info(request, "You have no open till.")
        return redirect("cashier:home")
    if request.method == "POST":
        counted = {d: request.POST.get(f"d{d}", 0) or 0 for d in DENOMINATIONS}
        try:
            counted = {d: int(v) for d, v in counted.items()}
            done = services.close_till(till, counted, request.POST.get("reason", ""), request.user)
        except (ValueError, ValidationError) as e:
            for m in (e.messages if hasattr(e, "messages") else ["Counts must be whole numbers."]):
                messages.error(request, m)
        else:
            v = done.variance
            if v == 0:
                messages.success(request, "Till closed. Cash is balanced.")
            else:
                messages.warning(request, f"Till closed with a {'shortage' if v < 0 else 'overage'} of UGX {abs(v):,.0f}, recorded with your explanation.")
            return redirect("cashier:till", pk=done.pk)
    return render(request, "cashier/close.html", {"till": till, "denoms": DENOMINATIONS, "post": request.POST})


@perm_required("cashier.till", "cashier.view_all")
def till_detail(request, pk):
    till = get_object_or_404(Till.objects.select_related("user"), pk=pk)
    if till.user_id != request.user.id and not can(request.user, "cashier.view_all"):
        raise PermissionDenied
    entries = till.entries.select_related("created_by")
    denoms = [(int(k), v, int(k) * v) for k, v in sorted(till.counted.items(), key=lambda x: -int(x[0])) if v]
    return render(request, "cashier/till.html", {"till": till, "entries": entries, "denoms": denoms, "cash_in": till.cash_in, "cash_out": till.cash_out,
                                                  "expected": till.expected_cash if till.status == "closed" else till.expected})


@perm_required("cashier.view_all")
def till_list(request):
    qs = Till.objects.select_related("user")
    page, qstr = paginate(request, qs)
    t = Table(["Till", "Cashier", "Date", "Float", "Expected", "Actual", "Difference", "Status", ""])
    for x in page:
        t.add([f"#{x.pk}", x.user.get_full_name() or x.user.username, f"{x.business_date:%d %b %Y}", M(x.opening_float), M(x.expected_cash if x.status == "closed" else x.expected),
               M(x.actual_cash) if x.actual_cash is not None else "-", C(f"{x.variance:,.0f}", badge="bad" if x.variance and x.variance < 0 else "warn" if x.variance else "ok", num=True) if x.variance is not None else "-",
               C(x.get_status_display(), badge=True), C("View", f"/cashier/till/{x.pk}/")])
    return render(request, "generic_list.html", {"title": "All tills", "subtitle": "Every cashier has an individual till record each day.", "table": t, "page": page, "qs": qstr})


@perm_required("cashier.till")
def transfer(request):
    till = services.get_open_till(request.user)
    if not till:
        messages.error(request, "Open your till first.")
        return redirect("cashier:home")
    form = TransferForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            ref = services.transfer_cash(till, form.cleaned_data["to_till"].pk, form.cleaned_data["amount"], request.user)
        except ValidationError as e:
            form.add_error(None, e)
        else:
            messages.success(request, f"Cash transferred ({ref}).")
            return redirect("cashier:home")
    return render(request, "generic_form.html", {"title": "Till-to-till transfer", "form": form, "submit": "Transfer cash",  "confirm": "Transfer UGX {amount} to the other till?", "confirm_title": "Confirm cash transfer", "cancel": "/cashier/",
                  "intro": f"Your till currently holds UGX {till.expected:,.0f}. Both tills are updated and the transfer is audited."})


@perm_required("cashier.view_all", "cashier.till")
def daily_report(request):
    day = request.GET.get("date") or str(today())
    qs = Till.objects.filter(business_date=day).select_related("user")
    if not can(request.user, "cashier.view_all"):
        qs = qs.filter(user=request.user)
    rows, tot = [], {"float": 0, "in": 0, "out": 0, "exp": 0, "act": 0, "var": 0}
    for x in qs:
        exp = x.expected_cash if x.status == "closed" else x.expected
        rows.append((x, x.cash_in, x.cash_out, exp))
        tot["float"] += x.opening_float; tot["in"] += x.cash_in; tot["out"] += x.cash_out; tot["exp"] += exp
        tot["act"] += x.actual_cash or 0; tot["var"] += x.variance or 0
    return render(request, "cashier/report.html", {"rows": rows, "tot": tot, "day": day})
