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
        is_new = self.pk is None
        if is_new and self.quantity_litres is not None:
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
        ('LOADED', 'Loaded from Depot (BoL Received)'), 
        ('GATEPASSED', 'Gatepassed from Depot'), 
        ('TRANSIT', 'In Transit to Customer'), 
        ('DELIVERED', 'Delivered to Customer'),  
        ('CANCELLED', 'Cancelled'),
    ]
    DEPLETION_TRIGGER_STATUSES = ['KPC_APPROVED'] 
    DEPLETED_STOCK_STATUSES = ['KPC_APPROVED', 'LOADING', 'LOADED', 'GATEPASSED', 'TRANSIT', 'DELIVERED'] 

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    destination = models.ForeignKey(Destination, on_delete=models.PROTECT, blank=False, help_text="Destination for this loading.") 
    
    kpc_order_number = models.CharField(max_length=100, unique=True, help_text="KPC Loading Order No (e.g., Sxxxxx) from Authority PDF. Required and must be unique.") 
    bol_number = models.CharField(max_length=100, blank=True, null=True, unique=True, verbose_name="Final BoL No / KPC Shipment No") 

    loading_date = models.DateField(default=timezone.now, verbose_name="Authority/BOL Date")
    loading_time = models.TimeField(default=timezone.now, verbose_name="Authority/BOL Time")
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='PENDING')
    notes = models.TextField(blank=True, null=True)
    kpc_comments = models.TextField(blank=True, null=True, help_text="Comments from KPC emails")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def _get_original_status(self):
        if self.pk:
            try: return Trip.objects.get(pk=self.pk).status
            except Trip.DoesNotExist: return None
        return None

    def perform_stock_depletion(self, raise_error=True):
        if ShipmentDepletion.objects.filter(trip=self).exists():
            print(f"Trip {self.id} ({self.kpc_order_number}): Stock depletion already performed.")
            return True 
        print(f"Trip {self.id} ({self.kpc_order_number}): Attempting stock depletion for {self.product.name} to {self.destination.name if self.destination else 'Unknown Destination'}")
        total_quantity_to_deplete = self.total_requested_from_compartments
        if not total_quantity_to_deplete or total_quantity_to_deplete <= Decimal('0.00'):
            print(f"Trip {self.id}: No quantity requested in compartments (Total: {total_quantity_to_deplete}). Skipping depletion.")
            return True 
        if not self.destination: 
             if raise_error: raise ValidationError("Cannot perform stock depletion: Trip destination is not set.")
             return False
        available_shipments = Shipment.objects.filter(product=self.product, destination=self.destination, quantity_remaining__gt=Decimal('0.00')).order_by('import_date', 'created_at')
        current_stock_for_product_dest = available_shipments.aggregate(total=Sum('quantity_remaining'))['total'] or Decimal('0.00')
        if total_quantity_to_deplete > current_stock_for_product_dest:
            error_message = (f"Insufficient stock for {self.product.name} destined for {self.destination.name}. Available: {current_stock_for_product_dest}L, Requested for this trip: {total_quantity_to_deplete}L.")
            print(f"ERROR for Trip {self.id}: {error_message}")
            if raise_error: raise ValidationError(error_message)
            return False
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
            error_message = (f"Critical Error: Could not source the full quantity for Trip {self.id} ({self.kpc_order_number}) for {self.product.name} to {self.destination.name}. Requested: {total_quantity_to_deplete}L, Sourced: {depleted_amount_for_trip}L. Stock might be inconsistent.")
            print(f"ERROR for Trip {self.id}: {error_message}")
            if raise_error: raise ValidationError(error_message)
            return False
        print(f"Trip {self.id}: Stock depletion successful. Depleted: {depleted_amount_for_trip}L")
        return True

    def reverse_stock_depletion(self):
        print(f"Trip {self.id} ({self.kpc_order_number}): Attempting to reverse stock depletions.")
        depletions = ShipmentDepletion.objects.filter(trip=self)
        if not depletions.exists():
            print(f"Trip {self.id}: No depletions found to reverse.")
            return True
        reversal_successful = True
        for depletion in depletions:
            try:
                shipment_batch = depletion.shipment_batch
                shipment_batch.quantity_remaining += depletion.quantity_depleted
                shipment_batch.save(update_fields=['quantity_remaining', 'updated_at'])
                print(f"  Reversed {depletion.quantity_depleted}L to Shipment ID {shipment_batch.id} ({getattr(shipment_batch, 'vessel_id_tag', 'N/A')})")
            except Exception as e:
                print(f"ERROR reversing depletion for shipment_batch {depletion.shipment_batch_id} on Trip {self.id}: {e}")
                reversal_successful = False 
        if reversal_successful:
            num_deleted, _ = depletions.delete()
            print(f"Trip {self.id}: Reversed and deleted {num_deleted} depletion records.")
        else:
            print(f"Trip {self.id}: Some errors occurred during stock reversal. Depletion records NOT deleted to allow investigation.")
        return reversal_successful

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        original_status = self._get_original_status()
        
        super().save(*args, **kwargs) 

        try:
            with transaction.atomic():
                if self.status in self.DEPLETION_TRIGGER_STATUSES and \
                   (is_new or original_status not in self.DEPLETION_TRIGGER_STATUSES) and \
                   not ShipmentDepletion.objects.filter(trip=self).exists():
                    print(f"Trip {self.id} status is '{self.status}'. Original: '{original_status}'. Attempting depletion.")
                    self.perform_stock_depletion(raise_error=True)

                elif original_status in self.DEPLETED_STOCK_STATUSES and \
                     self.status not in self.DEPLETED_STOCK_STATUSES and \
                     ShipmentDepletion.objects.filter(trip=self).exists():
                    print(f"Trip {self.id} status changed from '{original_status}' to '{self.status}'. Attempting to reverse depletions.")
                    self.reverse_stock_depletion()
        
        except ValidationError as e:
            print(f"VALIDATION ERROR during stock operation for Trip {self.id} on status change to '{self.status}': {e}")
            if not is_new and original_status:
                # Correct Indentation Starts Here
                self.status = original_status
                self.kpc_comments = (self.kpc_comments or "") + f"\nSTOCK OP. FAILED (status reverted): {str(e)[:200]}"
                Trip.objects.filter(pk=self.pk).update(status=self.status, kpc_comments=self.kpc_comments, updated_at=timezone.now())
                # Correct Indentation Ends Here
            raise 
        except Exception as e: 
            print(f"UNEXPECTED ERROR during stock operation for Trip {self.id}: {e}")
            if not is_new and original_status:
                # Correct Indentation Starts Here
                self.status = original_status
                self.kpc_comments = (self.kpc_comments or "") + f"\nUNEXPECTED STOCK OP. ERROR (status reverted): {str(e)[:200]}"
                Trip.objects.filter(pk=self.pk).update(status=self.status, kpc_comments=self.kpc_comments, updated_at=timezone.now())
                # Correct Indentation Ends Here
            raise

    class Meta:
         ordering = ['-loading_date', '-loading_time']

    def __str__(self):
        order_id_display = self.kpc_order_number or self.bol_number or "N/A"
        dest_name = f" to {self.destination.name}" if self.destination else ""
        return f"Trip {self.id} ({order_id_display}) on {self.loading_date} - {self.product.name} via {self.vehicle.plate_number}{dest_name}"

    @property
    def total_requested_from_compartments(self):
        if self.pk and hasattr(self, 'requested_compartments'):
            aggregation_result = self.requested_compartments.aggregate(total_sum=Sum('quantity_requested_litres'))
            return aggregation_result.get('total_sum') or Decimal('0.00')
        return Decimal('0.00')

    @property
    def total_loaded(self): 
        if self.pk and hasattr(self, 'depletions_for_trip'):
             aggregation_result = self.depletions_for_trip.aggregate(total_sum=Sum('quantity_depleted'))
             return aggregation_result.get('total_sum') or Decimal('0.00')
        return Decimal('0.00')

class LoadingCompartment(models.Model):
    trip = models.ForeignKey(Trip, related_name='requested_compartments', on_delete=models.CASCADE)
    compartment_number = models.PositiveIntegerField()
    quantity_requested_litres = models.DecimalField(max_digits=10, decimal_places=2)
    quantity_actual_l20 = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    temperature = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    density = models.DecimalField(max_digits=7, decimal_places=3, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        unique_together = ('trip', 'compartment_number')
        ordering = ['compartment_number']
    def __str__(self):
        try: product_name = self.trip.product.name
        except: product_name = "N/A" 
        actual_str = f", Actual L20: {self.quantity_actual_l20}L" if self.quantity_actual_l20 is not None else ""
        return f"Comp {self.compartment_number} ({product_name}): Req: {self.quantity_requested_litres}L{actual_str}"

class ShipmentDepletion(models.Model):
    trip = models.ForeignKey(Trip, related_name='depletions_for_trip', on_delete=models.CASCADE)
    shipment_batch = models.ForeignKey(Shipment, related_name='depletions_from_batch', on_delete=models.PROTECT)
    quantity_depleted = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    def __str__(self): return f"{self.quantity_depleted}L from Shpmt ID {self.shipment_batch.id} ({getattr(self.shipment_batch, 'vessel_id_tag', '')}) for Trip ID {self.trip.id} ({self.trip.kpc_order_number or self.trip.bol_number})"
    class Meta: ordering = ['created_at']