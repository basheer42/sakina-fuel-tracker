# shipments/models.py
from django.db import models, transaction
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.db.models import Sum, F, Value, DecimalField as DjangoDecimalField
from decimal import Decimal 
from django.core.exceptions import ValidationError
import sys 

User = get_user_model()

# Dummy class for stdout if not passed from management command or tests
class PrintLikeCommandOutput:
    def write(self, msg, style_func=None):
        if style_func:
            # If a style function is passed (like self.style.SUCCESS), call it
            print(style_func(msg))
        else:
            print(msg)

    class style:
        # These are now simple methods that can be called
        def SUCCESS(self, text): return f"SUCCESS: {text}"
        def ERROR(self, text): return f"ERROR: {text}"
        def WARNING(self, text): return f"WARNING: {text}"
        def NOTICE(self, text): return f"NOTICE: {text}"

# Instantiate the style class directly on the PrintLikeCommandOutput class
PrintLikeCommandOutput.style = PrintLikeCommandOutput.style()


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
         except Product.DoesNotExist: product_name = "N/A (Product Missing)"
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
    INITIAL_DEPLETION_TRIGGER_STATUSES = ['KPC_APPROVED']
    ACTUAL_L20_DEPLETION_TRIGGER_STATUSES = ['LOADED'] 
    DEPLETED_STOCK_STATUSES = ['KPC_APPROVED', 'LOADED', 'GATEPASSED', 'TRANSIT', 'DELIVERED']

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    destination = models.ForeignKey(Destination, on_delete=models.PROTECT, null=False, blank=False, help_text="Destination for this loading.") 
    kpc_order_number = models.CharField(max_length=100, unique=True, help_text="KPC Loading Order No (e.g., Sxxxxx) from Authority PDF. Required and must be unique.") 
    bol_number = models.CharField(max_length=100, blank=True, null=True, unique=True, verbose_name="Final BoL No / KPC Shipment No") 
    loading_date = models.DateField(default=timezone.now, verbose_name="Authority/BOL Date")
    loading_time = models.TimeField(default=timezone.now, verbose_name="Authority/BOL Time")
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='PENDING')
    notes = models.TextField(blank=True, null=True)
    kpc_comments = models.TextField(blank=True, null=True, help_text="Comments from KPC emails")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    _stdout_instance = None # Class attribute to hold the stdout object for model methods

    @property
    def stdout(self):
        # This ensures that model methods always have a stdout-like object to write to.
        # It will be PrintLikeCommandOutput by default, or can be overridden by management commands.
        if not hasattr(Trip, '_stdout_instance') or Trip._stdout_instance is None:
            Trip._stdout_instance = PrintLikeCommandOutput()
        return Trip._stdout_instance
    
    def _get_original_status(self):
        if self.pk:
            try: return Trip.objects.get(pk=self.pk).status
            except Trip.DoesNotExist: return None
        return None

    @property
    def total_requested_from_compartments(self):
        if self.pk and hasattr(self, 'requested_compartments'):
            aggregation_result = self.requested_compartments.aggregate(total_sum=Sum('quantity_requested_litres'))
            return aggregation_result.get('total_sum') or Decimal('0.00')
        return Decimal('0.00')

    @property
    def total_actual_l20_from_compartments(self):
        if self.pk and hasattr(self, 'requested_compartments'):
            aggregation_result = self.requested_compartments.filter(quantity_actual_l20__isnull=False).aggregate(
                total_sum=Sum('quantity_actual_l20')
            )
            return aggregation_result.get('total_sum') or Decimal('0.00')
        return Decimal('0.00')

    @property
    def total_loaded(self): 
        if self.pk and hasattr(self, 'depletions_for_trip'):
             aggregation_result = self.depletions_for_trip.aggregate(total_sum=Sum('quantity_depleted'))
             return aggregation_result.get('total_sum') or Decimal('0.00')
        return Decimal('0.00')

    def perform_stock_depletion(self, use_actual_l20=False, raise_error=True):
        if ShipmentDepletion.objects.filter(trip=self).exists():
            comment_to_add = f"\nATTEMPT TO RE-DEPLETE WITHOUT REVERSAL on {timezone.now().strftime('%Y-%m-%d %H:%M')}. Skipped."
            if self.pk:
                # Using F() expression to append to existing comment safely
                Trip.objects.filter(pk=self.pk).update(
                    kpc_comments=F('kpc_comments') + Value(comment_to_add) if Trip.objects.filter(pk=self.pk, kpc_comments__isnull=False).exists() else Value(comment_to_add.strip())
                )
                self.refresh_from_db(fields=['kpc_comments'])
            else: 
                self.kpc_comments = (self.kpc_comments or "") + comment_to_add
            self.stdout.write(f"Trip {self.id} ({self.kpc_order_number}): Stock depletion already performed and not reversed. Skipping duplicate depletion attempt.", style_func=self.stdout.style.WARNING)
            return True 

        if use_actual_l20:
            total_quantity_to_deplete = self.total_actual_l20_from_compartments
            depletion_basis = "actual L20 quantities"
            if total_quantity_to_deplete <= Decimal('0.00'):
                self.stdout.write(f"Trip {self.id}: Total actual L20 quantity is zero or not set. No stock will be depleted based on L20 actuals.", style_func=self.stdout.style.WARNING)
                return True 
        else:
            total_quantity_to_deplete = self.total_requested_from_compartments
            depletion_basis = "requested quantities"
        
        self.stdout.write(f"Trip {self.id} ({self.kpc_order_number}): Attempting stock depletion of {total_quantity_to_deplete}L based on {depletion_basis} for {self.product.name} to {self.destination.name if self.destination else 'Unknown Destination'}", style_func=self.stdout.style.NOTICE)

        if not total_quantity_to_deplete or total_quantity_to_deplete <= Decimal('0.00'):
            self.stdout.write(f"Trip {self.id}: Quantity to deplete is {total_quantity_to_deplete}L based on {depletion_basis}. Skipping actual depletion steps.", style_func=self.stdout.style.NOTICE)
            return True 

        if not self.destination: 
             if raise_error: raise ValidationError("Cannot perform stock depletion: Trip destination is not set.")
             return False

        available_shipments = Shipment.objects.filter(
            product=self.product, destination=self.destination, 
            quantity_remaining__gt=Decimal('0.00')
        ).order_by('import_date', 'created_at')
        
        current_stock_for_product_dest = available_shipments.aggregate(total=Sum('quantity_remaining'))['total'] or Decimal('0.00')
        
        if total_quantity_to_deplete > current_stock_for_product_dest:
            error_message = (f"Insufficient stock for {self.product.name} destined for {self.destination.name}. "
                             f"Available: {current_stock_for_product_dest}L, Required for depletion: {total_quantity_to_deplete}L (based on {depletion_basis}).")
            self.stdout.write(error_message, style_func=self.stdout.style.ERROR)
            if raise_error: raise ValidationError({'__all__': error_message})
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
            error_message = (f"Critical Error during depletion for Trip {self.id} ({self.kpc_order_number}) "
                             f"for {self.product.name} to {self.destination.name}. "
                             f"Attempted: {total_quantity_to_deplete}L, Sourced: {depleted_amount_for_trip}L ({depletion_basis}). "
                             f"Concurrent operations or stock inconsistency likely.")
            self.stdout.write(error_message, style_func=self.stdout.style.ERROR)
            if raise_error: raise ValidationError({'__all__': error_message})
            return False
        
        self.stdout.write(f"Trip {self.id}: Stock depletion successful. Depleted: {depleted_amount_for_trip}L based on {depletion_basis}.", style_func=self.stdout.style.SUCCESS)
        return True

    def reverse_stock_depletion(self): # stdout is now self.stdout
        self.stdout.write(f"Trip {self.id} ({self.kpc_order_number}): Attempting to reverse stock depletions.", style_func=self.stdout.style.NOTICE)
        depletions = ShipmentDepletion.objects.filter(trip=self)
        if not depletions.exists():
            self.stdout.write(f"Trip {self.id}: No depletions found to reverse.", style_func=self.stdout.style.WARNING)
            return True
        
        reversal_successful_overall = True
        for depletion in depletions:
            try:
                shipment_batch = Shipment.objects.select_for_update().get(pk=depletion.shipment_batch_id)
                shipment_batch.quantity_remaining += depletion.quantity_depleted
                shipment_batch.save(update_fields=['quantity_remaining', 'updated_at'])
                self.stdout.write(f"  Reversed {depletion.quantity_depleted}L to Shpmt ID {shipment_batch.id} ({getattr(shipment_batch, 'vessel_id_tag', 'N/A')})")
            except Shipment.DoesNotExist:
                self.stdout.write(f"ERROR: Shipment batch ID {depletion.shipment_batch_id} not found during reversal for Trip {self.id}.", style_func=self.stdout.style.ERROR)
                reversal_successful_overall = False; break 
            except Exception as e:
                self.stdout.write(f"ERROR reversing depletion for shipment_batch {depletion.shipment_batch_id} on Trip {self.id}: {e}", style_func=self.stdout.style.ERROR)
                reversal_successful_overall = False; break 
        
        if reversal_successful_overall:
            num_deleted, _ = depletions.delete()
            self.stdout.write(f"Trip {self.id}: Successfully reversed and deleted {num_deleted} depletion records.", style_func=self.stdout.style.SUCCESS)
        else:
            self.stdout.write(f"Trip {self.id}: Stock reversal failed or was incomplete. Depletion records NOT deleted. Transaction should be rolled back by caller.", style_func=self.stdout.style.ERROR)
        
        return reversal_successful_overall

    def save(self, *args, **kwargs):
        # Management commands can pass their own stdout object via kwargs
        # Views calling .save() will use the default PrintLikeCommandOutput
        caller_stdout = kwargs.pop('stdout', None)
        if caller_stdout:
            Trip._stdout_instance = caller_stdout # Set class-level stdout for this call context
        elif not hasattr(Trip, '_stdout_instance') or Trip._stdout_instance is None:
            Trip._stdout_instance = PrintLikeCommandOutput() # Ensure default if never set

        is_new = self.pk is None
        original_status_in_db = self._get_original_status()
        
        old_product = None; old_destination = None
        old_total_requested = None; old_total_actual_l20 = None

        if not is_new: 
            try:
                db_instance_before_save = Trip.objects.get(pk=self.pk)
                old_product = db_instance_before_save.product
                old_destination = db_instance_before_save.destination
                old_total_requested = db_instance_before_save.total_requested_from_compartments
                old_total_actual_l20 = db_instance_before_save.total_actual_l20_from_compartments
            except Trip.DoesNotExist: pass 

        status_being_saved = self.status 
        super(Trip, self).save(*args, **kwargs) 
        self.refresh_from_db() 

        current_total_requested_after_save = self.total_requested_from_compartments
        current_total_actual_l20_after_save = self.total_actual_l20_from_compartments
        
        critical_data_changed_for_requested = (not is_new and
                                           (self.product != old_product or
                                            self.destination != old_destination or
                                            current_total_requested_after_save != old_total_requested))
        
        critical_data_changed_for_actuals = (not is_new and
                                           (self.product != old_product or
                                            self.destination != old_destination or
                                            current_total_actual_l20_after_save != old_total_actual_l20))
        
        try:
            deplete_now = False
            use_l20_for_this_depletion = False
            status_has_changed = (original_status_in_db != status_being_saved)

            if status_being_saved in self.INITIAL_DEPLETION_TRIGGER_STATUSES and \
               (is_new or (status_has_changed and original_status_in_db not in self.INITIAL_DEPLETION_TRIGGER_STATUSES)):
                deplete_now = True; use_l20_for_this_depletion = False
                self.stdout.write(f"Trip {self.id}: Status '{status_being_saved}' triggers initial depletion (requested).", style_func=self.stdout.style.NOTICE)

            elif status_being_saved in self.ACTUAL_L20_DEPLETION_TRIGGER_STATUSES and \
                 (is_new or (status_has_changed and original_status_in_db not in self.ACTUAL_L20_DEPLETION_TRIGGER_STATUSES) or critical_data_changed_for_actuals):
                deplete_now = True; use_l20_for_this_depletion = True
                self.stdout.write(f"Trip {self.id}: Status '{status_being_saved}' triggers L20 actuals depletion.", style_func=self.stdout.style.NOTICE)

            elif status_being_saved in self.INITIAL_DEPLETION_TRIGGER_STATUSES and \
                 original_status_in_db == status_being_saved and critical_data_changed_for_requested:
                deplete_now = True; use_l20_for_this_depletion = False
                self.stdout.write(f"Trip {self.id}: Critical requested data changed while status is '{status_being_saved}'. Re-evaluating initial depletion.", style_func=self.stdout.style.NOTICE)

            elif status_being_saved in self.ACTUAL_L20_DEPLETION_TRIGGER_STATUSES and \
                 original_status_in_db == status_being_saved and critical_data_changed_for_actuals:
                deplete_now = True; use_l20_for_this_depletion = True
                self.stdout.write(f"Trip {self.id}: Critical actuals data changed while status is '{status_being_saved}'. Re-evaluating L20 depletion.", style_func=self.stdout.style.NOTICE)

            existing_depletions = ShipmentDepletion.objects.filter(trip=self)
            if existing_depletions.exists():
                if deplete_now: 
                    self.stdout.write(f"Trip {self.id}: Reversing existing depletions before re-depleting...", style_func=self.stdout.style.NOTICE)
                    if not self.reverse_stock_depletion():
                        raise ValidationError("Failed to reverse existing stock depletions for re-evaluation.")
                elif status_has_changed and original_status_in_db in self.DEPLETED_STOCK_STATUSES and status_being_saved not in self.DEPLETED_STOCK_STATUSES:
                    self.stdout.write(f"Trip {self.id}: Status changed from '{original_status_in_db}' to non-depleted '{status_being_saved}'. Reversing depletions...", style_func=self.stdout.style.NOTICE)
                    self.reverse_stock_depletion()

            if deplete_now:
                self.perform_stock_depletion(use_actual_l20=use_l20_for_this_depletion, raise_error=True)
        
        except ValidationError as e:
            self.stdout.write(f"VALIDATION ERROR in Trip.save() stock logic for Trip {self.id} (status: {status_being_saved}): {e}", style_func=self.stdout.style.ERROR)
            if not is_new and original_status_in_db is not None:
                current_kpc_comments = self.kpc_comments or ""
                error_message_short = str(e.message_dict if hasattr(e, 'message_dict') else e)[:200]
                new_comment = f"{current_kpc_comments}\nSTOCK OP. FAILED on {timezone.now().strftime('%Y-%m-%d %H:%M')} (status {original_status_in_db} restored): {error_message_short}".strip()
                Trip.objects.filter(pk=self.pk).update(status=original_status_in_db, kpc_comments=new_comment, updated_at=timezone.now())
            raise 
        except Exception as e: 
            self.stdout.write(f"UNEXPECTED ERROR in Trip.save() stock logic for Trip {self.id}: {e}", style_func=self.stdout.style.ERROR)
            import traceback
            traceback.print_exc() 
            if not is_new and original_status_in_db is not None:
                current_kpc_comments = self.kpc_comments or ""
                error_message_short = str(e)[:200]
                new_comment = f"{current_kpc_comments}\nUNEXPECTED STOCK OP. ERROR on {timezone.now().strftime('%Y-%m-%d %H:%M')} (status {original_status_in_db} restored): {error_message_short}".strip()
                Trip.objects.filter(pk=self.pk).update(status=original_status_in_db, kpc_comments=new_comment, updated_at=timezone.now())
            raise
        finally:
            if caller_stdout: # If stdout was passed, reset the class attribute to default
                Trip._stdout_instance = None


    class Meta:
         ordering = ['-loading_date', '-loading_time']

    def __str__(self):
        order_id_display = self.kpc_order_number or self.bol_number or "N/A"
        dest_name = f" to {self.destination.name}" if self.destination else ""
        product_name_str = self.product.name if self.product else "N/A Product"
        vehicle_plate_str = self.vehicle.plate_number if self.vehicle else "N/A Vehicle"
        return f"Trip {self.id} ({order_id_display}) on {self.loading_date} - {product_name_str} via {vehicle_plate_str}{dest_name}"


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
        except Product.DoesNotExist: product_name = "N/A Product"
        except AttributeError: product_name = "N/A (Trip product not set)"
        actual_str = f", Actual L20: {self.quantity_actual_l20}L" if self.quantity_actual_l20 is not None else ""
        return f"Comp {self.compartment_number} ({product_name}): Req: {self.quantity_requested_litres}L{actual_str} for Trip ID {self.trip_id}"

class ShipmentDepletion(models.Model):
    trip = models.ForeignKey(Trip, related_name='depletions_for_trip', on_delete=models.CASCADE)
    shipment_batch = models.ForeignKey(Shipment, related_name='depletions_from_batch', on_delete=models.PROTECT)
    quantity_depleted = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True) 
    def __str__(self):
        trip_identifier = self.trip.kpc_order_number or self.trip.bol_number or f"ID {self.trip.id}"
        shipment_identifier = self.shipment_batch.vessel_id_tag or f"ID {self.shipment_batch.id}"
        return f"{self.quantity_depleted}L from Shpmt {shipment_identifier} for Trip {trip_identifier}"
    class Meta: ordering = ['created_at']