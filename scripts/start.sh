#!/usr/bin/env bash
# Start script used by Render (Docker) and any Procfile-style host.
set -euo pipefail

echo "[start] applying database migrations"
python manage.py migrate --noinput

echo "[start] checking first administrator"
python manage.py bootstrap_admin || true      # creates the admin only if BOOTSTRAP_ADMIN_PASSWORD is set and no admin exists

echo "[start] starting gunicorn on port ${PORT:-10000}"
exec gunicorn sacco_project.wsgi:application \
  --bind "0.0.0.0:${PORT:-10000}" \
  --workers "${WEB_CONCURRENCY:-2}" \
  --timeout 60 \
  --access-logfile - \
  --error-logfile -
