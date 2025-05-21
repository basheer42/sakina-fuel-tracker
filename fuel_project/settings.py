# fuel_project/settings.py

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
# PASSWORD_CHANGE_REDIRECT_URL = '/' # This one wasn't in your file #3, but often useful
LOGIN_URL = '/accounts/login/' # Matches your path('accounts/', include('django.contrib.auth.urls'))

EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend' # For development email sending (prints to console)


# --- NEW: EMAIL ACCOUNT SETTINGS FOR PROCESSING INCOMING STATUS UPDATES ---
# WARNING: DO NOT COMMIT ACTUAL PASSWORDS TO GIT IF THIS IS A PUBLIC REPO.
# USE ENVIRONMENT VARIABLES OR A SECRETS MANAGER IN PRODUCTION.
# Replace placeholders below with your actual test credentials.

EMAIL_PROCESSING_HOST = 'mail.sakinagas.com'  # e.g., 'imap.gmail.com', 'outlook.office365.com', etc.
EMAIL_PROCESSING_PORT = 993                    # Standard IMAP SSL port (usually 993)
EMAIL_PROCESSING_USER = 'system@sakinagas.com' # The email account the system will read from
EMAIL_PROCESSING_PASSWORD = 'Aziz@2025' # The password for system@sakinagas.com
EMAIL_PROCESSING_SENDER_FILTER = 'info@sakinagas.com' # The email address that will be sending the test status updates
EMAIL_PROCESSING_MAILBOX = 'INBOX' # The mailbox/folder to check within system@sakinagas.com

# If using Gmail and 2-Step Verification, you'll need an "App Password".
# If using Outlook/Office365, ensure IMAP is enabled for the account.