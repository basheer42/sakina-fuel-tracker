# shipments/models.py
from django.db import models, transaction
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.db.models import Sum, F, DecimalField
from decimal import Decimal 
from django.core.exceptions import ValidationError

User = get_user_model()

class Product(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    def __str__(self): return self.name
    class Meta: ordering = ['name']

class Customer(models.Model):
    name = models.CharField(max_length=200, unique=True)
    address = models.TextField(blank=True, null=True)
    contact_person = models.CharField(max_length=200, blank=True, null=True)
    phone_number = models.CharField(max_length=50, blank=True, null=True)
    email = models.EmailField(max_length=254, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    def __str__(self): return self.name
    class Meta: ordering = ['name']

class Vehicle(models.Model):
    plate_number = models.CharField(max_length=50, unique=True)
    trailer_number = models.CharField(max_length=50, blank=True, null=True, unique=True) 
    capacity_pms = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True, help_text="Total capacity when configured for PMS")
    capacity_ago = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True, help_text="Total capacity when configured for AGO")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    def __str__(self): return self.plate_number
    class Meta: ordering = ['plate_number']

class Destination(models.Model):
    name = models.CharField(max_length=100, unique=True, help_text="e.g., South Sudan, DRC, Local Nairobi")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    def __str__(self): return self.name
    class Meta: ordering = ['name']

class Shipment(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    vessel_id_tag = models.CharField(
        max_length=100, unique=True, 
        verbose_name="Vessel ID / Shipment Unique ID",
        help_text="The unique identification tag for the shipment (e.g., AGO KG09/2025). This field is required and must be unique."
    )
    import_date = models.DateField(default=timezone.now)
    supplier_name = models.CharField(max_length=200)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    # Destination for Shipment: can remain nullable if shipments are not always pre-assigned
    destination = models.ForeignKey(Destination, on_delete=models.PROTECT, null=True, blank=True, help_text="Destination this shipment is earmarked for, if any.")
    quantity_litres = models.DecimalField(max_digits=10, decimal_places=2)
    price_per_litre = models.DecimalField(max_digits=7, decimal_places=3)
    notes = models.TextField(blank=True, null=True)
    quantity_remaining = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
         try: product_name = self.product.name
         except AttributeError: product_name = "N/A (No Product)"
         tag_display = self.vessel_id_tag if self.vessel_id_tag else "(No Vessel ID)"
         dest_display = f" for {self.destination.name}" if self.destination else ""
         return f"ID {self.id} [{tag_display}] - {product_name}{dest_display} ({self.quantity_litres}L)"

    @property
    def total_cost(self):
        if self.quantity_litres is not None and self.price_per_litre is not None:
            return self.quantity_litres * self.price_per_litre
        return Decimal('0.00') 
    
    def save(self, *args, **kwargs):
        if not self.pk and self.quantity_litres is not None and self.quantity_remaining == Decimal('0.00'):
            self.quantity_remaining = self.quantity_litres
        super().save(*args, **kwargs)
    
    class Meta:
         ordering = ['import_date', 'created_at']

class Trip(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending KPC Action'), 
        ('KPC_APPROVED', 'KPC Approved (Stock Booked)'),
        ('KPC_REJECTED', 'KPC Rejected'),  
        ('LOADING', 'Loading @ Depot'),   
        ('LOADED', 'Loaded from Depot'),  
        ('GATEPASSED', 'Gatepassed from Depot'), 
        ('TRANSIT', 'In Transit to Customer'), 
        ('DELIVERED', 'Delivered to Customer'),  
        ('CANCELLED', 'Cancelled'),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    # Destination for Trip: Made non-nullable (blank=False, null=False by default)
    # This assumes all new Trips created (e.g. from PDF) will have a destination.
    destination = models.ForeignKey(Destination, on_delete=models.PROTECT, blank=False, help_text="Destination for this loading.") 
    loading_date = models.DateField(default=timezone.now, verbose_name="Authority/BOL Date")
    loading_time = models.TimeField(default=timezone.now, verbose_name="Authority/BOL Time")
    bol_number = models.CharField(max_length=100, blank=True, null=True, unique=True, verbose_name="KPC Order No / Final BOL No")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    notes = models.TextField(blank=True, null=True)
    kpc_comments = models.TextField(blank=True, null=True, help_text="Comments from KPC emails")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def perform_stock_depletion(self):
        # ... (this method remains the same as last provided) ...
        if ShipmentDepletion.objects.filter(trip=self).exists():
            print(f"Trip {self.id} ({self.bol_number}): Stock depletion already performed or initiated.")
            return True 
        print(f"Trip {self.id} ({self.bol_number}): Attempting stock depletion for {self.product.name} to {self.destination.name if self.destination else 'Unknown Destination'}")
        total_quantity_to_deplete = self.total_requested_from_compartments
        if not total_quantity_to_deplete or total_quantity_to_deplete <= Decimal('0.00'):
            print(f"Trip {self.id}: No quantity requested in compartments (Total: {total_quantity_to_deplete}). Skipping depletion.")
            return True 
        if not self.destination: 
             raise ValidationError("Cannot perform stock depletion: Trip destination is not set.")
        available_shipments = Shipment.objects.filter(product=self.product, destination=self.destination, quantity_remaining__gt=Decimal('0.00')).order_by('import_date', 'created_at')
        current_stock_for_product_dest = available_shipments.aggregate(total=Sum('quantity_remaining'))['total'] or Decimal('0.00')
        if total_quantity_to_deplete > current_stock_for_product_dest:
            error_message = (f"Insufficient stock for {self.product.name} destined for {self.destination.name}. Available: {current_stock_for_product_dest}L, Requested for this trip: {total_quantity_to_deplete}L.")
            print(f"ERROR for Trip {self.id}: {error_message}")
            raise ValidationError({'status': error_message}) 
        depleted_amount_for_trip = Decimal('0.00')
        for batch in available_shipments:
            if depleted_amount_for_trip >= total_quantity_to_deplete: break
            can_take_from_batch = batch.quantity_remaining
            take_this_much = min(can_take_from_batch, total_quantity_to_deplete - depleted_amount_for_trip)
            if take_this_much > 0:
                ShipmentDepletion.objects.create(trip=self, shipment_batch=batch, quantity_depleted=take_this_much)
                batch.quantity_remaining -= take_this_much
                batch.save(update_fields=['quantity_remaining', 'updated_at'])
                depleted_amount_for_trip += take_this_much
        if depleted_amount_for_trip < total_quantity_to_deplete:
            error_message = (f"Critical Error: Could not source the full quantity for Trip {self.id} ({self.bol_number}) for {self.product.name} to {self.destination.name}. Requested: {total_quantity_to_deplete}L, Sourced: {depleted_amount_for_trip}L. Stock might be inconsistent.")
            print(f"ERROR for Trip {self.id}: {error_message}")
            raise ValidationError({'status': error_message})
        print(f"Trip {self.id}: Stock depletion successful. Depleted: {depleted_amount_for_trip}L")
        return True

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        original_status = None
        if not is_new:
            try: original_status = Trip.objects.get(pk=self.pk).status
            except Trip.DoesNotExist: pass
        super().save(*args, **kwargs) 
        if self.status == 'KPC_APPROVED' and (is_new or original_status != 'KPC_APPROVED') and not ShipmentDepletion.objects.filter(trip=self).exists():
            print(f"Trip {self.id} status changed to KPC_APPROVED. Original: {original_status}. Attempting depletion.")
            try:
                with transaction.atomic(): 
                    self.perform_stock_depletion()
            except ValidationError as e:
                print(f"VALIDATION ERROR during automated stock depletion for Trip {self.id}: {e.message if hasattr(e, 'message') else e}")
                raise 
            except Exception as e: 
                print(f"UNEXPECTED ERROR during automated stock depletion for Trip {self.id}: {e}")
                raise 

    class Meta:
         ordering = ['-loading_date', '-loading_time']

    def __str__(self):
        dest_name = f" to {self.destination.name}" if self.destination else ""
        return f"Trip {self.id} ({self.bol_number or 'N/A'}) on {self.loading_date} - {self.product.name} via {self.vehicle.plate_number}{dest_name}"

    @property
    def total_requested_from_compartments(self):
        if hasattr(self, 'requested_compartments'):
            aggregation_result = self.requested_compartments.aggregate(total_sum=Sum('quantity_requested_litres'))
            return aggregation_result.get('total_sum') or Decimal('0.00')
        return Decimal('0.00')

    @property
    def total_loaded(self): 
        if hasattr(self, 'depletions_for_trip'):
             aggregation_result = self.depletions_for_trip.aggregate(total_sum=Sum('quantity_depleted'))
             return aggregation_result.get('total_sum') or Decimal('0.00')
        return Decimal('0.00')

class LoadingCompartment(models.Model):
    trip = models.ForeignKey(Trip, related_name='requested_compartments', on_delete=models.CASCADE)
    compartment_number = models.PositiveIntegerField()
    quantity_requested_litres = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        unique_together = ('trip', 'compartment_number')
        ordering = ['compartment_number']
    def __str__(self):
        try: product_name = self.trip.product.name
        except: product_name = "N/A" 
        return f"Request Comp {self.compartment_number} ({product_name}): {self.quantity_requested_litres}L"

class ShipmentDepletion(models.Model):
    trip = models.ForeignKey(Trip, related_name='depletions_for_trip', on_delete=models.CASCADE)
    shipment_batch = models.ForeignKey(Shipment, related_name='depletions_from_batch', on_delete=models.PROTECT)
    quantity_depleted = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    def __str__(self):
        return f"{self.quantity_depleted}L from Shpmt ID {self.shipment_batch.id} for Trip ID {self.trip.id}"
    class Meta:
        ordering = ['created_at']