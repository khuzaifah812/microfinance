"""Tiny UI helpers: table builder, pagination, status badges."""
from django.core.paginator import Paginator

from .utils import fmt_money

PER_PAGE = 20

_BAD = ("reject", "overdue", "arrear", "fail", "locked", "suspend", "closed", "shortage", "frozen", "reversed", "error", "cancel", "inactive", "dormant")
_WARN = ("pending", "partial", "queued", "draft", "review", "testing", "open", "unmatched", "awaiting", "approved")
_OK = ("active", "paid", "cleared", "posted", "confirmed", "ok", "success", "balanced", "enabled", "settled", "disbursed", "verified")


def badge_class(status):
    s = str(status or "").lower()
    for w in _BAD:
        if w in s:
            return "bad"
    for w in _OK:
        if w in s:
            return "ok"
    for w in _WARN:
        if w in s:
            return "warn"
    return "mute"


class Cell:
    def __init__(self, text="", href=None, badge=False, num=False, cls="", sub=""):
        self.text = text
        self.href = href
        self.badge = badge_class(text) if badge is True else (badge or "")
        self.num = num
        self.cls = cls
        self.sub = sub


def C(text="", href=None, badge=False, num=False, **kw):
    return Cell(text, href, badge, num, **kw)


def M(x, href=None):
    return Cell(fmt_money(x), href, num=True)


class Table:
    def __init__(self, columns, rows=None, empty="Nothing recorded yet."):
        self.columns = columns
        self.rows = []
        self.empty = empty
        for r in rows or []:
            self.add(r)

    def add(self, row):
        self.rows.append([c if isinstance(c, Cell) else Cell("" if c is None else c) for c in row])


def paginate(request, qs, per=PER_PAGE):
    p = Paginator(qs, per)
    page = p.get_page(request.GET.get("page"))
    qd = request.GET.copy()
    qd.pop("page", None)
    return page, qd.urlencode()
