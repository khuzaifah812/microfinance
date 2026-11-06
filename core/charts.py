"""Pure inline-SVG charts (no JavaScript libraries). Colours come from CSS classes."""
from html import escape

from django.utils.safestring import mark_safe

from .utils import compact

W, H = 480, 210
PL, PR, PT, PB = 44, 10, 14, 30


def _nice_max(v):
    if v <= 0:
        return 1
    import math
    e = 10 ** int(math.floor(math.log10(v)))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * e:
            return m * e
    return 10 * e


def _empty(msg="No data yet"):
    return mark_safe(f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="{escape(msg)}">'
                     f'<text x="{W // 2}" y="{H // 2}" text-anchor="middle" class="ch-lbl">{escape(msg)}</text></svg>')


def _axes(mx, ticks=4):
    out = []
    for i in range(ticks + 1):
        y = PT + (H - PT - PB) * (1 - i / ticks)
        out.append(f'<line x1="{PL}" x2="{W - PR}" y1="{y:.1f}" y2="{y:.1f}" class="ch-grid"/>'
                   f'<text x="{PL - 6}" y="{y + 3:.1f}" text-anchor="end" class="ch-lbl">{compact(mx * i / ticks)}</text>')
    return "".join(out)


def bar_chart(labels, series, title="Chart"):
    """series: list of (name, values, css_class) — grouped bars."""
    if not labels or not any(any(v for v in s[1]) for s in series):
        return _empty()
    mx = _nice_max(max(float(v) for s in series for v in s[1]))
    ph = H - PT - PB
    gw = (W - PL - PR) / len(labels)
    bw = min(34, (gw - 8) / len(series))
    parts = [_axes(mx)]
    for i, lab in enumerate(labels):
        gx = PL + gw * i + (gw - bw * len(series)) / 2
        for j, (name, vals, cls) in enumerate(series):
            v = float(vals[i])
            h = max(0, v / mx * ph)
            parts.append(f'<rect x="{gx + j * bw:.1f}" y="{PT + ph - h:.1f}" width="{bw - 2:.1f}" height="{h:.1f}" rx="2" class="{cls}">'
                         f'<title>{escape(name)} {escape(str(lab))}: {v:,.0f}</title></rect>')
        parts.append(f'<text x="{PL + gw * i + gw / 2:.1f}" y="{H - 10}" text-anchor="middle" class="ch-lbl">{escape(str(lab))}</text>')
    legend = ""
    if len(series) > 1:
        legend = "".join(f'<span class="lg"><i class="{c}"></i>{escape(n)}</span>' for n, _, c in series)
        legend = f'<div class="legend">{legend}</div>'
    return mark_safe(f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="{escape(title)}">{"".join(parts)}</svg>{legend}')


def line_chart(labels, values, title="Trend"):
    if not labels or not any(values):
        return _empty()
    mx = _nice_max(max(float(v) for v in values))
    ph, pw = H - PT - PB, W - PL - PR
    n = len(values)
    pts = [(PL + (pw * i / (n - 1) if n > 1 else pw / 2), PT + ph - float(v) / mx * ph) for i, v in enumerate(values)]
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(pts))
    area = path + f" L{pts[-1][0]:.1f},{PT + ph} L{pts[0][0]:.1f},{PT + ph} Z"
    parts = [_axes(mx), f'<path d="{area}" class="ch-area"/>', f'<path d="{path}" class="ch-line" fill="none"/>']
    for (x, y), v, lab in zip(pts, values, labels):
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" class="ch-dot"><title>{escape(str(lab))}: {float(v):,.0f}</title></circle>'
                     f'<text x="{x:.1f}" y="{H - 10}" text-anchor="middle" class="ch-lbl">{escape(str(lab))}</text>')
    return mark_safe(f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="{escape(title)}">{"".join(parts)}</svg>')


def hbar_chart(items, title="Breakdown"):
    """items: [(label, value, css_class)]"""
    if not items or not any(i[1] for i in items):
        return _empty("No arrears")
    mx = _nice_max(max(float(i[1]) for i in items))
    rowh = 32
    h = rowh * len(items) + 10
    left = 110
    parts = []
    for k, (lab, v, cls) in enumerate(items):
        y = 6 + k * rowh
        w = float(v) / mx * (W - left - 70)
        parts.append(f'<text x="{left - 8}" y="{y + 16}" text-anchor="end" class="ch-lbl">{escape(str(lab))}</text>'
                     f'<rect x="{left}" y="{y + 4}" width="{max(w, 1):.1f}" height="18" rx="2" class="{cls}"><title>{escape(str(lab))}: {float(v):,.0f}</title></rect>'
                     f'<text x="{left + w + 6:.1f}" y="{y + 17}" class="ch-lbl">{compact(v)}</text>')
    return mark_safe(f'<svg viewBox="0 0 {W} {h}" class="chart" role="img" aria-label="{escape(title)}">{"".join(parts)}</svg>')
