"""Role-based access control. Roles map to permission strings; views declare what they need."""
from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

ROLES = [
    ("admin", "System Administrator"),
    ("manager", "General Manager"),
    ("accountant", "Accountant"),
    ("cashier", "Cashier"),
    ("loan_officer", "Loan Officer"),
    ("committee", "Credit Committee"),
    ("auditor", "Internal Auditor"),
]
ROLE_LABEL = dict(ROLES)
TWO_FA_ROLES = {"admin", "manager", "accountant"}

_VIEW = ["dashboard.view", "members.view", "savings.view", "loans.view", "accounting.view", "reports.view", "notifications.view"]

ROLE_PERMS = {
    "admin": set(_VIEW) | {
        "members.manage", "members.view_pii", "members.export", "savings.open", "loans.products", "reports.export",
        "reports.umra_config", "approvals.view", "approvals.configure", "audit.view", "users.manage",
        "maintenance.manage", "integrations.manage", "portal.manage", "accounting.manage_coa", "cashier.view_all",
        "integrations.view",
    },
    "manager": set(_VIEW) | {
        "members.manage", "members.view_pii", "members.export", "savings.open", "savings.transact", "savings.interest",
        "loans.products", "loans.apply", "loans.manager", "loans.disburse", "loans.repay", "loans.reschedule",
        "loans.settle", "loans.penalties", "approvals.view", "approvals.decide", "reports.export", "reports.umra_config",
        "audit.view", "cashier.view_all", "portal.manage", "integrations.view", "accounting.provision", "accounting.journal",
    },
    "accountant": set(_VIEW) | {
        "members.view_pii", "savings.transact", "savings.interest", "loans.disburse", "loans.repay", "loans.settle",
        "loans.penalties", "loans.reschedule", "accounting.post", "accounting.journal", "accounting.reverse", "accounting.provision",
        "accounting.reconcile", "accounting.manage_coa", "approvals.view", "approvals.decide", "reports.export", "cashier.view_all", "integrations.view",
    },
    "cashier": {"dashboard.view", "members.view", "members.manage", "savings.view", "savings.open", "savings.transact",
                "loans.view", "loans.apply", "loans.repay", "loans.disburse", "cashier.till", "accounting.post",
                "approvals.view", "reports.view", "notifications.view", "portal.manage"},
    "loan_officer": {"dashboard.view", "members.view", "members.manage", "savings.view", "loans.view", "loans.apply",
                     "loans.officer", "reports.view", "notifications.view", "approvals.view"},
    "committee": {"dashboard.view", "members.view", "loans.view", "loans.committee", "reports.view", "notifications.view"},
    "auditor": set(_VIEW) | {"approvals.view", "audit.view", "reports.export", "cashier.view_all"},
}


def get_role(user):
    if not user.is_authenticated:
        return None
    p = getattr(user, "profile", None)
    if p is None:
        return "admin" if user.is_superuser else None
    return p.role


def perms_for(user):
    return ROLE_PERMS.get(get_role(user), set())


def can(user, perm):
    return user.is_authenticated and user.is_active and perm in perms_for(user)


def perm_required(*perms):
    """Allow if the user holds ANY of the listed permissions."""
    def deco(view):
        @wraps(view)
        def wrapped(request, *a, **kw):
            if not request.user.is_authenticated:
                return redirect(f"/login/?next={request.path}")
            if not any(can(request.user, p) for p in perms):
                from accounts_core.audit import audit
                audit("access.denied", "security", detail=f"{request.method} {request.path} needs {perms}")
                raise PermissionDenied
            return view(request, *a, **kw)
        return wrapped
    return deco
