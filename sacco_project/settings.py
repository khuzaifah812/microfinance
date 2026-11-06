"""SACCO Financial Management System - settings (12-factor, driven by environment)."""
import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    return str(env(name, str(default))).strip().lower() in ("1", "true", "yes", "on")


def env_list(name, default=""):
    return [x.strip() for x in str(env(name, default)).split(",") if x.strip()]


TESTING = "test" in sys.argv or env_bool("SACCO_TESTING")
DEBUG = env_bool("DJANGO_DEBUG", False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if DEBUG or TESTING:
        SECRET_KEY = "insecure-dev-only-key-do-not-use-in-production-0123456789abcdef"
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set in the environment.")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
if env("RENDER_EXTERNAL_HOSTNAME"):
    ALLOWED_HOSTS.append(env("RENDER_EXTERNAL_HOSTNAME"))
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "")
if env("RENDER_EXTERNAL_HOSTNAME"):
    CSRF_TRUSTED_ORIGINS.append("https://" + env("RENDER_EXTERNAL_HOSTNAME"))

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "core",
    "accounts_core",
    "members",
    "savings",
    "accounting",
    "loans",
    "cashier",
    "approvals",
    "reports",
    "maintenance",
    "integrations",
    "selfservice",
    "notifications",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.RequestContextMiddleware",
    "accounts_core.middleware.IdleTimeoutMiddleware",
    "accounts_core.middleware.SecurityGateMiddleware",
    "core.middleware.SecurityHeadersMiddleware",
]

ROOT_URLCONF = "sacco_project.urls"
WSGI_APPLICATION = "sacco_project.wsgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "core.context.site_context",
    ], "builtins": ["core.templatetags.sacco"]},
}]

# ---------------------------------------------------------------- Database
DB_ENGINE = env("DB_ENGINE", "mysql").lower()
if DB_ENGINE == "sqlite":
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3",
                             "NAME": env("SQLITE_PATH", str(BASE_DIR / "db.sqlite3"))}}
else:
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": env("DB_NAME", env("MYSQL_ADDON_DB", "")),
        "USER": env("DB_USER", env("MYSQL_ADDON_USER", "")),
        "PASSWORD": env("DB_PASSWORD", env("MYSQL_ADDON_PASSWORD", "")),
        "HOST": env("DB_HOST", env("MYSQL_ADDON_HOST", "localhost")),
        "PORT": env("DB_PORT", env("MYSQL_ADDON_PORT", "3306")),
        "CONN_MAX_AGE": int(env("DB_CONN_MAX_AGE", "0")),  # Clever Cloud small plans allow few connections
        "OPTIONS": {"charset": "utf8mb4",
                    "init_command": "SET sql_mode='STRICT_TRANS_TABLES', time_zone='+00:00'"},
    }}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------- Auth / passwords
AUTH_PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
if TESTING:
    AUTH_PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
OTP_TOTP_ISSUER = "Umoja SACCO"

# ---------------------------------------------------------------- Sessions / cookies
SESSION_COOKIE_AGE = 60 * 60 * 8          # absolute cap; idle timeout is enforced separately
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = "Lax"
IDLE_TIMEOUT_SECONDS = 600                # 10 minutes
IDLE_WARNING_SECONDS = 540                # warn at 9 minutes
LOCKOUT_THRESHOLD = 5
LOCKOUT_MINUTES = 15

SECURE_SSL = env_bool("DJANGO_SECURE_SSL", not DEBUG) and not TESTING
if SECURE_SSL:
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_REDIRECT_EXEMPT = [r"^healthz/?$"]
if env_bool("DJANGO_BEHIND_PROXY", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True
TRUST_PROXY_IP = env_bool("DJANGO_BEHIND_PROXY", False)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024

# ---------------------------------------------------------------- i18n
LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Africa/Kampala"
USE_I18N = False
USE_TZ = True

# ---------------------------------------------------------------- Static / media
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage" if (DEBUG or TESTING)
                    else "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
MEDIA_ROOT = Path(env("MEDIA_ROOT", str(BASE_DIR / "media")))   # private: served only by permission-checked views
BACKUP_DIR = Path(env("BACKUP_DIR", str(BASE_DIR / "backups")))
MAX_UPLOAD_BYTES = 5 * 1024 * 1024

# ---------------------------------------------------------------- Email
if env("EMAIL_HOST"):
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = env("EMAIL_HOST")
    EMAIL_PORT = int(env("EMAIL_PORT", "587"))
    EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
    EMAIL_USE_TLS = True
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", env("EMAIL_HOST_USER") or "noreply@sacco.local")
# Staff must confirm their email with a 6-digit code after the first password change. On by default only when a real
# mail server is configured (otherwise nobody could receive the code); set EMAIL_VERIFICATION=True to force it.
EMAIL_VERIFICATION = env_bool("EMAIL_VERIFICATION", bool(env("EMAIL_HOST")))
EMAIL_CODE_MINUTES = 15
EMAIL_CODE_MAX_ATTEMPTS = 5
EMAIL_CODE_RESEND_SECONDS = 60

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# ---------------------------------------------------------------- App settings
PRODUCTION_MODE = env_bool("PRODUCTION_MODE", True)
AUDIT_HMAC_KEY = env("AUDIT_HMAC_KEY") or SECRET_KEY
BACKUP_ENCRYPTION_KEY = env("BACKUP_ENCRYPTION_KEY", "")
CURRENCY = "UGX"

LOGGING = {
    "version": 1, "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {"sacco": {"handlers": ["console"], "level": "INFO", "propagate": False}},
}
