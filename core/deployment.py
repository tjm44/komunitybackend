import os
import dj_database_url
from .settings import *
from .settings import BASE_DIR


# -------------------------------------------------------------------
# Core secrets — must be set as env vars
# -------------------------------------------------------------------
SECRET_KEY = os.environ.get('SECRET') or os.environ.get('SECRET_KEY')

DEBUG = False

# -------------------------------------------------------------------
# Hosts — Detect from environment (Railway, Render, etc.)
# -------------------------------------------------------------------
PRODUCTION_HOST = os.environ.get('RAILWAY_STATIC_URL') or os.environ.get('RENDER_EXTERNAL_HOSTNAME')

ALLOWED_HOSTS = [
    '127.0.0.1',
    'localhost',
]
if PRODUCTION_HOST:
    ALLOWED_HOSTS.append(PRODUCTION_HOST)

env_hosts = os.environ.get('ALLOWED_HOSTS', '')
if env_hosts:
    ALLOWED_HOSTS.extend([h.strip() for h in env_hosts.split(',') if h.strip()])

CSRF_TRUSTED_ORIGINS = []
if PRODUCTION_HOST:
    CSRF_TRUSTED_ORIGINS.append(f'https://{PRODUCTION_HOST}')

env_csrf = os.environ.get('CSRF_TRUSTED_ORIGINS', '')
if env_csrf:
    CSRF_TRUSTED_ORIGINS.extend([origin.strip() for origin in env_csrf.split(',') if origin.strip()])

# -------------------------------------------------------------------
# Middleware — add WhiteNoise right after SecurityMiddleware
# -------------------------------------------------------------------
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',   # serves static files
    'django.contrib.sessions.middleware.SessionMiddleware',
    'allauth.account.middleware.AccountMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# -------------------------------------------------------------------
# Static files — WhiteNoise compressed storage
# -------------------------------------------------------------------
STATICFILES_STORAGE = 'whitenoise.storage.CompressedStaticFilesStorage'
WHITENOISE_MANIFEST_STRICT = False

STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# -------------------------------------------------------------------
# Media files — Cloudflare R2 (S3-compatible) storage
# Set these env vars on Railway/Render/Fly:
#   R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY,
#   R2_BUCKET_NAME, R2_PUBLIC_CUSTOM_DOMAIN (optional CDN domain)
# If credentials are absent, falls back to local disk (safe for staging).
# -------------------------------------------------------------------
_R2_ACCOUNT_ID = os.environ.get('R2_ACCOUNT_ID')
_R2_ACCESS_KEY_ID = os.environ.get('R2_ACCESS_KEY_ID')
_R2_SECRET_ACCESS_KEY = os.environ.get('R2_SECRET_ACCESS_KEY')
_R2_BUCKET_NAME = os.environ.get('R2_BUCKET_NAME')
_R2_PUBLIC_DOMAIN = os.environ.get('R2_PUBLIC_CUSTOM_DOMAIN')  # e.g. 'media.komunity.co.za'

if _R2_ACCOUNT_ID and _R2_ACCESS_KEY_ID and _R2_SECRET_ACCESS_KEY and _R2_BUCKET_NAME:
    # Cloudflare R2 is S3-compatible; use the account-specific endpoint
    AWS_S3_ENDPOINT_URL = f'https://{_R2_ACCOUNT_ID}.r2.cloudflarestorage.com'
    AWS_ACCESS_KEY_ID = _R2_ACCESS_KEY_ID
    AWS_SECRET_ACCESS_KEY = _R2_SECRET_ACCESS_KEY
    AWS_STORAGE_BUCKET_NAME = _R2_BUCKET_NAME
    AWS_S3_REGION_NAME = 'auto'  # R2 uses 'auto' as the region
    AWS_DEFAULT_ACL = None       # R2 does not use ACLs; bucket policy controls access
    AWS_S3_FILE_OVERWRITE = False
    AWS_QUERYSTRING_AUTH = False  # Public bucket: no signed URLs needed
    if _R2_PUBLIC_DOMAIN:
        AWS_S3_CUSTOM_DOMAIN = _R2_PUBLIC_DOMAIN

    # Django 4.2+ STORAGES dict (replaces deprecated DEFAULT_FILE_STORAGE)
    STORAGES = {
        'default': {
            'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage',
        },
        'staticfiles': {
            'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage',
        },
    }

# -------------------------------------------------------------------
# Database — Configure via environment DATABASE_URL
# -------------------------------------------------------------------
DATABASES = {
    'default': dj_database_url.config(
        env='DATABASE_URL',
        conn_max_age=600,
    )
}

# -------------------------------------------------------------------
# Email — use real SMTP in production
# -------------------------------------------------------------------
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', 'manyadzatocky@gmail.com')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_PASSWORD', '')

# -------------------------------------------------------------------
# CORS — restrict in production to known origins
# -------------------------------------------------------------------
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = []
if PRODUCTION_HOST:
    CORS_ALLOWED_ORIGINS.append(f'https://{PRODUCTION_HOST}')

env_cors = os.environ.get('CORS_ALLOWED_ORIGINS', '')
if env_cors:
    CORS_ALLOWED_ORIGINS.extend([origin.strip() for origin in env_cors.split(',') if origin.strip()])

# -------------------------------------------------------------------
# Security hardening
# -------------------------------------------------------------------
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# HTTPS & HSTS Hardening
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
