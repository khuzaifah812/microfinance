# Production image for Render. mysqlclient needs the MySQL client headers, which Render's
# native Python runtime does not provide, so we build with Docker.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential pkg-config default-libmysqlclient-dev libjpeg62-turbo-dev zlib1g-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN chmod +x scripts/start.sh \
 && useradd --create-home --uid 10001 sacco \
 && mkdir -p /var/data/media /var/data/backups \
 && chown -R sacco:sacco /app /var/data

# Build-time only values so collectstatic can import settings. Real values come from the environment at run time.
RUN DJANGO_SECRET_KEY=build-only-not-a-secret DB_ENGINE=sqlite SQLITE_PATH=/tmp/build.sqlite3 python manage.py collectstatic --noinput

USER sacco
EXPOSE 10000
CMD ["scripts/start.sh"]
