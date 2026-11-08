#!/usr/bin/env bash
# Start script used by Render, ApexWeave (Docker) and any Procfile-style host.
set -euo pipefail

echo "[start] applying database migrations"
python manage.py migrate --noinput

echo "[start] checking first administrator"
python manage.py bootstrap_admin || true      # creates the admin only if BOOTSTRAP_ADMIN_PASSWORD is set and no admin exists

# DEMO SITE ONLY: a throw-away SQLite database that is re-filled with the Board demo data on every start.
# The guard on DB_ENGINE=sqlite means this can never touch a real MySQL database.
if [ "${SEED_DEMO_ON_START:-}" = "1" ] && [ "${DB_ENGINE:-}" = "sqlite" ]; then
  echo "[start] loading Board demo data (demo site)"
  python manage.py seed_board_demo --admin-username "${DEMO_ADMIN_USERNAME:-demo}" --admin-password "${DEMO_ADMIN_PASSWORD:-}" \
    ${DEMO_ADMIN_EMAIL:+--admin-email "$DEMO_ADMIN_EMAIL"} || true
fi

# ONE-TIME LOAD of the Board demo into the live database, run from inside the host (fast: the database is close).
# Safe by design: seed_board_demo refuses to run if any members already exist (no --force is passed),
# so a redeploy with the flag still on cannot erase or duplicate anything. Remove the flag after it has run.
if [ "${SEED_DEMO_ONCE:-}" = "1" ]; then
  echo "[start] one-time Board demo load (this takes a few minutes)"
  python manage.py seed_board_demo --admin-username "${DEMO_ADMIN_USERNAME:-kamcoder}" --admin-password "${DEMO_ADMIN_PASSWORD:-}" \
    ${DEMO_ADMIN_EMAIL:+--admin-email "$DEMO_ADMIN_EMAIL"} || echo "[start] demo load skipped or failed (see message above)"
fi

echo "[start] starting gunicorn on port ${PORT:-10000}"
exec gunicorn sacco_project.wsgi:application \
  --bind "0.0.0.0:${PORT:-10000}" \
  --workers "${WEB_CONCURRENCY:-2}" \
  --threads "${GUNICORN_THREADS:-1}" \
  --timeout 60 \
  --access-logfile - \
  --error-logfile -