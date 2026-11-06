"""Dashboard figures - every number is computed live from the database."""
from datetime import timedelta

from django.db.models import Count, Sum
from django.db.models.functions import TruncMonth

from accounting import services as gl
from core import charts
from core.utils import ZERO, D, add_months, last_n_months, month_start, prev_month_end, today


def _monthly(qs, date_field, value_field=None):
    agg = Sum(value_field) if value_field else Count("id")
    out = {}
    for row in qs.annotate(m=TruncMonth(date_field)).values("m").annotate(v=agg):
        out[row["m"]] = D(row["v"])
    return out


def kpi(label, cur, prev=None, fmt="ugx", good_up=True, icon="", sub=""):
    trend, pct = None, None
    if prev is not None:
        cur_f, prev_f = float(cur), float(prev)
        if prev_f != 0:
            pct = (cur_f - prev_f) / abs(prev_f) * 100
        trend = "up" if cur_f > prev_f else "down" if cur_f < prev_f else "flat"
    tone = "mute" if trend in (None, "flat") else ("ok" if (trend == "up") == good_up else "bad")
    return {"label": label, "value": cur, "fmt": fmt, "trend": trend, "pct": pct, "tone": tone, "icon": icon, "sub": sub}


def build(user=None):
    from approvals.models import ApprovalRequest
    from loans.models import Loan, LoanInstallment, LoanRepayment
    from loans.services import portfolio_snapshot
    from members.models import Member
    from savings.models import SavingsTransaction
    t = today()
    pm = prev_month_end(t)
    snap = portfolio_snapshot(t)

    total_m = Member.objects.count()
    active_m = Member.objects.filter(status="active").count()
    total_m_prev = Member.objects.filter(joined_on__lte=pm).count()
    disbursed = Loan.objects.filter(status__in=("active", "cleared")).aggregate(s=Sum("principal"))["s"] or ZERO
    disbursed_prev = Loan.objects.filter(status__in=("active", "cleared"), disbursed_on__lte=pm).aggregate(s=Sum("principal"))["s"] or ZERO
    repaid = LoanRepayment.objects.filter(is_reversed=False).aggregate(s=Sum("amount"))["s"] or ZERO
    repaid_prev = LoanRepayment.objects.filter(is_reversed=False, date__lte=pm).aggregate(s=Sum("amount"))["s"] or ZERO
    bank_now = gl.balance_of(gl.BANK) + gl.balance_of(gl.MOMO)
    bank_prev = gl.balance_of(gl.BANK, pm) + gl.balance_of(gl.MOMO, pm)
    inc, inc_p = gl.type_total("income"), gl.type_total("income", pm)
    exp, exp_p = gl.type_total("expense"), gl.type_total("expense", pm)
    borrowers = Loan.objects.filter(status="active").values("member").distinct().count()
    pending_apps = Loan.objects.filter(status__in=Loan.PENDING).count()
    pending_req = ApprovalRequest.objects.filter(status="pending")
    pend_tx = pending_req.filter(kind__in=("withdrawal", "expense", "manual_journal", "share_transfer")).count()
    pend_all = pending_req.count() + Loan.objects.filter(status__in=("pending_committee", "pending_manager", "approved")).count()

    cards = [
        kpi("Registered members", total_m, total_m_prev, "int", icon="groups"),
        kpi("Active members", active_m, None, "int", icon="how_to_reg"),
        kpi("Inactive members", total_m - active_m, None, "int", good_up=False, icon="person_off"),
        kpi("Member savings", gl.balance_of(gl.SAVINGS), gl.balance_of(gl.SAVINGS, pm), icon="savings"),
        kpi("Share capital", gl.balance_of(gl.SHARES), gl.balance_of(gl.SHARES, pm), icon="pie_chart"),
        kpi("Loans disbursed", disbursed, disbursed_prev, icon="request_quote"),
        kpi("Outstanding portfolio", snap["outstanding"], gl.balance_of(gl.LOANS, pm), icon="account_balance_wallet"),
        kpi("Loan repayments collected", repaid, repaid_prev, icon="payments"),
        kpi("Interest income", gl.balance_of(gl.INT_INCOME), gl.balance_of(gl.INT_INCOME, pm), icon="trending_up"),
        kpi("Cash balance", gl.balance_of(gl.CASH), gl.balance_of(gl.CASH, pm), icon="local_atm"),
        kpi("Bank & mobile money", bank_now, bank_prev, icon="account_balance"),
        kpi("Total income", inc, inc_p, icon="add_chart"),
        kpi("Total expenditure", exp, exp_p, good_up=False, icon="receipt_long"),
        kpi("Loans in arrears", snap["arrears"], None, good_up=False, icon="warning", sub=f"{snap['loans_in_arrears']} loans"),
        kpi("Portfolio at risk (PAR)", snap["par"], None, "pct", good_up=False, icon="shield"),
        kpi("Pending loan applications", pending_apps, None, "int", icon="pending_actions"),
        kpi("Active borrowers", borrowers, None, "int", icon="person"),
        kpi("Pending transactions", pend_tx, None, "int", icon="hourglass_top", sub="awaiting second approver"),
        kpi("Pending approvals", pend_all, None, "int", icon="task_alt", sub="requests & loan stages"),
    ]

    months = last_n_months(6, t)
    labels = [m.strftime("%b") for m in months]
    first = months[0]

    def series(d):
        return [float(d.get(m, 0)) for m in months]

    sav = _monthly(SavingsTransaction.objects.filter(kind="deposit", date__gte=first), "date", "amount")
    dis = _monthly(Loan.objects.filter(status__in=("active", "cleared"), disbursed_on__gte=first), "disbursed_on", "principal")
    rep = _monthly(LoanRepayment.objects.filter(is_reversed=False, date__gte=first), "date", "amount")
    due = _monthly(LoanInstallment.objects.filter(due_date__gte=first, due_date__lte=add_months(month_start(t), 1) - timedelta(days=1))
                   .exclude(status="superseded"), "due_date", "principal")
    due_i = _monthly(LoanInstallment.objects.filter(due_date__gte=first, due_date__lte=add_months(month_start(t), 1) - timedelta(days=1))
                     .exclude(status="superseded"), "due_date", "interest")
    from accounting.models import JournalLine
    ie = {"income": {}, "expense": {}}
    for typ in ie:
        for r in (JournalLine.objects.filter(account__type=typ, journal__date__gte=first).annotate(m=TruncMonth("journal__date")).values("m")
                  .annotate(d=Sum("debit"), c=Sum("credit"))):
            ie[typ][r["m"]] = D(r["c"]) - D(r["d"]) if typ == "income" else D(r["d"]) - D(r["c"])
    reg = _monthly(Member.objects.filter(joined_on__gte=first), "joined_on")
    cumulative, run = [], 0
    for v in series(sav):
        run += v
        cumulative.append(run)
    sav_series = series(sav)

    bk = snap["buckets"]
    cs = {
        "savings": charts.line_chart(labels, sav_series, "Monthly savings deposits"),
        "disb": charts.bar_chart(labels, [("Disbursed", series(dis), "c-bar1")], "Loan disbursements"),
        "repay": charts.bar_chart(labels, [("Due", [float(due.get(m, 0)) + float(due_i.get(m, 0)) for m in months], "c-bar3"),
                                           ("Collected", series(rep), "c-bar1")], "Repayment performance"),
        "ie": charts.bar_chart(labels, [("Income", series(ie["income"]), "c-bar1"), ("Expenditure", series(ie["expense"]), "c-bar2")], "Income vs expenditure"),
        "reg": charts.bar_chart(labels, [("New members", series(reg), "c-bar2")], "Member registrations"),
        "arrears": charts.hbar_chart([(f"{k} days", v[1], "c-bar2" if i < 2 else "c-bar4") for i, (k, v) in enumerate(bk.items())], "Loans by days overdue"),
    }
    return {"cards": cards, "charts": cs, "snap": snap}
