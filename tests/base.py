from datetime import date
from decimal import Decimal

from django.test import TestCase

from accounts_core.services import create_staff
from accounting import services as gl
from cashier.models import DENOMINATIONS
from core.models import next_ref
from loans.models import LoanProduct
from members.models import Member
from savings import services as sv


def mk_user(username, role):
    u, _ = create_staff(username, username.title(), "Test", role, f"{username}@example.com", "0700000000", password="Test@12345!x")
    u.profile.must_change_password = False
    u.profile.save()
    return u


def mk_member(user, name="Test Member", nin="CM12345678ABCD", phone="0772000001"):
    m = Member(member_no=next_ref("MB", "member_no"), full_name=name, gender="F", phone=phone, next_of_kin_name="Kin", next_of_kin_phone="0772000002",
               consent_given=True, created_by=user)
    m.set_nin(nin)
    m.save()
    return m


def break_down(amount):
    out, left = {}, int(amount)
    for d in DENOMINATIONS:
        out[d], left = divmod(left, d)
    return out


class SaccoTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        gl.ensure_coa()
        cls.admin = mk_user("admin1", "admin")
        cls.manager = mk_user("manager1", "manager")
        cls.accountant = mk_user("accountant1", "accountant")
        cls.cashier = mk_user("cashier1", "cashier")
        cls.cashier2 = mk_user("cashier2", "cashier")
        cls.officer = mk_user("officer1", "loan_officer")
        cls.committee = mk_user("committee1", "committee")
        cls.auditor = mk_user("auditor1", "auditor")
        cls.member = mk_member(cls.cashier)
        cls.savings = sv.open_account(cls.member, "savings", "Savings", cls.cashier)
        from cashier import services as cash
        cls.till = cash.open_till(cls.cashier, 0)

    def product(self, rate="18.00", method="reducing", **kw):
        return LoanProduct.objects.create(code=kw.pop("code", "TST"), name="Test loan", interest_rate=Decimal(rate) if rate is not None else None,
                                          interest_method=method, min_amount=50000, max_amount=20000000, min_term=1, max_term=36,
                                          guarantors_required=0, **kw)
