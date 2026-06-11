"""
Production settings for CondoParkShare.

Imports base settings and enforces production-only overrides.
"""

from parkshare.settings.base import *  # noqa: F401, F403
import environ

env = environ.Env()

DEBUG = False

SECRET_KEY = env('SECRET_KEY')

ALLOWED_HOSTS = env.list('ALLOWED_HOSTS')
