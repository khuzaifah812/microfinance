import gzip
import hashlib
import io
import json
import logging
import shutil
import sys
from datetime import timedelta
from pathlib import Path

import django
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.utils import timezone

from accounts_core.audit import audit, verify_chain
from accounts_core.rbac import ROLE_PERMS, ROLES
from core.models import SystemSetting

from .models import BackupRecord, FailedOperation, Module, ModuleRole

log = logging.getLogger("sacco")

# key, name, icon, category, url_name, permission(s) (any-of, "|" separated), order, description
BUILTIN = [
    ("dashboard", "Dashboard", "dashboard", "overview", "core:dashboard", "dashboard.view", 1, "Live financial overview"),
    ("members", "Members", "groups", "members", "members:list", "members.view", 10, "Registration, KYC, documents and statements"),
    ("savings", "Savings & shares", "savings", "members", "savings:list", "savings.view", 11, "Accounts, deposits, withdrawals, interest"),
    ("loans", "Loans", "request_quote", "lending", "loans:list", "loans.view", 20, "Applications, approvals, disbursement, repayment"),
    ("loan_products", "Loan products", "inventory_2", "lending", "loans:products", "loans.view", 21, "Product catalogue and rates"),
    ("cashier", "Cashier & tills", "point_of_sale", "lending", "cashier:home", "cashier.till|cashier.view_all", 22, "Till opening, closing, denominations"),
    ("accounting", "Journal & ledger", "menu_book", "finance", "accounting:journals", "accounting.view", 30, "General ledger and journals"),
    ("vouchers", "Income & expenses", "receipt_long", "finance", "accounting:vouchers", "accounting.view", 31, "Payments, receipts, payables, receivables"),
    ("coa", "Chart of accounts", "account_tree", "finance", "accounting:coa", "accounting.view", 32, "Account structure"),
    ("banking", "Banks & reconciliation", "account_balance", "finance", "accounting:banks", "accounting.view", 33, "Bank accounts and reconciliations"),
    ("approvals", "Approvals", "task_alt", "control", "approvals:queue", "approvals.view", 40, "Maker-checker queue"),
    ("reports", "Reports", "bar_chart", "control", "reports:index", "reports.view", 41, "Statements, regulatory and operational reports"),
    ("audit", "Audit trail", "policy", "control", "accounts:audit", "audit.view", 42, "Tamper-evident activity log"),
    ("integrations", "Mobile money & SMS", "sync_alt", "channels", "integrations:home", "integrations.view", 50, "MTN, Airtel and SMS integration"),
    ("portal_admin", "Member portal & USSD", "smartphone", "channels", "channels:portal_admin", "portal.manage", 51, "Member self-service access and USSD preview"),
    ("users", "Staff & access", "admin_panel_settings", "admin", "accounts:users", "users.manage", 60, "Staff accounts, roles, sign-in security"),
    ("maintenance", "Maintenance centre", "build_circle", "admin", "maintenance:home", "maintenance.manage", 61, "Modules, health, backups, notices"),
]


def _roles_with(permspec):
    perms = permspec.split("|")
    return [r for r, _ in ROLES if any(p in ROLE_PERMS[r] for p in perms)]


def seed_defaults(sender=None, **kw):
    from accounting.services import ensure_coa
    from approvals.services import ensure_rules
    from core.models import SiteConfig
    from integrations.models import ProviderConfig
    from .models import DocPage
    ensure_coa()
    ensure_rules()
    SiteConfig.get()
    for p, _ in ProviderConfig.PROVIDERS:
        ProviderConfig.objects.get_or_create(provider=p)
    for key, name, icon, cat, url, perm, order, desc in BUILTIN:
        m, created = Module.objects.get_or_create(key=key, defaults=dict(
            name=name, icon=icon, category=cat, url_name=url, required_perm=perm, sort_order=order, description=desc,
            builtin=True, enabled=True, status="active", dev_tested=True, security_reviewed=True, migrated=True))
        if created:
            for r in _roles_with(perm):
                ModuleRole.objects.get_or_create(module=m, role=r)
    if not DocPage.objects.exists():
        DocPage.objects.create(title="Operating procedures", slug="operating-procedures", body=(
            "Daily: cashiers open tills, post transactions, count denominations and close tills. Explain every difference.\n\n"
            "Weekly: accountant reviews pending approvals, bank reconciliation and arrears. Run penalty accrual.\n\n"
            "Monthly: post savings interest, review provisioning, export management reports.\n\n"
            "Backups: create and download an encrypted backup daily; store copies off-site, separate from the database host."))
        DocPage.objects.create(title="Change management for new modules", slug="new-modules", body=(
            "A custom module may only be activated after: (1) developer testing, (2) security review, (3) database migration applied "
            "and (4) administrator approval. Registering a module in this centre never grants it access to data by itself; "
            "role access is configured per module and enforced in code by the module's own permission checks."))


def log_failure(operation, error, context=None, user=None):
    try:
        FailedOperation.objects.create(operation=operation[:80], error=str(error)[:4000], context=context or {}, user=user)
    except Exception:
        log.exception("could not record failure")


# ------------------------------------------------------------------ navigation
def nav_for(user, role):
    if not role:
        return []
    from accounts_core.rbac import can
    mods = (Module.objects.filter(enabled=True, roles__role=role).order_by("category", "sort_order", "name").distinct())
    groups = {}
    cat_labels = dict(__import__("maintenance.models", fromlist=["CATEGORIES"]).CATEGORIES)
    for m in mods:
        if m.builtin and m.required_perm and not any(can(user, p) for p in m.required_perm.split("|")):
            continue
        groups.setdefault(m.category, {"label": cat_labels.get(m.category, m.category), "items": []})["items"].append(m)
    return list(groups.values())


# ------------------------------------------------------------------ backups
def _fernet():
    key = settings.BACKUP_ENCRYPTION_KEY
    if not key:
        return None
    return Fernet(key.encode() if isinstance(key, str) else key)


def create_backup(user=None):
    f = _fernet()
    if f is None and settings.PRODUCTION_MODE:
        raise RuntimeError("BACKUP_ENCRYPTION_KEY is not set. Encrypted backups are mandatory in production.")
    buf = io.StringIO()
    call_command("dumpdata", exclude=["contenttypes", "sessions", "auth.permission"], indent=None, stdout=buf)
    raw = buf.getvalue().encode()
    count = len(json.loads(raw))
    blob = gzip.compress(raw)
    encrypted = f is not None
    if encrypted:
        blob = f.encrypt(blob)
    Path(settings.BACKUP_DIR).mkdir(parents=True, exist_ok=True)
    name = f"sfms-backup-{timezone.localtime():%Y%m%d-%H%M%S}.sfmsbak"
    path = Path(settings.BACKUP_DIR) / name
    path.write_bytes(blob)
    rec = BackupRecord.objects.create(filename=name, size_bytes=len(blob), sha256=hashlib.sha256(blob).hexdigest(), encrypted=encrypted,
                                      records=count, created_by=user)
    audit("backup.create", "maintenance", "BackupRecord", rec.pk, name, new={"size": len(blob), "records": count, "encrypted": encrypted}, user=user)
    verify_backup(rec, user)
    return rec


def read_backup_json(rec):
    blob = (Path(settings.BACKUP_DIR) / rec.filename).read_bytes()
    if hashlib.sha256(blob).hexdigest() != rec.sha256:
        raise ValueError("Checksum mismatch: file was altered or corrupted.")
    if rec.encrypted:
        f = _fernet()
        if f is None:
            raise ValueError("BACKUP_ENCRYPTION_KEY is not configured.")
        blob = f.decrypt(blob)
    return gzip.decompress(blob)


def verify_backup(rec, user=None):
    try:
        data = json.loads(read_backup_json(rec))
        ok = len(data) == rec.records
        err = "" if ok else "record count differs"
    except (InvalidToken, ValueError, OSError, Exception) as e:  # noqa
        ok, err = False, str(e) or e.__class__.__name__
    rec.verified_at, rec.verified_ok = timezone.now(), ok
    rec.save(update_fields=["verified_at", "verified_ok"])
    audit("backup.verify", "maintenance", "BackupRecord", rec.pk, rec.filename, detail=("PASS" if ok else f"FAIL {err}"), user=user)
    if not ok:
        log_failure("backup.verify", err, {"file": rec.filename}, user)
    return ok, err


def last_backup_age_hours():
    b = BackupRecord.objects.filter(verified_ok=True).first()
    if not b:
        return None
    return (timezone.now() - b.created_at).total_seconds() / 3600


# ------------------------------------------------------------------ health
def health_report():
    from accounting.services import unbalanced_journals
    from django.db.migrations.executor import MigrationExecutor
    from accounts_core.models import Profile
    checks = []

    def add(name, ok, detail, warn=False):
        checks.append({"name": name, "status": "ok" if ok else ("warn" if warn else "bad"), "detail": detail})
    try:
        import time
        t = time.time()
        connection.ensure_connection()
        with connection.cursor() as c:
            c.execute("SELECT 1")
        add("Database connection", True, f"Responding in {(time.time() - t) * 1000:.0f} ms ({connection.vendor})")
    except Exception as e:
        add("Database connection", False, str(e))
    try:
        plan = MigrationExecutor(connection).migration_plan(MigrationExecutor(connection).loader.graph.leaf_nodes())
        add("Database migrations", not plan, "All applied" if not plan else f"{len(plan)} pending migration(s)")
    except Exception as e:
        add("Database migrations", False, str(e))
    ok, n, problems = verify_chain(limit=5000)
    add("Audit trail integrity", ok, f"{n} records verified" if ok else problems[0])
    ub = unbalanced_journals()
    add("Ledger balance", not ub, "Every journal balances" if not ub else f"{len(ub)} unbalanced journal(s)!")
    age = last_backup_age_hours()
    limit = float(SystemSetting.get("backup_reminder_hours") or 24)
    add("Latest verified backup", age is not None and age <= limit,
        "No verified backup yet" if age is None else f"{age:.1f} hours ago", warn=True)
    try:
        du = shutil.disk_usage(settings.BACKUP_DIR if Path(settings.BACKUP_DIR).exists() else settings.BASE_DIR)
        pct = du.free / du.total * 100
        add("Disk space", pct > 10, f"{du.free / 1e9:.1f} GB free ({pct:.0f}%)", warn=True)
    except Exception as e:
        add("Disk space", False, str(e), warn=True)
    fails = FailedOperation.objects.filter(resolved=False).count()
    add("Unresolved failed operations", fails == 0, f"{fails} open", warn=True)
    add("Debug mode off", not settings.DEBUG, "DEBUG is off" if not settings.DEBUG else "DEBUG is ON - disable before go-live")
    add("HTTPS enforced", bool(getattr(settings, "SECURE_SSL", False)) or settings.DEBUG, "HTTPS redirect, HSTS and secure cookies active"
        if getattr(settings, "SECURE_SSL", False) else "HTTPS redirect is off", warn=True)
    add("Backup encryption key", bool(settings.BACKUP_ENCRYPTION_KEY), "Configured" if settings.BACKUP_ENCRYPTION_KEY else "Missing - set BACKUP_ENCRYPTION_KEY", warn=True)
    from django_otp.plugins.otp_totp.models import TOTPDevice
    need = Profile.objects.filter(role__in=["admin", "manager", "accountant"], user__is_active=True)
    miss = [p.user.username for p in need if not TOTPDevice.objects.filter(user=p.user, confirmed=True).exists()]
    add("2FA enrolment (admin/manager/accountant)", not miss, "All enrolled" if not miss else "Not enrolled: " + ", ".join(miss), warn=True)
    locked = Profile.objects.filter(locked_until__gt=timezone.now()).count()
    add("Locked accounts", locked == 0, f"{locked} locked", warn=True)
    return checks


def app_info():
    return {"python": sys.version.split()[0], "django": django.get_version(), "db": connection.vendor}
