# shipments/admin.py
from django.contrib import admin
from django.db.models import Sum 
from .models import (
    Shipment, Customer, Vehicle, Product, Destination, # Added Destination
    Trip, LoadingCompartment, ShipmentDepletion
)

# Register Destination model if you want to manage Destinations in admin
@admin.register(Destination)
class DestinationAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_at', 'updated_at')
    search_fields = ('name',)

class LoadingCompartmentInline(admin.TabularInline):
    model = LoadingCompartment
    extra = 1 
    fields = ('compartment_number', 'quantity_requested_litres')

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'created_at')
    search_fields = ('name',)

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('name', 'contact_person', 'phone_number', 'email')
    search_fields = ('name', 'contact_person', 'email')

@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ('plate_number', 'trailer_number', 'capacity_pms', 'capacity_ago')
    search_fields = ('plate_number', 'trailer_number')

@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
   list_display = ('loading_date', 'bol_number', 'vehicle', 'product', 'customer', 'destination', 'status', 'user', 'total_loaded') # Added destination
   list_filter = ('loading_date', 'status', 'product', 'vehicle', 'customer', 'destination', 'user') # Added destination
   search_fields = ('vehicle__plate_number', 'customer__name', 'product__name', 'bol_number', 'destination__name') # Added destination
   date_hierarchy = 'loading_date'
   inlines = [LoadingCompartmentInline]
   readonly_fields = ('user',) 

   fieldsets = (
        (None, {'fields': ('user', ('loading_date', 'loading_time'), 'bol_number', 'status')}),
        ('Participants', {'fields': ('vehicle', 'customer', 'product', 'destination')}), # Added destination
        ('Additional Information', {'fields': ('notes','kpc_comments'), 'classes': ('collapse',)}), # Added kpc_comments
   )

   def get_changeform_initial_data(self, request):
       initial = super().get_changeform_initial_data(request)
       if not request.resolver_match.kwargs.get('object_id'):
            initial['user'] = request.user.pk
       return initial

   def save_model(self, request, obj, form, change):
       if not obj.pk: 
           obj.user = request.user
       super().save_model(request, obj, form, change)

@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = (
        'vessel_id_tag', 
        'import_date', 
        'supplier_name', 
        'product', 
        'destination', # Added destination
        'quantity_litres', 
        'quantity_remaining',
        'price_per_litre', 
        'user', 
        'created_at'
    )
    list_filter = ('product', 'supplier_name', 'import_date', 'destination', 'user') # Added destination
    search_fields = ('vessel_id_tag', 'supplier_name', 'product__name', 'destination__name', 'notes') # Added destination
    date_hierarchy = 'import_date'
    ordering = ('import_date',) 
    readonly_fields = ('user', 'quantity_remaining',) 

    def save_model(self, request, obj, form, change):
        if not obj.pk: 
            obj.user = request.user
        # quantity_remaining handled by model's save method
        super().save_model(request, obj, form, change)

@admin.register(ShipmentDepletion)
class ShipmentDepletionAdmin(admin.ModelAdmin):
    list_display = ('trip', 'shipment_batch', 'quantity_depleted', 'created_at')
    list_filter = ('trip__product', 'created_at', 'shipment_batch__product')
    readonly_fields = ('trip', 'shipment_batch', 'quantity_depleted', 'created_at')

    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False