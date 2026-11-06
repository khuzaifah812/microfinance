import calendar
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

ZERO = Decimal("0")


def D(x):
    if x is None or x == "":
        return ZERO
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


def q0(x):
    """Round to whole shillings (HALF_UP)."""
    return D(x).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def q2(x):
    return D(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def today():
    return timezone.localdate()


def add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    y += d.year
    m += 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def month_start(d):
    return d.replace(day=1)


def prev_month_end(d=None):
    d = d or today()
    return d.replace(day=1) - timedelta(days=1)


def last_n_months(n=6, ref=None):
    ref = ref or today()
    start = month_start(ref)
    return [add_months(start, -i) for i in range(n - 1, -1, -1)]


def fmt_money(x):
    return f"{q0(x):,.0f}"


def fmt_ugx(x):
    v = q0(x)
    return f"-UGX {abs(v):,.0f}" if v < 0 else f"UGX {v:,.0f}"


def compact(x):
    x = float(x)
    a = abs(x)
    if a >= 1e9:
        return f"{x / 1e9:.1f}B"
    if a >= 1e6:
        return f"{x / 1e6:.1f}M"
    if a >= 1e3:
        return f"{x / 1e3:.0f}K"
    return f"{x:.0f}"


def client_ip(request):
    from django.conf import settings
    if request is None:
        return None
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff and settings.TRUST_PROXY_IP:
        return xff.split(",")[-1].strip() or None
    return request.META.get("REMOTE_ADDR")
