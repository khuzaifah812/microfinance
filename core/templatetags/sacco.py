import re

from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from core.middleware import get_current_request
from core.ui import badge_class
from core.utils import fmt_money, fmt_ugx

register = template.Library()


@register.filter
def ugx(v):
    try:
        return fmt_ugx(v)
    except Exception:
        return v


@register.filter
def num(v):
    try:
        return fmt_money(v)
    except Exception:
        return v


@register.filter
def badge(v):
    return badge_class(v)


@register.filter
def get_item(d, k):
    try:
        return d.get(k)
    except Exception:
        return None


def _can_see_pii():
    from accounts_core.rbac import can
    req = get_current_request()
    return bool(req and can(req.user, "members.view_pii"))


@register.filter
def mask_phone(v):
    """07** **** 45 unless the viewer holds members.view_pii."""
    v = str(v or "")
    if not v or _can_see_pii():
        return v
    d = re.sub(r"\D", "", v)
    return f"{d[:2]}** **** {d[-2:]}" if len(d) >= 6 else "** ****"


@register.filter
def mask_nin(v):
    v = str(v or "")
    if not v or _can_see_pii():
        return v
    return f"{v[:2]}{'*' * max(len(v) - 4, 4)}{v[-2:]}"


@register.simple_tag
def icon(name, cls=""):
    return format_html('<span class="material-symbols-outlined {}" aria-hidden="true">{}</span>', cls, name)


@register.inclusion_tag("_form_fields.html")
def render_form(form):
    return {"form": form}


@register.filter
def pct(v):
    try:
        return f"{float(v):.1f}%"
    except Exception:
        return v
