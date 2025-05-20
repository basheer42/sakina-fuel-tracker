# shipments/urls.py
from django.urls import path
from . import views # This correctly imports your views.py

app_name = 'shipments'

urlpatterns = [
    path('', views.home_view, name='home'),
    
    # Shipment URLs (Your original URLs)
    path('shipments/list/', views.shipment_list_view, name='shipment-list'),
    path('shipments/add/', views.shipment_add_view, name='shipment-add'),
    path('shipments/<int:pk>/', views.shipment_detail_view, name='shipment-detail'),
    path('shipments/edit/<int:pk>/', views.shipment_edit_view, name='shipment-edit'),
    path('shipments/delete/<int:pk>/', views.shipment_delete_view, name='shipment-delete'),
    
    # Trip/Loading URLs (Your original URLs)
    path('trips/list/', views.trip_list_view, name='trip-list'),
    path('trips/add/', views.trip_add_view, name='trip-add'),
    path('trips/<int:pk>/', views.trip_detail_view, name='trip-detail'),
    path('trips/edit/<int:pk>/', views.trip_edit_view, name='trip-edit'),
    path('trips/delete/<int:pk>/', views.trip_delete_view, name='trip-delete'),
    
    # NEW URL for PDF Loading Authority Upload
    path('trips/upload-authority/', views.upload_loading_authority_view, name='trip-upload-authority'),

    # Auth related (Your original URL)
    path('signup/', views.signup_view, name='signup'), # Assuming this is still how you handle signup via this app

    # AJAX (Your original URL)
    path('ajax/get-vehicle-capacity/', views.get_vehicle_capacity_for_product_view, name='ajax_get_vehicle_capacity'),
    
    # Reports (Your original URLs)
    path('reports/monthly-stock/', views.monthly_stock_summary_view, name='monthly-stock-summary'),
    path('reports/truck-activity/', views.truck_activity_dashboard_view, name='truck-activity-dashboard'),
]