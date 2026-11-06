# Security

## Controls built in
| Area | Control |
|---|---|
| Config | `DEBUG` off by default; `DJANGO_SECRET_KEY`, hosts and CSRF origins from the environment; the app refuses to start in production without a secret key |
| Transport | HTTPS redirect, HSTS, secure + HttpOnly + SameSite=Lax cookies, `SECURE_PROXY_SSL_HEADER` for Render |
| Headers | Content-Security-Policy (no inline scripts), no-store on pages, X-Frame-Options DENY, nosniff, referrer policy |
| Passwords | Argon2, minimum length and common-password validation, forced change on first login |
| Login | Lockout after 5 failures for 15 minutes (Profile.failed_attempts / locked_until), per-IP throttle, login history with failures |
| Email verification | 6-digit code emailed after first password change (hashed, 15 min expiry, 5 attempts, 60 s resend throttle); address changes need re-verification |
| 2FA | TOTP (Google Authenticator) mandatory for admin, manager, accountant; gate is enforced by middleware on every page |
| Sessions | 10 minute idle timeout, warning at 9 minutes, server-side check |
| Authorisation | Role permission matrix (`accounts_core/rbac.py`), `perm_required` on every view; staff pages cannot be reached without passing the 2FA gate |
| Maker-checker | Loan stages need different people; approvals queue blocks self-approval; thresholds are configurable |
| Ledger | Double-entry enforced on every post; posted journals and lines cannot be edited or deleted (model + queryset guards); corrections are approved reversal entries |
| Audit | Append-only audit log; each row is HMAC-chained to the previous one; Verify chain detects edits, deletions and truncation |
| Data | Parameterised ORM only (no string-built SQL), Decimal money, PROTECT foreign keys, National ID encrypted (Fernet) with blind-hash for duplicate detection, PII masked by permission |
| Uploads | Extension allow-list and 5 MB limit; files stored outside static, served through permission-checked views |
| Backups | Gzip + Fernet encryption, SHA-256 checksum, verify action, restore refuses non-empty databases |
| Integrations | Webhooks accepted only with a valid HMAC signature; payments are never simulated; secrets live in environment variables |
| Demo safety | Demo reset is blocked when `PRODUCTION_MODE=True` |

## Go-live checklist
1. `DJANGO_DEBUG=False`, a new random `DJANGO_SECRET_KEY`, `AUDIT_HMAC_KEY` and `BACKUP_ENCRYPTION_KEY` (store copies in a password manager, not in the repo).
2. `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS` list only your real domain.
3. `PRODUCTION_MODE=True`. Do **not** run `seed_data` in production.
4. Use a dedicated MySQL user for the app; restrict by IP where Clever Cloud allows; enable provider backups.
5. Change every default password; confirm every admin/manager/accountant has enrolled 2FA.
6. Set approval limits and provisioning percentages with the finance committee.
7. Attach a persistent disk, schedule `make_backup`, download and test-restore a backup.
8. Run `python manage.py check --deploy` with production variables; run the test suite against MySQL.
9. Review Maintenance > System health and run Audit > Verify chain.
10. Decide how long to retain data and how members can request their data (member page > Export personal data) in line with the Uganda Data Protection and Privacy Act, 2019. Get legal advice for your SACCO.
11. Commission an independent penetration test before holding real member funds.

## Known limits
* Security controls were tested through the automated suite and manual route checks on SQLite; no external penetration test was done.
* Rate limiting uses Django's cache (per process). For several instances configure a shared cache such as Redis.
* The CSP allows Google Fonts. Material Symbols and fonts load from Google; self-host them if you need an offline/strict-network install.
