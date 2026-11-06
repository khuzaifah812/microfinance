import os
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts_core.audit import audit
from accounts_core.services import create_staff
from accounting import services as gl
from cashier import services as cash
from cashier.models import DENOMINATIONS
from core.models import SystemSetting, next_ref
from loans import services as ls
from loans.models import Loan, LoanProduct
from members.models import Member
from savings import services as sv

PRODUCTS = [("SFL", "School Fees Loan"), ("AGL", "Agricultural Loan"), ("PML", "Property Management Loan"), ("BDL", "Business Development Loan"),
            ("EML", "Emergency Loan"), ("SAL", "Salary Advance Loan"), ("AFL", "Asset Financing Loan"), ("GDL", "General Development Loan")]
STAFF = [("admin", "System", "Administrator", "admin"), ("manager", "Grace", "Nakato", "manager"), ("accountant", "Peter", "Okello", "accountant"),
         ("cashier", "Sarah", "Namukasa", "cashier"), ("loanofficer", "David", "Mugisha", "loan_officer"), ("committee", "Ruth", "Atim", "committee"),
         ("auditor", "Moses", "Kato", "auditor")]
MEMBERS = [("Nabirye Joan", "F", "0772100001", "CF90000100AAAA", "Nabirye Peter", "0772100002"),
           ("Ssebunya Henry", "M", "0701200003", "CM85000200BBBB", "Ssebunya Rose", "0701200004"),
           ("Akello Beatrice", "F", "0755300005", "CF92000300CCCC", "Akello Samuel", "0755300006")]


def break_down(amount):
    out, left = {}, int(amount)
    for d in DENOMINATIONS:
        out[d], left = divmod(left, d)
    return out


class Command(BaseCommand):
    help = "Create demo staff, members, accounts, loan products (rates NULL) and a few real, balanced transactions."

    def add_arguments(self, p):
        p.add_argument("--force", action="store_true", help="Seed even if members already exist")
        p.add_argument("--no-force-change", action="store_true", help="Do not require password change at first login (demo convenience)")

    @transaction.atomic
    def handle(self, *a, **o):
        if Member.objects.exists() and not o["force"]:
            raise CommandError("Members already exist. Refusing to seed a database that holds data (use --force only on a demo database).")
        pw = os.environ.get("SEED_PASSWORD", "Sacco@Demo2026!")
        users = {}
        from django.contrib.auth import get_user_model
        User = get_user_model()
        for u, f, l, role in STAFF:
            existing = User.objects.filter(username=u).first()
            if existing:
                users[u] = existing
                continue
            users[u], _ = create_staff(u, f, l, role, f"{u}@example.com", "0700000000", password=pw)
            if o["no_force_change"]:
                users[u].profile.must_change_password = False
                users[u].profile.save()
        for code, name in PRODUCTS:
            LoanProduct.objects.get_or_create(code=code, defaults=dict(
                name=name, description=name, interest_rate=None, interest_method="reducing", min_amount=50000, max_amount=20000000, min_term=1,
                max_term=36, processing_fee_pct=Decimal("1.00"), penalty_pct=Decimal("2.00"), guarantors_required=1,
                eligibility="Active member for at least 3 months with regular savings."))
        cashier, accountant, officer, committee, manager = (users[k] for k in ("cashier", "accountant", "loanofficer", "committee", "manager"))
        # opening capital held at the bank, so the demo bank balance is realistic before the first disbursement
        gl.post_journal([(gl.BANK, 5000000, 0, "Opening bank balance"), (gl.RESERVES, 0, 5000000, "Opening bank balance")], "Opening balance: SACCO funds at bank", source="opening", user=accountant)
        till = cash.open_till(cashier, 200000)
        accounts = []
        for i, (name, g, phone, nin, kin, kinp) in enumerate(MEMBERS):
            m = Member(member_no=next_ref("MB", "member_no"), full_name=name, gender=g, phone=phone, next_of_kin_name=kin, next_of_kin_phone=kinp,
                       address="Kampala", occupation="Trader", consent_given=True, created_by=cashier)
            m.set_nin(nin)
            from django.utils import timezone
            m.consent_at = timezone.now()
            m.save()
            audit("member.register", "member", "Member", m.pk, m.member_no, detail="Seed member", user=cashier)
            sav = sv.open_account(m, "savings", "Ordinary savings", cashier)
            shr = sv.open_account(m, "shares", "Share capital", cashier)
            sv.deposit(sav, 400000 + i * 250000, cashier, "cash", "Opening deposit")
            sv.deposit(shr, 100000, cashier, "cash", "Share purchase")
            accounts.append((m, sav))
        # a real, fully approved & disbursed demo loan (rate set on the loan itself; product rates stay NULL)
        m1, sav1 = accounts[0]
        m2, sav2 = accounts[1]
        prod = LoanProduct.objects.get(code="BDL")
        loan = Loan.objects.create(loan_no=next_ref("LN"), member=m1, product=prod, principal=Decimal("1000000"), interest_rate=Decimal("18.00"),
                                   interest_method="reducing", frequency="monthly", term=12, processing_fee_pct=Decimal("1.00"), penalty_pct=Decimal("2.00"),
                                   purpose="Stock for retail shop", applied_by=cashier)
        audit("loan.apply", "loan", "Loan", loan.pk, loan.loan_no, detail="Seed loan (rate 18% reducing set on the loan)", user=cashier)
        ls.add_guarantor(loan, m2, sav2, 300000, cashier)
        ls.stage_action(loan, officer, "approve", "Good savings record; guarantor in place.")
        ls.stage_action(loan, committee, "approve", "Approved by committee.")
        ls.stage_action(loan, manager, "approve", "Final approval.")
        ls.disburse(loan, accountant, "bank")
        loan.refresh_from_db()
        first = loan.installments.first()
        ls.repay(loan, first.principal + first.interest, accountant, "bank", note="First instalment")
        # a pending application to demonstrate the queue
        m3 = accounts[2][0]
        pend = Loan.objects.create(loan_no=next_ref("LN"), member=m3, product=LoanProduct.objects.get(code="EML"), principal=Decimal("300000"),
                                   interest_rate=Decimal("20.00"), interest_method="flat", frequency="monthly", term=6, processing_fee_pct=Decimal("1.00"),
                                   penalty_pct=Decimal("2.00"), purpose="Medical emergency", applied_by=cashier)
        audit("loan.apply", "loan", "Loan", pend.pk, pend.loan_no, detail="Seed pending application", user=cashier)
        # close the till balanced
        till.refresh_from_db()
        cash.close_till(till, break_down(till.expected), "", cashier)
        SystemSetting.set("demo_data_loaded", "1")
        self.stdout.write(self.style.SUCCESS("Demo data created."))
        self.stdout.write(f"Staff usernames: {', '.join(u for u, *_ in STAFF)}  |  password: {pw}  (change at first login; admin/manager/accountant must also enrol 2FA)")
