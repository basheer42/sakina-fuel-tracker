# WSGI config for Sakinagas-Basheer42.pythonanywhere.com

import os
import sys

# Add your project directory to the sys.path
path = '/home/sakinagas-Basheer42/sakina-fuel-tracker'
if path not in sys.path:
    sys.path.insert(0, path)

# Set the Django settings module
os.environ['DJANGO_SETTINGS_MODULE'] = 'fuel_project.settings'

# Import Django's WSGI handler
from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()