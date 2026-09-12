import os
import socket
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from django.core.exceptions import ImproperlyConfigured
from .runtime_config import secret_value

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')


def env_list(name: str, default: str = '') -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(',') if item.strip()]


def env_bool(name: str, default: str = '0') -> bool:
    return os.getenv(name, default).strip().lower() in {'1', 'true', 'yes', 'on'}


DEBUG = env_bool('SITEOS_ADMIN_DEBUG', '0')
SECRET_KEY = secret_value('SITEOS_ADMIN_SECRET_KEY').strip()
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = 'siteos-v2-admin-dev-only-secret'
    else:
        raise ImproperlyConfigured('SITEOS_ADMIN_SECRET_KEY is required when DEBUG is disabled.')
ALLOWED_HOSTS = env_list('SITEOS_ADMIN_ALLOWED_HOSTS', '127.0.0.1,localhost,testserver')
if DEBUG and '*' not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append('*')
try:
    for candidate in {
        socket.gethostbyname(socket.gethostname()),
        *socket.gethostbyname_ex(socket.gethostname())[2],
    }:
        if candidate and candidate not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(candidate)
except OSError:
    pass

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'console',
    'leads',
    'marketing',
    'sitecore',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'console.access.ConsoleAccessMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'siteos_admin.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'console.brand_context.brand_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'siteos_admin.wsgi.application'


def database_config_from_url(database_url: str) -> dict:
    if not database_url:
        if os.getenv('SITEOS_ADMIN_DATABASE_HOST'):
            return {
                'ENGINE': 'django.db.backends.postgresql',
                'NAME': os.environ['SITEOS_ADMIN_DATABASE_NAME'],
                'USER': os.environ['SITEOS_ADMIN_DATABASE_USER'],
                'PASSWORD': secret_value('SITEOS_ADMIN_DATABASE_PASSWORD'),
                'HOST': os.environ['SITEOS_ADMIN_DATABASE_HOST'],
                'PORT': os.getenv('SITEOS_ADMIN_DATABASE_PORT', '5432'),
                'OPTIONS': {'sslmode': os.getenv('SITEOS_ADMIN_DATABASE_SSLMODE', 'disable')},
            }
        if not DEBUG:
            raise ImproperlyConfigured(
                'SITEOS_ADMIN_DATABASE_URL is required when DEBUG is disabled.'
            )
        return {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'admin.sqlite3',
        }

    parsed = urlparse(database_url)
    sslmode = os.getenv('SITEOS_ADMIN_DATABASE_SSLMODE', 'disable').strip() or 'disable'
    return {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': parsed.path.lstrip('/'),
        'USER': parsed.username or '',
        'PASSWORD': parsed.password or '',
        'HOST': parsed.hostname or '',
        'PORT': str(parsed.port or 5432),
        'OPTIONS': {'sslmode': sslmode},
    }


DATABASES = {
    'default': database_config_from_url(secret_value('SITEOS_ADMIN_DATABASE_URL')),
}

# Separate from consent and integration settings: restoration/demos are silent.
NEWCROWN_ALLOW_EXTERNAL_IO = env_bool('NEWCROWN_ALLOW_EXTERNAL_IO', '0')
NEWCROWN_RUN_SCHEDULED_TASKS = env_bool('NEWCROWN_RUN_SCHEDULED_TASKS', '0')

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static'] if (BASE_DIR / 'static').exists() else []
STATIC_ROOT = Path(os.getenv('SITEOS_ADMIN_STATIC_ROOT', BASE_DIR / 'staticfiles'))
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

CSRF_TRUSTED_ORIGINS = env_list('SITEOS_ADMIN_CSRF_TRUSTED_ORIGINS')
secure_default = '0' if DEBUG else '1'
SECURE_SSL_REDIRECT = env_bool('SITEOS_ADMIN_SECURE_SSL_REDIRECT', secure_default)
SESSION_COOKIE_SECURE = env_bool('SITEOS_ADMIN_SESSION_COOKIE_SECURE', secure_default)
CSRF_COOKIE_SECURE = env_bool('SITEOS_ADMIN_CSRF_COOKIE_SECURE', secure_default)
SECURE_HSTS_SECONDS = int(
    os.getenv('SITEOS_ADMIN_SECURE_HSTS_SECONDS', '0' if DEBUG else '31536000')
)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool('SITEOS_ADMIN_SECURE_HSTS_INCLUDE_SUBDOMAINS')
SECURE_HSTS_PRELOAD = env_bool('SITEOS_ADMIN_SECURE_HSTS_PRELOAD')
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
X_FRAME_OPTIONS = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'
if os.getenv('SITEOS_ADMIN_TRUST_X_FORWARDED_PROTO', '1') == '1':
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

EMAIL_BACKEND = os.getenv('SITEOS_EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend')
EMAIL_HOST = os.getenv('SITEOS_EMAIL_HOST', 'localhost')
EMAIL_PORT = int(os.getenv('SITEOS_EMAIL_PORT', '25'))
EMAIL_HOST_USER = os.getenv('SITEOS_EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('SITEOS_EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = env_bool('SITEOS_EMAIL_USE_TLS')
EMAIL_USE_SSL = env_bool('SITEOS_EMAIL_USE_SSL')
EMAIL_TIMEOUT = int(os.getenv('SITEOS_EMAIL_TIMEOUT', '8'))
DEFAULT_FROM_EMAIL = os.getenv('SITEOS_DEFAULT_FROM_EMAIL', 'website@vorntek.example')

SITEOS_ADMIN_PUBLIC_URL = os.getenv('SITEOS_ADMIN_PUBLIC_URL', 'http://localhost:8088/admin')
VORNTEK_BRAND_NAME = os.getenv('VORNTEK_BRAND_NAME', 'Vorntek')
SITEOS_LEAD_RATE_LIMIT_COUNT = int(os.getenv('SITEOS_LEAD_RATE_LIMIT_COUNT', '5'))
SITEOS_LEAD_RATE_LIMIT_MINUTES = int(os.getenv('SITEOS_LEAD_RATE_LIMIT_MINUTES', '10'))
SITEOS_LEAD_DUPLICATE_MINUTES = int(os.getenv('SITEOS_LEAD_DUPLICATE_MINUTES', '30'))
SITEOS_LEAD_REMINDER_HOURS = int(os.getenv('SITEOS_LEAD_REMINDER_HOURS', '4'))
SITEOS_LEAD_REMINDER_REPEAT_HOURS = int(os.getenv('SITEOS_LEAD_REMINDER_REPEAT_HOURS', '24'))
SITEOS_OUTBOX_CLAIM_TIMEOUT_MINUTES = int(os.getenv('SITEOS_OUTBOX_CLAIM_TIMEOUT_MINUTES', '5'))
SITEOS_WHATSAPP_ALLOW_LIVE_SEND = env_bool('SITEOS_WHATSAPP_ALLOW_LIVE_SEND', '0')
SITEOS_WHATSAPP_INLINE_MOCK_DISPATCH = env_bool('SITEOS_WHATSAPP_INLINE_MOCK_DISPATCH', '1')
SITEOS_WHATSAPP_HTTP_TIMEOUT_SECONDS = int(os.getenv('SITEOS_WHATSAPP_HTTP_TIMEOUT_SECONDS', '20'))
SITEOS_WHATSAPP_SEND_CLAIM_TIMEOUT_MINUTES = int(
    os.getenv('SITEOS_WHATSAPP_SEND_CLAIM_TIMEOUT_MINUTES', '5')
)
SITEOS_WHATSAPP_MEDIA_ROOT = Path(
    os.getenv('SITEOS_WHATSAPP_MEDIA_ROOT', BASE_DIR / '.runtime' / 'whatsapp-media')
)
SITEOS_WHATSAPP_MEDIA_MAX_BYTES = int(
    os.getenv('SITEOS_WHATSAPP_MEDIA_MAX_BYTES', str(25 * 1024 * 1024))
)
SITEOS_WHATSAPP_MEDIA_MAX_ATTEMPTS = int(
    os.getenv('SITEOS_WHATSAPP_MEDIA_MAX_ATTEMPTS', '5')
)

LOGIN_URL = '/admin/login/'
LOGIN_REDIRECT_URL = '/admin/'
LOGOUT_REDIRECT_URL = '/admin/login/'
