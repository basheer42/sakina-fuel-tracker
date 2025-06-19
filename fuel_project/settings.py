"""
Django settings for fuel_project project.
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

# Gemini AI Configuration
GEMINI_API_KEY = config('GEMINI_API_KEY', default='')

# WhatsApp Configuration
WHATSAPP_CONFIG = {
    'VERIFY_TOKEN': config('WHATSAPP_VERIFY_TOKEN', default=''),
    'ACCESS_TOKEN': config('WHATSAPP_ACCESS_TOKEN', default=''),
    'PHONE_NUMBER_ID': config('WHATSAPP_PHONE_NUMBER_ID', default=''),
}

ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='localhost,127.0.0.1', cast=lambda v: [s.strip() for s in v.split(',')])

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'shipments',
    'simple_history',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'simple_history.middleware.HistoryRequestMiddleware',
]

ROOT_URLCONF = 'fuel_project.urls'

# Replace the TEMPLATES section in your settings.py with this:

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],
        'APP_DIRS': True,  # Keep this as True for development and production
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

# Remove or comment out the Performance Settings section that modifies TEMPLATES
# Performance Settings
if not DEBUG:
    # Don't modify TEMPLATES for now - this was causing the conflict
    pass
    # TEMPLATES[0]['OPTIONS']['loaders'] = [
    #     ('django.template.loaders.cached.Loader', [
    #         'django.template.loaders.filesystem.Loader',
    #         'django.template.loaders.app_directories.Loader',
    #     ]),
    # ]

WSGI_APPLICATION = 'fuel_project.wsgi.application'

# Database Configuration
DATABASE_ENGINE = config('DATABASE_ENGINE', default='sqlite')

if DATABASE_ENGINE == 'postgresql':
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
TIME_ZONE = 'Africa/Nairobi'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [
    BASE_DIR / 'static',
] if (BASE_DIR / 'static').exists() else []

# Media files
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

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
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
SESSION_SAVE_EVERY_REQUEST = True

# CSRF Configuration
CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_HTTPONLY = True
CSRF_TRUSTED_ORIGINS = config('CSRF_TRUSTED_ORIGINS', default='', cast=lambda v: [s.strip() for s in v.split(',')] if v else [])

# Security Settings
if not DEBUG:
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_SSL_REDIRECT = config('SECURE_SSL_REDIRECT', default=False, cast=bool)

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

# Logging Configuration
LOGS_DIR = BASE_DIR / 'logs'
LOGS_DIR.mkdir(exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
    },
    'filters': {
        'require_debug_false': {
            '()': 'django.utils.log.RequireDebugFalse',
        },
        'require_debug_true': {
            '()': 'django.utils.log.RequireDebugTrue',
        },
    },
    'handlers': {
        'console': {
            'level': 'DEBUG',
            'filters': ['require_debug_true'],
            'class': 'logging.StreamHandler',
            'formatter': 'simple'
        },
        'file': {
            'level': 'INFO',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': LOGS_DIR / 'django.log',
            'maxBytes': 1024*1024*5,  # 5MB
            'backupCount': 5,
            'formatter': 'verbose',
        },
        'error_file': {
            'level': 'ERROR',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': LOGS_DIR / 'error.log',
            'maxBytes': 1024*1024*5,  # 5MB
            'backupCount': 5,
            'formatter': 'verbose',
        },
        'mail_admins': {
            'level': 'ERROR',
            'filters': ['require_debug_false'],
            'class': 'django.utils.log.AdminEmailHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console', 'file'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console', 'file', 'mail_admins'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.request': {
            'handlers': ['error_file', 'mail_admins'],
            'level': 'ERROR',
            'propagate': False,
        },
        'django.security': {
            'handlers': ['error_file', 'mail_admins'],
            'level': 'ERROR',
            'propagate': False,
        },
        'shipments': {
            'handlers': ['console', 'file'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
        'shipments.models': {
            'handlers': ['file', 'error_file'],
            'level': 'INFO',
            'propagate': False,
        },
        'shipments.views': {
            'handlers': ['file'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

# Email Processing Settings - Validate required settings
EMAIL_PROCESSING_REQUIRED_SETTINGS = [
    'EMAIL_PROCESSING_HOST',
    'EMAIL_PROCESSING_USER', 
    'EMAIL_PROCESSING_PASSWORD'
]

# Check if we're running tests or migrations
is_testing = 'test' in sys.argv
is_migrating = 'migrate' in sys.argv or 'makemigrations' in sys.argv

# Only validate email settings if not testing/migrating and email processing is enabled
EMAIL_PROCESSING_ENABLED = config('EMAIL_PROCESSING_ENABLED', default=False, cast=bool)

if EMAIL_PROCESSING_ENABLED and not (is_testing or is_migrating):
    missing_email_settings = []
    for setting_name in EMAIL_PROCESSING_REQUIRED_SETTINGS:
        try:
            globals()[setting_name] = config(setting_name)
        except Exception:
            missing_email_settings.append(setting_name)
    
    if missing_email_settings:
        print(f"Warning: Missing required email processing settings: {', '.join(missing_email_settings)}")
        print("Email processing will be disabled. Set EMAIL_PROCESSING_ENABLED=False to suppress this warning.")
        EMAIL_PROCESSING_ENABLED = False

# Email Processing Configuration
if EMAIL_PROCESSING_ENABLED:
    EMAIL_PROCESSING_HOST = config('EMAIL_PROCESSING_HOST')
    EMAIL_PROCESSING_PORT = config('EMAIL_PROCESSING_PORT', default=993, cast=int)
    EMAIL_PROCESSING_USER = config('EMAIL_PROCESSING_USER')
    EMAIL_PROCESSING_PASSWORD = config('EMAIL_PROCESSING_PASSWORD')
    EMAIL_PROCESSING_MAILBOX = config('EMAIL_PROCESSING_MAILBOX', default='INBOX')
    
    # Email Sender Filters
    EMAIL_STATUS_UPDATE_SENDER_FILTER = config('EMAIL_STATUS_UPDATE_SENDER_FILTER', default='')
    EMAIL_BOL_SENDER_FILTER = config('EMAIL_BOL_SENDER_FILTER', default='')
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
    ('Admin', config('ADMIN_EMAIL', default='admin@example.com')),
]
MANAGERS = ADMINS

# Performance Settings
if not DEBUG:
    # Use cached template loader in production
    TEMPLATES[0]['OPTIONS']['loaders'] = [
        ('django.template.loaders.cached.Loader', [
            'django.template.loaders.filesystem.Loader',
            'django.template.loaders.app_directories.Loader',
        ]),
    ]

# Database Connection Pooling (if using PostgreSQL)
if DATABASE_ENGINE == 'postgresql':
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
if DEBUG:
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
if DEBUG and not (is_testing or is_migrating):
    print(f"Django settings loaded:")
    print(f"  - DEBUG: {DEBUG}")
    print(f"  - DATABASE: {DATABASE_ENGINE}")
    print(f"  - EMAIL_PROCESSING: {EMAIL_PROCESSING_ENABLED}")
    print(f"  - CACHE: {CACHES['default']['BACKEND']}")
    print(f"  - TIME_ZONE: {TIME_ZONE}")
    print(f"  - STATIC_ROOT: {STATIC_ROOT}")
    print(f"  - LOGS_DIR: {LOGS_DIR}")
