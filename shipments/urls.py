# shipments/urls.py
# COMPLETE FIXED VERSION - Based on actual available views
from django.urls import path
from . import views

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
    path('webhooks/telegram/', views.telegram_webhook, name='telegram_webhook'),
    path('telegram/health/', views.telegram_health_check, name='telegram_health_check'),

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
    # API ENDPOINTS (if needed for future expansion)
    # ===================================================================
    # Note: Add API endpoints here when implemented

    # ===================================================================
    # AUTHENTICATION & USER PROFILES
    # ===================================================================
    # Note: Django auth URLs are handled in main urls.py via include('django.contrib.auth.urls')

    # ===================================================================
    # CUSTOMER MANAGEMENT (if implemented)
    # ===================================================================
    # Note: Add customer management URLs when customer views are implemented
    # path('customers/list/', views.customer_list_view, name='customer-list'),
    # path('customers/add/', views.customer_add_view, name='customer-add'),
    # path('customers/<int:pk>/', views.customer_detail_view, name='customer-detail'),
    # path('customers/edit/<int:pk>/', views.customer_edit_view, name='customer-edit'),
    # path('customers/delete/<int:pk>/', views.customer_delete_view, name='customer-delete'),

    # ===================================================================
    # STOCK MANAGEMENT (if implemented)
    # ===================================================================
    # Note: Add stock management URLs when stock views are implemented
    # path('stock/list/', views.stock_list_view, name='stock-list'),
    # path('stock/check-aging/', views.check_aging_stock_view, name='check-aging-stock'),
    # path('stock/deplete/', views.deplete_stock_view, name='deplete-stock'),

    # ===================================================================
    # VEHICLE MANAGEMENT (if implemented)
    # ===================================================================
    # Note: Add vehicle management URLs when vehicle views are implemented
    # path('vehicles/list/', views.vehicle_list_view, name='vehicle-list'),
    # path('vehicles/add/', views.vehicle_add_view, name='vehicle-add'),
    # path('vehicles/<int:pk>/', views.vehicle_detail_view, name='vehicle-detail'),
    # path('vehicles/edit/<int:pk>/', views.vehicle_edit_view, name='vehicle-edit'),
    # path('vehicles/delete/<int:pk>/', views.vehicle_delete_view, name='vehicle-delete'),

    # ===================================================================
    # DRIVER MANAGEMENT (if implemented)
    # ===================================================================
    # Note: Add driver management URLs when driver views are implemented
    # path('drivers/list/', views.driver_list_view, name='driver-list'),
    # path('drivers/add/', views.driver_add_view, name='driver-add'),
    # path('drivers/<int:pk>/', views.driver_detail_view, name='driver-detail'),
    # path('drivers/edit/<int:pk>/', views.driver_edit_view, name='driver-edit'),
    # path('drivers/delete/<int:pk>/', views.driver_delete_view, name='driver-delete'),

    # ===================================================================
    # AUDIT & TRACKING (if implemented)
    # ===================================================================
    # Note: Add audit URLs when audit views are implemented
    # path('audit/shipments/', views.shipment_audit_view, name='shipment-audit'),
    # path('audit/trips/', views.trip_audit_view, name='trip-audit'),
    # path('audit/users/', views.user_audit_view, name='user-audit'),

    # ===================================================================
    # NOTIFICATIONS & ALERTS (if implemented)
    # ===================================================================
    # Note: Add notification URLs when notification views are implemented
    # path('notifications/', views.notification_list_view, name='notification-list'),
    # path('notifications/mark-read/<int:pk>/', views.mark_notification_read_view, name='mark-notification-read'),

    # ===================================================================
    # BACKUP & MAINTENANCE (if implemented)
    # ===================================================================
    # Note: Add maintenance URLs when maintenance views are implemented
    # path('maintenance/backup/', views.backup_database_view, name='backup-database'),
    # path('maintenance/cleanup/', views.cleanup_old_data_view, name='cleanup-old-data'),

    # ===================================================================
    # LEGACY/DEPRECATED ENDPOINTS
    # ===================================================================
    # Note: Removed WhatsApp webhook as per previous cleanup
    # Note: Removed debug endpoints for production
    # Note: Commented out non-existent views to prevent AttributeError
]