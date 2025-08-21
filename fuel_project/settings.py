"""
Django settings for fuel_project project - FIXED VERSION
"""
from pathlib import Path
import os
import sys
from decouple import config

BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = config('SECRET_KEY')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = config('DEBUG', default=False, cast=bool)

# Host Configuration
def get_host():
    return os.environ.get('HTTP_HOST', '')

# PythonAnywhere Detection and Configuration
IS_PYTHONANYWHERE = '.pythonanywhere.com' in get_host()
IS_PRODUCTION = not DEBUG or IS_PYTHONANYWHERE

# FIXED: Allowed Hosts Configuration with proper testing support
if IS_PYTHONANYWHERE:
    ALLOWED_HOSTS = ['Sakinagas-Basheer42.pythonanywhere.com', 'testserver', '127.0.0.1', 'localhost']
elif 'RAILWAY_ENVIRONMENT' in os.environ or 'RENDER' in os.environ:
    ALLOWED_HOSTS = ['*']  # Platform handles this securely
else:
    ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='localhost,127.0.0.1,testserver', cast=lambda v: [s.strip() for s in v.split(',')])

# Gemini AI Configuration
GEMINI_API_KEY = config('GEMINI_API_KEY', default='')

TELEGRAM_CONFIG = {
    'BOT_TOKEN': config('TELEGRAM_BOT_TOKEN', default=''),
}

# AI Order Matching Configuration (FIXED: Removed duplicates)
AI_MATCHER_ENABLED = config('AI_MATCHER_ENABLED', default=True, cast=bool)
AI_MATCHER_LOCAL_THRESHOLD = config('AI_MATCHER_LOCAL_THRESHOLD', default=0.6, cast=float)
AI_MATCHER_AI_THRESHOLD = config('AI_MATCHER_AI_THRESHOLD', default=0.8, cast=float)
AI_MATCHER_DEBUG = True

# Groq API Configuration
GROQ_API_KEY = config('GROQ_API_KEY', default='')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.core.cache',  # For TR830 state management
    'shipments',
    'simple_history',
]

# Middleware Configuration
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
]

# Add WhiteNoise only for non-PythonAnywhere deployments
if not IS_PYTHONANYWHERE:
    MIDDLEWARE.append('whitenoise.middleware.WhiteNoiseMiddleware')

MIDDLEWARE.extend([
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'simple_history.middleware.HistoryRequestMiddleware',
])

ROOT_URLCONF = 'fuel_project.urls'

# TEMPLATES CONFIGURATION
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],
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

WSGI_APPLICATION = 'fuel_project.wsgi.application'

# DATABASE CONFIGURATION
DATABASE_ENGINE = config('DATABASE_ENGINE', default='sqlite')

# Force MySQL for production (PythonAnywhere)
if DATABASE_ENGINE == 'mysql' or IS_PYTHONANYWHERE:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.mysql',
            'NAME': 'Basheer42$sakinafueldb',
            'USER': 'Basheer42',
            'PASSWORD': config('MYSQL_PASSWORD'),
            'HOST': 'Basheer42.mysql.pythonanywhere-services.com',
            'PORT': '3306',
            'OPTIONS': {
                'sql_mode': 'STRICT_TRANS_TABLES',
                'charset': 'utf8mb4',
                'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
            }
        }
    }
# Production database configuration (Railway/Render)
elif 'DATABASE_URL' in os.environ:
    import dj_database_url
    DATABASES = {
        'default': dj_database_url.config(
            default=config('DATABASE_URL')
        )
    }
elif DATABASE_ENGINE == 'postgresql':
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': config('DB_NAME'),
            'USER': config('DB_USER'),
            'PASSWORD': config('DB_PASSWORD'),
            'HOST': config('DB_HOST', default='localhost'),
            'PORT': config('DB_PORT', default='5432'),
            'OPTIONS': {
                'connect_timeout': 20,
            },
        }
    }
else:
    # SQLite for development
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',},
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = config('TIME_ZONE', default='Africa/Nairobi')
USE_I18N = True
USE_TZ = True

# Static files configuration
STATIC_URL = '/static/'

if IS_PYTHONANYWHERE:
    # PythonAnywhere static file paths
    username = get_host().split('.')[0] if get_host() else 'Basheer42'
    STATIC_ROOT = f'/home/{username}/sakina-fuel-tracker/static'
    MEDIA_ROOT = f'/home/{username}/sakina-fuel-tracker/media'
else:
    # Default static file configuration
    STATIC_ROOT = BASE_DIR / 'staticfiles'
    MEDIA_ROOT = BASE_DIR / 'media'

STATICFILES_DIRS = [
    BASE_DIR / 'static',
] if (BASE_DIR / 'static').exists() else []

# WhiteNoise configuration (only for non-PythonAnywhere deployments)
if not IS_PYTHONANYWHERE:
    STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# Media files
MEDIA_URL = '/media/'

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Authentication URLs
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/'
LOGIN_URL = '/accounts/login/'

# Email Configuration
EMAIL_BACKEND = config('EMAIL_BACKEND', default='django.core.mail.backends.console.EmailBackend')

if EMAIL_BACKEND == 'django.core.mail.backends.smtp.EmailBackend':
    EMAIL_HOST = config('EMAIL_HOST', default='')
    EMAIL_PORT = config('EMAIL_PORT', default=587, cast=int)
    EMAIL_USE_TLS = config('EMAIL_USE_TLS', default=True, cast=bool)
    EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
    EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
    DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='noreply@example.com')

# Cache Configuration
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'fuel-tracker-cache',
        'TIMEOUT': 300,  # 5 minutes default
        'OPTIONS': {
            'MAX_ENTRIES': 1000,
        }
    }
}

# Session Configuration
SESSION_COOKIE_AGE = 3600 * 8  # 8 hours
SESSION_COOKIE_SECURE = IS_PRODUCTION
SESSION_COOKIE_HTTPONLY = True
SESSION_SAVE_EVERY_REQUEST = True

# CSRF Configuration
CSRF_COOKIE_SECURE = IS_PRODUCTION
CSRF_COOKIE_HTTPONLY = True
CSRF_TRUSTED_ORIGINS = config('CSRF_TRUSTED_ORIGINS', default='', cast=lambda v: [s.strip() for s in v.split(',')] if v else [])

# Update CSRF_TRUSTED_ORIGINS for production platforms
if IS_PYTHONANYWHERE and get_host():
    CSRF_TRUSTED_ORIGINS.append(f'https://{get_host()}')

if 'RAILWAY_ENVIRONMENT' in os.environ:
    railway_domain = os.environ.get('RAILWAY_PUBLIC_DOMAIN', '')
    if railway_domain:
        CSRF_TRUSTED_ORIGINS.append(f'https://{railway_domain}')

if 'RENDER' in os.environ:
    render_domain = os.environ.get('RENDER_EXTERNAL_HOSTNAME', '')
    if render_domain:
        CSRF_TRUSTED_ORIGINS.append(f'https://{render_domain}')

# Security Settings - Enhanced for production
if IS_PRODUCTION:
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_SSL_REDIRECT = config('SECURE_SSL_REDIRECT', default=True, cast=bool)
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Custom Settings for Application
FUEL_TRACKER_SETTINGS = {
    'AGING_STOCK_THRESHOLD_DAYS': config('AGING_STOCK_THRESHOLD_DAYS', default=25, cast=int),
    'INACTIVITY_THRESHOLD_DAYS': config('INACTIVITY_THRESHOLD_DAYS', default=5, cast=int),
    'UTILIZED_THRESHOLD_DAYS': config('UTILIZED_THRESHOLD_DAYS', default=7, cast=int),
    'MAX_FILE_UPLOAD_SIZE_MB': config('MAX_FILE_UPLOAD_SIZE_MB', default=10, cast=int),
    'ENABLE_STOCK_ALERTS': config('ENABLE_STOCK_ALERTS', default=True, cast=bool),
    'DEFAULT_PAGINATION_SIZE': config('DEFAULT_PAGINATION_SIZE', default=50, cast=int),
}

# File Upload Settings
max_size_mb = FUEL_TRACKER_SETTINGS['MAX_FILE_UPLOAD_SIZE_MB']
max_size_bytes = max_size_mb * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = max_size_bytes
DATA_UPLOAD_MAX_MEMORY_SIZE = max_size_bytes
DATA_UPLOAD_MAX_NUMBER_FIELDS = 1000

# FIXED: Single logging configuration (removed duplicates)
# Create logs directory if it doesn't exist
os.makedirs(os.path.join(BASE_DIR, 'logs'), exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {asctime} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
        'file': {
            'level': 'INFO',
            'class': 'logging.FileHandler',
            'filename': os.path.join(BASE_DIR, 'logs', 'django.log'),
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console', 'file'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
        'shipments': {
            'handlers': ['console', 'file'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
    },
}

# Email Processing Settings - Check if we're running tests or migrations
is_testing = 'test' in sys.argv
is_migrating = 'migrate' in sys.argv or 'makemigrations' in sys.argv

# Email Processing Configuration
EMAIL_PROCESSING_ENABLED = config('EMAIL_PROCESSING_ENABLED', default=True, cast=bool)

if EMAIL_PROCESSING_ENABLED:
    EMAIL_PROCESSING_HOST = config('EMAIL_PROCESSING_HOST', default='mail.sakinagas.com')
    EMAIL_PROCESSING_PORT = config('EMAIL_PROCESSING_PORT', default=993, cast=int)
    EMAIL_PROCESSING_USER = config('EMAIL_PROCESSING_USER', default='info@sakinagas.com')
    EMAIL_PROCESSING_PASSWORD = config('EMAIL_PROCESSING_PASSWORD', default='')
    EMAIL_PROCESSING_MAILBOX = config('EMAIL_PROCESSING_MAILBOX', default='INBOX')

    # Email Sender Filters (Production KPC Integration)
    EMAIL_STATUS_UPDATE_SENDER_FILTER = config('EMAIL_STATUS_UPDATE_SENDER_FILTER', default='truckloading@kpc.co.ke')
    EMAIL_BOL_SENDER_FILTER = config('EMAIL_BOL_SENDER_FILTER', default='bolconfirmation@kpc.co.ke')
else:
    # Set defaults when email processing is disabled
    EMAIL_PROCESSING_HOST = ''
    EMAIL_PROCESSING_PORT = 993
    EMAIL_PROCESSING_USER = ''
    EMAIL_PROCESSING_PASSWORD = ''
    EMAIL_PROCESSING_MAILBOX = 'INBOX'
    EMAIL_STATUS_UPDATE_SENDER_FILTER = ''
    EMAIL_BOL_SENDER_FILTER = ''

# Admin Configuration
ADMINS = [
    ('Admin', config('ADMIN_EMAIL', default='admin@sakinagas.com')),
]
MANAGERS = ADMINS

# Database Connection Pooling (if using PostgreSQL)
if DATABASE_ENGINE == 'postgresql' or 'DATABASE_URL' in os.environ:
    DATABASES['default']['CONN_MAX_AGE'] = 60

# Internationalization and Localization
USE_L10N = True
DECIMAL_SEPARATOR = '.'
THOUSAND_SEPARATOR = ','
USE_THOUSAND_SEPARATOR = True

# Date and Time Formats
DATE_FORMAT = 'Y-m-d'
DATETIME_FORMAT = 'Y-m-d H:i:s'
TIME_FORMAT = 'H:i'

# Message Framework
from django.contrib.messages import constants as messages
MESSAGE_TAGS = {
    messages.DEBUG: 'debug',
    messages.INFO: 'info',
    messages.SUCCESS: 'success',
    messages.WARNING: 'warning',
    messages.ERROR: 'error',
}

# Development Settings
if DEBUG and not IS_PYTHONANYWHERE:
    # Add debug toolbar in development
    try:
        import debug_toolbar
        INSTALLED_APPS.append('debug_toolbar')
        MIDDLEWARE.insert(1, 'debug_toolbar.middleware.DebugToolbarMiddleware')
        INTERNAL_IPS = ['127.0.0.1', '::1']
        DEBUG_TOOLBAR_CONFIG = {
            'SHOW_TOOLBAR_CALLBACK': lambda request: DEBUG,
        }
    except ImportError:
        pass

    # Email backend for development
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# Test Settings
if is_testing:
    # Use in-memory database for tests
    DATABASES['default'] = {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }

    # Disable logging during tests
    LOGGING['root']['level'] = 'CRITICAL'

    # Use dummy cache for tests
    CACHES['default']['BACKEND'] = 'django.core.cache.backends.dummy.DummyCache'

    # Speed up password hashing for tests
    PASSWORD_HASHERS = [
        'django.contrib.auth.hashers.MD5PasswordHasher',
    ]

# Print configuration summary in debug mode
if DEBUG and not (is_testing or is_migrating or IS_PYTHONANYWHERE):
    print(f"Django settings loaded:")
    print(f"  - DEBUG: {DEBUG}")
    print(f"  - DATABASE: {DATABASE_ENGINE}")
    print(f"  - EMAIL_PROCESSING: {EMAIL_PROCESSING_ENABLED}")
    print(f"  - CACHE: {CACHES['default']['BACKEND']}")
    print(f"  - TIME_ZONE: {TIME_ZONE}")
    print(f"  - STATIC_ROOT: {STATIC_ROOT}")
    print(f"  - GEMINI_API_KEY: {'SET' if GEMINI_API_KEY else 'MISSING'}")

# PythonAnywhere specific configuration summary
if IS_PYTHONANYWHERE and not (is_testing or is_migrating):
    username = get_host().split('.')[0] if get_host() else 'Basheer42'
    print(f"PythonAnywhere Configuration Active:")
    print(f"  - Username: {username}")
    print(f"  - Database: MySQL ({username}$sakinafueldb)")
    print(f"  - Static Root: /home/{username}/sakina-fuel-tracker/static")
    print(f"  - Email Processing: {EMAIL_PROCESSING_ENABLED}")
    print(f"  - Allowed Hosts: {ALLOWED_HOSTS}")
ALLOWED_HOSTS = ['Sakinagas-Basheer42.pythonanywhere.com', 'testserver', '127.0.0.1', 'localhost']