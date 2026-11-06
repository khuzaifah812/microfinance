"""Double-entry engine. Every financial event in the system funnels through post_journal()."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum

from accounts_core.audit import audit
from core.models import next_ref
from core.utils import ZERO, D, today

from .models import Account, Journal, JournalLine

CASH, BANK, MOMO = "1000", "1010", "1020"
LOANS, PROVISION, RECEIVABLE = "1100", "1150", "1200"
SAVINGS, PAYABLE, SHARES, RESERVES = "2000", "2100", "3000", "3100"
INT_INCOME, FEE_INCOME, PEN_INCOME, OTHER_INCOME, OVERAGE = "4000", "4100", "4200", "4300", "4400"
GEN_EXP, SAV_INT_EXP, PROV_EXP, SHORTAGE = "5000", "5300", "5400", "5500"

CHANNEL_GL = {"cash": CASH, "bank": BANK, "momo": MOMO}

DEFAULT_COA = [
    ("1000", "Cash in hand (tills & vault)", "asset", True), ("1010", "Bank - main account", "asset", True),
    ("1020", "Mobile money float", "asset", True), ("1100", "Loan portfolio", "asset", True),
    ("1150", "Loan-loss provision (contra)", "asset", True), ("1200", "Accounts receivable", "asset", True),
    ("1500", "Furniture & equipment", "asset", False),
    ("2000", "Members' savings deposits", "liability", True), ("2100", "Accounts payable", "liability", True),
    ("3000", "Share capital", "equity", True), ("3100", "Reserves & retained earnings", "equity", True),
    ("4000", "Loan interest income", "income", True), ("4100", "Processing fee income", "income", True),
    ("4200", "Penalty income", "income", True), ("4300", "Other income", "income", True),
    ("4400", "Cash overage", "income", True),
    ("5000", "General & administrative expenses", "expense", True), ("5100", "Salaries & wages", "expense", False),
    ("5200", "Rent & utilities", "expense", False), ("5300", "Interest on savings", "expense", True),
    ("5400", "Loan-loss provision expense", "expense", True), ("5500", "Cash shortage", "expense", True),
]


def ensure_coa():
    for code, name, typ, system in DEFAULT_COA:
        Account.objects.get_or_create(code=code, defaults={"name": name, "type": typ, "is_system": system})


def acct(code):
    try:
        return Account.objects.get(code=code, is_active=True)
    except Account.DoesNotExist:
        ensure_coa()
        return Account.objects.get(code=code, is_active=True)


@transaction.atomic
def post_journal(lines, description, date=None, source="system", source_ref="", user=None, reverses=None):
    """lines: [(account_code, debit, credit, memo)]. Raises ValidationError unless balanced."""
    clean, dr, cr = [], ZERO, ZERO
    for ln in lines:
        code, d, c = ln[0], D(ln[1]), D(ln[2])
        memo = ln[3] if len(ln) > 3 else ""
        if d < 0 or c < 0 or (d and c):
            raise ValidationError("Each journal line must carry either a debit or a credit amount.")
        if d == 0 and c == 0:
            continue
        clean.append((code, d, c, memo))
        dr += d
        cr += c
    if not clean or dr != cr or dr <= 0:
        raise ValidationError(f"Journal is not balanced (debits {dr}, credits {cr}).")
    if len(clean) < 2:
        raise ValidationError("A journal needs at least two lines.")
    if user is None:
        from core.middleware import get_current_request
        req = get_current_request()
        user = req.user if req else None
    j = Journal(ref=next_ref("JV"), date=date or today(), description=description[:250], source=source,
                source_ref=source_ref, reverses=reverses, posted_by=user)
    j.save()
    for code, d, c, memo in clean:
        JournalLine(journal=j, account=acct(code), debit=d, credit=c, memo=memo[:200]).save()
    audit("journal.post", "finance", "Journal", j.pk, j.ref, new={"lines": [(c, str(d), str(cr_)) for c, d, cr_, _ in clean],
                                                                  "source": source}, detail=description[:200], user=user)
    return j


def reverse_journal_gl(journal, reason, user=None, date=None):
    """Pure GL reversal. Domain-level side effects are handled by the caller."""
    if journal.reverses_id:
        raise ValidationError("A reversal entry cannot itself be reversed.")
    if journal.reversals.exists():
        raise ValidationError("This journal has already been reversed.")
    lines = [(l.account.code, l.credit, l.debit, "Reversal") for l in journal.lines.select_related("account")]
    return post_journal(lines, f"REVERSAL of {journal.ref}: {reason}", date or today(), "reversal", journal.ref,
                        user=user, reverses=journal)


def balance_of(code, as_of=None, start=None):
    """Natural-sign balance of an account (debit-normal for assets/expenses, credit-normal otherwise)."""
    try:
        a = Account.objects.get(code=code)
    except Account.DoesNotExist:
        return ZERO
    f = Q(account=a)
    if as_of:
        f &= Q(journal__date__lte=as_of)
    if start:
        f &= Q(journal__date__gte=start)
    agg = JournalLine.objects.filter(f).aggregate(d=Sum("debit"), c=Sum("credit"))
    d, c = D(agg["d"]), D(agg["c"])
    return d - c if a.debit_normal else c - d


def type_total(typ, as_of=None, start=None):
    f = Q(account__type=typ)
    if as_of:
        f &= Q(journal__date__lte=as_of)
    if start:
        f &= Q(journal__date__gte=start)
    agg = JournalLine.objects.filter(f).aggregate(d=Sum("debit"), c=Sum("credit"))
    d, c = D(agg["d"]), D(agg["c"])
    return d - c if typ in ("asset", "expense") else c - d


def trial_balance(as_of=None):
    f = Q()
    if as_of:
        f = Q(journal__date__lte=as_of)
    rows = (JournalLine.objects.filter(f).values("account__code", "account__name", "account__type")
            .annotate(d=Sum("debit"), c=Sum("credit")).order_by("account__code"))
    out, td, tc = [], ZERO, ZERO
    for r in rows:
        net = D(r["d"]) - D(r["c"])
        dr, cr = (net, ZERO) if net > 0 else (ZERO, -net)
        out.append({"code": r["account__code"], "name": r["account__name"], "type": r["account__type"], "debit": dr, "credit": cr})
        td += dr
        tc += cr
    return out, td, tc


def period_amounts(start, end):
    """Income & expense by account for the income statement."""
    rows = (JournalLine.objects.filter(journal__date__gte=start, journal__date__lte=end, account__type__in=("income", "expense"))
            .values("account__code", "account__name", "account__type").annotate(d=Sum("debit"), c=Sum("credit")).order_by("account__code"))
    inc, exp = [], []
    for r in rows:
        if r["account__type"] == "income":
            inc.append((r["account__code"], r["account__name"], D(r["c"]) - D(r["d"])))
        else:
            exp.append((r["account__code"], r["account__name"], D(r["d"]) - D(r["c"])))
    return inc, exp


def unbalanced_journals():
    """Integrity check: journals whose debits != credits (should always be empty)."""
    rows = JournalLine.objects.values("journal_id").annotate(d=Sum("debit"), c=Sum("credit"))
    return [r["journal_id"] for r in rows if D(r["d"]) != D(r["c"])]
