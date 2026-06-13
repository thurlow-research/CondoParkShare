"""
Local development settings for CondoParkShare.

Imports base settings and relaxes the transport-security hardening that base.py
applies for production (secure-only cookies, HSTS, SSL redirect). Intended for
local bring-up over plain HTTP on the build host — NEVER for production.

Select with: DJANGO_SETTINGS_MODULE=parkshare.settings.dev
"""

import environ

from parkshare.settings.base import *  # noqa: F401, F403

env = environ.Env()
environ.Env.read_env()

DEBUG = True

SECRET_KEY = env("SECRET_KEY")

ALLOWED_HOSTS = env.list(
    "ALLOWED_HOSTS",
    default=["localhost", "127.0.0.1", "0.0.0.0", "web"],
)

# Plain-HTTP local dev: undo base.py's HTTPS-only hardening so login/cookies work.
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
