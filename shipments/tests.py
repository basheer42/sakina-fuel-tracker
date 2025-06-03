# shipments/tests.py
from django.test import TestCase, Client, TransactionTestCase
from django.contrib.auth.models import User, Group, Permission
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from decimal import Decimal
from datetime import date, time, timedelta
from django.utils import timezone
import tempfile
import json

from .models import (
    Product, Customer, Vehicle, Destination, 
    Shipment, Trip, LoadingCompartment, ShipmentDepletion
)
from .forms import ShipmentForm, TripForm


class BaseTestCase(TestCase):
    """Base test case with common setup."""
    
    def setUp(self):
        # Create test users
        self.admin_user = User.objects.create_user(
            username='admin', 
            email='admin@test.com', 
            password='testpass123'
        )
        self.viewer_user = User.objects.create_user(
            username='viewer', 
            email='viewer@test.com', 
            password='testpass123'
        )
        self.regular_user = User.objects.create_user(
            username='regular', 
            email='regular@test.com', 
            password='testpass123'
        )
        
        # Create groups
        self.admin_group = Group.objects.create(name='Admin')
        self.viewer_group = Group.objects.create(name='Viewer')
        
        # Add permissions to groups
        permissions = Permission.objects.filter(
            content_type__app_label='shipments'
        )
        self.admin_group.permissions.set(permissions)
        self.viewer_group.permissions.set(
            permissions.filter(codename__startswith='view_')
        )
        
        # Assign users to groups
        self.admin_user.groups.add(self.admin_group)
        self.viewer_user.groups.add(self.viewer_group)
        
        # Create test data
        self.product_pms = Product.objects.create(name='PMS')
        self.product_ago = Product.objects.create(name='AGO')
        
        self.customer = Customer.objects.create(
            name='Test Customer',
            contact_person='John Doe',
            email='customer@test.com'
        )
        
        self.vehicle = Vehicle.objects.create(
            plate_number='TEST001',
            trailer_number='TR001'
        )
        
        self.destination = Destination.objects.create(
            name='South Sudan'
        )
        
        self.client = Client()


class ModelTestCase(BaseTestCase):
    """Test model validation and behavior."""
    
    def test_product_model(self):
        """Test Product model validation."""
        # Test valid product
        product = Product(name='DIESEL')
        product.full_clean()  # Should not raise
        
        # Test invalid product name
        with self.assertRaises(ValidationError):
            product = Product(name='A')  # Too short
            product.full_clean()
        
        # Test uppercase conversion
        product = Product.objects.create(name='diesel')
        self.assertEqual(product.name, 'DIESEL')
    
    def test_customer_model(self):
        """Test Customer model validation."""
        # Test valid customer
        customer = Customer(
            name='Valid Customer',
            email='valid@test.com'
        )
        customer.full_clean()  # Should not raise
        
        # Test invalid email
        with self.assertRaises(ValidationError):
            customer = Customer(name='Test', email='invalid-email')
            customer.full_clean()
    
    def test_vehicle_model(self):
        """Test Vehicle model validation."""
        # Test valid vehicle
        vehicle = Vehicle(plate_number='ABC123')
        vehicle.full_clean()  # Should not raise
        
        # Test invalid plate number
        with self.assertRaises(ValidationError):
            vehicle = Vehicle(plate_number='AB')  # Too short
            vehicle.full_clean()
        
        # Test uppercase conversion
        vehicle = Vehicle.objects.create(plate_number='abc123')
        self.assertEqual(vehicle.plate_number, 'ABC123')
    
    def test_shipment_model(self):
        """Test Shipment model validation and behavior."""
        # Test valid shipment
        shipment = Shipment(
            user=self.admin_user,
            vessel_id_tag='TEST001',
            supplier_name='Test Supplier',
            product=self.product_pms,
            destination=self.destination,
            quantity_litres=Decimal('1000.00'),
            price_per_litre=Decimal('1.50')
        )
        shipment.full_clean()  # Should not raise
        
        # Test quantity_remaining is set on save
        shipment.save()
        self.assertEqual(shipment.quantity_remaining, Decimal('1000.00'))
        
        # Test total_cost property
        self.assertEqual(shipment.total_cost, Decimal('1500.00'))
        
        # Test invalid quantities
        with self.assertRaises(ValidationError):
            shipment = Shipment(
                user=self.admin_user,
                vessel_id_tag='TEST002',
                supplier_name='Test',
                product=self.product_pms,
                quantity_litres=Decimal('-100.00'),  # Negative
                price_per_litre=Decimal('1.50')
            )
            shipment.full_clean()
    
    def test_trip_model(self):
        """Test Trip model validation and behavior."""
        # Create a shipment first
        shipment = Shipment.objects.create(
            user=self.admin_user,
            vessel_id_tag='SHIP001',
            supplier_name='Test Supplier',
            product=self.product_pms,
            destination=self.destination,
            quantity_litres=Decimal('1000.00'),
            price_per_litre=Decimal('1.50')
        )
        
        # Test valid trip
        trip = Trip(
            user=self.admin_user,
            vehicle=self.vehicle,
            customer=self.customer,
            product=self.product_pms,
            destination=self.destination,
            kpc_order_number='S12345',
            loading_date=date.today(),
            loading_time=time(10, 0),
            status='PENDING'
        )
        trip.full_clean()  # Should not raise
        
        # Test invalid KPC order number
        with self.assertRaises(ValidationError):
            trip = Trip(
                user=self.admin_user,
                vehicle=self.vehicle,
                customer=self.customer,
                product=self.product_pms,
                destination=self.destination,
                kpc_order_number='X12345',  # Should start with S
                loading_date=date.today(),
                loading_time=time(10, 0),
                status='PENDING'
            )
            trip.full_clean()
    
    def test_loading_compartment_model(self):
        """Test LoadingCompartment model validation."""
        # Create a trip first
        trip = Trip.objects.create(
            user=self.admin_user,
            vehicle=self.vehicle,
            customer=self.customer,
            product=self.product_pms,
            destination=self.destination,
            kpc_order_number='S12345',
            loading_date=date.today(),
            loading_time=time(10, 0),
            status='PENDING'
        )
        
        # Test valid compartment
        compartment = LoadingCompartment(
            trip=trip,
            compartment_number=1,
            quantity_requested_litres=Decimal('500.00')
        )
        compartment.full_clean()  # Should not raise
        
        # Test invalid compartment number
        with self.assertRaises(ValidationError):
            compartment = LoadingCompartment(
                trip=trip,
                compartment_number=0,  # Must be positive
                quantity_requested_litres=Decimal('500.00')
            )
            compartment.full_clean()
        
        # Test invalid quantity
        with self.assertRaises(ValidationError):
            compartment = LoadingCompartment(
                trip=trip,
                compartment_number=2,
                quantity_requested_litres=Decimal('-100.00')  # Negative
            )
            compartment.full_clean()


class StockDepletionTestCase(TransactionTestCase):
    """Test stock depletion logic with transactions."""
    
    def setUp(self):
        # Create test data
        self.user = User.objects.create_user(
            username='testuser', 
            password='testpass123'
        )
        self.product = Product.objects.create(name='PMS')
        self.destination = Destination.objects.create(name='Test Dest')
        self.customer = Customer.objects.create(name='Test Customer')
        self.vehicle = Vehicle.objects.create(plate_number='TEST001')
        
        # Create shipment with stock
        self.shipment = Shipment.objects.create(
            user=self.user,
            vessel_id_tag='SHIP001',
            supplier_name='Test Supplier',
            product=self.product,
            destination=self.destination,
            quantity_litres=Decimal('1000.00'),
            price_per_litre=Decimal('1.50')
        )
    
    def test_stock_depletion_on_approval(self):
        """Test that stock is depleted when trip is approved."""
        # Create trip with compartments
        trip = Trip.objects.create(
            user=self.user,
            vehicle=self.vehicle,
            customer=self.customer,
            product=self.product,
            destination=self.destination,
            kpc_order_number='S12345',
            status='PENDING'
        )
        
        # Add compartments
        LoadingCompartment.objects.create(
            trip=trip,
            compartment_number=1,
            quantity_requested_litres=Decimal('300.00')
        )
        LoadingCompartment.objects.create(
            trip=trip,
            compartment_number=2,
            quantity_requested_litres=Decimal('200.00')
        )
        
        # Change status to KPC_APPROVED should trigger depletion
        trip.status = 'KPC_APPROVED'
        trip.save()
        
        # Check that stock was depleted
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.quantity_remaining, Decimal('500.00'))
        
        # Check that depletion records exist
        depletions = ShipmentDepletion.objects.filter(trip=trip)
        self.assertEqual(depletions.count(), 1)
        self.assertEqual(depletions.first().quantity_depleted, Decimal('500.00'))
    
    def test_insufficient_stock_error(self):
        """Test error when trying to deplete more stock than available."""
        trip = Trip.objects.create(
            user=self.user,
            vehicle=self.vehicle,
            customer=self.customer,
            product=self.product,
            destination=self.destination,
            kpc_order_number='S12346',
            status='PENDING'
        )
        
        # Add compartment requiring more than available
        LoadingCompartment.objects.create(
            trip=trip,
            compartment_number=1,
            quantity_requested_litres=Decimal('1500.00')  # More than 1000 available
        )
        
        # Trying to approve should raise ValidationError
        trip.status = 'KPC_APPROVED'
        with self.assertRaises(ValidationError):
            trip.save()
    
    def test_stock_reversal(self):
        """Test that stock depletion can be reversed."""
        trip = Trip.objects.create(
            user=self.user,
            vehicle=self.vehicle,
            customer=self.customer,
            product=self.product,
            destination=self.destination,
            kpc_order_number='S12347',
            status='PENDING'
        )
        
        LoadingCompartment.objects.create(
            trip=trip,
            compartment_number=1,
            quantity_requested_litres=Decimal('300.00')
        )
        
        # Approve trip (depletes stock)
        trip.status = 'KPC_APPROVED'
        trip.save()
        
        # Verify depletion
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.quantity_remaining, Decimal('700.00'))
        
        # Cancel trip (should reverse depletion)
        trip.status = 'CANCELLED'
        trip.save()
        
        # Verify reversal
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.quantity_remaining, Decimal('1000.00'))
        
        # Verify depletion records are deleted
        depletions = ShipmentDepletion.objects.filter(trip=trip)
        self.assertEqual(depletions.count(), 0)


class ViewTestCase(BaseTestCase):
    """Test views and user permissions."""
    
    def test_home_view_requires_login(self):
        """Test that home view requires authentication."""
        response = self.client.get(reverse('shipments:home'))
        self.assertRedirects(response, '/accounts/login/?next=/')
    
    def test_home_view_with_login(self):
        """Test home view with authenticated user."""
        self.client.login(username='viewer', password='testpass123')
        response = self.client.get(reverse('shipments:home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Welcome to Sakina Gas Fuel Tracker')
    
    def test_shipment_list_view_permissions(self):
        """Test shipment list view permissions."""
        # Test without login
        response = self.client.get(reverse('shipments:shipment-list'))
        self.assertEqual(response.status_code, 403)  # Permission denied
        
        # Test with viewer
        self.client.login(username='viewer', password='testpass123')
        response = self.client.get(reverse('shipments:shipment-list'))
        self.assertEqual(response.status_code, 200)
        
        # Test with admin
        self.client.login(username='admin', password='testpass123')
        response = self.client.get(reverse('shipments:shipment-list'))
        self.assertEqual(response.status_code, 200)
    
    def test_shipment_add_view_permissions(self):
        """Test shipment add view permissions."""
        # Test without permission
        self.client.login(username='viewer', password='testpass123')
        response = self.client.get(reverse('shipments:shipment-add'))
        self.assertEqual(response.status_code, 403)
        
        # Test with admin permission
        self.client.login(username='admin', password='testpass123')
        response = self.client.get(reverse('shipments:shipment-add'))
        self.assertEqual(response.status_code, 200)
    
    def test_shipment_create(self):
        """Test shipment creation through view."""
        self.client.login(username='admin', password='testpass123')
        
        data = {
            'vessel_id_tag': 'TEST001',
            'import_date': date.today(),
            'supplier_name': 'Test Supplier',
            'product': self.product_pms.id,
            'destination': self.destination.id,
            'quantity_litres': '1000.00',
            'price_per_litre': '1.50',
            'notes': 'Test shipment'
        }
        
        response = self.client.post(reverse('shipments:shipment-add'), data)
        self.assertEqual(response.status_code, 302)  # Redirect after success
        
        # Verify shipment was created
        shipment = Shipment.objects.get(vessel_id_tag='TEST001')
        self.assertEqual(shipment.supplier_name, 'Test Supplier')
        self.assertEqual(shipment.user, self.admin_user)
    
    def test_trip_list_view(self):
        """Test trip list view."""
        # Create test trip
        trip = Trip.objects.create(
            user=self.admin_user,
            vehicle=self.vehicle,
            customer=self.customer,
            product=self.product_pms,
            destination=self.destination,
            kpc_order_number='S12345',
            status='PENDING'
        )
        
        self.client.login(username='viewer', password='testpass123')
        response = self.client.get(reverse('shipments:trip-list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'S12345')
    
    def test_trip_detail_view(self):
        """Test trip detail view."""
        trip = Trip.objects.create(
            user=self.admin_user,
            vehicle=self.vehicle,
            customer=self.customer,
            product=self.product_pms,
            destination=self.destination,
            kpc_order_number='S12345',
            status='PENDING'
        )
        
        # Add compartments
        LoadingCompartment.objects.create(
            trip=trip,
            compartment_number=1,
            quantity_requested_litres=Decimal('500.00')
        )
        
        self.client.login(username='viewer', password='testpass123')
        response = self.client.get(reverse('shipments:trip-detail', kwargs={'pk': trip.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'S12345')
        self.assertContains(response, '500.00')


class FormTestCase(BaseTestCase):
    """Test form validation."""
    
    def test_shipment_form_valid(self):
        """Test valid shipment form."""
        form_data = {
            'vessel_id_tag': 'TEST001',
            'import_date': date.today(),
            'supplier_name': 'Test Supplier',
            'product': self.product_pms.id,
            'destination': self.destination.id,
            'quantity_litres': '1000.00',
            'price_per_litre': '1.50',
            'notes': 'Test notes'
        }
        
        form = ShipmentForm(data=form_data)
        self.assertTrue(form.is_valid())
    
    def test_shipment_form_invalid(self):
        """Test invalid shipment form."""
        form_data = {
            'vessel_id_tag': 'AB',  # Too short
            'import_date': date.today(),
            'supplier_name': 'Test Supplier',
            'product': self.product_pms.id,
            'quantity_litres': '-100.00',  # Negative
            'price_per_litre': '1.50',
        }
        
        form = ShipmentForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn('vessel_id_tag', form.errors)
        self.assertIn('quantity_litres', form.errors)
    
    def test_trip_form_valid(self):
        """Test valid trip form."""
        form_data = {
            'vehicle': self.vehicle.id,
            'customer': self.customer.id,
            'product': self.product_pms.id,
            'destination': self.destination.id,
            'loading_date': date.today(),
            'loading_time': time(10, 0),
            'bol_number': 'S12345',
            'status': 'PENDING',
            'notes': 'Test trip'
        }
        
        form = TripForm(data=form_data)
        self.assertTrue(form.is_valid())


class IntegrationTestCase(BaseTestCase):
    """Integration tests for complete workflows."""
    
    def test_complete_shipment_to_delivery_workflow(self):
        """Test complete workflow from shipment to delivery."""
        # Step 1: Create shipment
        self.client.login(username='admin', password='testpass123')
        
        shipment_data = {
            'vessel_id_tag': 'WORKFLOW001',
            'import_date': date.today(),
            'supplier_name': 'Integration Test Supplier',
            'product': self.product_pms.id,
            'destination': self.destination.id,
            'quantity_litres': '1000.00',
            'price_per_litre': '1.50',
            'notes': 'Integration test shipment'
        }
        
        response = self.client.post(reverse('shipments:shipment-add'), shipment_data)
        self.assertEqual(response.status_code, 302)
        
        shipment = Shipment.objects.get(vessel_id_tag='WORKFLOW001')
        self.assertEqual(shipment.quantity_remaining, Decimal('1000.00'))
        
        # Step 2: Create trip
        trip_data = {
            'vehicle': self.vehicle.id,
            'customer': self.customer.id,
            'product': self.product_pms.id,
            'destination': self.destination.id,
            'loading_date': date.today(),
            'loading_time': time(10, 0),
            'bol_number': 'S99999',
            'status': 'PENDING',
            'notes': 'Integration test trip',
            'compartments-TOTAL_FORMS': '3',
            'compartments-INITIAL_FORMS': '0',
            'compartments-MIN_NUM_FORMS': '3',
            'compartments-MAX_NUM_FORMS': '3',
            'compartments-0-compartment_number': '1',
            'compartments-0-quantity_requested_litres': '300.00',
            'compartments-1-compartment_number': '2',
            'compartments-1-quantity_requested_litres': '200.00',
            'compartments-2-compartment_number': '3',
            'compartments-2-quantity_requested_litres': '100.00',
        }
        
        response = self.client.post(reverse('shipments:trip-add'), trip_data)
        self.assertEqual(response.status_code, 302)
        
        trip = Trip.objects.get(bol_number='S99999')
        self.assertEqual(trip.total_requested_from_compartments, Decimal('600.00'))
        
        # Step 3: Approve trip (should deplete stock)
        trip.status = 'KPC_APPROVED'
        trip.save()
        
        # Verify stock depletion
        shipment.refresh_from_db()
        self.assertEqual(shipment.quantity_remaining, Decimal('400.00'))
        
        # Step 4: Mark as delivered
        trip.status = 'DELIVERED'
        trip.save()
        
        # Verify final state
        self.assertEqual(trip.total_loaded, Decimal('600.00'))
        
        # Step 5: Check depletion records
        depletions = ShipmentDepletion.objects.filter(trip=trip)
        self.assertEqual(depletions.count(), 1)
        self.assertEqual(depletions.first().quantity_depleted, Decimal('600.00'))


class APITestCase(BaseTestCase):
    """Test API-like functionality and AJAX views."""
    
    def test_shipment_detail_json_response(self):
        """Test that views can handle JSON requests properly."""
        shipment = Shipment.objects.create(
            user=self.admin_user,
            vessel_id_tag='JSON001',
            supplier_name='JSON Test Supplier',
            product=self.product_pms,
            destination=self.destination,
            quantity_litres=Decimal('1000.00'),
            price_per_litre=Decimal('1.50')
        )
        
        self.client.login(username='viewer', password='testpass123')
        response = self.client.get(
            reverse('shipments:shipment-detail', kwargs={'pk': shipment.pk}),
            HTTP_ACCEPT='application/json'
        )
        self.assertEqual(response.status_code, 200)


class SecurityTestCase(BaseTestCase):
    """Test security aspects of the application."""
    
    def test_csrf_protection(self):
        """Test CSRF protection on forms."""
        self.client.login(username='admin', password='testpass123')
        
        # Try to submit form without CSRF token
        data = {
            'vessel_id_tag': 'CSRF001',
            'import_date': date.today(),
            'supplier_name': 'CSRF Test',
            'product': self.product_pms.id,
            'quantity_litres': '1000.00',
            'price_per_litre': '1.50',
        }
        
        response = self.client.post(
            reverse('shipments:shipment-add'), 
            data,
            HTTP_X_CSRFTOKEN='invalid'
        )
        # Should be rejected due to CSRF
        self.assertNotEqual(response.status_code, 302)
    
    def test_user_data_isolation(self):
        """Test that users can only see their own data when appropriate."""
        # Create shipment by admin user
        admin_shipment = Shipment.objects.create(
            user=self.admin_user,
            vessel_id_tag='ADMIN001',
            supplier_name='Admin Shipment',
            product=self.product_pms,
            destination=self.destination,
            quantity_litres=Decimal('1000.00'),
            price_per_litre=Decimal('1.50')
        )
        
        # Create shipment by regular user
        regular_shipment = Shipment.objects.create(
            user=self.regular_user,
            vessel_id_tag='REGULAR001',
            supplier_name='Regular Shipment',
            product=self.product_pms,
            destination=self.destination,
            quantity_litres=Decimal('1000.00'),
            price_per_litre=Decimal('1.50')
        )
        
        # Admin should see all shipments
        self.client.login(username='admin', password='testpass123')
        response = self.client.get(reverse('shipments:shipment-list'))
        self.assertContains(response, 'ADMIN001')
        self.assertContains(response, 'REGULAR001')
        
        # Regular user should only see their own shipments
        self.client.login(username='regular', password='testpass123')
        response = self.client.get(reverse('shipments:shipment-list'))
        self.assertNotContains(response, 'ADMIN001')
        self.assertContains(response, 'REGULAR001')
    
    def test_sql_injection_protection(self):
        """Test protection against SQL injection attempts."""
        self.client.login(username='viewer', password='testpass123')
        
        # Try SQL injection in search parameters
        malicious_params = {
            'supplier_name': "'; DROP TABLE shipments_shipment; --",
            'start_date': '2024-01-01',
        }
        
        response = self.client.get(reverse('shipments:shipment-list'), malicious_params)
        self.assertEqual(response.status_code, 200)
        
        # Verify table still exists by checking shipment count
        shipment_count = Shipment.objects.count()
        self.assertGreaterEqual(shipment_count, 0)


class PerformanceTestCase(BaseTestCase):
    """Test performance aspects and query optimization."""
    
    def test_shipment_list_query_count(self):
        """Test that shipment list doesn't have N+1 query problems."""
        # Create multiple shipments
        for i in range(10):
            Shipment.objects.create(
                user=self.admin_user,
                vessel_id_tag=f'PERF{i:03d}',
                supplier_name=f'Supplier {i}',
                product=self.product_pms,
                destination=self.destination,
                quantity_litres=Decimal('1000.00'),
                price_per_litre=Decimal('1.50')
            )
        
        self.client.login(username='admin', password='testpass123')
        
        # Test with query counting
        with self.assertNumQueries(less_than=10):  # Should be efficient
            response = self.client.get(reverse('shipments:shipment-list'))
            self.assertEqual(response.status_code, 200)
    
    def test_trip_list_query_optimization(self):
        """Test trip list query efficiency."""
        # Create trips with related data
        for i in range(5):
            trip = Trip.objects.create(
                user=self.admin_user,
                vehicle=self.vehicle,
                customer=self.customer,
                product=self.product_pms,
                destination=self.destination,
                kpc_order_number=f'S{i:05d}',
                status='PENDING'
            )
            
            # Add compartments
            for j in range(3):
                LoadingCompartment.objects.create(
                    trip=trip,
                    compartment_number=j + 1,
                    quantity_requested_litres=Decimal('100.00')
                )
        
        self.client.login(username='admin', password='testpass123')
        
        with self.assertNumQueries(less_than=15):  # Should be efficient
            response = self.client.get(reverse('shipments:trip-list'))
            self.assertEqual(response.status_code, 200)


class ErrorHandlingTestCase(BaseTestCase):
    """Test error handling and edge cases."""
    
    def test_nonexistent_shipment_detail(self):
        """Test accessing non-existent shipment."""
        self.client.login(username='viewer', password='testpass123')
        response = self.client.get(
            reverse('shipments:shipment-detail', kwargs={'pk': 99999})
        )
        self.assertEqual(response.status_code, 404)
    
    def test_invalid_form_data(self):
        """Test handling of invalid form data."""
        self.client.login(username='admin', password='testpass123')
        
        # Submit completely invalid data
        invalid_data = {
            'vessel_id_tag': '',  # Required field empty
            'import_date': 'invalid-date',
            'supplier_name': '',  # Required field empty
            'quantity_litres': 'not-a-number',
            'price_per_litre': 'not-a-number',
        }
        
        response = self.client.post(reverse('shipments:shipment-add'), invalid_data)
        self.assertEqual(response.status_code, 200)  # Form redisplayed with errors
        self.assertContains(response, 'error')  # Should show error messages
    
    def test_concurrent_stock_depletion(self):
        """Test handling of concurrent stock depletion attempts."""
        # Create shipment
        shipment = Shipment.objects.create(
            user=self.admin_user,
            vessel_id_tag='CONCURRENT001',
            supplier_name='Concurrent Test',
            product=self.product_pms,
            destination=self.destination,
            quantity_litres=Decimal('100.00'),  # Small amount
            price_per_litre=Decimal('1.50')
        )
        
        # Create two trips that together would exceed available stock
        trip1 = Trip.objects.create(
            user=self.admin_user,
            vehicle=self.vehicle,
            customer=self.customer,
            product=self.product_pms,
            destination=self.destination,
            kpc_order_number='S11111',
            status='PENDING'
        )
        
        trip2 = Trip.objects.create(
            user=self.admin_user,
            vehicle=self.vehicle,
            customer=self.customer,
            product=self.product_pms,
            destination=self.destination,
            kpc_order_number='S22222',
            status='PENDING'
        )
        
        # Add compartments requiring more stock than available
        LoadingCompartment.objects.create(
            trip=trip1,
            compartment_number=1,
            quantity_requested_litres=Decimal('80.00')
        )
        
        LoadingCompartment.objects.create(
            trip=trip2,
            compartment_number=1,
            quantity_requested_litres=Decimal('80.00')
        )
        
        # First trip should succeed
        trip1.status = 'KPC_APPROVED'
        trip1.save()
        
        # Second trip should fail due to insufficient stock
        trip2.status = 'KPC_APPROVED'
        with self.assertRaises(ValidationError):
            trip2.save()


class DataExportTestCase(BaseTestCase):
    """Test data export functionality."""
    
    def test_monthly_report_data(self):
        """Test monthly report view with data."""
        # Create test data for specific month
        test_date = date(2024, 6, 15)
        
        shipment = Shipment.objects.create(
            user=self.admin_user,
            vessel_id_tag='MONTHLY001',
            supplier_name='Monthly Test',
            product=self.product_pms,
            destination=self.destination,
            quantity_litres=Decimal('1000.00'),
            price_per_litre=Decimal('1.50'),
            import_date=test_date
        )
        
        trip = Trip.objects.create(
            user=self.admin_user,
            vehicle=self.vehicle,
            customer=self.customer,
            product=self.product_pms,
            destination=self.destination,
            kpc_order_number='S33333',
            status='PENDING',
            loading_date=test_date
        )
        
        LoadingCompartment.objects.create(
            trip=trip,
            compartment_number=1,
            quantity_requested_litres=Decimal('500.00')
        )
        
        # Approve trip to create depletion
        trip.status = 'DELIVERED'
        trip.save()
        
        self.client.login(username='viewer', password='testpass123')
        response = self.client.get(
            reverse('shipments:monthly-stock-summary'),
            {'month': '6', 'year': '2024'}
        )
        
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'PMS')  # Product name
        self.assertContains(response, '500.00')  # Quantity


class CleanupTestCase(TestCase):
    """Test cleanup and maintenance operations."""
    
    def test_model_str_methods(self):
        """Test that all model __str__ methods work properly."""
        user = User.objects.create_user('test', 'test@test.com', 'pass')
        product = Product.objects.create(name='TEST')
        customer = Customer.objects.create(name='Test Customer')
        vehicle = Vehicle.objects.create(plate_number='TEST001')
        destination = Destination.objects.create(name='Test Dest')
        
        # Test Product str
        self.assertIn('TEST', str(product))
        
        # Test Customer str
        self.assertIn('Test Customer', str(customer))
        
        # Test Vehicle str
        self.assertIn('TEST001', str(vehicle))
        
        # Test Destination str
        self.assertIn('Test Dest', str(destination))
        
        # Test Shipment str
        shipment = Shipment.objects.create(
            user=user,
            vessel_id_tag='STR001',
            supplier_name='Test',
            product=product,
            destination=destination,
            quantity_litres=Decimal('100.00'),
            price_per_litre=Decimal('1.00')
        )
        self.assertIn('STR001', str(shipment))
        
        # Test Trip str
        trip = Trip.objects.create(
            user=user,
            vehicle=vehicle,
            customer=customer,
            product=product,
            destination=destination,
            kpc_order_number='S99999'
        )
        self.assertIn('S99999', str(trip))
        
        # Test LoadingCompartment str
        compartment = LoadingCompartment.objects.create(
            trip=trip,
            compartment_number=1,
            quantity_requested_litres=Decimal('50.00')
        )
        self.assertIn('Comp 1', str(compartment))
        
        # Test ShipmentDepletion str
        depletion = ShipmentDepletion.objects.create(
            trip=trip,
            shipment_batch=shipment,
            quantity_depleted=Decimal('25.00')
        )
        self.assertIn('25.00', str(depletion))


# Custom assertion for query counting
class CustomAssertNumQueries:
    def __init__(self, test_case, num):
        self.test_case = test_case
        self.num = num
    
    def __enter__(self):
        from django.test.utils import override_settings
        from django.db import connection
        self.old_queries = len(connection.queries)
        return self
    
    def __exit__(self, exc_type, exc_value, traceback):
        from django.db import connection
        executed = len(connection.queries) - self.old_queries
        if hasattr(self, 'less_than') and executed >= self.num:
            raise AssertionError(f"Query count {executed} was not less than {self.num}")
        elif not hasattr(self, 'less_than') and executed != self.num:
            raise AssertionError(f"Query count {executed} did not equal expected {self.num}")

# Monkey patch for less_than functionality
def assertNumQueries(self, num=None, less_than=None):
    if less_than is not None:
        assertion = CustomAssertNumQueries(self, less_than)
        assertion.less_than = True
        return assertion
    else:
        from django.test import TestCase
        return TestCase.assertNumQueries(self, num)

# Add the method to TestCase
TestCase.assertNumQueries = assertNumQueries