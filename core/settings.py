import dotenv
from pathlib import Path
import os
from dotenv import load_dotenv

# Load .env for local development (no-op in production where env vars are injected directly)
load_dotenv(Path(__file__).resolve().parent.parent / '.env')

BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY: In production this must be set as the SECRET env var on Render.
# For local dev, fall back to an insecure placeholder — never commit a real secret here.
SECRET_KEY = os.environ.get(
    'SECRET',
    'django-insecure-local-dev-only-replace-in-production'
)

DEBUG = os.environ.get('DJANGO_ENV') != 'production'

_base_hosts = ['127.0.0.1', 'localhost', '192.168.88.245', '192.168.88.243', '192.168.88.236']
import socket
try:
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    if local_ip not in _base_hosts:
        _base_hosts.append(local_ip)
except Exception:
    local_ip = None

_prod_hosts = [
    'komunityweb.onrender.com',
    os.environ.get('RENDER_EXTERNAL_HOSTNAME', ''),
    os.environ.get('RAILWAY_STATIC_URL', ''),
]
env_hosts = os.environ.get('ALLOWED_HOSTS', '')
if env_hosts:
    _prod_hosts.extend([h.strip() for h in env_hosts.split(',') if h.strip()])

if DEBUG:
    ALLOWED_HOSTS = ['*']
else:
    ALLOWED_HOSTS = _base_hosts + [h for h in _prod_hosts if h]

CSRF_TRUSTED_ORIGINS = [
    'https://chemaonline.azurewebsites.net',
    'https://127.0.0.1',
    'https://chema.com',
    'http://192.168.88.245:8000',
    'http://192.168.88.243:8000',
]
railway_url = os.environ.get('RAILWAY_STATIC_URL')
if railway_url:
    CSRF_TRUSTED_ORIGINS.append(f'https://{railway_url}')

env_csrf = os.environ.get('CSRF_TRUSTED_ORIGINS', '')
if env_csrf:
    CSRF_TRUSTED_ORIGINS.extend([origin.strip() for origin in env_csrf.split(',') if origin.strip()])
if local_ip:
    CSRF_TRUSTED_ORIGINS.append(f'http://{local_ip}:8000')


INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sites',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    'chema',
    'user.apps.UserConfig',
    'condolence',
    'wallet',
    'rest_framework',
    'rest_framework.authtoken',
    'corsheaders',
    'api_v1',
    'dashboard',
    
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',
    'allauth.socialaccount.providers.facebook',
    'dj_rest_auth',
    'dj_rest_auth.registration',
]

AUTH_USER_MODEL = 'user.CustomUser'

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    "allauth.account.middleware.AccountMiddleware",
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'core.urls'
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR,'templates'),
                     os.path.join(BASE_DIR,'templates','account')],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'


AUTHENTICATION_BACKENDS = (
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend'
   
)

# DATABASES ={
#     'default': {
#         'ENGINE': 'django.db.backends.sqlite3',
#         'NAME': BASE_DIR / 'db.sqlite3',
#     }
# }``

import dj_database_url

database_url = os.environ.get('DATABASE_URL')

# If DATABASE_URL points to an unreachable host (e.g. Render/Railway internal host),
# it is not reachable from outside. Fall back to SQLite for local development.
if database_url and not any(ext in database_url for ext in ['.render.com', 'oregon-postgres', 'up.railway.app', 'localhost', '127.0.0.1']):
    if os.environ.get('DJANGO_ENV') != 'production':
        print("WARNING: DATABASE_URL points to an internal cloud host which is not reachable locally. Falling back to SQLite.")
        database_url = f'sqlite:///{BASE_DIR / "db.sqlite3"}'
        os.environ['DATABASE_URL'] = database_url

DATABASES = {
    'default': dj_database_url.config(
        default=database_url or f'sqlite:///{BASE_DIR / "db.sqlite3"}',
        conn_max_age=600,
    )
}


AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True



DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, 'static'),
    # NOTE: Do NOT add theme/static_src here — that is the Tailwind *source* dir.
    # The compiled CSS in theme/static/ is auto-discovered via INSTALLED_APPS.
]

LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/'

LOGIN_URL = 'account_login'
LOGOUT_URL = 'account_logout'

LANDING_PAGE_URL = os.environ.get('LANDING_PAGE_URL', 'http://localhost:3000')

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

CRISPY_ALLOWED_TEMPLATE_PACKS = "tailwind"
CRISPY_TEMPLATE_PACK = 'tailwind'


EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'  # overridden in deployment.py
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', 'manyadzatocky@gmail.com')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_PASSWORD', '')  # NEVER hardcode this

# django-allauth configuration
ACCOUNT_LOGIN_METHODS = {'email'}
ACCOUNT_SIGNUP_FIELDS = ['email*', 'password1*', 'password2*']
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_EMAIL_VERIFICATION = 'none' # Can be 'mandatory' in production

SITE_ID = 1

SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'SCOPE': [
            'profile',
            'email',
        ],
        'AUTH_PARAMS': {
            'access_type': 'online',
        },
        'APP': {
            'client_id': os.environ.get('GOOGLE_CLIENT_ID') or 'YOUR_GOOGLE_CLIENT_ID',
            'secret': os.environ.get('GOOGLE_CLIENT_SECRET') or 'YOUR_GOOGLE_SECRET',
            'key': ''
        }
    },
    'facebook': {
        'METHOD': 'oauth2',
        'SDK_URL': '//connect.facebook.net/{locale}/sdk.js',
        'SCOPE': ['email', 'public_profile'],
        'AUTH_PARAMS': {'auth_type': 'reauthenticate'},
        'INIT_PARAMS': {'cookie': True},
        'FIELDS': [
            'id',
            'first_name',
            'last_name',
            'middle_name',
            'name',
            'name_format',
            'picture',
            'short_name'
        ],
        'EXCHANGE_TOKEN': True,
        'APP': {
            'client_id': os.environ.get('FACEBOOK_CLIENT_ID') or 'YOUR_FACEBOOK_CLIENT_ID',
            'secret': os.environ.get('FACEBOOK_CLIENT_SECRET') or 'YOUR_FACEBOOK_SECRET',
            'key': ''
        }
    }
}

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    # ------------------------------------------------------------------
    # API Throttling — protects OTP/auth endpoints against brute-force
    # and SMS-flooding attacks.  Rates are intentionally relaxed in DEBUG
    # so local development is not impeded.
    # ------------------------------------------------------------------
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '60/min',
        'user': '200/min',
        # Custom scopes used on OTP/auth views (see api_v1/views.py)
        'otp_request': '5/hour',   # stops SMS-flood: max 5 OTP requests per hour per IP
        'otp_verify':  '10/hour',  # stops OTP brute-force
        'pin_verify':  '10/hour',  # stops PIN brute-force
    },
}

CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://app.komunity.co.za",
]
# Allow all origins in local development so the Expo mobile client
# (which runs on a LAN IP like 192.168.x.x) can reach the Django backend.
# In production DJANGO_ENV=production, this will be False.
if DEBUG:
    CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_CREDENTIALS = True


SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_HTTPONLY = False


# Flutterwave Configuration
FLW_CLIENT_ID = os.environ.get('FLW_CLIENT_ID')
FLW_CLIENT_SECRET = os.environ.get('FLW_CLIENT_SECRET')
FLW_ENCRYPTION_KEY = os.environ.get('FLW_ENCRYPTION_KEY')

# ------------------------------------------------------------------
# Fernet symmetric encryption (bank account numbers, mobile-money
# phone numbers stored in withdrawal_metadata)
# Generate key: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# ------------------------------------------------------------------
FERNET_KEY = os.environ.get('FERNET_KEY', '')