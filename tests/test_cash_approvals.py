from datetime import date
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError

from accounting import services as gl
from accounting import vouchers
from accounting.models import Account, Voucher
from approvals import services as appr
from approvals.models import ApprovalRule
from cashier import services as cash
from savings import services as sv

from .base import SaccoTestCase, break_down


class TillTests(SaccoTestCase):
    def setUp(self):
        cash.close_till(self.till, {}, "", self.cashier)

    def test_shortage_of_1500_is_logged_with_reason(self):
        till = cash.open_till(self.cashier, 100000)
        till.refresh_from_db()
        done = cash.close_till(till, break_down(98500), "Counting error at counter 2", self.cashier)
        self.assertEqual(done.variance, Decimal(-1500))
        self.assertEqual(done.variance_reason, "Counting error at counter 2")
        self.assertEqual(gl.balance_of(gl.SHORTAGE), Decimal(1500))
        _, dr, cr = gl.trial_balance()
        self.assertEqual(dr, cr)

    def test_difference_without_reason_is_refused(self):
        till = cash.open_till(self.cashier, 100000)
        with self.assertRaises(ValidationError):
            cash.close_till(till, break_down(98500), "", self.cashier)

    def test_balanced_close_has_no_variance_journal(self):
        till = cash.open_till(self.cashier, 100000)
        done = cash.close_till(till, break_down(100000), "", self.cashier)
        self.assertEqual(done.variance, 0)
        self.assertIsNone(done.variance_journal)

    def test_only_owner_can_close_and_one_open_till_per_user(self):
        till = cash.open_till(self.cashier, 50000)
        with self.assertRaises(ValidationError):
            cash.close_till(till, break_down(50000), "", self.cashier2)
        with self.assertRaises(ValidationError):
            cash.open_till(self.cashier, 10000)


class MakerChecker(SaccoTestCase):
    def setUp(self):
        appr.ensure_rules()
        self.expense_acct = Account.objects.filter(type="expense").first()

    def test_expense_over_limit_is_queued_not_posted(self):
        ApprovalRule.objects.filter(kind="expense").update(threshold=100000)
        before = gl.balance_of(gl.CASH)
        v, req = vouchers.create_voucher("expense", self.expense_acct, 250000, "Supplier", "Office rent", "bank", date.today(), self.accountant)
        self.assertIsNotNone(req)
        v.refresh_from_db()
        self.assertEqual(v.status, "pending_approval")
        self.assertIsNone(v.journal)
        self.assertEqual(gl.balance_of(gl.CASH), before)

    def test_second_user_approval_posts_it(self):
        ApprovalRule.objects.filter(kind="expense").update(threshold=100000)
        v, req = vouchers.create_voucher("expense", self.expense_acct, 250000, "Supplier", "Office rent", "bank", date.today(), self.accountant)
        with self.assertRaises(ValidationError):
            appr.decide(req.pk, self.accountant, True)           # maker cannot approve own
        appr.decide(req.pk, self.manager, True)
        v.refresh_from_db()
        self.assertEqual(v.status, "posted")
        self.assertIsNotNone(v.journal)

    def test_rejection_requires_reason_and_never_posts(self):
        ApprovalRule.objects.filter(kind="expense").update(threshold=100000)
        v, req = vouchers.create_voucher("expense", self.expense_acct, 250000, "S", "Rent", "bank", date.today(), self.accountant)
        with self.assertRaises(ValidationError):
            appr.decide(req.pk, self.manager, False, "")
        appr.decide(req.pk, self.manager, False, "Not budgeted")
        v.refresh_from_db()
        self.assertEqual(v.status, "rejected")
        self.assertIsNone(v.journal)
        with self.assertRaises(ValidationError):
            appr.decide(req.pk, self.manager, True)             # already decided

    def test_below_limit_posts_directly(self):
        ApprovalRule.objects.filter(kind="expense").update(threshold=500000)
        v, req = vouchers.create_voucher("expense", self.expense_acct, 1000, "S", "Tea", "bank", date.today(), self.accountant)
        self.assertIsNone(req)
        self.assertEqual(v.status, "posted")

    def test_cashier_role_cannot_decide(self):
        ApprovalRule.objects.filter(kind="expense").update(threshold=0)
        v, req = vouchers.create_voucher("expense", self.expense_acct, 1000, "S", "Tea", "bank", date.today(), self.accountant)
        with self.assertRaises(PermissionDenied):
            appr.decide(req.pk, self.cashier, True)
