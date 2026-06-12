"""
Django base settings for CondoParkShare.

All environment-specific settings override this file.
No secrets here — all sensitive values come from environment variables.
"""

from pathlib import Path
import base64
import hashlib
import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
    EMAIL_BACKEND=(str, 'anymail.backends.brevo.EmailBackend'),
)

# Read .env if it exists (dev convenience only — production uses real env vars)
environ.Env.read_env(BASE_DIR / '.env', overwrite=False)

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

SECRET_KEY = env('SECRET_KEY')

DEBUG = env('DEBUG')

ALLOWED_HOSTS = env('ALLOWED_HOSTS')

ROOT_URLCONF = 'parkshare.urls'

WSGI_APPLICATION = 'parkshare.wsgi.application'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTH_USER_MODEL = 'accounts.User'

# Suppress auth.E003: USERNAME_FIELD (email) is not unique at the column level by design.
# Multi-tenant: the same email address may exist in multiple organisations.
# Uniqueness is enforced via unique_together = [('organization', 'email')].
SILENCED_SYSTEM_CHECKS = ['auth.E003']

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------

INSTALLED_APPS = [
    # Django contrib
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.postgres',

    # Third-party
    'django_otp',
    'django_otp.plugins.otp_totp',
    'django_otp.plugins.otp_static',
    'encrypted_model_fields',
    'anymail',

    # Project apps — accounts MUST precede parking (User model dependency)
    'accounts',
    'parking',
    'notifications',
    'portal',
    'operator',
]

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'parkshare.middleware.TenantMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django_otp.middleware.OTPMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'parkshare.middleware.ImpersonationMiddleware',
]

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'parkshare.context_processors.impersonation',
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

DATABASES = {
    'default': env.db('DATABASE_URL'),
}

# ---------------------------------------------------------------------------
# Password hashing — Argon2 first
# ---------------------------------------------------------------------------

PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.Argon2PasswordHasher',
    'django.contrib.auth.hashers.BCryptSHA256PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher',
]

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']

# ---------------------------------------------------------------------------
# PII field encryption (django-encrypted-model-fields)
# ---------------------------------------------------------------------------

def _fernet_key(raw: str) -> str:
    """Return *raw* unchanged if it is already a valid 32-byte url-safe base64
    Fernet key; otherwise derive one deterministically via SHA-256.  This lets
    CI / smoke-test environments pass a plain memorable string without having to
    generate a proper Fernet key, while production environments continue to use
    a real key."""
    try:
        decoded = base64.urlsafe_b64decode(raw + '==')
        if len(decoded) == 32:
            return raw
    except Exception:
        pass
    derived = hashlib.sha256(raw.encode()).digest()
    return base64.urlsafe_b64encode(derived).decode()


FIELD_ENCRYPTION_KEY = _fernet_key(env('PII_ENCRYPTION_KEY'))

# ---------------------------------------------------------------------------
# Email — django-anymail
# ---------------------------------------------------------------------------

ANYMAIL = {
    'BREVO_API_KEY': env('BREVO_API_KEY', default=''),
}

EMAIL_BACKEND = env('EMAIL_BACKEND', default='anymail.backends.brevo.EmailBackend')
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default='noreply@parkshare.local')

# ---------------------------------------------------------------------------
# Web push — pywebpush / VAPID
# ---------------------------------------------------------------------------

VAPID_PRIVATE_KEY = env('VAPID_PRIVATE_KEY', default='')
VAPID_PUBLIC_KEY = env('VAPID_PUBLIC_KEY', default='')
VAPID_ADMIN_EMAIL = env('VAPID_ADMIN_EMAIL', default='')

# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
X_FRAME_OPTIONS = 'DENY'
