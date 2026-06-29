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

from .base import *  # noqa: F401, F403

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
