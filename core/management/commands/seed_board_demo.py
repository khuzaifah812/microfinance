"""Board-presentation dataset: ~20 members, eight months of history, loans in every stage.

Everything is created through the real services (savings.deposit, loans.apply_for_loan, stage_action, disburse, repay ...),
so every figure on every screen is computed from a balanced double-entry ledger and every action is in the audit chain.
Nothing is typed into a report. All people, phone numbers and ID numbers below are INVENTED.

Run:  python manage.py seed_board_demo
Needs the same DJANGO_SECRET_KEY (and AUDIT_HMAC_KEY / FIELD_ENCRYPTION_KEY if set) as the site that will read the data.
"""
import os
import random
import secrets
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounting import services as gl
from accounts_core.audit import audit
from accounts_core.services import create_staff
from cashier import services as cash
from cashier.models import DENOMINATIONS
from core.models import SiteConfig, SystemSetting, next_ref
from core.utils import today
from loans import services as ls
from loans.models import Loan, LoanProduct
from members.models import Member
from savings import services as sv

from .seed_data import PRODUCTS, STAFF, break_down

# name, gender, phone, occupation, address, months since joining, monthly saving (UGX), kin, kin relation
MEMBERS = [
    ("Nakato Grace", "F", "0772410011", "Primary school teacher", "Kawempe, Kampala", 8, 220000, "Ssemwogerere Paul", "Husband"),
    ("Okello Peter", "M", "0701530022", "Shop owner", "Gulu City", 8, 300000, "Akello Mary", "Wife"),
    ("Namukasa Sarah", "F", "0755620033", "Market vendor", "Nakasero Market, Kampala", 8, 180000, "Namukasa Jane", "Sister"),
    ("Mugisha David", "M", "0782740044", "Farmer", "Mbarara", 7, 200000, "Mugisha Annet", "Wife"),
    ("Atim Ruth", "F", "0759850055", "Nurse", "Lira", 7, 260000, "Atim Joseph", "Brother"),
    ("Kato Moses", "M", "0772960066", "Boda-boda operator", "Masaka", 7, 120000, "Kato Betty", "Mother"),
    ("Nabirye Joan", "F", "0701170077", "Tailor", "Jinja", 6, 140000, "Nabirye Peter", "Father"),
    ("Ssebunya Henry", "M", "0755280088", "Mechanic", "Wakiso", 6, 160000, "Ssebunya Rose", "Wife"),
    ("Akello Beatrice", "F", "0782390099", "Hair salon owner", "Entebbe", 6, 150000, "Akello Samuel", "Husband"),
    ("Byaruhanga Isaac", "M", "0759100110", "Dairy farmer", "Kiruhura", 6, 240000, "Byaruhanga Esther", "Wife"),
    ("Namatovu Esther", "F", "0772210121", "Caterer", "Mukono", 5, 130000, "Namatovu John", "Brother"),
    ("Opio Charles", "M", "0701320132", "Accountant (SME)", "Kampala", 5, 280000, "Opio Dorothy", "Wife"),
    ("Tumusiime Alice", "F", "0755430143", "Pharmacy attendant", "Fort Portal", 5, 170000, "Tumusiime Robert", "Husband"),
    ("Wasswa Brian", "M", "0782540154", "Electrician", "Kira", 4, 150000, "Wasswa Agnes", "Mother"),
    ("Nankya Prossy", "F", "0759650165", "Vegetable seller", "Nansana", 4, 90000, "Nankya Fred", "Husband"),
    ("Kiiza Patrick", "M", "0772760176", "Hardware shop", "Kasese", 3, 200000, "Kiiza Mary", "Wife"),
    ("Achan Florence", "F", "0701870187", "Bakery owner", "Arua", 3, 190000, "Achan Denis", "Son"),
    ("Mwesigwa Tom", "M", "0755980198", "Driver", "Mbale", 2, 110000, "Mwesigwa Lydia", "Wife"),
    ("Nalubega Irene", "F", "0782090209", "Poultry farmer", "Luwero", 2, 120000, "Nalubega Ben", "Brother"),
    ("Odongo Simon", "M", "0759200220", "Welder", "Soroti", 1, 100000, "Odongo Cate", "Wife"),
]

# demo rates ONLY - the SACCO's approved rates must be entered by an administrator under Loan products
RATES = {"SFL": ("15.00", "reducing"), "AGL": ("14.00", "reducing"), "PML": ("16.00", "reducing"), "BDL": ("18.00", "reducing"),
         "EML": ("20.00", "flat"), "SAL": ("12.00", "flat"), "AFL": ("17.00", "reducing"), "GDL": ("16.00", "reducing")}

# member index, product, amount, term, purpose, guarantor index, months since disbursement, instalments paid on time
ACTIVE_LOANS = [
    (1, "BDL", 3000000, 12, "Restock shop and add a cold room", 0, 6, 6),
    (3, "AGL", 2000000, 10, "Buy two dairy cows and feed", 9, 5, 3),       # 3 of 5 due paid -> two instalments in arrears
    (4, "PML", 4000000, 24, "Rental property renovation", 11, 4, 4),
    (7, "SFL", 1200000, 4, "Term fees for three children", 2, 6, 4),        # fully repaid
    (8, "EML", 400000, 4, "Salon equipment repair", 6, 3, 3),
    (10, "SAL", 600000, 3, "Catering event deposit", 12, 2, 2),
    (9, "AGL", 3500000, 12, "Expand the dairy herd and a milk cooler", 1, 4, 4),
    (0, "AFL", 2500000, 18, "Buy a motorcycle for school-run business", 3, 3, 3),
]
MOMO_LOANS = {9, 0}     # disbursed from mobile money float so the bank balance stays realistic


def months_ago(n, day=15):
    t = today()
    y, m = t.year, t.month - n
    while m <= 0:
        m += 12
        y -= 1
    d = date(y, m, min(day, 28))
    return min(d, t)


def whole(x, step=5000):
    return int(round(x / step) * step)


class Command(BaseCommand):
    help = "Create a realistic, Board-ready demonstration dataset (invented people) through the real services."

    def add_arguments(self, p):
        p.add_argument("--force", action="store_true", help="Seed even if members already exist (never on real data)")
        p.add_argument("--staff-password", default="", help="Password for the demo staff (default: a random one is generated and shown once)")
        p.add_argument("--admin-username", default="admin", help="Username of the demo System Administrator (default: admin)")
        p.add_argument("--admin-password", default="", help="Password for that administrator (default: same as the other demo staff)")
        p.add_argument("--admin-email", default="", help="Email for that administrator (verification codes are sent here)")

    @transaction.atomic
    def handle(self, *a, **o):
        if Member.objects.exists() and not o["force"]:
            raise CommandError("Members already exist. Refusing to seed a database that holds data.")
        rnd = random.Random(2026)
        User = get_user_model()
        pw = o["staff_password"] or os.environ.get("SEED_PASSWORD") or (secrets.token_urlsafe(9) + "#7a")
        users = {}
        admin_name = o["admin_username"].strip().lower()
        staff = [(admin_name if u == "admin" else u, f, l, role) for u, f, l, role in STAFF]
        for u, f, l, role in staff:
            existing = User.objects.filter(username=u).first()
            if existing:
                users[u] = existing
                continue
            is_admin = role == "admin"
            users[u], _ = create_staff(u, f, l, role, (o["admin_email"] if is_admin and o["admin_email"] else f"{u}@example.com"), "0700000000",
                                       password=(o["admin_password"] if is_admin and o["admin_password"] else pw))
            pr = users[u].profile
            pr.must_change_password, pr.email_verified = False, True       # demo convenience: they sign in directly
            pr.save()
        if admin_name != "admin":
            users["admin"] = users[admin_name]
        cashier, accountant, officer, committee, manager = (users[k] for k in ("cashier", "accountant", "loanofficer", "committee", "manager"))

        # ---- loan products with DEMO rates (flagged in the guide: replace with the SACCO's own)
        for code, name in PRODUCTS:
            prod, _ = LoanProduct.objects.get_or_create(code=code, defaults=dict(
                name=name, description=name, interest_method="reducing", min_amount=50000, max_amount=20000000, min_term=1, max_term=36,
                processing_fee_pct=Decimal("1.00"), penalty_pct=Decimal("2.00"), guarantors_required=1,
                eligibility="Active member for at least 3 months with regular savings."))
            if prod.interest_rate is None:
                prod.interest_rate, prod.interest_method = Decimal(RATES[code][0]), RATES[code][1]
                prod.savings_multiple = Decimal("3")
                prod.save()

        # ---- opening capital, dated at the start of the story
        start = months_ago(8, 1)
        gl.post_journal([(gl.BANK, 15000000, 0, "Opening bank balance"), (gl.RESERVES, 0, 15000000, "Opening reserves")],
                        "Opening balance: SACCO funds at bank", start, "opening", user=accountant)

        # ---- members, accounts and savings history
        members, savings = [], []
        for i, (name, g, phone, occ, addr, ago, monthly, kin, rel) in enumerate(MEMBERS):
            joined = months_ago(ago, 3 + (i % 20))
            m = Member(member_no=next_ref("MB", "member_no"), full_name=name, gender=g, phone=phone, occupation=occ, address=addr,
                       next_of_kin_name=kin, next_of_kin_relation=rel, next_of_kin_phone="07" + phone[2:5] + "999" + phone[-1],
                       consent_given=True, consent_at=timezone.now(), joined_on=joined, created_by=cashier)
            m.set_nin(f"C{g}{90 + i % 9}{i:03d}{rnd.randint(10000, 99999)}{'ABCDEFGH'[i % 8]}{'KLMNPQRS'[i % 8]}")
            m.save()
            audit("member.register", "member", "Member", m.pk, m.member_no, detail="Demonstration member", user=cashier)
            sav = sv.open_account(m, "savings", "Ordinary savings", cashier)
            shr = sv.open_account(m, "shares", "Share capital", cashier)
            sv.deposit(shr, 100000, cashier, "bank", "Share purchase on joining", date=joined)
            for k in range(ago, -1, -1):
                if k != ago and rnd.random() < 0.18:        # a few missed months look real
                    continue
                d = months_ago(k, 5 + rnd.randint(0, 20))
                if d < joined:
                    d = joined
                amount = whole(monthly * rnd.uniform(0.8, 1.25))
                sv.deposit(sav, amount, cashier, rnd.choice(["momo", "momo", "bank"]), "Monthly savings", date=d)
            if ago >= 6:
                sv.deposit(shr, 50000, cashier, "momo", "Additional shares", date=months_ago(3, 12))
            members.append(m)
            savings.append(sav)
        # two members have withdrawn small amounts
        for idx, amt, ago in ((2, 60000, 2), (5, 40000, 1)):
            sv.execute_withdrawal(savings[idx], amt, cashier, "momo", "Member withdrawal", date=months_ago(ago, 18))
        # one member has gone quiet, one is suspended (status shown on the members page)
        members[18].status = "inactive"
        members[18].save(update_fields=["status"])

        # ---- loans that are already running
        def guarantee(loan, gi, share=Decimal("0.3")):
            gacct = savings[gi]
            gacct.refresh_from_db()
            eligible = Decimal(gacct.balance) - Decimal(gacct.pledged)
            amt = int(min(loan.principal * share, eligible * Decimal("0.6")) // 1000 * 1000)
            ls.add_guarantor(loan, members[gi], gacct, amt, cashier)

        def recommend(loan, upto):
            """upto: 'officer' | 'committee' | 'manager'  (how many stages to approve)"""
            chain = [("officer", officer, "Good savings record; guarantor in place."), ("committee", committee, "Approved by credit committee."),
                     ("manager", manager, "Final approval.")]
            for stage, user, note in chain:
                ls.stage_action(loan, user, "approve", note)
                if stage == upto:
                    break

        running = []
        for mi, code, amt, term, purpose, gi, ago, paid in ACTIVE_LOANS:
            loan = ls.apply_for_loan(members[mi], LoanProduct.objects.get(code=code), amt, term, purpose, cashier)
            guarantee(loan, gi)
            recommend(loan, "manager")
            loan.refresh_from_db()
            ls.disburse(loan, accountant, "momo" if mi in MOMO_LOANS else "bank", date=months_ago(ago, 10))
            loan.refresh_from_db()
            for inst in list(loan.installments.all()[:paid]):
                ls.repay(loan, inst.principal + inst.interest, accountant, rnd.choice(["momo", "bank"]), date=inst.due_date, note=f"Instalment {inst.no}")
            running.append(loan)

        # ---- the approval pipeline, so the Board sees every stage of maker-checker
        pipeline = [
            (11, "SFL", 800000, 6, "School fees for next term", 5, None, "Waiting for the loan officer"),
            (12, "BDL", 1500000, 12, "Buy a second delivery motorcycle", 4, "officer", "Recommended; waiting for the credit committee"),
            (13, "AGL", 1200000, 10, "Irrigation pump and seed", 3, "committee", "Waiting for the General Manager"),
            (14, "EML", 500000, 6, "Hospital bill", 2, "manager", "Fully approved; ready to disburse"),
        ]
        for mi, code, amt, term, purpose, gi, upto, _ in pipeline:
            loan = ls.apply_for_loan(members[mi], LoanProduct.objects.get(code=code), amt, term, purpose, cashier)
            guarantee(loan, gi)
            if upto:
                recommend(loan, upto)
        rej = ls.apply_for_loan(members[15], LoanProduct.objects.get(code="BDL"), 1200000, 12, "Expand hardware stock", cashier)
        guarantee(rej, 8)
        ls.stage_action(rej, officer, "approve", "Recommended.")
        ls.stage_action(rej, committee, "reject", "Existing obligations are too high for the proposed instalment.")

        # ---- running costs of the SACCO, month by month
        for k in range(7, -1, -1):
            gl.post_journal([("5100", 90000, 0, "Part-time staff allowances"), (gl.BANK, 0, 90000, "Part-time staff allowances")],
                            "Part-time staff allowances", months_ago(k, 28), "manual", user=accountant)
            gl.post_journal([("5200", 60000, 0, "Office rent and utilities"), (gl.BANK, 0, 60000, "Office rent and utilities")],
                            "Office rent and utilities", months_ago(k, 27), "manual", user=accountant)
            fees = 20000 * sum(1 for mm in members if mm.joined_on.year == months_ago(k, 15).year and mm.joined_on.month == months_ago(k, 15).month)
            if fees:
                gl.post_journal([(gl.BANK, fees, 0, "Registration fees"), (gl.OTHER_INCOME, 0, fees, "Registration fees")],
                                "Member registration and passbook fees", months_ago(k, 20), "manual", user=accountant)

        # ---- today at the counter: a few cash deposits through the cashier's till, then a balanced close
        till = cash.open_till(cashier, 300000)
        for idx, amt in ((0, 150000), (5, 60000), (12, 120000)):
            sv.deposit(savings[idx], amt, cashier, "cash", "Counter deposit", date=today())
        till.refresh_from_db()
        cash.close_till(till, break_down(till.expected), "", cashier)

        ls.accrue_penalties(user=accountant)
        if gl.unbalanced_journals():
            raise CommandError("Ledger check failed: unbalanced journal found. Nothing was saved.")
        SystemSetting.set("demo_data_loaded", "1")
        self.stdout.write(self.style.SUCCESS(f"Board demo ready: {len(members)} members, {Loan.objects.count()} loans."))
        self.stdout.write(f"Staff usernames: {', '.join(u for u, *_ in staff)}")
        self.stdout.write(self.style.WARNING(f"Demo staff password (shown once): {pw}"))
        self.stdout.write("admin / manager / accountant still enrol an authenticator app at first sign-in; the other roles sign in directly.")