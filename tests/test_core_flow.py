from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from accounting import services as gl
from accounting.models import Journal, JournalLine
from accounts_core.models import AuditLog
from members.models import Member
from savings import services as sv
from savings.models import SavingsTransaction

from .base import SaccoTestCase, mk_member


class MemberAndAccountUniqueness(SaccoTestCase):
    def test_duplicate_member_number_rejected(self):
        dup = Member(member_no=self.member.member_no, full_name="Other", gender="M", phone="0772999999", next_of_kin_name="K", next_of_kin_phone="0772999998",
                     created_by=self.cashier)
        with self.assertRaises(IntegrityError), transaction.atomic():
            dup.save()

    def test_duplicate_nin_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            mk_member(self.cashier, name="Twin", nin="CM12345678ABCD", phone="0772555555")

    def test_duplicate_account_number_rejected(self):
        m2 = mk_member(self.cashier, name="Second", nin="CM99999999ZZZZ", phone="0772111222")
        with self.assertRaises(ValidationError):
            sv.open_account(m2, "savings", "Savings", self.cashier, account_no=self.savings.account_no)
        with self.assertRaises(ValidationError):
            sv.open_account(m2, "savings", "Savings", self.cashier, account_no=self.savings.account_no.lower())

    def test_generated_account_numbers_are_unique(self):
        m2 = mk_member(self.cashier, name="Second", nin="CM99999999ZZZZ", phone="0772111222")
        a = sv.open_account(m2, "savings", "S", self.cashier)
        b = sv.open_account(m2, "savings", "S2", self.cashier)
        self.assertNotEqual(a.account_no, b.account_no)

    def test_nin_is_encrypted_at_rest(self):
        self.assertNotIn("CM12345678ABCD", self.member.nin_enc)
        self.assertEqual(self.member.nin, "CM12345678ABCD")


class DepositWithdraw(SaccoTestCase):
    def test_deposit_updates_savings_cash_journal_and_audit(self):
        cash0, sav0 = gl.balance_of(gl.CASH), gl.balance_of(gl.SAVINGS)
        n_audit = AuditLog.objects.count()
        sv.deposit(self.savings, Decimal(50000), self.cashier, "cash", "Test deposit")
        self.savings.refresh_from_db()
        self.assertEqual(self.savings.balance, Decimal(50000))
        self.assertEqual(gl.balance_of(gl.CASH) - cash0, Decimal(50000))
        self.assertEqual(gl.balance_of(gl.SAVINGS) - sav0, Decimal(50000))
        tx = SavingsTransaction.objects.get(account=self.savings)
        j = Journal.objects.get(source_ref=tx.ref)
        lines = list(j.lines.all())
        self.assertEqual(len(lines), 2)
        self.assertEqual(sum(l.debit for l in lines), sum(l.credit for l in lines))
        self.assertGreater(AuditLog.objects.count(), n_audit)
        self.assertTrue(AuditLog.objects.filter(action__startswith="deposit").exists() or AuditLog.objects.filter(reference=tx.ref).exists())

    def test_withdrawal_over_balance_rejected(self):
        sv.deposit(self.savings, Decimal(20000), self.cashier, "cash")
        with self.assertRaises(ValidationError):
            sv.execute_withdrawal(self.savings, Decimal(20001), self.cashier)
        self.savings.refresh_from_db()
        self.assertEqual(self.savings.balance, Decimal(20000))

    def test_zero_and_negative_amounts_rejected(self):
        for bad in (0, -5, Decimal("100.5")):
            with self.assertRaises(ValidationError):
                sv.deposit(self.savings, Decimal(bad), self.cashier, "cash")

    def test_trial_balance_balances_after_activity(self):
        sv.deposit(self.savings, Decimal(75000), self.cashier, "cash")
        rows, dr, cr = gl.trial_balance()
        self.assertEqual(dr, cr)
        self.assertFalse(list(gl.unbalanced_journals()))


class LedgerImmutability(SaccoTestCase):
    def setUp(self):
        self.j = gl.post_journal([(gl.CASH, 1000, 0, ""), (gl.SAVINGS, 0, 1000, "")], "t", user=self.accountant)

    def test_unbalanced_journal_rejected(self):
        with self.assertRaises(ValidationError):
            gl.post_journal([(gl.CASH, 1000, 0, ""), (gl.SAVINGS, 0, 900, "")], "bad", user=self.accountant)

    def test_posted_journal_cannot_be_deleted_or_edited(self):
        with self.assertRaises(PermissionError):
            self.j.delete()
        with self.assertRaises(PermissionError):
            Journal.objects.filter(pk=self.j.pk).delete()
        with self.assertRaises(PermissionError):
            Journal.objects.filter(pk=self.j.pk).update(description="x")
        self.j.description = "changed"
        with self.assertRaises(PermissionError):
            self.j.save()
        with self.assertRaises(PermissionError):
            JournalLine.objects.filter(journal=self.j).delete()

    def test_reversal_nets_to_zero(self):
        before = gl.balance_of(gl.CASH)
        r = gl.reverse_journal_gl(self.j, "mistake", self.accountant)
        self.assertEqual(r.reverses_id, self.j.pk)
        self.assertEqual(gl.balance_of(gl.CASH), before - Decimal(1000))
