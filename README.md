# SACCO Financial Management System (SFMS)

Branded for **Omukazi W’omutima Umoja SACCO**: logo and name on the welcome page, receipts and statements; colours taken from the logo (purple, orange, green). Change the name or upload a different logo under Settings.

A Django 5 / MySQL 8 system for Ugandan SACCOs: member registration and KYC, savings and share capital, a three-stage loan workflow (flat and reducing balance), cashier tills, a double-entry general ledger with trial balance, income statement, balance sheet and cash flow, maker-checker approvals, 20+ exportable reports, a System Maintenance & Extension Centre, a member portal, and mobile-money / SMS integration points. Every transaction writes a balanced journal and a tamper-evident audit row. The front end is plain HTML, CSS and JavaScript with Google Material Symbols and Fonts.

## Quickstart (local, SQLite)

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt                        # mysqlclient needs MySQL client headers; see INSTALL.md
export DJANGO_DEBUG=1 DB_ENGINE=sqlite                 # Windows PowerShell: $env:DJANGO_DEBUG="1"; $env:DB_ENGINE="sqlite"
python manage.py migrate
python manage.py seed_data --no-force-change           # demo staff, members, loans (password Sacco@Demo2026!)
python manage.py runserver
```

Open http://127.0.0.1:8000 and sign in as `cashier`, `loanofficer`, `committee`, `manager`, `accountant`, `auditor` or `admin`. Admin, manager and accountant must enrol Google Authenticator on first sign-in.

Run the tests: `SACCO_TESTING=1 DB_ENGINE=sqlite SQLITE_PATH=:memory: python manage.py test tests`

## Documentation

| File | For |
|---|---|
| INSTALL.md | Local setup on Windows, macOS, Linux |
| DEPLOY.md | Render (app) + Clever Cloud (MySQL) step by step |
| ADMIN_GUIDE.md | Onboarding staff, rates, approvals, tills, backups |
| USER_GUIDE.md | What each role sees and does |
| SECURITY.md | Controls built in and the go-live checklist |
| API.md | JSON endpoints and webhooks |
| BACKUP.md | Backup and restore |

## Project layout

`core` dashboard, theme, settings · `accounts_core` auth, RBAC, 2FA, audit · `members` · `savings` · `loans` · `cashier` · `accounting` · `approvals` · `reports` · `maintenance` · `integrations` · `selfservice` · `notifications` · `tests`

## Honest scope notes

* Mobile money and SMS are wired up but **not connected to any provider**. Payments are never simulated; SMS stays "queued" until a gateway is plugged into `integrations/sms.py`. Search the code for `TODO` to find the exact points.
* The UMRA page is an internal **management summary**, not an official regulatory filing. Confirm the current UMRA return format with the regulator.
* Loan-loss provisioning rates and approval limits are configurable defaults; have your auditors confirm them.
