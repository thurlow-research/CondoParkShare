"""
Production settings for CondoParkShare.

Imports base settings and enforces production-only overrides.
HSTS, SESSION_COOKIE_SECURE, CSRF_COOKIE_SECURE, and X_FRAME_OPTIONS
are already set in base.py and apply in all environments.
"""

from parkshare.settings.base import *  # noqa: F401, F403
import environ

env = environ.Env()
environ.Env.read_env()

DEBUG = False

SECRET_KEY = env('SECRET_KEY')

ALLOWED_HOSTS = env.list('ALLOWED_HOSTS')

SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
