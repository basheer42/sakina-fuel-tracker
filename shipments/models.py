# shipments/models.py
from django.db import models, transaction
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.db.models import Sum, F, Value, DecimalField as DjangoDecimalField 
from django.core.cache import cache
from django.core.exceptions import ValidationError
from decimal import Decimal 
import logging
import datetime 

logger = logging.getLogger(__name__)
User = get_user_model()

class Product(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self): 
        return self.name
    
    def clean(self):
        super().clean()
        if self.name and len(self.name.strip()) < 2:
            raise ValidationError('Product name must be at least 2 characters long')
        if self.name:
            self.name = self.name.strip().upper()
    
    class Meta: 
        ordering = ['name']
        indexes = [
            models.Index(fields=['name']),
        ]


class Customer(models.Model):
    name = models.CharField(max_length=200, unique=True)
    address = models.TextField(blank=True, null=True)
    contact_person = models.CharField(max_length=200, blank=True, null=True)
    phone_number = models.CharField(max_length=50, blank=True, null=True)
    email = models.EmailField(max_length=254, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self): 
        return self.name
    
    def clean(self):
        super().clean()
        if self.name and len(self.name.strip()) < 2:
            raise ValidationError('Customer name must be at least 2 characters long')
        if self.email and '@' not in self.email: 
            raise ValidationError('Please enter a valid email address')
    
    class Meta: 
        ordering = ['name']
        indexes = [
            models.Index(fields=['name']),
        ]


class Vehicle(models.Model):
    plate_number = models.CharField(max_length=50, unique=True)
    trailer_number = models.CharField(max_length=50, blank=True, null=True, unique=True) 
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self): 
        return self.plate_number
    
    def clean(self):
        super().clean()
        if self.plate_number and len(self.plate_number.strip()) < 3:
            raise ValidationError('Plate number must be at least 3 characters long')
        if self.plate_number:
            self.plate_number = self.plate_number.strip().upper()
        if self.trailer_number:
            self.trailer_number = self.trailer_number.strip().upper()
    
    class Meta: 
        ordering = ['plate_number']
        indexes = [
            models.Index(fields=['plate_number']),
        ]


class Destination(models.Model):
    name = models.CharField(max_length=100, unique=True, help_text="e.g., South Sudan, DRC, Local Nairobi")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self): 
        return self.name
    
    def clean(self):
        super().clean()
        if self.name and len(self.name.strip()) < 2:
            raise ValidationError('Destination name must be at least 2 characters long')
    
    class Meta: 
        ordering = ['name']
        indexes = [
            models.Index(fields=['name']),
        ]


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
         try: 
             product_name = self.product.name
         except (Product.DoesNotExist, AttributeError): 
             product_name = "N/A (Product Missing)"
         tag_display = self.vessel_id_tag if self.vessel_id_tag else "(No Vessel ID)"
         dest_display = f" for {self.destination.name}" if self.destination else ""
         return f"ID {self.id} [{tag_display}] - {product_name}{dest_display} ({self.quantity_litres}L)"

    @property
    def total_cost(self):
        if self.quantity_litres is not None and self.price_per_litre is not None:
            return self.quantity_litres * self.price_per_litre
        return Decimal('0.00') 
    
    def clean(self):
        super().clean()
        if self.quantity_litres and self.quantity_litres <= 0:
            raise ValidationError('Quantity must be positive')
        if self.price_per_litre and self.price_per_litre <= 0:
            raise ValidationError('Price per litre must be positive')
        if self.quantity_remaining and self.quantity_remaining < 0:
            raise ValidationError('Quantity remaining cannot be negative')
        if self.vessel_id_tag and len(self.vessel_id_tag.strip()) < 3:
            raise ValidationError('Vessel ID must be at least 3 characters long')
        if (self.quantity_litres is not None and self.quantity_remaining is not None and 
            self.quantity_remaining > self.quantity_litres):
            raise ValidationError('Quantity remaining cannot exceed total quantity')
    
    def save(self, *args, **kwargs):
        self.full_clean()
        is_new = self.pk is None
        if is_new and self.quantity_litres is not None:
            self.quantity_remaining = self.quantity_litres 
        if self.pk:
            cache.delete(f"shipment_total_cost_{self.pk}")
        super().save(*args, **kwargs)
    
    class Meta:
        ordering = ['-import_date', '-created_at']
        indexes = [
            models.Index(fields=['import_date']),
            models.Index(fields=['product', 'destination']),
            models.Index(fields=['quantity_remaining']),
            models.Index(fields=['user', 'import_date']),
            models.Index(fields=['vessel_id_tag']),
            models.Index(fields=['product', 'import_date']),
            models.Index(fields=['supplier_name', 'import_date']),
        ]


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
    # Statuses that trigger depletion based on *requested* quantities
    INITIAL_DEPLETION_TRIGGER_STATUSES = ['KPC_APPROVED']
    # Statuses that trigger depletion based on *actual L20* quantities from BoL
    ACTUAL_L20_DEPLETION_TRIGGER_STATUSES = ['LOADED'] 
    # All statuses where stock should be considered depleted/committed in some form
    DEPLETED_STOCK_STATUSES = ['KPC_APPROVED', 'LOADING', 'LOADED', 'GATEPASSED', 'TRANSIT', 'DELIVERED']


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
    
    def _get_original_status(self):
        if self.pk:
            try: 
                return Trip.objects.get(pk=self.pk).status
            except Trip.DoesNotExist: 
                return None
        return None

    @property
    def total_requested_from_compartments(self):
        cache_key = f"trip_requested_{self.pk}"
        cached_value = cache.get(cache_key)
        if cached_value is not None:
            return cached_value
            
        if self.pk and hasattr(self, 'requested_compartments'):
            qs = self.requested_compartments.all() if hasattr(self.requested_compartments, 'all') else self.requested_compartments
            aggregation_result = qs.aggregate(total_sum=Sum('quantity_requested_litres'))
            result = aggregation_result.get('total_sum') or Decimal('0.00')
            cache.set(cache_key, result, 300) 
            return result
        return Decimal('0.00')

    @property
    def total_actual_l20_from_compartments(self):
        cache_key = f"trip_actual_l20_{self.pk}"
        cached_value = cache.get(cache_key)
        if cached_value is not None:
            return cached_value
            
        if self.pk and hasattr(self, 'requested_compartments'):
            qs = self.requested_compartments.all() if hasattr(self.requested_compartments, 'all') else self.requested_compartments
            aggregation_result = qs.filter(quantity_actual_l20__isnull=False).aggregate(
                total_sum=Sum('quantity_actual_l20')
            )
            result = aggregation_result.get('total_sum') or Decimal('0.00')
            cache.set(cache_key, result, 300) 
            return result
        return Decimal('0.00')

    @property
    def total_loaded(self): 
        cache_key = f"trip_total_loaded_{self.pk}"
        cached_value = cache.get(cache_key)
        if cached_value is not None:
            return cached_value
            
        if self.pk and hasattr(self, 'depletions_for_trip'):
             qs = self.depletions_for_trip.all() if hasattr(self.depletions_for_trip, 'all') else self.depletions_for_trip
             aggregation_result = qs.aggregate(total_sum=Sum('quantity_depleted'))
             result = aggregation_result.get('total_sum') or Decimal('0.00')
             cache.set(cache_key, result, 300) 
             return result
        return Decimal('0.00')

    def _log_message(self, writer, message, style_name='NOTICE'):
        if writer and hasattr(writer, 'style') and hasattr(writer.style, style_name) and hasattr(writer, 'stdout'):
            style_func = getattr(writer.style, style_name)
            writer.stdout.write(style_func(message + "\n")) 
        elif writer and hasattr(writer, 'write'): 
            writer.write(f"LOG-{style_name}: {message}\n")
        else: 
            log_func = getattr(logger, style_name.lower(), logger.info)
            log_func(message)

    def perform_stock_depletion(self, stdout_writer, use_actual_l20=False, raise_error=True):
        # Removed explicit_total_l20 parameter
        with transaction.atomic():
            self._log_message(stdout_writer, f"Trip {self.id}: --- ENTERING perform_stock_depletion ---", 'DEBUG')
            self._log_message(stdout_writer, f"Trip {self.id}: use_actual_l20={use_actual_l20}", 'DEBUG')

            if ShipmentDepletion.objects.filter(trip=self).exists():
                self._log_message(stdout_writer, f"Trip {self.id} ({self.kpc_order_number}): Stock depletion already performed and not reversed. Skipping duplicate depletion attempt.", 'WARNING')
                return True, "Depletion already performed."

            total_quantity_to_deplete = Decimal('0.00')
            depletion_basis = ""

            if use_actual_l20:
                total_quantity_to_deplete = self.total_actual_l20_from_compartments 
                depletion_basis = "actual L20 from BoL/compartments"
                self._log_message(stdout_writer, f"Trip {self.id}: L20 Depletion. Total to deplete: {total_quantity_to_deplete} based on {depletion_basis}", 'DEBUG')
                if total_quantity_to_deplete <= Decimal('0.00'):
                    self._log_message(stdout_writer, f"Trip {self.id}: Total actual L20 quantity is zero or not set ({total_quantity_to_deplete}L from {depletion_basis}). No stock will be depleted based on L20 actuals.", 'WARNING')
                    return True, f"L20 actuals ({depletion_basis}) are zero."
            else: # use_requested_quantities
                total_quantity_to_deplete = self.total_requested_from_compartments
                depletion_basis = "requested quantities from Loading Authority"
                self._log_message(stdout_writer, f"Trip {self.id}: Requested Qty Depletion. Total to deplete: {total_quantity_to_deplete}", 'DEBUG')
            
            self._log_message(stdout_writer, f"Trip {self.id} ({self.kpc_order_number}): Attempting stock depletion of {total_quantity_to_deplete}L based on {depletion_basis} for {self.product.name} to {self.destination.name if self.destination else 'Unknown Destination'}", 'NOTICE')

            if total_quantity_to_deplete <= Decimal('0.00'):
                self._log_message(stdout_writer, f"Trip {self.id}: Quantity to deplete is zero or less ({total_quantity_to_deplete}L based on {depletion_basis}). Skipping actual depletion steps.", 'NOTICE')
                return True, "Quantity to deplete is zero or less."

            if not self.destination: 
                 msg = "Cannot perform stock depletion: Trip destination is not set."
                 self._log_message(stdout_writer, msg, 'ERROR')
                 if raise_error: raise ValidationError(msg)
                 return False, msg

            self._log_message(stdout_writer, f"Trip {self.id}: Fetching available shipments for Product: {self.product.name}, Destination: {self.destination.name}", 'DEBUG')
            available_shipments = Shipment.objects.select_for_update().filter(
                product=self.product, destination=self.destination, 
                quantity_remaining__gt=Decimal('0.00')
            ).order_by('import_date', 'created_at')
            
            current_stock_for_product_dest = available_shipments.aggregate(total=Sum('quantity_remaining'))['total'] or Decimal('0.00')
            self._log_message(stdout_writer, f"Trip {self.id}: Current available stock for {self.product.name} to {self.destination.name}: {current_stock_for_product_dest}L. Required: {total_quantity_to_deplete}L", 'DEBUG')
            
            if total_quantity_to_deplete > current_stock_for_product_dest:
                error_message = (f"Insufficient stock for {self.product.name} destined for {self.destination.name}. Available: {current_stock_for_product_dest}L, Required: {total_quantity_to_deplete}L ({depletion_basis}).")
                logger.error(error_message)
                self._log_message(stdout_writer, error_message, 'ERROR')
                if raise_error: raise ValidationError({'__all__': error_message})
                return False, error_message

            depleted_amount_for_trip = Decimal('0.00')
            self._log_message(stdout_writer, f"Trip {self.id}: Starting depletion loop. Found {available_shipments.count()} available shipment batches.", 'DEBUG')
            for batch_count, batch in enumerate(available_shipments):
                self._log_message(stdout_writer, f"Trip {self.id}: Processing batch {batch_count+1} (ID: {batch.id}, Remaining: {batch.quantity_remaining}L)", 'DEBUG')
                if depleted_amount_for_trip >= total_quantity_to_deplete: 
                    self._log_message(stdout_writer, f"Trip {self.id}: Target depletion {total_quantity_to_deplete}L met. Breaking loop.", 'DEBUG')
                    break
                
                can_take_from_batch = batch.quantity_remaining
                take_this_much = min(can_take_from_batch, total_quantity_to_deplete - depleted_amount_for_trip)
                
                self._log_message(stdout_writer, f"Trip {self.id}: Can take {can_take_from_batch}L from batch. Need to take {take_this_much}L.", 'DEBUG')

                if take_this_much > 0:
                    ShipmentDepletion.objects.create(trip=self, shipment_batch=batch, quantity_depleted=take_this_much)
                    batch.quantity_remaining -= take_this_much
                    batch.save(update_fields=['quantity_remaining', 'updated_at']) 
                    depleted_amount_for_trip += take_this_much
                    self._log_message(stdout_writer, f"Trip {self.id}: Depleted {take_this_much}L from Batch ID {batch.id}. Batch remaining: {batch.quantity_remaining}L. Trip total depleted so far: {depleted_amount_for_trip}L", 'INFO')
            
            if depleted_amount_for_trip < total_quantity_to_deplete:
                error_message = (f"Critical Error: Trip {self.id} ({self.kpc_order_number}) for {self.product.name} to {self.destination.name}. Attempted: {total_quantity_to_deplete}L, Sourced: {depleted_amount_for_trip}L ({depletion_basis}). Stock inconsistency?")
                logger.error(error_message)
                self._log_message(stdout_writer, error_message, 'ERROR')
                if raise_error: raise ValidationError({'__all__': error_message})
                return False, error_message
                
            cache.delete(f"trip_total_loaded_{self.pk}")
            success_msg = f"Trip {self.id}: Stock depletion successful. Depleted: {depleted_amount_for_trip}L based on {depletion_basis}."
            self._log_message(stdout_writer, success_msg, 'SUCCESS')
            self._log_message(stdout_writer, f"Trip {self.id}: --- EXITING perform_stock_depletion (Success) ---", 'DEBUG')
            return True, success_msg

    def reverse_stock_depletion(self, stdout_writer=None):
        with transaction.atomic():
            self._log_message(stdout_writer, f"Trip {self.id} ({self.kpc_order_number}): Attempting to reverse stock depletions.", 'NOTICE')
            depletions = ShipmentDepletion.objects.filter(trip=self)
            if not depletions.exists():
                msg = f"Trip {self.id}: No depletions found to reverse."
                self._log_message(stdout_writer, msg, 'WARNING')
                return True, msg
                
            reversal_successful_overall = True
            total_reversed_qty = Decimal('0.00')
            for depletion in depletions:
                try:
                    shipment_batch = Shipment.objects.select_for_update().get(pk=depletion.shipment_batch_id)
                    shipment_batch.quantity_remaining += depletion.quantity_depleted
                    shipment_batch.save(update_fields=['quantity_remaining', 'updated_at'])
                    total_reversed_qty += depletion.quantity_depleted
                    self._log_message(stdout_writer, f"  Reversed {depletion.quantity_depleted}L to Shpmt ID {shipment_batch.id} ({getattr(shipment_batch, 'vessel_id_tag', 'N/A')})", 'NOTICE')
                except Shipment.DoesNotExist:
                    error_msg = f"ERROR: Shipment batch ID {depletion.shipment_batch_id} not found during reversal for Trip {self.id}."
                    logger.error(error_msg)
                    self._log_message(stdout_writer, error_msg, 'ERROR')
                    reversal_successful_overall = False; break 
                except Exception as e:
                    error_msg = f"ERROR reversing depletion for shipment_batch {depletion.shipment_batch_id} on Trip {self.id}: {e}"
                    logger.error(error_msg, exc_info=True)
                    self._log_message(stdout_writer, error_msg, 'ERROR')
                    reversal_successful_overall = False; break 
                    
            if reversal_successful_overall:
                num_deleted, _ = depletions.delete()
                cache.delete(f"trip_total_loaded_{self.pk}")
                success_msg = f"Trip {self.id}: Successfully reversed {total_reversed_qty}L and deleted {num_deleted} depletion records."
                self._log_message(stdout_writer, success_msg, 'SUCCESS')
                return True, success_msg
            else:
                fail_msg = f"Trip {self.id}: Stock reversal failed or incomplete. Depletion records NOT deleted. Transaction will be rolled back by caller."
                self._log_message(stdout_writer, fail_msg, 'ERROR')
                return False, fail_msg

    def clean(self):
        super().clean()
        if self.kpc_order_number and not self.kpc_order_number.upper().startswith('S'):
            raise ValidationError('KPC Order Number should start with "S"')
        if self.loading_date and self.loading_date > (timezone.now() + datetime.timedelta(days=1)).date(): 
            raise ValidationError('Loading date cannot be in the far future')
        if self.kpc_order_number:
            self.kpc_order_number = self.kpc_order_number.upper()
        if self.bol_number:
            self.bol_number = self.bol_number.upper()

    def save(self, *args, **kwargs):
        stdout_writer_param = kwargs.pop('stdout_writer', None) 
        # bol_total_actual_l20 removed as perform_stock_depletion will use property
        
        if not kwargs.get('skip_full_clean', False):
            self.full_clean()

        is_new = self.pk is None
        original_status_in_db = None
        # We don't need to fetch old product/destination/quantities here
        # as the logic will rely on the current state of self and status changes.
        
        if not is_new: 
            try:
                original_status_in_db = Trip.objects.get(pk=self.pk).status
            except Trip.DoesNotExist: 
                is_new = True 

        status_being_saved = self.status 
        
        # Perform the actual database save of the Trip instance first
        super(Trip, self).save(*args, **kwargs) 
        
        # Clear relevant caches *after* the save
        cache.delete_many([
            f"trip_requested_{self.pk}", f"trip_actual_l20_{self.pk}", f"trip_total_loaded_{self.pk}"
        ])
        
        try:
            with transaction.atomic(savepoint=False): # Outer transaction for stock ops
                deplete_now = False
                use_l20_for_this_depletion = False
                status_has_changed = (original_status_in_db != status_being_saved)

                # Determine if depletion is needed and which quantity to use
                if status_being_saved in self.INITIAL_DEPLETION_TRIGGER_STATUSES:
                    if is_new or (status_has_changed and original_status_in_db not in self.DEPLETED_STOCK_STATUSES):
                        deplete_now = True
                        use_l20_for_this_depletion = False # Use requested
                        self._log_message(stdout_writer_param, f"Trip {self.id}: Status '{status_being_saved}' triggers initial depletion (using requested quantities).", 'NOTICE')
                
                elif status_being_saved in self.ACTUAL_L20_DEPLETION_TRIGGER_STATUSES:
                    # This depletion happens when status becomes LOADED (typically after BoL)
                    # It should always use L20 actuals.
                    # Reversal of previous (requested) depletion should happen *before* this.
                    deplete_now = True
                    use_l20_for_this_depletion = True # Use L20 actuals
                    self._log_message(stdout_writer_param, f"Trip {self.id}: Status '{status_being_saved}' triggers actuals depletion (using L20 from BoL/compartments).", 'NOTICE')

                # --- Reversal Logic ---
                # Reverse if:
                # 1. We are about to deplete now (to ensure idempotency if re-saving in a trigger status).
                # 2. Status changed from a depleted state to a non-depleted one.
                existing_depletions = ShipmentDepletion.objects.filter(trip=self)
                needs_reversal = False
                if existing_depletions.exists():
                    if deplete_now: # If we will deplete, reverse previous first
                        needs_reversal = True
                        self._log_message(stdout_writer_param, f"Trip {self.id}: Reversing existing depletions before re-depleting for status '{status_being_saved}'.", 'NOTICE')
                    elif status_has_changed and original_status_in_db in self.DEPLETED_STOCK_STATUSES and status_being_saved not in self.DEPLETED_STOCK_STATUSES:
                        needs_reversal = True
                        self._log_message(stdout_writer_param, f"Trip {self.id}: Status changed from '{original_status_in_db}' to non-depleted '{status_being_saved}'. Reversing depletions...", 'NOTICE')
                
                if needs_reversal:
                    reversal_ok, reversal_msg = self.reverse_stock_depletion(stdout_writer=stdout_writer_param)
                    if not reversal_ok:
                        raise ValidationError(f"Failed to reverse existing stock depletions: {reversal_msg}")

                # --- Depletion Logic ---
                if deplete_now:
                    depletion_ok, depletion_msg = self.perform_stock_depletion(
                        stdout_writer=stdout_writer_param, 
                        use_actual_l20=use_l20_for_this_depletion, 
                        raise_error=True
                    )
                    if not depletion_ok:
                         raise ValidationError(f"Stock depletion failed: {depletion_msg}")
        
        except ValidationError as e: 
            error_msg_detail = e.message_dict if hasattr(e, 'message_dict') else str(e)
            error_msg = f"VALIDATION ERROR in Trip.save() stock logic for Trip {self.id} (attempted status: {status_being_saved}): {error_msg_detail}"
            logger.error(error_msg, exc_info=True)
            self._log_message(stdout_writer_param, error_msg, 'ERROR')
            raise 
        except Exception as e: 
            error_msg = f"UNEXPECTED ERROR in Trip.save() stock logic for Trip {self.id} (attempted status: {status_being_saved}): {e}"
            logger.exception(error_msg) 
            self._log_message(stdout_writer_param, error_msg, 'ERROR')
            raise

    class Meta:
        ordering = ['-loading_date', '-loading_time']
        indexes = [
            models.Index(fields=['loading_date']),
            models.Index(fields=['status']),
            models.Index(fields=['kpc_order_number']),
            models.Index(fields=['product', 'destination']),
            models.Index(fields=['user', 'loading_date']),
            models.Index(fields=['status', 'loading_date']),
            models.Index(fields=['vehicle', 'loading_date']),
        ]
        
    def __str__(self):
        order_id_display = self.kpc_order_number or self.bol_number or "N/A"
        dest_name = f" to {self.destination.name}" if self.destination else ""
        product_name_str = self.product.name if self.product else "N/A Product"
        vehicle_plate_str = self.vehicle.plate_number if self.vehicle else "N/A Vehicle"
        return f"Trip {self.id} ({order_id_display}) on {self.loading_date} - {product_name_str} via {vehicle_plate_str}{dest_name}"


class LoadingCompartment(models.Model):
    trip = models.ForeignKey(Trip, related_name='requested_compartments', on_delete=models.CASCADE)
    compartment_number = models.PositiveIntegerField()
    # This field stores the quantity from the initial Loading Authority
    quantity_requested_litres = models.DecimalField(max_digits=10, decimal_places=2) 
    # This field stores the actual L20 quantity from the BoL
    quantity_actual_l20 = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    temperature = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    density = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True) 
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def clean(self):
        super().clean()
        if self.quantity_requested_litres is not None and self.quantity_requested_litres < 0:
            raise ValidationError('Requested quantity cannot be negative.')
        if self.quantity_actual_l20 is not None and self.quantity_actual_l20 < 0: 
            raise ValidationError('Actual quantity cannot be negative')
        if self.compartment_number and self.compartment_number <= 0:
            raise ValidationError('Compartment number must be positive')
        if self.compartment_number and self.compartment_number > 10: 
            raise ValidationError('Compartment number cannot exceed 10')
        if self.temperature and (self.temperature < -50 or self.temperature > 100):
            raise ValidationError('Temperature must be between -50°C and 100°C')
        if self.density and (self.density < Decimal('0.6000') or self.density > Decimal('0.9000')): 
            raise ValidationError('Density (kg/L) must be between 0.6000 and 0.9000')
    
    def save(self, *args, **kwargs):
        self.full_clean()
        if self.trip_id:
            cache.delete_many([
                f"trip_requested_{self.trip_id}", f"trip_actual_l20_{self.trip_id}",
            ])
        super().save(*args, **kwargs)
    
    class Meta:
        unique_together = ('trip', 'compartment_number')
        ordering = ['compartment_number']
        indexes = [
            models.Index(fields=['trip', 'compartment_number']),
        ]
        
    def __str__(self):
        try: 
            product_name = self.trip.product.name
        except: 
            product_name = "N/A" 
        actual_str = f", Actual L20: {self.quantity_actual_l20}L" if self.quantity_actual_l20 is not None else ""
        req_str = f"Req: {self.quantity_requested_litres}L"
        return f"Comp {self.compartment_number} ({product_name}): {req_str}{actual_str} for Trip ID {self.trip_id}"


class ShipmentDepletion(models.Model):
    trip = models.ForeignKey(Trip, related_name='depletions_for_trip', on_delete=models.CASCADE)
    shipment_batch = models.ForeignKey(Shipment, related_name='depletions_from_batch', on_delete=models.PROTECT)
    quantity_depleted = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True) 
    
    def clean(self):
        super().clean()
        if self.quantity_depleted and self.quantity_depleted <= 0:
            raise ValidationError('Depleted quantity must be positive')
    
    def save(self, *args, **kwargs):
        self.full_clean()
        if self.trip_id:
            cache.delete(f"trip_total_loaded_{self.trip_id}")
        super().save(*args, **kwargs)
    
    def __str__(self):
        trip_identifier = self.trip.kpc_order_number or self.trip.bol_number or f"ID {self.trip.id}"
        shipment_identifier = self.shipment_batch.vessel_id_tag or f"ID {self.shipment_batch.id}"
        return f"{self.quantity_depleted}L from Shpmt {shipment_identifier} for Trip {trip_identifier}"
    
    class Meta: 
        ordering = ['-created_at'] 
        indexes = [
            models.Index(fields=['trip']),
            models.Index(fields=['shipment_batch']),
            models.Index(fields=['created_at']),
            models.Index(fields=['trip', 'created_at']),
        ]