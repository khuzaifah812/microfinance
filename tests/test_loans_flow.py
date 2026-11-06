from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError

from accounting import services as gl
from loans import services as ls
from loans.models import Loan
from savings import services as sv

from .base import SaccoTestCase, mk_member


class LoanLifecycle(SaccoTestCase):
    def approved_loan(self, amount=1000000, term=12):
        p = self.product(rate="18.00")
        loan = ls.apply_for_loan(self.member, p, amount, term, "stock", self.cashier)
        ls.stage_action(loan, self.officer, "approve")
        ls.stage_action(loan, self.committee, "approve")
        ls.stage_action(loan, self.manager, "approve")
        loan.refresh_from_db()
        return loan

    def test_rate_not_set_blocks_application(self):
        p = self.product(rate=None, code="NR")
        self.assertFalse(p.rate_set)
        with self.assertRaises(ValidationError) as cm:
            ls.apply_for_loan(self.member, p, 100000, 6, "x", self.cashier)
        self.assertIn("Rate not set", str(cm.exception))

    def test_full_lifecycle_18pct_reducing_12_months(self):
        loan = self.approved_loan()
        self.assertEqual(loan.status, "approved")
        ls.disburse(loan, self.accountant, "bank")
        loan.refresh_from_db()
        self.assertEqual(loan.status, "active")
        rows = list(loan.installments.exclude(status="superseded"))
        self.assertEqual(len(rows), 12)
        self.assertEqual(sum(r.principal + r.interest for r in rows), loan.total_payable)
        first = rows[0]
        ls.repay(loan, first.principal + first.interest, self.accountant, "bank")
        loan.refresh_from_db()
        self.assertEqual(loan.outstanding_principal, Decimal(1000000) - first.principal)
        rows_tb, dr, cr = gl.trial_balance()
        self.assertEqual(dr, cr)

    def test_loan_officer_cannot_approve_own_application(self):
        p = self.product()
        loan = ls.apply_for_loan(self.member, p, 200000, 6, "x", self.officer)
        with self.assertRaises(ValidationError):
            ls.stage_action(loan, self.officer, "approve")

    def test_each_stage_needs_a_different_person(self):
        p = self.product()
        loan = ls.apply_for_loan(self.member, p, 200000, 6, "x", self.cashier)
        ls.stage_action(loan, self.officer, "approve")
        with self.assertRaises((ValidationError, PermissionDenied)):
            ls.stage_action(loan, self.officer, "approve")      # same person again
        with self.assertRaises(PermissionDenied):
            ls.stage_action(loan, self.cashier, "approve")      # wrong role

    def test_approver_cannot_disburse(self):
        loan = self.approved_loan()
        with self.assertRaises(ValidationError):
            ls.disburse(loan, self.manager, "bank")

    def test_cannot_disburse_before_full_approval(self):
        p = self.product()
        loan = ls.apply_for_loan(self.member, p, 200000, 6, "x", self.cashier)
        ls.stage_action(loan, self.officer, "approve")
        with self.assertRaises(ValidationError):
            ls.disburse(loan, self.accountant, "bank")

    def test_rejection_ends_workflow(self):
        p = self.product()
        loan = ls.apply_for_loan(self.member, p, 200000, 6, "x", self.cashier)
        ls.stage_action(loan, self.officer, "reject", "Not eligible")
        loan.refresh_from_db()
        self.assertEqual(loan.status, "rejected")

    def test_terms_are_snapshotted_when_product_rate_changes(self):
        p = self.product(rate="18.00")
        loan = ls.apply_for_loan(self.member, p, 200000, 6, "x", self.cashier)
        p.interest_rate = Decimal("30.00")
        p.save()
        loan.refresh_from_db()
        self.assertEqual(loan.interest_rate, Decimal("18.00"))

    def test_guarantor_pledge_cannot_be_reused_beyond_balance(self):
        g = mk_member(self.cashier, name="Guarantor", nin="CM88888888GGGG", phone="0772777777")
        ga = sv.open_account(g, "savings", "S", self.cashier)
        sv.deposit(ga, Decimal(100000), self.cashier, "cash")
        p = self.product()
        l1 = ls.apply_for_loan(self.member, p, 200000, 6, "x", self.cashier)
        l2 = ls.apply_for_loan(self.member, p, 200000, 6, "y", self.cashier)
        ls.add_guarantor(l1, g, ga, 80000, self.cashier)
        with self.assertRaises(ValidationError):
            ls.add_guarantor(l2, g, ga, 80000, self.cashier)    # only 20,000 left unpledged
