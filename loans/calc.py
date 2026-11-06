"""Pure loan mathematics (no database). Whole-shilling rounding; the last instalment absorbs rounding."""
from datetime import timedelta
from decimal import ROUND_FLOOR, Decimal

from core.utils import D, add_months, q0


def periods_per_year(frequency):
    return 52 if frequency == "weekly" else 12


def due_date(first_due, index, frequency):
    return first_due + timedelta(days=7 * index) if frequency == "weekly" else add_months(first_due, index)


def build_schedule(principal, annual_rate, periods, method, frequency, first_due):
    """Returns [{'no','due','principal','interest'}]. `annual_rate` is a percentage (e.g. 18)."""
    P, n = q0(principal), int(periods)
    if n < 1 or P <= 0:
        raise ValueError("Principal and number of periods must be positive.")
    rate = D(annual_rate) / 100
    ppy = periods_per_year(frequency)
    rows = []
    if method == "flat":
        # flat: interest charged on the ORIGINAL principal for the whole term; annual rate pro-rated by term length
        total_int = q0(P * rate * Decimal(n) / ppy)
        base_p = (P / n).to_integral_value(ROUND_FLOOR)
        base_i = (total_int / n).to_integral_value(ROUND_FLOOR)
        for k in range(1, n + 1):
            p = P - base_p * (n - 1) if k == n else base_p
            i = total_int - base_i * (n - 1) if k == n else base_i
            rows.append({"no": k, "due": due_date(first_due, k - 1, frequency), "principal": p, "interest": i})
    elif method == "reducing":
        r = rate / ppy
        emi = q0(P / n) if r == 0 else q0(P * r / (1 - (1 + r) ** (-n)))
        bal = P
        for k in range(1, n + 1):
            interest = q0(bal * r)
            if k == n:
                principal_part = bal
            else:
                principal_part = min(bal, max(Decimal(0), emi - interest))
            bal -= principal_part
            rows.append({"no": k, "due": due_date(first_due, k - 1, frequency), "principal": principal_part, "interest": interest})
    else:
        raise ValueError("Unknown interest method.")
    return rows


def totals(rows):
    p = sum((r["principal"] for r in rows), Decimal(0))
    i = sum((r["interest"] for r in rows), Decimal(0))
    return p, i, p + i
