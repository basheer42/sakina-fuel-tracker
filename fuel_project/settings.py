"""
Django settings for fuel_project project.
"""
from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = 'django-insecure-k%c$*3e$1369*+1m6z6k6^q*p*@#4-b2=m^w*c8g9k5q7r!j#u' # Ensure you have your own unique secret key for production
DEBUG = True
ALLOWED_HOSTS = []

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'shipments',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'fuel_project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')], # Your project-level templates directory
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

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Africa/Nairobi' # Your specified timezone
USE_I18N = True
USE_TZ = True # Important for timezone-aware datetimes

STATIC_URL = 'static/'
# STATICFILES_DIRS = [os.path.join(BASE_DIR, 'static')] # Add this if you have a project-level static folder not in an app
STATIC_ROOT = BASE_DIR / 'staticfiles' # For collectstatic in production

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Your original redirect URLs
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/'
# PASSWORD_CHANGE_REDIRECT_URL = '/'
LOGIN_URL = '/accounts/login/'

EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend' # For development email sending (prints to console)


# --- EMAIL ACCOUNT SETTINGS FOR PROCESSING INCOMING EMAILS ---
# WARNING: DO NOT COMMIT ACTUAL PASSWORDS TO GIT IF THIS IS A PUBLIC REPO.
# USE ENVIRONMENT VARIABLES OR A SECRETS MANAGER IN PRODUCTION.

EMAIL_PROCESSING_HOST = 'mail.sakinagas.com'
EMAIL_PROCESSING_PORT = 993
EMAIL_PROCESSING_USER = 'system@sakinagas.com' # The email account the system will read from
EMAIL_PROCESSING_PASSWORD = 'Aziz@2025' # The password for system@sakinagas.com
EMAIL_PROCESSING_MAILBOX = 'INBOX'

# --- SENDER FILTERS FOR TESTING PURPOSES ---
# For testing, all emails will be sent FROM 'info@sakinagas.com'
# In production, these will be the actual KPC sender addresses.

# Sender for KPC STATUS UPDATE emails (e.g., APPROVED, REJECTED)
EMAIL_STATUS_UPDATE_SENDER_FILTER = 'info@sakinagas.com' # WAS: 'truckloading@kpc.co.ke' - CHANGED FOR TESTING

# Sender for KPC BILL OF LADING (BoL) PDF emails
EMAIL_BOL_SENDER_FILTER = 'info@sakinagas.com' # WAS: 'bolconfirmation@kpc.co.ke' - CHANGED FOR TESTING


# If using Gmail and 2-Step Verification, you'll need an "App Password".
# If using Outlook/Office365, ensure IMAP is enabled for the account.