# Deploying: Render (web) + Clever Cloud (MySQL)

The app runs from the included `Dockerfile` because `mysqlclient` needs the MySQL client library, which Render's native Python runtime does not provide.

## 1. Create the MySQL database on Clever Cloud
1. Console > Create > an add-on > **MySQL**. Choose a plan. Note: small plans allow few simultaneous connections; the app is configured with `DB_CONN_MAX_AGE=0` and 2 gunicorn workers to stay within them.
2. Open the add-on's **Information** tab. Copy host, database name, user, password and port.
3. Charset is utf8mb4 by default on current MySQL 8 add-ons.

## 2. Prepare the repository
Push this project to GitHub/GitLab (the `.gitignore` already excludes secrets and local data).

## 3. Create the Render service
Either use the blueprint (New > Blueprint, pick the repo; it reads `render.yaml`) or New > Web Service > Docker, then set:

| Variable | Value |
|---|---|
| `DJANGO_SECRET_KEY` | long random string (Render can generate) |
| `DJANGO_DEBUG` | `False` |
| `DJANGO_ALLOWED_HOSTS` | `your-service.onrender.com` (add your custom domain, comma separated) |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://your-service.onrender.com` |
| `DJANGO_BEHIND_PROXY` / `DJANGO_SECURE_SSL` | `True` (HTTPS redirect, HSTS, secure cookies, `X-Forwarded-Proto`) |
| `DB_ENGINE` | `mysql` |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | from Clever Cloud |
| `DB_CONN_MAX_AGE` | `0` |
| `PRODUCTION_MODE` | `True` (blocks demo reset) |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` | SMTP account that sends verification codes (set these before the first sign-in; with `EMAIL_HOST` set, email verification is on) |
| `BACKUP_ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"` — keep a copy off the server |
| `AUDIT_HMAC_KEY` | another random string; never change it later or the audit chain will fail verification |
| `BOOTSTRAP_ADMIN_USERNAME/EMAIL/PASSWORD` | first administrator (password at least 10 characters) |
| `MEDIA_ROOT` / `BACKUP_DIR` | `/var/data/media` / `/var/data/backups` |

Build command: none (Docker). Start command: none (the image runs `scripts/start.sh`).
If you use the Procfile on another host: `web: bash scripts/start.sh`.

## 4. What happens on each deploy
`scripts/start.sh` runs `python manage.py migrate --noinput`, then `bootstrap_admin` (creates the first admin only if none exists with that username), then gunicorn. Static files are collected at image build time and served by WhiteNoise.

## 5. Persistent disk
Render's disk is **ephemeral** unless you attach a persistent disk (paid plans). Uploaded KYC documents and backups live under `/var/data`. The blueprint attaches a disk there. Without a disk, uploaded files and on-server backups are lost on every deploy; in that case download every backup immediately (Maintenance > Backups).

## 6. Scheduled jobs (Render Cron Jobs, same Docker image and env vars)
* Daily 02:00 `python manage.py make_backup`
* Daily 07:00 `python manage.py send_reminders` (due and overdue alerts; SMS are queued, not sent)
* Penalties are accrued from Loans > Penalties (manager/accountant) so a person reviews each run

## 7. First sign-in
Open the site, sign in as the bootstrap admin, change the password, enrol Google Authenticator, then follow ADMIN_GUIDE.md. Set loan product rates under Loans > Loan products (they are intentionally blank until you set them).

## 8. Verify
`https://your-service.onrender.com/healthz` returns `ok` when the database is reachable. In the app, Maintenance > System health should show all checks passing.

## Known limitations
* The Docker build and a live Clever Cloud connection were not exercised in the development environment (no MySQL server or Docker available there); the code was tested on SQLite. Run the test suite against your MySQL instance once: set the DB variables and run `python manage.py test tests` (the DB user needs permission to create a test database).
* Free Render instances sleep; use a paid instance for a SACCO in daily use.
