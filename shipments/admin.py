# shipments/admin.py
from django.contrib import admin
from django.db.models import Sum
from .models import (
    Shipment, Customer, Vehicle, Product,
    Trip, LoadingCompartment, ShipmentDepletion # Added ShipmentDepletion
)

# --- Define Inlines for the admin ---

class LoadingCompartmentInline(admin.TabularInline):
    model = LoadingCompartment
    extra = 1 # For new Trips, show 1 by default (formset itself ensures 3)
    fields = ('compartment_number', 'quantity_requested_litres')
    # For editing existing trips, this will show existing compartments

# Inline to show depletions for a Trip (read-only is often best for depletions)
class ShipmentDepletionInline(admin.TabularInline):
    model = ShipmentDepletion
    extra = 0 # Don't show extra blank forms for depletions
    fields = ('shipment_batch', 'quantity_depleted', 'created_at')
    readonly_fields = ('shipment_batch', 'quantity_depleted', 'created_at') # Make them read-only
    can_delete = False # Usually, depletions shouldn't be manually deleted from here

    def has_add_permission(self, request, obj=None): # Prevent adding depletions manually via Trip admin
        return False


# --- Define ModelAdmins ---

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
   list_display = ('loading_date', 'loading_time', 'bol_number', 'vehicle', 'product', 'customer', 'status', 'user', 'total_loaded')
   list_filter = ('loading_date', 'status', 'product', 'vehicle', 'customer', 'user')
   search_fields = ('vehicle__plate_number', 'customer__name', 'product__name', 'bol_number')
   date_hierarchy = 'loading_date'
   # Show requested compartments first, then the depletions
   inlines = [LoadingCompartmentInline, ShipmentDepletionInline] 
   readonly_fields = ('user',)

   def get_changeform_initial_data(self, request):
       initial = super().get_changeform_initial_data(request)
       if not request.resolver_match.kwargs.get('object_id'):
            initial['user'] = request.user.pk
       return initial

   def save_model(self, request, obj, form, change):
       if not change: # New object
           obj.user = request.user
       super().save_model(request, obj, form, change)
       # FIFO logic and ShipmentDepletion creation will be handled by the public-facing view's save.
       # For admin, we might need to add similar logic or a signal if direct admin Trip creation/editing should trigger FIFO.
       # For now, admin is for raw data management; FIFO processing is for user-facing forms.


@admin.register(Shipment) # <-- MODIFIED
class ShipmentAdmin(admin.ModelAdmin):
    list_display = (
        'import_date', 'supplier_name', 'product', 
        'quantity_litres', 'quantity_remaining', # Added quantity_remaining
        'price_per_litre', 'user', 'created_at'
    )
    list_filter = ('product', 'supplier_name', 'import_date', 'user')
    search_fields = ('supplier_name', 'product__name', 'notes')
    date_hierarchy = 'import_date'
    ordering = ('import_date',) # Changed to oldest first to see FIFO order
    # Make user and quantity_remaining read-only in the form. 
    # quantity_remaining is managed by FIFO logic.
    readonly_fields = ('user', 'quantity_remaining',) 

    def save_model(self, request, obj, form, change):
        if not change: # If this is a new object being added
            obj.user = request.user
            # Initialize quantity_remaining to full quantity for new shipments
            obj.quantity_remaining = form.cleaned_data.get('quantity_litres', 0) 
        super().save_model(request, obj, form, change)

# Register ShipmentDepletion to make it visible (optional, mostly for debugging)
@admin.register(ShipmentDepletion)
class ShipmentDepletionAdmin(admin.ModelAdmin):
    list_display = ('trip', 'shipment_batch', 'quantity_depleted', 'created_at')
    list_filter = ('trip__product', 'created_at')
    readonly_fields = ('trip', 'shipment_batch', 'quantity_depleted') # Generally, these shouldn't be edited directly

    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    # Allow deletion for admin cleanup if necessary, but typically these are system-generated
    # def has_delete_permission(self, request, obj=None): return False 