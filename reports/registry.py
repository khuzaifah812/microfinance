"""Report definitions. Every figure is computed from the live database."""
from datetime import date, timedelta

from django.db.models import Count, Sum

from accounting import services as gl, statements as st
from accounting.models import BankReconciliation, Journal, JournalLine
from accounts_core.models import AuditLog
from core.utils import ZERO, D, month_start, today

REPORTS = {}


def report(key, title, category, perm="reports.view", params=("from", "to")):
    def deco(fn):
        REPORTS[key] = {"key": key, "title": title, "category": category, "perm": perm, "params": params, "fn": fn}
        return fn
    return deco


def _pii_phone(p):
    from core.templatetags.sacco import mask_phone
    return mask_phone(p)


def _total(rows, idx):
    return sum((r[i] for r in rows for i in [idx] if isinstance(r[i], (int, float, type(ZERO)))), ZERO)


@report("member_register", "Member register", "Members & savings", params=())
def member_register(p):
    from members.models import Member
    rows = [[m.member_no, m.full_name, m.get_gender_display(), _pii_phone(m.phone), m.joined_on, m.get_status_display()] for m in Member.objects.all()]
    return {"columns": [("Member no.", "text"), ("Name", "text"), ("Gender", "text"), ("Phone", "text"), ("Joined", "date"), ("Status", "text")], "rows": rows, "totals": None}


def _accounts_report(kind):
    from savings.models import SavingsAccount
    rows = [[a.account_no, a.member.member_no, a.member.full_name, a.get_status_display(), a.balance, a.pledged, a.balance - a.pledged]
            for a in SavingsAccount.objects.filter(kind=kind).select_related("member")]
    cols = [("Account", "text"), ("Member no.", "text"), ("Member", "text"), ("Status", "text"), ("Balance", "money"), ("Pledged", "money"), ("Available", "money")]
    return {"columns": cols, "rows": rows, "totals": ["Total", "", "", "", _total(rows, 4), _total(rows, 5), _total(rows, 6)]}


@report("savings", "Savings report", "Members & savings", params=())
def savings_report(p):
    return _accounts_report("savings")


@report("shares", "Share capital report", "Members & savings", params=())
def shares_report(p):
    return _accounts_report("shares")


@report("loan_disbursement", "Loan disbursement report", "Lending")
def loan_disb(p):
    from loans.models import Loan
    qs = Loan.objects.filter(disbursed_on__gte=p["from"], disbursed_on__lte=p["to"]).select_related("member", "product").order_by("disbursed_on")
    rows = [[l.loan_no, l.member.full_name, l.product.name, l.disbursed_on, l.principal, l.fee_charged, l.interest_rate, l.get_interest_method_display(), l.term, l.total_payable] for l in qs]
    cols = [("Loan", "text"), ("Member", "text"), ("Product", "text"), ("Date", "date"), ("Principal", "money"), ("Fee", "money"), ("Rate %", "text"), ("Method", "text"), ("Periods", "int"), ("Total payable", "money")]
    return {"columns": cols, "rows": rows, "totals": ["Total", "", "", "", _total(rows, 4), _total(rows, 5), "", "", "", _total(rows, 9)]}


@report("loan_repayment", "Loan repayment report", "Lending")
def loan_rep(p):
    from loans.models import LoanRepayment
    qs = LoanRepayment.objects.filter(date__gte=p["from"], date__lte=p["to"], is_reversed=False).select_related("loan__member").order_by("date")
    rows = [[r.ref, r.loan.loan_no, r.loan.member.full_name, r.date, r.channel, r.amount, r.principal, r.interest, r.penalty] for r in qs]
    cols = [("Receipt", "text"), ("Loan", "text"), ("Member", "text"), ("Date", "date"), ("Channel", "text"), ("Amount", "money"), ("Principal", "money"), ("Interest", "money"), ("Penalty", "money")]
    return {"columns": cols, "rows": rows, "totals": ["Total", "", "", "", "", _total(rows, 5), _total(rows, 6), _total(rows, 7), _total(rows, 8)]}


@report("loan_arrears", "Loan arrears report", "Lending", params=("as_of",))
def loan_arrears(p):
    from loans.models import Loan
    from loans.services import overdue_info, portfolio_snapshot
    snap = portfolio_snapshot(p["as_of"])
    rows = []
    for l in Loan.objects.filter(pk__in=list(snap["first_overdue"])).select_related("member").prefetch_related("installments"):
        amt, days, n = overdue_info(l, p["as_of"])
        rows.append([l.loan_no, l.member.full_name, _pii_phone(l.member.phone), n, amt, days, l.outstanding_principal])
    rows.sort(key=lambda r: -r[5])
    cols = [("Loan", "text"), ("Member", "text"), ("Phone", "text"), ("Overdue instalments", "int"), ("Arrears", "money"), ("Days overdue", "int"), ("Outstanding principal", "money")]
    return {"columns": cols, "rows": rows, "totals": ["Total", "", "", "", _total(rows, 4), "", _total(rows, 6)]}


@report("par", "Portfolio at risk (PAR)", "Lending", params=("as_of",))
def par(p):
    s = __import__("loans.services", fromlist=["x"]).portfolio_snapshot(p["as_of"])
    rows = [["Outstanding portfolio", s["outstanding"], ""], ["Portfolio at risk (principal of loans with any overdue instalment)", s["par_amount"], f"{s['par']:.2f}%"],
            ["Arrears (overdue principal + interest)", s["arrears"], ""], ["Loans in arrears", s["loans_in_arrears"], ""]]
    for k, (n, amt) in s["buckets"].items():
        rows.append([f"PAR {k} days - {n} loans", amt, f"{(amt / s['outstanding'] * 100) if s['outstanding'] else 0:.2f}%"])
    return {"columns": [("Measure", "text"), ("Amount", "money"), ("% of portfolio", "text")], "rows": rows, "totals": None}


@report("aging", "Loans by days overdue", "Lending", params=("as_of",))
def aging(p):
    s = __import__("loans.services", fromlist=["x"]).portfolio_snapshot(p["as_of"])
    rows = [[k, n, amt] for k, (n, amt) in s["buckets"].items()]
    return {"columns": [("Days overdue", "text"), ("Loans", "int"), ("Outstanding principal", "money")], "rows": rows, "totals": ["Total", _total(rows, 1), _total(rows, 2)]}


@report("provisioning", "Loan-loss provisioning", "Lending", params=("as_of",))
def provisioning(p):
    from loans.services import provision_required
    items, total = provision_required(p["as_of"])
    rows = [[a, b, c, d] for a, b, c, d in items]
    rows.append(["Provision held in ledger", "", "", -gl.balance_of(gl.PROVISION, p["as_of"])])
    return {"columns": [("Category", "text"), ("Exposure", "money"), ("Rate %", "text"), ("Provision required", "money")], "rows": rows,
            "totals": ["Required in total", "", "", total]}


@report("cashier", "Cashier report", "Control")
def cashier(p):
    from cashier.models import Till
    rows = []
    for t in Till.objects.filter(business_date__gte=p["from"], business_date__lte=p["to"]).select_related("user"):
        exp = t.expected_cash if t.status == "closed" else t.expected
        rows.append([t.pk, t.user.get_full_name() or t.user.username, t.business_date, t.opening_float, t.cash_in, t.cash_out, exp,
                     t.actual_cash if t.actual_cash is not None else ZERO, t.variance if t.variance is not None else ZERO, t.variance_reason, t.get_status_display()])
    cols = [("Till", "int"), ("Cashier", "text"), ("Date", "date"), ("Float", "money"), ("Cash in", "money"), ("Cash out", "money"), ("Expected", "money"), ("Actual", "money"), ("Difference", "money"), ("Reason", "text"), ("Status", "text")]
    return {"columns": cols, "rows": rows, "totals": ["Total", "", "", _total(rows, 3), _total(rows, 4), _total(rows, 5), _total(rows, 6), _total(rows, 7), _total(rows, 8), "", ""]}


@report("income_expenditure", "Income and expenditure (monthly)", "Finance")
def inc_exp(p):
    rows, cur = [], month_start(p["from"])
    while cur <= p["to"]:
        nxt = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
        inc, exp = gl.type_total("income", nxt - timedelta(days=1), cur), gl.type_total("expense", nxt - timedelta(days=1), cur)
        rows.append([cur.strftime("%b %Y"), inc, exp, inc - exp])
        cur = nxt
    return {"columns": [("Month", "text"), ("Income", "money"), ("Expenditure", "money"), ("Surplus / (deficit)", "money")], "rows": rows,
            "totals": ["Total", _total(rows, 1), _total(rows, 2), _total(rows, 3)]}


@report("income_statement", "Income statement", "Finance")
def income_statement(p):
    s = st.income_statement(p["from"], p["to"])
    rows = [["INCOME", "", ""]] + [[c, n, a] for c, n, a in s["income"]] + [["", "Total income", s["total_income"]], ["EXPENDITURE", "", ""]] + \
           [[c, n, a] for c, n, a in s["expenses"]] + [["", "Total expenditure", s["total_expenses"]]]
    return {"columns": [("Code", "text"), ("Account", "text"), ("Amount", "money")], "rows": rows, "totals": ["", "Net surplus / (deficit)", s["net"]]}


@report("trial_balance", "Trial balance", "Finance", params=("as_of",))
def trial_balance(p):
    rows, td, tc = gl.trial_balance(p["as_of"])
    return {"columns": [("Code", "text"), ("Account", "text"), ("Debit", "money"), ("Credit", "money")], "rows": [[r["code"], r["name"], r["debit"], r["credit"]] for r in rows],
            "totals": ["", "Total" + ("" if td == tc else "  (OUT OF BALANCE)"), td, tc]}


@report("balance_sheet", "Balance sheet", "Finance", params=("as_of",))
def balance_sheet(p):
    b = st.balance_sheet(p["as_of"])
    rows = [["ASSETS", "", ""]] + [[c, n, a] for c, n, a in b["assets"]] + [["", "Total assets", b["total_assets"]], ["LIABILITIES", "", ""]] + \
           [[c, n, a] for c, n, a in b["liabilities"]] + [["", "Total liabilities", b["total_liabilities"]], ["EQUITY", "", ""]] + [[c, n, a] for c, n, a in b["equity"]] + \
           [["", "Current period surplus / (deficit)", b["earnings"]], ["", "Total equity", b["total_equity"]]]
    return {"columns": [("Code", "text"), ("Account", "text"), ("Amount", "money")], "rows": rows,
            "totals": ["", "Total liabilities + equity" + ("" if b["balanced"] else "  (OUT OF BALANCE)"), b["total_liabilities"] + b["total_equity"]]}


@report("cash_flow", "Cash flow statement", "Finance")
def cash_flow(p):
    c = st.cash_flow(p["from"], p["to"])
    rows = [["Opening cash, bank and mobile money", "", c["opening"]]]
    for sec in ("Operating activities", "Investing activities (loans & assets)", "Financing activities (savings & shares)"):
        items = c["sections"].get(sec, [])
        rows.append([sec.upper(), "", ""])
        rows += [[code, name, amt] for code, name, amt in items]
        rows.append(["", f"Net cash from {sec.split(' (')[0].lower()}", c["totals"].get(sec, ZERO)])
    rows.append(["", "Net increase / (decrease) in cash", c["net"]])
    return {"columns": [("Code", "text"), ("Item", "text"), ("Amount", "money")], "rows": rows, "totals": ["", "Closing cash, bank and mobile money" + ("" if c["check"] else "  (CHECK FAILED)"), c["closing"]]}


@report("bank_recon", "Bank reconciliation report", "Finance")
def bank_recon(p):
    qs = BankReconciliation.objects.filter(as_of__gte=p["from"], as_of__lte=p["to"]).select_related("bank_account")
    rows = [[r.as_of, r.bank_account.name, r.statement_balance, r.deposits_in_transit, r.outstanding_payments, r.gl_balance, r.difference, "Reconciled" if r.reconciled else "Unreconciled"] for r in qs]
    cols = [("Date", "date"), ("Bank account", "text"), ("Statement", "money"), ("In transit", "money"), ("Outstanding", "money"), ("Ledger", "money"), ("Difference", "money"), ("Result", "text")]
    return {"columns": cols, "rows": rows, "totals": None}


@report("general_ledger", "General ledger", "Finance")
def general_ledger(p):
    qs = JournalLine.objects.filter(journal__date__gte=p["from"], journal__date__lte=p["to"]).select_related("journal", "account").order_by("journal__date", "journal_id", "id")[:5000]
    rows = [[l.journal.date, l.journal.ref, l.account.code, l.account.name, l.journal.description[:60], l.debit, l.credit] for l in qs]
    cols = [("Date", "date"), ("Journal", "text"), ("Code", "text"), ("Account", "text"), ("Description", "text"), ("Debit", "money"), ("Credit", "money")]
    return {"columns": cols, "rows": rows, "totals": ["Total", "", "", "", "", _total(rows, 5), _total(rows, 6)], "notes": "Limited to the first 5,000 lines; narrow the date range for more."}


@report("cashbook", "Cashbook (cash, bank & mobile money)", "Finance")
def cashbook(p):
    codes = (gl.CASH, gl.BANK, gl.MOMO)
    opening = sum((gl.balance_of(c, p["from"] - timedelta(days=1)) for c in codes), ZERO)
    run = opening
    rows = [[p["from"], "", "Opening balance", ZERO, ZERO, opening]]
    for l in JournalLine.objects.filter(account__code__in=codes, journal__date__gte=p["from"], journal__date__lte=p["to"]).select_related("journal", "account").order_by("journal__date", "journal_id", "id")[:5000]:
        run += l.debit - l.credit
        rows.append([l.journal.date, l.journal.ref, f"{l.account.name}: {l.journal.description[:50]}", l.debit, l.credit, run])
    cols = [("Date", "date"), ("Journal", "text"), ("Description", "text"), ("Receipts", "money"), ("Payments", "money"), ("Balance", "money")]
    return {"columns": cols, "rows": rows, "totals": ["Closing", "", "", _total(rows, 3), _total(rows, 4), run]}


@report("staff_activity", "Staff activity report", "Control", perm="audit.view")
def staff_activity(p):
    qs = AuditLog.objects.filter(ts__gte=_start(p["from"]), ts__lt=_start(p["to"] + timedelta(days=1)))
    agg = qs.values("username", "role", "category").annotate(n=Count("id")).order_by("username", "category")
    rows = [[a["username"] or "system", a["role"], a["category"], a["n"]] for a in agg]
    return {"columns": [("User", "text"), ("Role", "text"), ("Category", "text"), ("Actions", "int")], "rows": rows, "totals": ["Total", "", "", _total(rows, 3)]}


def _start(d):
    from django.utils import timezone
    return timezone.make_aware(__import__("datetime").datetime.combine(d, __import__("datetime").time.min))


@report("audit_trail", "Audit trail report", "Control", perm="audit.view")
def audit_trail(p):
    from django.utils import timezone
    qs = AuditLog.objects.filter(ts__gte=_start(p["from"]), ts__lt=_start(p["to"] + timedelta(days=1))).order_by("id")[:3000]
    rows = [[timezone.localtime(a.ts).strftime("%Y-%m-%d %H:%M:%S"), a.username or "system", a.action, a.reference, (a.detail or "")[:80], a.ip or ""] for a in qs]
    return {"columns": [("When", "text"), ("User", "text"), ("Action", "text"), ("Reference", "text"), ("Detail", "text"), ("IP", "text")], "rows": rows, "totals": None,
            "notes": "Limited to 3,000 records. Chain integrity is verified from Audit trail > Verify integrity."}


def compute_metric(key, as_of):
    from loans.models import Loan
    from loans.services import portfolio_snapshot, provision_required
    from members.models import Member
    ys = date(as_of.year, 1, 1)
    snap = portfolio_snapshot(as_of)
    if key == "members_total": return Member.objects.count()
    if key == "members_active": return Member.objects.filter(status="active").count()
    if key == "savings_total": return gl.balance_of(gl.SAVINGS, as_of)
    if key == "shares_total": return gl.balance_of(gl.SHARES, as_of)
    if key == "loans_outstanding": return snap["outstanding"]
    if key == "loans_disbursed_ytd": return Loan.objects.filter(disbursed_on__gte=ys, disbursed_on__lte=as_of).aggregate(s=Sum("principal"))["s"] or ZERO
    if key == "loans_count_active": return Loan.objects.filter(status="active").count()
    if key == "arrears_amount": return snap["arrears"]
    if key == "par_percent": return round(snap["par"], 2)
    if key == "provision_held": return -gl.balance_of(gl.PROVISION, as_of)
    if key == "provision_required": return provision_required(as_of)[1]
    if key == "income_ytd": return gl.type_total("income", as_of, ys)
    if key == "expense_ytd": return gl.type_total("expense", as_of, ys)
    if key == "surplus_ytd": return gl.type_total("income", as_of, ys) - gl.type_total("expense", as_of, ys)
    if key == "liquid_assets": return sum((gl.balance_of(c, as_of) for c in (gl.CASH, gl.BANK, gl.MOMO)), ZERO)
    b = st.balance_sheet(as_of)
    return {"total_assets": b["total_assets"], "total_liabilities": b["total_liabilities"], "total_equity": b["total_equity"]}.get(key, ZERO)


@report("umra_summary", "UMRA management summary", "Regulatory", params=("as_of",), perm="reports.view")
def umra(p):
    from .models import METRICS, UmraLine
    ensure_umra_lines()
    pct = {"par_percent"}
    cnt = {"members_total", "members_active", "loans_count_active"}
    rows = []
    for ln in UmraLine.objects.filter(enabled=True):
        v = compute_metric(ln.metric, p["as_of"])
        rows.append([ln.code, ln.label, f"{v}%" if ln.metric in pct else (int(v) if ln.metric in cnt else v)])
    return {"columns": [("Ref", "text"), ("Item", "text"), ("Value", "money")], "rows": rows, "totals": None,
            "notes": "Internally generated management summary. It does NOT replace an official UMRA return: map these figures to the regulator's current template before filing."}


def ensure_umra_lines():
    from .models import UmraLine
    if UmraLine.objects.exists():
        return
    defaults = [("A1", "Total number of members", "members_total"), ("A2", "Active members", "members_active"), ("B1", "Member savings", "savings_total"),
                ("B2", "Share capital", "shares_total"), ("C1", "Gross loan portfolio", "loans_outstanding"), ("C2", "Number of active loans", "loans_count_active"),
                ("C3", "Loans disbursed (year to date)", "loans_disbursed_ytd"), ("D1", "Loans in arrears", "arrears_amount"), ("D2", "Portfolio at risk", "par_percent"),
                ("D3", "Loan-loss provision held", "provision_held"), ("D4", "Loan-loss provision required", "provision_required"),
                ("E1", "Total income (year to date)", "income_ytd"), ("E2", "Total expenditure (year to date)", "expense_ytd"), ("E3", "Surplus / (deficit)", "surplus_ytd"),
                ("F1", "Cash, bank and mobile money", "liquid_assets"), ("F2", "Total assets", "total_assets"), ("F3", "Total liabilities", "total_liabilities"), ("F4", "Total equity", "total_equity")]
    for i, (c, l, m) in enumerate(defaults):
        UmraLine.objects.create(code=c, label=l, metric=m, order=i * 10)
