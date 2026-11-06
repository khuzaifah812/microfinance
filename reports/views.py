from datetime import date

from django import forms
from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render

from accounts_core.audit import audit
from accounts_core.rbac import can, perm_required
from core.forms import StyledMixin
from core.models import SiteConfig
from core.utils import month_start, today

from . import render as rr
from .models import UmraLine
from .registry import REPORTS, ensure_umra_lines


def _parse(v, default):
    try:
        return date.fromisoformat(v)
    except (TypeError, ValueError):
        return default


@perm_required("reports.view")
def index(request):
    cats = {}
    for r in REPORTS.values():
        if can(request.user, r["perm"]):
            cats.setdefault(r["category"], []).append(r)
    order = ["Regulatory", "Finance", "Lending", "Members & savings", "Control"]
    return render(request, "reports/index.html", {"cats": [(c, cats[c]) for c in order if c in cats]})


@perm_required("reports.view")
def show(request, key):
    r = REPORTS.get(key)
    if not r or not can(request.user, r["perm"]):
        raise Http404
    p = {"from": _parse(request.GET.get("from"), month_start(today())), "to": _parse(request.GET.get("to"), today()), "as_of": _parse(request.GET.get("as_of"), today())}
    data = r["fn"](p)
    fmt = request.GET.get("export")
    cfg = SiteConfig.get()
    if "from" in r["params"]:
        sub = f"Period: {p['from']:%d %b %Y} to {p['to']:%d %b %Y}"
    elif "as_of" in r["params"]:
        sub = f"As at {p['as_of']:%d %b %Y}"
    else:
        sub = f"As at {today():%d %b %Y}"
    if fmt in ("csv", "xlsx", "pdf"):
        if not can(request.user, "reports.export"):
            messages.error(request, "You do not have permission to export reports.")
            return redirect(request.path)
        audit("report.export", "report", "Report", key, key, detail=f"{r['title']} exported as {fmt.upper()} ({sub})")
        fname = f"{key}-{today():%Y%m%d}"
        if fmt == "csv":
            return rr.to_csv(r["title"], data, fname)
        if fmt == "xlsx":
            return rr.to_xlsx(r["title"], data, fname, cfg.name)
        return rr.to_pdf(r["title"], data, fname, cfg.name, sub)
    rows, tot = rr.table_rows(data)
    audit("report.view", "report", "Report", key, key, detail=r["title"])
    cols = [{"name": c[0], "num": c[1] in ("money", "int")} for c in data["columns"]]
    return render(request, "reports/view.html", {"r": r, "cols": cols, "rows": rows, "tot": tot, "notes": data.get("notes"), "p": p, "sub": sub,
                  "has_from": "from" in r["params"], "has_asof": "as_of" in r["params"], "can_export": can(request.user, "reports.export"),
                  "numcols": [c["num"] for c in cols]})


class LineForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = UmraLine
        fields = ["code", "label", "metric", "order", "enabled"]


@perm_required("reports.umra_config")
def umra_config(request):
    ensure_umra_lines()
    edit_id = request.GET.get("edit")
    inst = UmraLine.objects.filter(pk=edit_id).first() if edit_id else None
    form = LineForm(request.POST or None, instance=inst)
    if request.method == "POST":
        if request.POST.get("delete"):
            UmraLine.objects.filter(pk=request.POST["delete"]).delete()
            audit("report.umra_config", "maintenance", detail="Line removed")
            return redirect("reports:umra_config")
        if form.is_valid():
            o = form.save()
            audit("report.umra_config", "maintenance", "UmraLine", o.pk, o.code, new={"label": o.label, "metric": o.metric, "enabled": o.enabled})
            messages.success(request, "Summary line saved.")
            return redirect("reports:umra_config")
    return render(request, "reports/umra_config.html", {"lines": UmraLine.objects.all(), "form": form, "editing": inst})
