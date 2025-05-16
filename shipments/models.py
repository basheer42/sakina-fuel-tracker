# shipments/models.py
from django.db import models
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.db.models import Sum, F
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

# Shipment (Stock In / Incoming Fuel) - MODIFIED for FIFO
class Shipment(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    import_date = models.DateField(default=timezone.now)
    supplier_name = models.CharField(max_length=200)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity_litres = models.DecimalField(max_digits=10, decimal_places=2) # Original quantity
    price_per_litre = models.DecimalField(max_digits=7, decimal_places=3)
    notes = models.TextField(blank=True, null=True)
    
    # New field for FIFO: tracks how much of this shipment is still available
    quantity_remaining = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
         try: product_name = self.product.name
         except Product.DoesNotExist: product_name = "N/A"
         return f"{product_name} from {self.supplier_name} on {self.import_date} ({self.quantity_litres}L, Rem: {self.quantity_remaining}L)"

    @property
    def total_cost(self):
        if self.quantity_litres is not None and self.price_per_litre is not None:
            return self.quantity_litres * self.price_per_litre
        return 0
    
    # When a shipment is saved, ensure quantity_remaining is set if it's a new object
    # or if quantity_litres has changed (more complex scenario, handle in form/view for now)
    def save(self, *args, **kwargs):
        if self._state.adding and self.quantity_remaining == 0.00 and self.quantity_litres > 0:
            # For new shipments, initialize quantity_remaining to the full quantity
            # This might be better handled when the form is saved if we want to set it based on form.cleaned_data
            # For now, this is a basic initialization.
            # If it's an update and quantity_litres changes, quantity_remaining needs careful adjustment.
            # Let's assume for now, initial setup or view logic handles this.
            # A more robust way is to set it based on quantity_litres if it's a new object
            # and the field hasn't been explicitly set.
            pass # We'll set this explicitly in the form/view save for new shipments.
        super().save(*args, **kwargs)


    class Meta:
         ordering = ['import_date', 'created_at'] # Oldest first for FIFO processing

# Trip (Loadings / Stock Out)
class Trip(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'), ('LOADING', 'Loading'), ('LOADED', 'Loaded'),
        ('GATEPASSED', 'Gatepassed'), ('TRANSIT', 'In Transit'),
        ('DELIVERED', 'Delivered'), ('CANCELLED', 'Cancelled'),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    loading_date = models.DateField(default=timezone.now, verbose_name="BOL Date")
    loading_time = models.TimeField(default=timezone.now, verbose_name="BOL Time")
    bol_number = models.CharField(max_length=100, blank=True, null=True, unique_for_date='loading_date', verbose_name="BOL Number")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
         ordering = ['-loading_date', '-loading_time']

    def __str__(self):
        return f"Trip {self.id} ({self.bol_number or 'N/A'}) on {self.loading_date} - {self.product.name} - {self.vehicle.plate_number} to {self.customer.name}"

    @property
    def total_loaded(self):
        # Sums from the ShipmentDepletion records linked to this trip's compartments
        total = 0
        # This assumes we change LoadingCompartment to link to ShipmentDepletion
        # OR that ShipmentDepletion links directly to Trip.
        # Let's simplify: ShipmentDepletion will link to Trip.
        # This property will now sum ShipmentDepletions linked to this Trip.
        if hasattr(self, 'depletions_for_trip'): # Check if related_name exists
             aggregation_result = self.depletions_for_trip.aggregate(total_sum=Sum('quantity_depleted'))
             return aggregation_result.get('total_sum', 0)
        return 0


# LoadingCompartment - Represents the user's *request* for what goes into each compartment
class LoadingCompartment(models.Model):
    trip = models.ForeignKey(Trip, related_name='requested_compartments', on_delete=models.CASCADE) # Changed related_name
    compartment_number = models.PositiveIntegerField()
    # This is the quantity the user *wants* to load, not necessarily what's depleted from a single shipment batch
    quantity_requested_litres = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        unique_together = ('trip', 'compartment_number')
        ordering = ['compartment_number']
    def __str__(self):
        return f"Request Comp {self.compartment_number}: {self.quantity_requested_litres}L"


# New Model: ShipmentDepletion - Links a Trip (or LoadingCompartment) to a specific Shipment batch for FIFO
class ShipmentDepletion(models.Model):
    # Which trip caused this depletion
    trip = models.ForeignKey(Trip, related_name='depletions_for_trip', on_delete=models.CASCADE)
    # Which specific incoming shipment batch was this quantity drawn from
    shipment_batch = models.ForeignKey(Shipment, related_name='depletions_from_batch', on_delete=models.PROTECT) # Protect shipment from deletion if it has depletions
    # How much was depleted from this specific shipment_batch for this trip
    quantity_depleted = models.DecimalField(max_digits=10, decimal_places=2)
    
    created_at = models.DateTimeField(auto_now_add=True) # When this depletion record was made
    
    def __str__(self):
        return f"{self.quantity_depleted}L from Shipment ID {self.shipment_batch.id} for Trip ID {self.trip.id}"

    class Meta:
        ordering = ['created_at'] # Order by when the depletion occurred