# shipments/admin.py
from django.contrib import admin, messages
from django.db.models import Sum, DecimalField as DjangoDecimalField
from decimal import Decimal
from .models import (
    Shipment, Customer, Vehicle, Product, Destination,
    Trip, LoadingCompartment, ShipmentDepletion, UserProfile
)

@admin.register(Destination)
class DestinationAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_at', 'updated_at')
    search_fields = ('name',)

class LoadingCompartmentInline(admin.TabularInline):
    model = LoadingCompartment
    extra = 3
    min_num = 0
    fields = ('compartment_number', 'quantity_requested_litres', 'quantity_actual_l20', 'temperature', 'density') # Added new fields
    readonly_fields = ()

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
    list_display = ('plate_number', 'trailer_number', 'created_at', 'updated_at')
    search_fields = ('plate_number', 'trailer_number')
    readonly_fields = ('created_at', 'updated_at')

@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
   list_display = ('id', 'loading_date', 'kpc_order_number', 'bol_number', 'vehicle', 'product', 'customer', 'destination', 'status', 'user', 'total_requested_from_compartments', 'total_loaded') # Added kpc_order_number
   list_filter = ('loading_date', 'status', 'product', 'vehicle', 'customer', 'destination', 'user')
   search_fields = ('kpc_order_number', 'bol_number', 'vehicle__plate_number', 'customer__name', 'product__name', 'destination__name') # Added kpc_order_number
   date_hierarchy = 'loading_date'
   inlines = [LoadingCompartmentInline]
   readonly_fields = ('user', 'total_loaded', 'total_requested_from_compartments', 'created_at', 'updated_at')

   fieldsets = (
        (None, {'fields': ('user', ('loading_date', 'loading_time'), 'status')}),
        ('Order & BoL Numbers', {'fields': ('kpc_order_number', 'bol_number')}), # Grouped order numbers
        ('Participants & Product', {'fields': ('vehicle', 'customer', 'product', 'destination')}),
        ('Quantities (Read-only Summary)', {'fields': ('total_requested_from_compartments', 'total_loaded')}),
        ('Additional Information', {'fields': ('notes','kpc_comments'), 'classes': ('collapse',)}),
        ('Timestamps', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)})
   )

   def get_changeform_initial_data(self, request):
       initial = super().get_changeform_initial_data(request)
       if not request.resolver_match.kwargs.get('object_id'):
            initial['user'] = request.user
       return initial

   def save_model(self, request, obj, form, change):
       if not obj.pk:
           obj.user = request.user
       super().save_model(request, obj, form, change)

   def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for instance in instances:
            if not instance.pk and hasattr(instance, 'trip') and not instance.trip_id:
                instance.trip = form.instance
            instance.save()
        for obj_to_delete in formset.deleted_objects:
            obj_to_delete.delete()
        formset.save_m2m()


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = (
        'vessel_id_tag', 'import_date', 'supplier_name', 'product',
        'destination', 'quantity_litres', 'quantity_remaining',
        'price_per_litre', 'user', 'created_at'
    )
    list_filter = ('product', 'supplier_name', 'import_date', 'destination', 'user')
    search_fields = ('vessel_id_tag', 'supplier_name', 'product__name', 'destination__name', 'notes')
    date_hierarchy = 'import_date'
    ordering = ('import_date',)
    readonly_fields = ('user', 'quantity_remaining', 'total_cost', 'created_at', 'updated_at')

    fieldsets = (
        (None, {'fields': ('user', 'vessel_id_tag', 'import_date', 'supplier_name')}),
        ('Product Details', {'fields': ('product', 'destination')}),
        ('Quantities & Cost', {'fields': ('quantity_litres', 'price_per_litre', 'total_cost', 'quantity_remaining')}),
        ('Notes', {'fields': ('notes',)}),
        ('Timestamps', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)})
    )

    def save_model(self, request, obj, form, change):
        is_new = not obj.pk
        original_quantity_litres = None
        if is_new: obj.user = request.user
        elif change and 'quantity_litres' in form.changed_data:
            try: original_shipment = Shipment.objects.get(pk=obj.pk); original_quantity_litres = original_shipment.quantity_litres
            except Shipment.DoesNotExist: pass
            new_quantity_litres = form.cleaned_data.get('quantity_litres', obj.quantity_litres)
            depleted_from_this_batch = ShipmentDepletion.objects.filter(shipment_batch=obj).aggregate(total_depleted=Sum('quantity_depleted'))['total_depleted'] or Decimal('0.00')
            if new_quantity_litres < depleted_from_this_batch:
                messages.error(request, f"Cannot set total quantity ({new_quantity_litres}L) less than what has already been depleted ({depleted_from_this_batch}L) for shipment '{obj.vessel_id_tag}'. Update aborted.")
                return
            obj.quantity_remaining = new_quantity_litres - depleted_from_this_batch
            messages.info(request, f"Quantity remaining for shipment {obj.vessel_id_tag} updated to {obj.quantity_remaining}L.")
        super().save_model(request, obj, form, change)

@admin.register(ShipmentDepletion)
class ShipmentDepletionAdmin(admin.ModelAdmin):
    list_display = ('trip', 'shipment_batch', 'quantity_depleted', 'created_at')
    list_filter = ('trip__product', 'created_at', 'shipment_batch__product')
    readonly_fields = ('trip', 'shipment_batch', 'quantity_depleted', 'created_at')
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'phone_number', 'telegram_chat_id', 'department', 'created_at']
    list_filter = ['created_at', 'department']
    search_fields = ['user__username', 'phone_number', 'telegram_chat_id']
    readonly_fields = ['created_at', 'updated_at']
