"""USSD menu engine used by the staff preview. Real USSD must be wired to a telecom/aggregator gateway."""
from savings.models import SavingsAccount


def respond(member, text):
    from loans.models import Loan
    parts = [p for p in text.split("*") if p != ""]
    if not parts:
        return "CON Welcome to {sacco}\n1. Savings balance\n2. Share balance\n3. Loan balance\n4. Next repayment\n5. Mini statement\n0. Exit", True
    c = parts[0]
    if c == "1":
        b = sum(a.balance for a in SavingsAccount.objects.filter(member=member, kind="savings"))
        return f"END Savings balance: UGX {b:,.0f}", False
    if c == "2":
        b = sum(a.balance for a in SavingsAccount.objects.filter(member=member, kind="shares"))
        return f"END Share capital: UGX {b:,.0f}", False
    if c == "3":
        loans = Loan.objects.filter(member=member, status="active")
        if not loans:
            return "END You have no active loan.", False
        return "END " + "\n".join(f"{l.loan_no}: UGX {l.outstanding_principal:,.0f} principal outstanding" for l in loans), False
    if c == "4":
        from loans.models import LoanInstallment
        i = LoanInstallment.objects.filter(loan__member=member, loan__status="active", status__in=("pending", "partial")).order_by("due_date").first()
        if not i:
            return "END No repayment due.", False
        return f"END Next: UGX {i.balance:,.0f} due {i.due_date:%d %b %Y} ({i.loan.loan_no})", False
    if c == "5":
        from savings.models import SavingsTransaction
        tx = SavingsTransaction.objects.filter(account__member=member).order_by("-id")[:5]
        if not tx:
            return "END No transactions yet.", False
        return "END " + "\n".join(f"{t.date:%d/%m} {t.get_kind_display()[:10]} {t.amount:,.0f}" for t in tx), False
    if c == "0":
        return "END Thank you.", False
    return "END Invalid choice.", False
