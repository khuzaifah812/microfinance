from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from loans import calc


class ScheduleMaths(SimpleTestCase):
    first = date(2026, 12, 5)

    def test_flat_interest_is_on_original_principal(self):
        # 18% a year, 12 months, UGX 1,000,000 -> interest 180,000 exactly
        rows = calc.build_schedule(Decimal(1000000), Decimal(18), 12, "flat", "monthly", self.first)
        self.assertEqual(sum(r["interest"] for r in rows), Decimal(180000))
        self.assertEqual(sum(r["principal"] for r in rows), Decimal(1000000))
        self.assertEqual(len(rows), 12)

    def test_flat_six_months_prorated(self):
        rows = calc.build_schedule(Decimal(300000), Decimal(20), 6, "flat", "monthly", self.first)
        self.assertEqual(sum(r["interest"] for r in rows), Decimal(30000))

    def test_reducing_balance_first_interest_and_total(self):
        rows = calc.build_schedule(Decimal(1000000), Decimal(18), 12, "reducing", "monthly", self.first)
        self.assertEqual(rows[0]["interest"], Decimal(15000))        # 1,000,000 x 1.5%
        self.assertEqual(sum(r["principal"] for r in rows), Decimal(1000000))
        _, interest, payable = calc.totals(rows)
        self.assertEqual(payable, Decimal(1000000) + interest)
        self.assertTrue(Decimal(98000) < interest < Decimal(102000))   # about 100,161
        self.assertGreater(rows[0]["interest"], rows[-1]["interest"])

    def test_schedule_sums_exactly_to_total_payable_in_whole_shillings(self):
        for method in ("flat", "reducing"):
            for amount, n in ((1000000, 12), (777777, 7), (123457, 5), (5000000, 36)):
                rows = calc.build_schedule(Decimal(amount), Decimal("17.5"), n, method, "monthly", self.first)
                _, interest, payable = calc.totals(rows)
                self.assertEqual(sum(r["principal"] + r["interest"] for r in rows), payable)
                self.assertEqual(payable, Decimal(amount) + interest)
                for r in rows:
                    self.assertEqual(r["principal"], r["principal"].to_integral_value())
                    self.assertEqual(r["interest"], r["interest"].to_integral_value())

    def test_due_dates_step_monthly(self):
        rows = calc.build_schedule(Decimal(120000), Decimal(12), 3, "flat", "monthly", self.first)
        self.assertEqual([r["due"].month for r in rows], [12, 1, 2])

    def test_invalid_input_rejected(self):
        with self.assertRaises(ValueError):
            calc.build_schedule(Decimal(0), Decimal(10), 12, "flat", "monthly", self.first)
        with self.assertRaises(ValueError):
            calc.build_schedule(Decimal(1000), Decimal(10), 0, "reducing", "monthly", self.first)
