"""
Test-only settings for CondoParkShare.

Inherits from base and makes the test suite deterministic:
  - RATELIMIT_ENABLE = False disables django_ratelimit globally so counter
    state cannot accumulate across tests sharing the same process.
  - DummyCache (no-op) replaces LocMemCache to eliminate any cache state
    leakage between tests regardless of test ordering.
  - Transport-security hardening from base.py is relaxed to match dev.py
    so the Django test client can set cookies without HTTPS enforcement.

This file must not be loaded in production or staging.
Select with: DJANGO_SETTINGS_MODULE=parkshare.settings.test
"""

import os
from pathlib import Path

import environ

# ---------------------------------------------------------------------------
# Bootstrap: guarantee required env vars are set before base.py imports.
#
# pytest-django's pytest_load_initial_conftests hook imports this module
# BEFORE the project conftest.py's module-level code executes, so
# conftest.py's os.environ.setdefault calls arrive too late when no .env
# file is present (e.g. the HOS worker cron thin environment).
#
# Loading order:
#   1. Read .env (same call as base.py, with overwrite=False so real OS env
#      vars and previously-set CI values take precedence).  Deployments that
#      have a .env file get their real DATABASE_URL and other credentials here.
#   2. Set test-safe fallbacks for any variable still unset after step 1.
#      These values are intentionally weak — this module is only ever loaded
#      when DJANGO_SETTINGS_MODULE=parkshare.settings.test.
# ---------------------------------------------------------------------------
_BASE_DIR = Path(__file__).resolve().parent.parent.parent
environ.Env.read_env(_BASE_DIR / ".env", overwrite=False)

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production-use-only")
# dGVzdC1rZXktZG8tbm90LXVzZS1pbi1wcm9kISEhISE= decodes to exactly 32 bytes —
# a valid Fernet key that satisfies base.py's _fernet_key() without needing
# the SHA-256 derivation path, which raises ImproperlyConfigured when
# DEBUG=False — the value base.py sees at this point, before test.py's
# DEBUG=True override takes effect.
os.environ.setdefault("PII_ENCRYPTION_KEY", "dGVzdC1rZXktZG8tbm90LXVzZS1pbi1wcm9kISEhISE=")
# Fallback DATABASE_URL targets a throwaway database (parkshare_test) so
# a no-.env run cannot accidentally clobber the dev (parkshare) database.
# Django's test runner prefixes "test_", creating "test_parkshare_test".
# On deployments where the DB lives elsewhere (e.g. Docker-compose postgres),
# .env must supply the real URL and it will already be set by step 1 above,
# making this setdefault a no-op.
os.environ.setdefault("DATABASE_URL", "postgres://parkshare@localhost/parkshare_test")
os.environ.setdefault("DJANGO_ENV", "test")

from .base import *  # noqa: E402, F401, F403

# ---------------------------------------------------------------------------
# Test-only overrides — no application/production behavior changes here
# ---------------------------------------------------------------------------

DEBUG = True

ALLOWED_HOSTS = ["*"]

# Undo base.py HTTPS-only hardening so the test client can operate over HTTP.
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False

# Disable rate limiting — counters must not accumulate across tests sharing
# the same process. The decorator is still present in production code; only
# the enforcement is disabled here.
RATELIMIT_ENABLE = False

# No-op cache — eliminates all cross-test state via the cache layer.
# DummyCache accepts every write and returns a cache miss on every read,
# so there is no per-test teardown required to reset counters.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.dummy.DummyCache",
    }
}
