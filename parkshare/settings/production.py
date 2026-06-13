"""
Production settings for CondoParkShare.

Imports base settings and enforces production-only overrides.
HSTS, SESSION_COOKIE_SECURE, and CSRF_COOKIE_SECURE are set in base.py.
XFrameOptionsMiddleware is omitted — CSP frame-ancestors is the authoritative control.
"""

import environ

from parkshare.settings.base import *  # noqa: F401, F403

env = environ.Env()
environ.Env.read_env()

DEBUG = False

SECRET_KEY = env("SECRET_KEY")

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")

SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Production cache — must be a shared backend for rate limiting to be effective.
# Configure CACHE_URL in the production .env (e.g. redis://redis:6379/1).
# The ratelimit system checks (E003/W001) are restored here so that
# `manage.py check --deploy` will catch a misconfigured (locmem) cache in production.
SILENCED_SYSTEM_CHECKS = ["auth.E003"]  # ratelimit checks intentionally NOT suppressed

if env("CACHE_URL", default=None):
    CACHES = {"default": env.cache("CACHE_URL")}
else:
    import warnings

    warnings.warn(
        "CACHE_URL not set in production — rate limiting counters are per-worker (locmem). "
        "Configure Redis via CACHE_URL for effective brute-force protection.",
        RuntimeWarning,
        stacklevel=1,
    )
