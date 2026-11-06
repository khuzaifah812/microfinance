# Administrator guide

## First day
1. Sign in with the bootstrap administrator. You are forced to change the password and scan the QR code with Google Authenticator (admin, manager and accountant always use 2FA).
2. **Settings** (sidebar > Settings): set the SACCO name, short name, address, phone, tagline and upload the logo (PNG/JPG, max 5 MB). Policy values (minimum savings balance, provisioning percentages, idle limits) are on the same page.
3. **Chart of accounts**: review the accounts created for you; add accounts as needed. System accounts cannot be removed.
4. **Approval limits** (Approvals > Approval limits): set the amount above which withdrawals, expenses, reschedules, reversals, manual journals and share transfers need a second person.

## Onboarding staff
Users > New user. Choose the role; the system generates a temporary password shown once. The person must change it at first sign-in. Roles: System Administrator, General Manager, Accountant, Cashier, Loan Officer, Credit Committee, Internal Auditor. Never share accounts; every action is attributed to a person. To help a locked-out user use the user's page: Unlock, Reset password, Reset 2FA. Accounts lock for 15 minutes after 5 failed sign-ins.

## Email verification codes
When a mail server is configured (`EMAIL_HOST` set), every staff member must confirm their email once: after the first password change the system emails a 6-digit code (valid 15 minutes, five attempts, one resend a minute). Codes are stored hashed. A user with no email is asked for one. If you correct someone's address under Users > Edit, they must verify the new address. If mail is down or an address was typed wrongly, rescue from the server: `python manage.py verify_email <username> [--email new@address] [--unverified]`. Set up the mail server before creating real accounts, and test it with your own account first.

## Setting interest rates
Loans > Loan products. Products are created with **no rate**; they show "Rate not set" and cannot be used for applications. Edit a product, enter the annual rate, interest method (flat or reducing balance), limits, fees, penalty %, guarantors required. Changing a product later does **not** change existing loans: each loan keeps the terms it was approved on.

## Loan workflow
Applied by a cashier/loan officer > Loan Officer review > Credit Committee > General Manager > disbursement by someone who did not approve (accountant/manager). Nobody can approve a loan they captured, and nobody can approve twice. Guarantors reserve part of their savings; the reserve is released when the loan clears. Use Early settlement, Reschedule (needs approval) or Top-up from the loan page.

## Tills
Cashier: Cashier > Open till with a float. Cash deposits and repayments are recorded against the till. At close the cashier counts notes and coins; a difference needs a written reason and is posted to the shortage/overage accounts and notified to the manager and accountant. Managers/auditors see all tills.

## Approvals
Approvals queue lists held items. Approve or reject (a rejection needs a reason). The person who submitted a request can never decide it.

## Reports
Reports menu: 20+ reports, each exportable as PDF, Excel or CSV by users with export permission. Exports are written to the audit log.

## Modules and extensions
Maintenance > Modules. Built-in modules are always available. A custom module must pass four gates (developer tested, security reviewed, migration applied, administrator approval) before **Activate** works; it then appears in the sidebar only for the roles assigned to it. Configuration can be exported/imported; imports never switch a module on.

## Backups
Maintenance > Backups > Create backup now, then Download and store off-site. Schedule `make_backup` daily. See BACKUP.md.

## Audit
Audit trail shows every recorded action; **Verify chain** re-computes the tamper-evident hash chain. Run it weekly and after any restore.
