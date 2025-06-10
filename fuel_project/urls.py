"""
URL configuration for fuel_project project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings # For static files in development
from django.conf.urls.static import static # For static files in development

# Import views from your shipments app to reference the handlers
from shipments import views as shipment_views

urlpatterns = [
    path('admin/', admin.site.urls), # Django admin URLs

    # Include Django's built-in authentication URLs under the /accounts/ path
    # This provides login, logout, password_change, password_reset, etc.
    path('accounts/', include('django.contrib.auth.urls')),

    # Include URLs from your shipments app (this should be the main entry point)
    path('', include('shipments.urls')), # Assuming 'shipments.urls' defines the root path ''
]

# Serve static files during development if DEBUG is True
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    # Optionally, serve media files during development as well
    # urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Custom error handlers
# These tell Django which views to use when specific HTTP errors occur
# Ensure these views are defined in shipments/views.py or adjust import path
handler403 = shipment_views.handler403
handler404 = shipment_views.handler404
handler500 = shipment_views.handler500