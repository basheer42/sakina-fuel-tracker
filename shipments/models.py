# shipments/models.py - COMPLETE FIXED VERSION
from django.db import models, transaction
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.db.models import Sum, F, Value, DecimalField as DjangoDecimalField
from django.core.cache import cache
from django.core.exceptions import ValidationError
from decimal import Decimal
from simple_history.models import HistoricalRecords
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

    class Meta:
        ordering = ['plate_number']
        indexes = [
            models.Index(fields=['plate_number']),
        ]


class Destination(models.Model):
    name = models.CharField(max_length=200, unique=True)
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
    user = models.ForeignKey(User, on_delete=models.PROTECT)
    vessel_id_tag = models.CharField(
        max_length=100, unique=True, 
        help_text="Unique identifier for the vessel/shipment. This field is required and must be unique."
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
    history = HistoricalRecords()

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

    def save(self, *args, **kwargs):
        is_new = not self.pk
        self.full_clean()
        if is_new:
            self.quantity_remaining = self.quantity_litres
        super().save(*args, **kwargs)

    class Meta:
        ordering = ['-import_date', '-created_at']
        indexes = [
            models.Index(fields=['vessel_id_tag']),
            models.Index(fields=['import_date']),
            models.Index(fields=['product', 'destination']),
            models.Index(fields=['user', 'import_date']),
            models.Index(fields=['quantity_remaining']),
            models.Index(fields=['supplier_name']),
        ]


class Trip(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending Authority'),
        ('KPC_APPROVED', 'KPC Approved'),
        ('LOADING', 'Loading in Progress'),
        ('LOADED', 'Loaded (BoL Issued)'),
        ('GATEPASSED', 'Gate Passed'),
        ('TRANSIT', 'In Transit'),
        ('DELIVERED', 'Delivered'),
        ('CANCELLED', 'Cancelled'),
    ]

    user = models.ForeignKey(User, on_delete=models.PROTECT)
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT)
    destination = models.ForeignKey(Destination, on_delete=models.PROTECT)
    kpc_order_number = models.CharField(max_length=100, unique=True, help_text="KPC Loading Order No (e.g., Sxxxxx) from Authority PDF. Required and must be unique.")
    bol_number = models.CharField(max_length=100, blank=True, null=True, unique=True, verbose_name="Final BoL No / KPC Shipment No")
    loading_date = models.DateField(default=timezone.now, verbose_name="Authority/BOL Date")
    loading_time = models.TimeField(default=timezone.now, verbose_name="Authority/BOL Time")
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='PENDING')
    notes = models.TextField(blank=True, null=True)
    kpc_comments = models.TextField(blank=True, null=True, help_text="Comments from KPC emails")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    history = HistoricalRecords()

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

    def clean(self):
        super().clean()
        if self.loading_date and self.loading_date > timezone.now().date() + datetime.timedelta(days=7):
            raise ValidationError('Loading date cannot be more than 7 days in the future')

    def save(self, *args, **kwargs):
        self.full_clean()
        status_being_saved = self.status
        original_status = self._get_original_status()
        
        stdout_writer_param = kwargs.pop('stdout_writer', None)
        
        def _log_message(writer, message, level='INFO'):
            if writer and hasattr(writer, 'write'):
                if level == 'ERROR':
                    writer.write(f"ERROR: {message}")
                else:
                    writer.write(message)
            if level == 'ERROR':
                logger.error(message)
            else:
                logger.info(message)

        try:
            with transaction.atomic():
                super().save(*args, **kwargs)
                
                if original_status != status_being_saved and status_being_saved == 'LOADED':
                    _log_message(stdout_writer_param, f"Trip {self.id} status changed from {original_status} to LOADED. Initiating FIFO stock depletion.")
                    
                    from .fifo_manager import FIFOStockManager
                    fifo_manager = FIFOStockManager()
                    
                    try:
                        result = fifo_manager.deplete_stock_for_trip(self, stdout_writer=stdout_writer_param)
                        if result['success']:
                            _log_message(stdout_writer_param, f"✅ FIFO depletion completed for Trip {self.id}")
                        else:
                            error_msg = f"❌ FIFO depletion failed for Trip {self.id}: {result.get('error', 'Unknown error')}"
                            _log_message(stdout_writer_param, error_msg, 'ERROR')
                            raise ValidationError(f"Stock depletion failed: {result.get('error', 'Unknown error')}")
                    except Exception as fifo_error:
                        error_msg = f"FIFO STOCK DEPLETION ERROR for Trip {self.id}: {fifo_error}"
                        _log_message(stdout_writer_param, error_msg, 'ERROR')
                        raise
                        
        except ValidationError as ve:
            error_msg = f"VALIDATION ERROR in Trip.save() for Trip {self.id} (attempted status: {status_being_saved}): {ve}"
            logger.error(error_msg)
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
    quantity_requested_litres = models.DecimalField(max_digits=10, decimal_places=2)
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


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    phone_number = models.CharField(max_length=20, blank=True, null=True, help_text="WhatsApp phone number")
    telegram_chat_id = models.CharField(max_length=50, blank=True, null=True, help_text="Telegram chat ID")
    department = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} - Profile"

    class Meta:
        verbose_name = "User Profile"
        verbose_name_plural = "User Profiles"


class TR830ProcessingState(models.Model):
    """Model to store TR830 processing state when users upload documents via Telegram"""
    chat_id = models.CharField(max_length=50, unique=True, help_text="Telegram chat ID")
    filename = models.CharField(max_length=255, help_text="Original filename of TR830 document")
    import_date = models.DateTimeField(help_text="Import date extracted from TR830")
    vessel = models.CharField(max_length=200, help_text="Vessel name from TR830")
    product_type = models.CharField(max_length=50, help_text="Product type (AGO, PMS, etc.)")
    destination = models.CharField(max_length=200, help_text="Destination from TR830")
    quantity = models.DecimalField(max_digits=12, decimal_places=2, help_text="Quantity in litres")
    description = models.TextField(help_text="Description from TR830")
    step = models.CharField(max_length=20, choices=[
        ('supplier', 'Waiting for Supplier'),
        ('price', 'Waiting for Price'),
    ], help_text="Current step in processing flow")
    user = models.ForeignKey(User, on_delete=models.CASCADE, help_text="User processing this TR830")
    supplier = models.CharField(max_length=200, blank=True, default='', help_text="Supplier name entered by user")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "TR830 Processing State"
        verbose_name_plural = "TR830 Processing States"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['chat_id']),
            models.Index(fields=['user', 'created_at']),
        ]
    
    def __str__(self):
        return f"TR830 {self.filename} - Step: {self.step} - User: {self.user.username}"

    def clean(self):
        super().clean()
        if self.quantity and self.quantity <= 0:
            raise ValidationError('Quantity must be positive')
        if not self.chat_id.strip():
            raise ValidationError('Chat ID cannot be empty')
            
    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


# Signal to create UserProfile automatically when User is created
from django.db.models.signals import post_save
from django.dispatch import receiver

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    if hasattr(instance, 'userprofile'):
        instance.userprofile.save()
    else:
        UserProfile.objects.create(user=instance)