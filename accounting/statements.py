"""Financial statements computed straight from the journal (never from sub-ledgers)."""
from collections import defaultdict

from django.db.models import Sum

from core.utils import ZERO, D

from . import services as gl
from .models import Account, JournalLine

CASH_CODES = (gl.CASH, gl.BANK, gl.MOMO)


def income_statement(start, end):
    inc, exp = gl.period_amounts(start, end)
    ti, te = sum((x[2] for x in inc), ZERO), sum((x[2] for x in exp), ZERO)
    return {"income": inc, "expenses": exp, "total_income": ti, "total_expenses": te, "net": ti - te}


def balance_sheet(as_of):
    sections = {}
    for typ in ("asset", "liability", "equity"):
        rows = []
        for a in Account.objects.filter(type=typ).order_by("code"):
            b = gl.balance_of(a.code, as_of)
            if b != 0:
                rows.append((a.code, a.name, b))
        sections[typ] = rows
    earnings = gl.type_total("income", as_of) - gl.type_total("expense", as_of)
    ta = sum((r[2] for r in sections["asset"]), ZERO)
    tl = sum((r[2] for r in sections["liability"]), ZERO)
    te = sum((r[2] for r in sections["equity"]), ZERO) + earnings
    return {"assets": sections["asset"], "liabilities": sections["liability"], "equity": sections["equity"], "earnings": earnings,
            "total_assets": ta, "total_liabilities": tl, "total_equity": te, "balanced": ta == tl + te}


def _category(code):
    if code[0] in "45" or code in (gl.PAYABLE, gl.RECEIVABLE):
        return "Operating activities"
    if code in (gl.LOANS, gl.PROVISION, "1500"):
        return "Investing activities (loans & assets)"
    return "Financing activities (savings & shares)"


def cash_flow(start, end):
    """Direct method. For any journal, cash change = sum over non-cash lines of (credit - debit)."""
    opening = sum((gl.balance_of(c, start - __import__("datetime").timedelta(days=1)) for c in CASH_CODES), ZERO)
    closing = sum((gl.balance_of(c, end) for c in CASH_CODES), ZERO)
    journals = (JournalLine.objects.filter(journal__date__gte=start, journal__date__lte=end, account__code__in=CASH_CODES)
                .values_list("journal_id", flat=True).distinct())
    agg = defaultdict(lambda: ZERO)
    names = {}
    for l in JournalLine.objects.filter(journal_id__in=list(journals)).exclude(account__code__in=CASH_CODES).select_related("account"):
        agg[l.account.code] += l.credit - l.debit
        names[l.account.code] = l.account.name
    sections = defaultdict(list)
    for code in sorted(agg):
        if agg[code] != 0:
            sections[_category(code)].append((code, names[code], agg[code]))
    totals = {k: sum((x[2] for x in v), ZERO) for k, v in sections.items()}
    net = sum(totals.values(), ZERO)
    return {"sections": dict(sections), "totals": totals, "net": net, "opening": opening, "closing": closing, "check": opening + net == closing}
