"""
Django base settings for CondoParkShare.

All environment-specific settings override this file.
No secrets here — all sensitive values come from environment variables.
"""

import base64
import hashlib
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
    EMAIL_BACKEND=(str, "anymail.backends.brevo.EmailBackend"),
    DJANGO_ENV=(str, "unknown"),
)

# Read .env if it exists (dev convenience only — production uses real env vars)
environ.Env.read_env(BASE_DIR / ".env", overwrite=False)

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

SECRET_KEY = env("SECRET_KEY")

DEBUG = env("DEBUG")

ALLOWED_HOSTS = env("ALLOWED_HOSTS")

ROOT_URLCONF = "parkshare.urls"

WSGI_APPLICATION = "parkshare.wsgi.application"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"

# Suppress auth.E003: USERNAME_FIELD (email) is not unique at the column level by design.
# Multi-tenant: the same email address may exist in multiple organisations.
# Uniqueness is enforced via unique_together = [('organization', 'email')].
# Suppress django_ratelimit.E003: locmem cache is not shared across processes but is
# correct for single-process dev/test.  Production deployments must configure Redis via
# CACHES and remove this suppression.
SILENCED_SYSTEM_CHECKS = ["auth.E003", "django_ratelimit.E003", "django_ratelimit.W001"]
# auth.E003 — email USERNAME_FIELD uniqueness is enforced via unique_together, not column UNIQUE.
# django_ratelimit.E003/W001 — locmem cache is not shared across workers; acceptable for
# dev/test (single-process). production.py overrides this list to restore these checks
# and requires a shared cache (Redis) via CACHE_URL.

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------

INSTALLED_APPS = [
    # Django contrib
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    # Third-party
    "django_otp",
    "django_otp.plugins.otp_totp",
    "django_otp.plugins.otp_static",
    "encrypted_model_fields",
    "anymail",
    "django_ratelimit",
    "csp",
    # Project apps — accounts MUST precede parking (User model dependency)
    "accounts",
    "parking",
    "notifications",
    "portal",
    "operator_console",
]

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "csp.middleware.CSPMiddleware",
    "parkshare.middleware.RatelimitMiddleware",
    "parkshare.middleware.TenantMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    # XFrameOptionsMiddleware removed — frame-ancestors in CSP is the authoritative control.
    "parkshare.middleware.ImpersonationMiddleware",
]

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "parkshare.context_processors.impersonation",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

DATABASES = {
    "default": env.db("DATABASE_URL"),
}

# ---------------------------------------------------------------------------
# Cache — used by django-ratelimit
# ---------------------------------------------------------------------------
# Default: in-process memory cache (functionally correct for dev/single-process).
# Production deployments must override with a shared cache (Redis recommended)
# and remove the django_ratelimit.E003 suppression from SILENCED_SYSTEM_CHECKS.

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

# ---------------------------------------------------------------------------
# Password hashing — Argon2 first
# ---------------------------------------------------------------------------

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# ---------------------------------------------------------------------------
# PII field encryption (django-encrypted-model-fields)
# ---------------------------------------------------------------------------


def _fernet_key(raw: str) -> str:
    """Return *raw* unchanged if it is already a valid 32-byte url-safe base64
    Fernet key; otherwise derive one deterministically via SHA-256.  This lets
    CI / smoke-test environments pass a plain memorable string without having to
    generate a proper Fernet key, while production environments continue to use
    a real key.

    In production (DEBUG=False) the SHA-256 derivation path is rejected: a
    developer who sets PII_ENCRYPTION_KEY=testkey in a production .env would
    silently receive a deterministic, low-entropy key — that failure mode is
    blocked here instead of silently encrypting PII with a weak key."""
    try:
        decoded = base64.urlsafe_b64decode(raw + "==")
        if len(decoded) == 32:
            return raw
    except Exception:
        pass

    # raw is not a valid Fernet key — derive one only in non-production.
    if not DEBUG:
        raise ImproperlyConfigured(
            "PII_ENCRYPTION_KEY must be a valid 32-byte url-safe base64 Fernet key "
            'in production. Generate one with: python -c "from cryptography.fernet '
            'import Fernet; print(Fernet.generate_key().decode())"'
        )

    derived = hashlib.sha256(raw.encode()).digest()
    return base64.urlsafe_b64encode(derived).decode()


FIELD_ENCRYPTION_KEY = _fernet_key(env("PII_ENCRYPTION_KEY"))

# ---------------------------------------------------------------------------
# Email — django-anymail
# ---------------------------------------------------------------------------

ANYMAIL = {
    "BREVO_API_KEY": env("BREVO_API_KEY", default=""),
}

EMAIL_BACKEND = env("EMAIL_BACKEND", default="anymail.backends.brevo.EmailBackend")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="noreply@parkshare.local")

# ---------------------------------------------------------------------------
# Web push — pywebpush / VAPID
# ---------------------------------------------------------------------------

VAPID_PRIVATE_KEY = env("VAPID_PRIVATE_KEY", default="")
VAPID_PUBLIC_KEY = env("VAPID_PUBLIC_KEY", default="")
VAPID_ADMIN_EMAIL = env("VAPID_ADMIN_EMAIL", default="")

# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
# ---------------------------------------------------------------------------
# Content Security Policy (django-csp 4.x)
# ---------------------------------------------------------------------------
# Google Fonts requires fonts.googleapis.com for stylesheets and
# fonts.gstatic.com for font files.  HTMX is self-hosted; no external scripts.
# frame-ancestors: 'none' is the authoritative clickjacking control (CSP Level 3).
# XFrameOptionsMiddleware and X_FRAME_OPTIONS are omitted — redundant with frame-ancestors.

# ---------------------------------------------------------------------------
# Deployment environment identifier
# ---------------------------------------------------------------------------
# Used by audit_healthcheck to label Prometheus metrics.  Must match the
# label regex ^[a-zA-Z0-9._-]{1,64}$ — audit_healthcheck validates at startup.
# opus = prod, faberix = ppe.  Default "unknown" triggers a startup warning.
ENVIRONMENT = env("DJANGO_ENV")

# ---------------------------------------------------------------------------
# Audit-recovery logging
# ---------------------------------------------------------------------------
# AUDIT_RECOVERY_LOG — path for the JSONL recovery sink (one JSON object per
# line). Override via the environment variable of the same name.
#
# The default resolves to /app/logs/audit-recovery.jsonl inside the container.
# docker-compose.yml mounts the named volume audit_logs:/app/logs, so the file
# survives container restart/replacement.  If you run outside Docker, override
# this variable to a path on durable storage.
#
AUDIT_RECOVERY_LOG = env(
    "AUDIT_RECOVERY_LOG",
    default=str(BASE_DIR / "logs" / "audit-recovery.jsonl"),
)

# AUDIT_LIVENESS_STATUS — one-line JSON appended by audit_healthcheck on each
# probe run.  Must be on the audit_logs named volume (same dir as the recovery
# log) so it survives container restarts.
AUDIT_LIVENESS_STATUS = env(
    "AUDIT_LIVENESS_STATUS",
    default=str(BASE_DIR / "logs" / "audit-liveness.jsonl"),
)

# How often the host cron runs audit_healthcheck (seconds).  Informational only
# for the command — the actual scheduler is a host cron or systemd timer.
AUDIT_LIVENESS_INTERVAL_SECONDS = env.int("AUDIT_LIVENESS_INTERVAL_SECONDS", default=60)

# node_exporter textfile-collector directory.  audit_healthcheck writes
# parkshare_audit.prom here atomically so node_exporter picks up the gauges
# on its next scrape without reading a partial file.
# Must match the --collector.textfile.directory flag on node_exporter.
NODE_EXPORTER_TEXTFILE_DIR = env(
    "NODE_EXPORTER_TEXTFILE_DIR",
    default="/var/lib/prometheus/node-exporter/",
)

# Create the parent directory so FileHandler(delay=True) can open the file on
# first write without raising FileNotFoundError and silently discarding the record.
try:
    Path(AUDIT_RECOVERY_LOG).parent.mkdir(parents=True, exist_ok=True)
except OSError:
    # Read-only filesystem or permission error — fall through; the handler will
    # fail gracefully on first write rather than crashing at startup.
    pass

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {
            # Raw JSONL — no prefix so the file is machine-parseable line-by-line.
            "format": "%(message)s",
        },
        "console_audit": {
            # Human-readable prefix for stderr/stdout so operators can see
            # the severity and logger name at a glance.
            "format": "%(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "audit_recovery_file": {
            # WatchedFileHandler re-opens the file when it detects that
            # logrotate has renamed/replaced it, so recovery records are
            # never silently lost to an orphaned file descriptor.
            "class": "logging.handlers.WatchedFileHandler",
            "filename": AUDIT_RECOVERY_LOG,
            "formatter": "plain",
            "delay": True,
        },
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "console_audit",
        },
    },
    "loggers": {
        "audit_recovery": {
            "handlers": ["audit_recovery_file", "console"],
            "level": "ERROR",
            "propagate": False,
        },
    },
}

CONTENT_SECURITY_POLICY = {
    "DIRECTIVES": {
        "default-src": ["'self'"],
        "font-src": ["'self'", "https://fonts.gstatic.com"],
        "style-src": ["'self'", "https://fonts.googleapis.com"],
        "script-src": ["'self'"],
        "img-src": ["'self'", "data:"],
        "object-src": ["'none'"],
        "frame-ancestors": ["'none'"],
        "base-uri": ["'self'"],
        "form-action": ["'self'"],
    },
}
