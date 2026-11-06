from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from accounts_core.audit import audit
from accounting import services as gl
from approvals import services as appr
from cashier import services as cash
from core.models import Sequence, SystemSetting, next_ref
from core.utils import D, ZERO, q0, today

from .models import InterestRun, SavingsAccount, SavingsTransaction


def new_account_no(kind):
    prefix = "SV" if kind == "savings" else "SH"
    while True:
        no = f"{prefix}{Sequence.next('acct_' + kind):08d}"
        if not SavingsAccount.objects.filter(account_no=no).exists():
            return no


@transaction.atomic
def open_account(member, kind, name, user, account_no="", min_balance=None):
    if member.status != "active":
        raise ValidationError("Accounts can only be opened for active members.")
    account_no = (account_no or "").strip().upper()
    if account_no:
        if SavingsAccount.objects.filter(account_no__iexact=account_no).exists():
            raise ValidationError(f"Account number {account_no} already exists. Account numbers must be unique.")
    else:
        account_no = new_account_no(kind)
    if kind == "shares" and SavingsAccount.objects.filter(member=member, kind="shares").exclude(status="closed").exists():
        raise ValidationError("This member already has a share capital account.")
    a = SavingsAccount.objects.create(account_no=account_no, member=member, kind=kind,
                                      name=name or ("Share capital" if kind == "shares" else "Ordinary savings"),
                                      min_balance=min_balance, opened_by=user)
    audit("account.open", "savings", "SavingsAccount", a.pk, a.account_no, new={"member": member.member_no, "kind": kind}, user=user)
    return a


def min_balance_for(account):
    if account.min_balance is not None:
        return D(account.min_balance)
    return D(SystemSetting.get("min_savings_balance") or 0)


def _lock(account):
    return SavingsAccount.objects.select_for_update().get(pk=account.pk)


def _credit_gl(account):
    return gl.SHARES if account.kind == "shares" else gl.SAVINGS


def _check_amount(amount):
    amount = D(amount)
    if amount <= 0:
        raise ValidationError("Amount must be greater than zero.")
    if amount != amount.to_integral_value():
        raise ValidationError("Amounts must be whole Uganda shillings.")
    return amount


@transaction.atomic
def deposit(account, amount, user, channel="cash", narration="", date=None, kind=None, external_ref=""):
    amount = _check_amount(amount)
    acct = _lock(account)
    if acct.status in ("frozen", "closed"):
        raise ValidationError(f"Account is {acct.status}; deposits are not allowed.")
    kind = kind or ("share_purchase" if acct.kind == "shares" else "deposit")
    till = cash.require_till(user) if channel == "cash" else None
    ref = next_ref("TX")
    j = gl.post_journal([(gl.CHANNEL_GL[channel], amount, 0, acct.account_no), (_credit_gl(acct), 0, amount, acct.account_no)],
                        f"{kind.replace('_', ' ').title()} {acct.account_no} {narration}".strip(), date, "savings", ref, user=user)
    acct.balance += amount
    if acct.status == "dormant":
        acct.status = "active"
    acct.save(update_fields=["balance", "status"])
    tx = SavingsTransaction.objects.create(ref=ref, account=acct, kind=kind, amount=amount, balance_after=acct.balance,
                                           date=date or today(), channel=channel, narration=narration[:200] or external_ref,
                                           journal=j, till=till, created_by=user)
    if till:
        cash.record_cash(till, "in", kind, amount, ref, f"{kind} {acct.account_no}", j, user)
    audit("savings.deposit", "finance", "SavingsAccount", acct.pk, ref, new={"amount": amount, "balance": acct.balance, "journal": j.ref},
          detail=f"{kind} {acct.account_no}", user=user)
    from notifications.services import notify
    notify("deposit", f"Deposit UGX {amount:,.0f}", f"{acct.member.full_name} {acct.account_no}", roles=["manager"], link=f"/savings/{acct.pk}/")
    return tx


def validate_withdrawal(acct, amount):
    if acct.kind == "shares":
        raise ValidationError("Share capital cannot be withdrawn. Use the member exit process.")
    if acct.status != "active":
        raise ValidationError(f"Account is {acct.status}; withdrawals are not allowed.")
    floor = min_balance_for(acct) + D(acct.pledged)
    if D(amount) > D(acct.balance) - floor:
        avail = max(ZERO, D(acct.balance) - floor)
        raise ValidationError(f"Insufficient funds. Available to withdraw: UGX {avail:,.0f} "
                              f"(balance {acct.balance:,.0f}, pledged {acct.pledged:,.0f}, minimum {min_balance_for(acct):,.0f}).")


@transaction.atomic
def execute_withdrawal(account, amount, user, channel="cash", narration="", date=None):
    amount = _check_amount(amount)
    acct = _lock(account)
    validate_withdrawal(acct, amount)
    till = cash.require_till(user, amount) if channel == "cash" else None
    ref = next_ref("TX")
    j = gl.post_journal([(gl.SAVINGS, amount, 0, acct.account_no), (gl.CHANNEL_GL[channel], 0, amount, acct.account_no)],
                        f"Withdrawal {acct.account_no} {narration}".strip(), date, "savings", ref, user=user)
    acct.balance -= amount
    acct.save(update_fields=["balance"])
    tx = SavingsTransaction.objects.create(ref=ref, account=acct, kind="withdrawal", amount=amount, balance_after=acct.balance,
                                           date=date or today(), channel=channel, narration=narration[:200], journal=j, till=till, created_by=user)
    if till:
        cash.record_cash(till, "out", "withdrawal", amount, ref, f"withdrawal {acct.account_no}", j, user)
    audit("savings.withdraw", "finance", "SavingsAccount", acct.pk, ref, new={"amount": amount, "balance": acct.balance, "journal": j.ref},
          detail=f"Withdrawal {acct.account_no}", user=user)
    from notifications.services import notify
    notify("withdrawal", f"Withdrawal UGX {amount:,.0f}", f"{acct.member.full_name} {acct.account_no}", roles=["manager"], link=f"/savings/{acct.pk}/")
    return tx


def request_withdrawal(account, amount, user, channel="cash", narration="", date=None):
    """Returns (tx, None) when posted, or (None, ApprovalRequest) when the amount needs a second approver."""
    amount = _check_amount(amount)
    acct = SavingsAccount.objects.get(pk=account.pk)
    validate_withdrawal(acct, amount)   # fail fast before queueing
    if channel == "cash":
        cash.require_till(user, amount)
    if appr.needs_approval("withdrawal", amount):
        req = appr.submit("withdrawal", amount, f"Withdrawal UGX {amount:,.0f} from {acct.account_no} ({acct.member.full_name})",
                          {"account_id": acct.pk, "amount": str(amount), "channel": channel, "narration": narration,
                           "till_user_id": user.pk}, user)
        return None, req
    return execute_withdrawal(acct, amount, user, channel, narration, date), None


@appr.register("withdrawal")
def _approve_withdrawal(req, approver):
    from django.contrib.auth import get_user_model
    p = req.payload
    maker = get_user_model().objects.get(pk=p["till_user_id"])
    tx = execute_withdrawal(SavingsAccount.objects.get(pk=p["account_id"]), D(p["amount"]), maker, p["channel"], p.get("narration", ""))
    return tx.ref


@transaction.atomic
def transfer(src, dst, amount, user, narration="", share_transfer=False):
    amount = _check_amount(amount)
    if src.pk == dst.pk:
        raise ValidationError("Choose two different accounts.")
    s, d = (_lock(x) for x in sorted([src, dst], key=lambda a: a.pk))
    s, d = (s, d) if s.pk == src.pk else (d, s)
    if s.kind != d.kind:
        raise ValidationError("Transfers must be between accounts of the same type (savings-to-savings or shares-to-shares).")
    if d.status in ("frozen", "closed"):
        raise ValidationError("Destination account cannot receive funds.")
    if s.kind == "shares":
        if SystemSetting.get("allow_share_transfers") != "1":
            raise ValidationError("Share transfers are disabled by policy.")
        if D(s.balance) - D(s.pledged) < amount:
            raise ValidationError("Insufficient unpledged shares.")
    else:
        validate_withdrawal(s, amount)
    group = next_ref("TR")
    s.balance -= amount
    d.balance += amount
    s.save(update_fields=["balance"])
    d.save(update_fields=["balance"])
    out = SavingsTransaction.objects.create(ref=next_ref("TX"), account=s, kind="transfer_out", amount=amount, balance_after=s.balance,
                                            channel="internal", narration=f"To {d.account_no} {narration}"[:200], group_ref=group, created_by=user)
    SavingsTransaction.objects.create(ref=next_ref("TX"), account=d, kind="transfer_in", amount=amount, balance_after=d.balance,
                                      channel="internal", narration=f"From {s.account_no} {narration}"[:200], group_ref=group, created_by=user)
    audit("savings.transfer", "finance", "SavingsAccount", s.pk, group, new={"from": s.account_no, "to": d.account_no, "amount": amount},
          detail="Internal transfer (no GL effect: both sides are member liabilities/equity)", user=user)
    return out


@appr.register("share_transfer")
def _approve_share_transfer(req, approver):
    p = req.payload
    from django.contrib.auth import get_user_model
    maker = get_user_model().objects.get(pk=p["maker_id"])
    return transfer(SavingsAccount.objects.get(pk=p["src"]), SavingsAccount.objects.get(pk=p["dst"]), D(p["amount"]), maker,
                    p.get("narration", ""), True).ref


@transaction.atomic
def post_interest(kind, rate, period, user):
    """kind: 'interest' (savings, monthly on current balance) or 'dividend' (shares, annual)."""
    rate = D(rate)
    if rate <= 0:
        raise ValidationError("Enter a rate greater than zero.")
    period = period.strip()
    if InterestRun.objects.filter(kind=kind, period=period).exists():
        raise ValidationError(f"{kind.title()} for period '{period}' has already been posted.")
    acc_kind = "savings" if kind == "interest" else "shares"
    accounts = SavingsAccount.objects.select_for_update().filter(kind=acc_kind, status__in=("active", "dormant"), balance__gt=0)
    total, rows = ZERO, []
    for a in accounts:
        amt = q0(D(a.balance) * rate / 100 / (12 if kind == "interest" else 1))
        if amt > 0:
            rows.append((a, amt))
            total += amt
    if not rows:
        raise ValidationError("No eligible account would receive anything at this rate.")
    if kind == "interest":
        lines = [(gl.SAV_INT_EXP, total, 0, f"Interest {period}"), (gl.SAVINGS, 0, total, f"Interest {period}")]
    else:
        lines = [(gl.RESERVES, total, 0, f"Dividend {period}"), (gl.SHARES, 0, total, f"Dividend {period}")]
    j = gl.post_journal(lines, f"{kind.title()} posting {period} @ {rate}%", source="interest", source_ref=period, user=user)
    for a, amt in rows:
        a.balance += amt
        a.save(update_fields=["balance"])
        SavingsTransaction.objects.create(ref=next_ref("TX"), account=a, kind=kind, amount=amt, balance_after=a.balance,
                                          channel="internal", narration=f"{kind.title()} {period} @ {rate}%", journal=j, created_by=user)
    run = InterestRun.objects.create(kind=kind, period=period, rate=rate, total=total, accounts_count=len(rows), journal=j, run_by=user)
    audit("savings.interest_run", "finance", "InterestRun", run.pk, j.ref, new={"kind": kind, "period": period, "rate": rate, "total": total, "accounts": len(rows)}, user=user)
    return run


@transaction.atomic
def reverse_transaction(tx, reason, user):
    """Reverses a deposit/withdrawal: GL reversal + opposite sub-ledger entry. Used by the approved-reversal handler."""
    tx = SavingsTransaction.objects.select_for_update().get(pk=tx.pk)
    if tx.kind not in ("deposit", "withdrawal", "share_purchase"):
        raise ValidationError("Only deposits, share purchases and withdrawals can be reversed here.")
    if tx.reversed_by.exists():
        raise ValidationError("This transaction was already reversed.")
    acct = _lock(tx.account)
    till = None
    if tx.till_id:
        till = cash.Till.objects.select_for_update().get(pk=tx.till_id)
        if till.status != "open":
            raise ValidationError("The original till is closed. Post a manual correcting journal instead.")
    if tx.kind == "withdrawal":
        acct.balance += tx.amount
    else:
        if D(acct.balance) - tx.amount < D(acct.pledged):
            raise ValidationError("Reversal would take the balance below the pledged amount.")
        acct.balance -= tx.amount
    acct.save(update_fields=["balance"])
    gl.reverse_journal_gl(tx.journal, reason, user=user)
    rev = SavingsTransaction.objects.create(ref=next_ref("TX"), account=acct, kind="reversal", amount=tx.amount, balance_after=acct.balance,
                                            channel=tx.channel, narration=f"Reversal of {tx.ref}: {reason}"[:200], reverses=tx, created_by=user)
    if till:
        cash.record_cash(till, "out" if tx.kind != "withdrawal" else "in", "reversal", tx.amount, rev.ref, f"Reversal of {tx.ref}", None, user)
    audit("savings.reverse", "finance", "SavingsTransaction", tx.pk, rev.ref, old={"ref": tx.ref, "amount": tx.amount}, new={"reversal": rev.ref}, detail=reason, user=user)
    return rev
