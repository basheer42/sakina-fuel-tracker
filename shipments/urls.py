# shipments/urls.py
from django.urls import path
from . import views
from .telegram_bot import telegram_webhook

app_name = 'shipments'

urlpatterns = [
    # ===================================================================
    # HOME & DASHBOARD
    # ===================================================================
    path('', views.home_view, name='home'),

    # ===================================================================
    # SHIPMENT MANAGEMENT
    # ===================================================================
    path('shipments/list/', views.shipment_list_view, name='shipment-list'),
    path('shipments/add/', views.shipment_add_view, name='shipment-add'),
    path('shipments/<int:pk>/', views.shipment_detail_view, name='shipment-detail'),
    path('shipments/edit/<int:pk>/', views.shipment_edit_view, name='shipment-edit'),
    path('shipments/delete/<int:pk>/', views.shipment_delete_view, name='shipment-delete'),

    # ===================================================================
    # TR830 DOCUMENT PROCESSING (ENHANCED)
    # ===================================================================
    path('shipments/upload-tr830/', views.upload_tr830_view, name='upload-tr830'),
    path('shipments/bulk-upload-tr830/', views.bulk_upload_tr830_view, name='bulk-upload-tr830'),

    # ===================================================================
    # TRIP MANAGEMENT
    # ===================================================================
    path('trips/list/', views.trip_list_view, name='trip-list'),
    path('trips/add/', views.trip_add_view, name='trip-add'),
    path('trips/<int:pk>/', views.trip_detail_view, name='trip-detail'),
    path('trips/edit/<int:pk>/', views.trip_edit_view, name='trip-edit'),
    path('trips/delete/<int:pk>/', views.trip_delete_view, name='trip-delete'),

    # ===================================================================
    # PDF LOADING AUTHORITY PROCESSING
    # ===================================================================
    path('trips/upload-authority/', views.upload_loading_authority_view, name='trip-upload-authority'),
    path('trips/bulk-upload-authority/', views.bulk_upload_loading_authority_view, name='bulk-upload-authority'),

    # ===================================================================
    # EXTERNAL INTEGRATIONS
    # ===================================================================
    path('webhooks/telegram/', telegram_webhook, name='telegram_webhook'),

    # ===================================================================
    # REPORTS & ANALYTICS
    # ===================================================================
    path('reports/monthly-stock/', views.monthly_stock_summary_view, name='monthly-stock-summary'),
    path('reports/truck-activity/', views.truck_activity_dashboard_view, name='truck-activity-dashboard'),

    # ===================================================================
    # USER MANAGEMENT
    # ===================================================================
    path('signup/', views.signup_view, name='signup'),
    path('setup-admin/', views.setup_admin, name='setup-admin'),

    # ===================================================================
    # LEGACY/DEPRECATED ENDPOINTS
    # ===================================================================
    # Note: Removed WhatsApp webhook as per previous cleanup
    # Note: Removed debug endpoints for production
]