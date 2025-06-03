# shipments/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import login as auth_login, get_user_model
from django.contrib.auth.models import Group
from django.db.models import Sum, Count, F, ExpressionWrapper, DecimalField as DjangoDecimalField, Max, Q, Value
from django.utils import timezone
from django.db import transaction, IntegrityError
from django.http import HttpResponseForbidden, JsonResponse, Http404, HttpResponseBadRequest
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.core.cache import cache
from django.views.decorators.cache import cache_page
from django.views.decorators.http import require_http_methods, require_POST
from django.views.decorators.csrf import csrf_protect
from django.conf import settings
from collections import defaultdict
from calendar import monthrange
import datetime
import logging
import tempfile
import os
from decimal import Decimal, InvalidOperation

import pdfplumber
import re

from .models import Shipment, Product, Customer, Vehicle, Trip, LoadingCompartment, ShipmentDepletion, Destination
from .forms import ShipmentForm, TripForm, LoadingCompartmentFormSet, PdfLoadingAuthorityUploadForm

# Initialize logger
logger = logging.getLogger(__name__)
User = get_user_model()

# --- Helper Functions for Permissions ---
def is_viewer_or_admin_or_superuser(user):
    """Check if user has viewer, admin, or superuser privileges."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if user.groups.filter(name__in=['Admin', 'Viewer']).exists():
        return True
    return False

def is_admin_or_superuser(user):
    """Check if user has admin or superuser privileges."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if user.groups.filter(name='Admin').exists():
        return True
    return False

def get_user_accessible_shipments(user):
    """Get shipments accessible to the user based on permissions."""
    if is_viewer_or_admin_or_superuser(user):
        return Shipment.objects.select_related('product', 'destination', 'user')
    else:
        return Shipment.objects.filter(user=user).select_related('product', 'destination', 'user')

def get_user_accessible_trips(user):
    """Get trips accessible to the user based on permissions."""
    base_qs = Trip.objects.select_related(
        'product', 'destination', 'vehicle', 'customer', 'user'
    ).prefetch_related(
        'requested_compartments', 
        'depletions_for_trip__shipment_batch__product'
    )
    
    if is_viewer_or_admin_or_superuser(user):
        return base_qs.all()
    else:
        return base_qs.filter(user=user)

def validate_file_upload(uploaded_file, max_size_mb=None, allowed_extensions=None):
    """Validate uploaded file for security and size constraints."""
    if max_size_mb is None:
        max_size_mb = getattr(settings, 'FUEL_TRACKER_SETTINGS', {}).get('MAX_FILE_UPLOAD_SIZE_MB', 10)
    
    max_size_bytes = max_size_mb * 1024 * 1024
    
    if uploaded_file.size > max_size_bytes:
        raise ValidationError(f"File size ({uploaded_file.size} bytes) exceeds maximum allowed size ({max_size_mb}MB)")
    
    if allowed_extensions:
        file_extension = os.path.splitext(uploaded_file.name)[1].lower()
        if file_extension not in allowed_extensions:
            raise ValidationError(f"File type {file_extension} not allowed. Allowed types: {', '.join(allowed_extensions)}")
    
    # Basic file content validation for PDFs
    if uploaded_file.name.lower().endswith('.pdf'):
        uploaded_file.seek(0)
        header = uploaded_file.read(5)
        if header != b'%PDF-':
            raise ValidationError("Invalid PDF file format")
        uploaded_file.seek(0)
    
    return True

# --- Default Command Output Class ---
class DefaultCommandOutput:
    """Default output handler for model operations that expect stdout."""
    def write(self, msg, style_func=None): 
        logger.info(msg)
    
    class style: 
        @staticmethod
        def SUCCESS(x): 
            logger.info(f"SUCCESS: {x}")
            return x
        @staticmethod
        def ERROR(x): 
            logger.error(f"ERROR: {x}")
            return x
        @staticmethod
        def WARNING(x): 
            logger.warning(f"WARNING: {x}")
            return x
        @staticmethod
        def NOTICE(x): 
            logger.info(f"NOTICE: {x}")
            return x
    
    style = style()

# --- Home View (Dashboard) ---
@login_required
def home_view(request):
    """Main dashboard view with stock summary and notifications."""
    try:
        context = {
            'message': 'Welcome to Sakina Gas Fuel Tracker!',
            'description': 'Manage your fuel inventory efficiently.',
            'is_authenticated_user': request.user.is_authenticated
        }

        # Permissions for display toggles in template
        can_view_shipments = request.user.has_perm('shipments.view_shipment')
        can_add_shipment = request.user.has_perm('shipments.add_shipment')
        can_view_trip = request.user.has_perm('shipments.view_trip')
        
        context.update({
             'can_view_shipments': can_view_shipments, 
             'can_add_shipment': can_add_shipment,
             'can_view_trip': can_view_trip,
             'can_view_product': request.user.has_perm('shipments.view_product'), 
             'can_view_customer': request.user.has_perm('shipments.view_customer'), 
             'can_view_vehicle': request.user.has_perm('shipments.view_vehicle'),
             'can_add_trip': request.user.has_perm('shipments.add_trip')
        })

        # Get user-accessible data
        shipments_qs_base = get_user_accessible_shipments(request.user)
        trips_qs_base = get_user_accessible_trips(request.user)

        # Overall stats with error handling
        try:
            total_shipments_val = shipments_qs_base.count() if can_view_shipments else 0
            total_quantity_shipments_val = Decimal('0.00')
            total_value_shipments_val = Decimal('0.00')
            
            if can_view_shipments and total_shipments_val > 0:
                aggregation = shipments_qs_base.aggregate(
                    total_qty=Sum('quantity_litres'),
                    total_value=Sum(F('quantity_litres') * F('price_per_litre'))
                )
                total_quantity_shipments_val = aggregation.get('total_qty') or Decimal('0.00')
                total_value_shipments_val = aggregation.get('total_value') or Decimal('0.00')

            # Trip stats
            delivered_trips_qs = trips_qs_base.filter(status='DELIVERED') if can_view_trip else Trip.objects.none()
            total_trips_delivered = delivered_trips_qs.count()
            
            # Calculate total loaded quantity efficiently
            total_quantity_loaded_val = Decimal('0.00')
            if total_trips_delivered > 0:
                depletion_total = ShipmentDepletion.objects.filter(
                    trip__in=delivered_trips_qs
                ).aggregate(total=Sum('quantity_depleted'))
                total_quantity_loaded_val = depletion_total.get('total') or Decimal('0.00')

            context.update({
                'total_shipments': total_shipments_val,
                'total_quantity_shipments': total_quantity_shipments_val,
                'total_value_shipments': total_value_shipments_val,
                'total_trips': total_trips_delivered, 
                'total_quantity_loaded': total_quantity_loaded_val,
            })

        except Exception as e:
            logger.error(f"Error calculating dashboard stats: {e}")
            # Set defaults on error
            context.update({
                'total_shipments': 0, 'total_quantity_shipments': Decimal('0.00'),
                'total_value_shipments': Decimal('0.00'), 'total_trips': 0, 
                'total_quantity_loaded': Decimal('0.00'),
            })

        # Product stock summary with caching
        cache_key = f"dashboard_stock_summary_{request.user.id}"
        stock_by_product_detailed = cache.get(cache_key)
        
        if stock_by_product_detailed is None:
            try:
                stock_by_product_detailed = calculate_product_stock_summary(
                    shipments_qs_base, trips_qs_base, request.user
                )
                cache.set(cache_key, stock_by_product_detailed, 300)  # 5 minutes
            except Exception as e:
                logger.error(f"Error calculating stock summary: {e}")
                stock_by_product_detailed = {}
        
        context['stock_by_product_detailed'] = stock_by_product_detailed

        # Chart data and notifications
        if can_view_trip:
            try:
                chart_data = calculate_chart_data(trips_qs_base)
                context.update(chart_data)
            except Exception as e:
                logger.error(f"Error calculating chart data: {e}")
                context.update({
                    'loadings_chart_labels': [], 'pms_loadings_data': [], 'ago_loadings_data': []
                })

        # Notifications with error handling
        if can_view_shipments:
            try:
                notifications = calculate_notifications(shipments_qs_base, request.user)
                context.update(notifications)
            except Exception as e:
                logger.error(f"Error calculating notifications: {e}")
                context.update({
                    'aging_stock_notifications': [],
                    'inactive_product_notifications': [],
                    'utilized_shipment_notifications': []
                })

        # Trip quantity by product for delivered trips
        if can_view_trip:
            try:
                trip_quantity_by_product = ShipmentDepletion.objects.filter(
                    trip__in=delivered_trips_qs 
                ).values('shipment_batch__product__name').annotate(
                    total_litres=Sum('quantity_depleted')
                ).order_by('shipment_batch__product__name')
                context['trip_quantity_by_product'] = trip_quantity_by_product
            except Exception as e:
                logger.error(f"Error calculating trip quantities by product: {e}")
                context['trip_quantity_by_product'] = []

        if not any([can_view_shipments, can_view_trip, context.get('can_view_product'), 
                   context.get('can_view_customer'), context.get('can_view_vehicle')]):
             context['description'] = 'You do not have permission to view fuel data. Contact admin for access.'

        return render(request, 'shipments/home.html', context)
        
    except Exception as e:
        logger.exception(f"Unexpected error in home_view: {e}")
        messages.error(request, "An error occurred while loading the dashboard. Please try again.")
        return render(request, 'shipments/home.html', {
            'message': 'Welcome to Sakina Gas Fuel Tracker!',
            'description': 'An error occurred while loading dashboard data.',
        })

def calculate_product_stock_summary(shipments_qs, trips_qs, user):
    """Calculate detailed stock summary by product."""
    stock_by_product = {}
    committed_statuses = ['PENDING', 'KPC_APPROVED', 'LOADING', 'LOADED', 'GATEPASSED', 'TRANSIT']
    show_global_data = is_viewer_or_admin_or_superuser(user)
    
    for product_obj in Product.objects.all().order_by('name'):
        try:
            # Calculate received stock
            total_received = shipments_qs.filter(product=product_obj).aggregate(
                total=Sum('quantity_litres')
            )['total'] or Decimal('0.00')
            
            # Calculate delivered stock
            delivered_depletions = ShipmentDepletion.objects.filter(
                shipment_batch__product=product_obj,
                trip__status='DELIVERED'
            )
            if not show_global_data:
                delivered_depletions = delivered_depletions.filter(trip__user=user)
            
            total_delivered = delivered_depletions.aggregate(
                total=Sum('quantity_depleted')
            )['total'] or Decimal('0.00')
            
            physical_stock = total_received - total_delivered
            
            # Calculate committed stock
            committed_trips = trips_qs.filter(product=product_obj, status__in=committed_statuses)
            total_committed = Decimal('0.00')
            
            for trip in committed_trips:
                if trip.status == 'LOADED':
                    actual_l20 = trip.total_actual_l20_from_compartments
                    if actual_l20 > Decimal('0.00'):
                        total_committed += actual_l20
                    else:
                        total_committed += trip.total_loaded or trip.total_requested_from_compartments
                elif trip.status in ['GATEPASSED', 'TRANSIT']:
                    total_committed += trip.total_loaded or trip.total_requested_from_compartments
                else:
                    total_committed += trip.total_requested_from_compartments
            
            net_available = physical_stock - total_committed
            
            # Calculate average cost
            total_cost = shipments_qs.filter(product=product_obj).aggregate(
                total=Sum(F('quantity_litres') * F('price_per_litre'))
            )['total'] or Decimal('0.00')
            avg_price = total_cost / total_received if total_received > 0 else Decimal('0.000')
            
            # Only include products with activity
            if any([total_received > 0, total_delivered > 0, physical_stock != 0, total_committed != 0]):
                stock_by_product[product_obj.name] = {
                    'shipped': total_received,
                    'dispatched': total_delivered,
                    'physical_stock': physical_stock,
                    'booked_stock': total_committed,
                    'net_available': net_available,
                    'avg_price': avg_price
                }
                
        except Exception as e:
            logger.error(f"Error calculating stock for product {product_obj.name}: {e}")
            continue
    
    return stock_by_product

def calculate_chart_data(trips_qs):
    """Calculate chart data for the last 30 days."""
    today = timezone.now().date()
    chart_statuses = ['LOADED', 'GATEPASSED', 'TRANSIT', 'DELIVERED']
    
    daily_totals_pms = defaultdict(Decimal)
    daily_totals_ago = defaultdict(Decimal)
    
    # Get trips from last 30 days
    trips_last_30_days = trips_qs.filter(
        status__in=chart_statuses,
        loading_date__gte=today - datetime.timedelta(days=30)
    ).select_related('product').prefetch_related('depletions_for_trip')
    
    for trip in trips_last_30_days:
        chart_date = trip.loading_date
        trip_total = trip.total_loaded
        
        if trip.product.name.upper() == 'PMS':
            daily_totals_pms[chart_date] += trip_total
        elif trip.product.name.upper() == 'AGO':
            daily_totals_ago[chart_date] += trip_total
    
    # Generate chart data
    labels = []
    pms_data = []
    ago_data = []
    
    for i in range(29, -1, -1):
        current_day = today - datetime.timedelta(days=i)
        labels.append(current_day.strftime("%b %d"))
        pms_data.append(float(daily_totals_pms.get(current_day, Decimal('0.00'))))
        ago_data.append(float(daily_totals_ago.get(current_day, Decimal('0.00'))))
    
    return {
        'loadings_chart_labels': labels,
        'pms_loadings_data': pms_data,
        'ago_loadings_data': ago_data,
    }

def calculate_notifications(shipments_qs, user):
    """Calculate various notifications for the dashboard."""
    today = timezone.now().date()
    settings_dict = getattr(settings, 'FUEL_TRACKER_SETTINGS', {})
    
    age_threshold = settings_dict.get('AGING_STOCK_THRESHOLD_DAYS', 25)
    inactivity_threshold = settings_dict.get('INACTIVITY_THRESHOLD_DAYS', 5)
    utilized_threshold = settings_dict.get('UTILIZED_THRESHOLD_DAYS', 7)
    show_global_data = is_viewer_or_admin_or_superuser(user)
    
    notifications = {
        'aging_stock_notifications': [],
        'inactive_product_notifications': [],
        'utilized_shipment_notifications': []
    }
    
    try:
        # Aging stock notifications
        aging_shipments = shipments_qs.filter(
            quantity_remaining__gt=0,
            import_date__lte=today - datetime.timedelta(days=age_threshold)
        ).select_related('product', 'destination')
        
        for shipment in aging_shipments:
            days_old = (today - shipment.import_date).days
            dest_info = f" for {shipment.destination.name}" if shipment.destination else ""
            notifications['aging_stock_notifications'].append(
                f"Shipment '{shipment.vessel_id_tag}' ({shipment.product.name}{dest_info}) "
                f"imported on {shipment.import_date.strftime('%Y-%m-%d')} ({days_old} days old) "
                f"still has {shipment.quantity_remaining}L remaining."
            )
        
        # Product inactivity notifications
        for product in Product.objects.all():
            product_shipments = shipments_qs.filter(
                product=product, 
                quantity_remaining__gt=0
            )
            
            if product_shipments.exists():
                # Check last depletion
                depletion_filter = {'shipment_batch__product': product}
                if not show_global_data:
                    depletion_filter['trip__user'] = user
                
                last_depletion = ShipmentDepletion.objects.filter(
                    **depletion_filter
                ).aggregate(max_date=Max('created_at'))['max_date']
                
                days_inactive = 0
                message = ""
                
                if last_depletion:
                    days_inactive = (today - last_depletion.date()).days
                    if days_inactive > inactivity_threshold:
                        message = (f"Product '{product.name}' has stock but no dispatch "
                                 f"in {days_inactive} days (last: {last_depletion.date().strftime('%Y-%m-%d')}).")
                else:
                    oldest_stock = product_shipments.order_by('import_date').first()
                    if oldest_stock:
                        days_inactive = (today - oldest_stock.import_date).days
                        if days_inactive > inactivity_threshold:
                            message = (f"Product '{product.name}' has stock (oldest from "
                                     f"{oldest_stock.import_date.strftime('%Y-%m-%d')}) "
                                     f"and no dispatch in {days_inactive} days.")
                
                if message:
                    idle_batches = []
                    for batch in product_shipments.order_by('import_date'):
                        idle_batches.append({
                            'id': batch.id,
                            'vessel_id_tag': batch.vessel_id_tag,
                            'import_date': batch.import_date,
                            'supplier_name': batch.supplier_name,
                            'quantity_remaining': batch.quantity_remaining,
                        })
                    
                    notifications['inactive_product_notifications'].append({
                        'message': message,
                        'shipments': idle_batches
                    })
        
        # Utilized shipments notifications
        utilized_shipments = shipments_qs.filter(
            quantity_remaining__lte=Decimal('0.00'),
            updated_at__date__lte=today - datetime.timedelta(days=utilized_threshold)
        ).select_related('product')
        
        for shipment in utilized_shipments:
            last_depletion = ShipmentDepletion.objects.filter(
                shipment_batch=shipment
            ).aggregate(max_date=Max('created_at'))['max_date']
            
            utilized_date = shipment.updated_at.date()
            if last_depletion and last_depletion.date() > utilized_date:
                utilized_date = last_depletion.date()
            
            days_since_utilized = (today - utilized_date).days
            if days_since_utilized >= utilized_threshold:
                notifications['utilized_shipment_notifications'].append({
                    'id': shipment.id,
                    'vessel_id_tag': shipment.vessel_id_tag,
                    'product_name': shipment.product.name,
                    'supplier_name': shipment.supplier_name,
                    'import_date': shipment.import_date,
                    'utilized_date': utilized_date,
                    'days_since_utilized': days_since_utilized
                })
        
        # Sort utilized notifications by date
        notifications['utilized_shipment_notifications'].sort(
            key=lambda x: x['utilized_date']
        )
        
    except Exception as e:
        logger.error(f"Error calculating notifications: {e}")
    
    return notifications

# --- PDF Parsing with Enhanced Security ---
def parse_loading_authority_pdf(pdf_file_obj, request_for_messages=None):
    """Parse loading authority PDF with enhanced security and validation."""
    try:
        # Validate file
        validate_file_upload(pdf_file_obj, allowed_extensions=['.pdf'])
        
        extracted_data = {
            'compartment_quantities_litres': [],
            'total_quantity_litres': Decimal('0.00')
        }
        
        full_text = ""
        temp_file = None
        
        try:
            # Create temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as temp_file:
                for chunk in pdf_file_obj.chunks():
                    temp_file.write(chunk)
                temp_file_path = temp_file.name
            
            # Extract text from PDF
            with pdfplumber.open(temp_file_path) as pdf:
                if not pdf.pages:
                    if request_for_messages:
                        messages.error(request_for_messages, "PDF is empty or unreadable.")
                    return None
                
                for page_num, page in enumerate(pdf.pages):
                    try:
                        page_text = page.extract_text_simple(x_tolerance=1.5, y_tolerance=1.5)
                        if page_text:
                            full_text += page_text + "\n"
                    except Exception as e:
                        logger.warning(f"Error extracting text from page {page_num + 1}: {e}")
                        continue
            
            if not full_text.strip():
                if request_for_messages:
                    messages.error(request_for_messages, "No text could be extracted from the PDF.")
                return None
            
            # Parse required fields with improved regex patterns
            parsing_results = parse_pdf_fields(full_text)
            extracted_data.update(parsing_results)
            
            # Validate required fields
            required_fields = [
                'order_number', 'loading_date', 'destination_name',
                'truck_plate', 'customer_name', 'product_name'
            ]
            
            missing_fields = [
                field for field in required_fields 
                if not extracted_data.get(field)
            ]
            
            if (extracted_data.get('total_quantity_litres', Decimal('0.00')) <= Decimal('0.00') and 
                not extracted_data.get('compartment_quantities_litres')):
                missing_fields.append('total_quantity_litres')
            
            if missing_fields:
                error_msg = f"Essential data missing from PDF: {', '.join(missing_fields)}. Trip not created."
                if request_for_messages:
                    messages.error(request_for_messages, error_msg)
                logger.warning(f"PDF parsing failed - missing fields: {missing_fields}")
                return None
            
            logger.info(f"Successfully parsed PDF: {pdf_file_obj.name}")
            return extracted_data
            
        finally:
            # Clean up temporary file
            if temp_file and os.path.exists(temp_file_path):
                try:
                    os.unlink(temp_file_path)
                except OSError as e:
                    logger.warning(f"Could not delete temporary file {temp_file_path}: {e}")
                    
    except ValidationError as e:
        if request_for_messages:
            messages.error(request_for_messages, str(e))
        logger.warning(f"PDF validation failed: {e}")
        return None
    except Exception as e:
        error_msg = f"Error processing PDF: {e}"
        if request_for_messages:
            messages.error(request_for_messages, error_msg)
        logger.error(f"PDF parsing error: {e}", exc_info=True)
        return None

def parse_pdf_fields(text_content):
    """Parse specific fields from PDF text content."""
    extracted = {}
    
    try:
        # Order number
        order_match = re.search(r"ORDER\s+NUMBER\s*[:\-]?\s*(S\d+)", text_content, re.IGNORECASE)
        if order_match:
            extracted['order_number'] = order_match.group(1).strip().upper()
        
        # Date parsing with multiple formats
        date_patterns = [
            r"DATE\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{4})",
            r"DATE\s*[:\-]?\s*(\d{1,2}-\d{1,2}-\d{4})",
            r"DATE\s*[:\-]?\s*(\d{4}-\d{1,2}-\d{1,2})"
        ]
        
        for pattern in date_patterns:
            date_match = re.search(pattern, text_content, re.IGNORECASE)
            if date_match:
                date_str = date_match.group(1).strip()
                try:
                    # Try different date formats
                    for fmt in ['%d/%m/%Y', '%m/%d/%Y', '%Y-%m-%d']:
                        try:
                            extracted['loading_date'] = datetime.datetime.strptime(date_str, fmt).date()
                            break
                        except ValueError:
                            continue
                    break
                except ValueError:
                    logger.warning(f"Could not parse date: {date_str}")
        
        # Destination
        dest_match = re.search(
            r"DESTINATION\s*[:\-]?\s*(.*?)(?=\s*ID NO:|\s*TRUCK\s*NO:|\s*TRAILER\s*NO:|\s*DRIVER:|$)",
            text_content, re.IGNORECASE | re.DOTALL
        )
        if dest_match:
            extracted['destination_name'] = dest_match.group(1).replace('\n', ' ').strip()
        
        # Truck plate
        truck_match = re.search(
            r"TRUCK\s*(?:NO|PLATE)?\s*[:\-]?\s*([A-Z0-9\s\-]+?)(?=\s*TRAILER|\s*DEPOT|\s*DRIVER|$)",
            text_content, re.IGNORECASE | re.DOTALL
        )
        if truck_match:
            extracted['truck_plate'] = truck_match.group(1).replace('\n', ' ').strip().upper()
        
        # Trailer (optional)
        trailer_match = re.search(
            r"TRAILER\s*(?:NO|PLATE)?\s*[:\-]?\s*([A-Z0-9\s\-]*?)(?=\s*DRIVER|\s*ID NO|\s*DEPOT|$)",
            text_content, re.IGNORECASE | re.DOTALL
        )
        if trailer_match and trailer_match.group(1).strip():
            extracted['trailer_number'] = trailer_match.group(1).replace('\n', ' ').strip().upper()
        
        # Customer
        client_match = re.search(
            r"CLIENT\s*[:\-]?\s*(.*?)(?=\s*TRANSPORTER|\s*DEPOT|\s*PRODUCT\s*DESCRIPTION|$)",
            text_content, re.IGNORECASE | re.DOTALL
        )
        if client_match:
            extracted['customer_name'] = client_match.group(1).replace('\n', ' ').strip()
        
        # Product and quantity parsing
        header_match = re.search(
            r"DESCRIPTION\s+QUANTITY\s+(?:UNIT\s+OF\s+MEASURE|UOM)\s+COMPARTMENT",
            text_content, re.IGNORECASE
        )
        
        if header_match:
            text_after_header = text_content[header_match.end():]
            product_line_pattern = re.compile(
                r"^\s*(.+?)\s{2,}([\d\.,]+)\s+(m³|litres|l|ltrs)\s*(?:m³\s*)?([\d:\s]*?)\s*$",
                re.IGNORECASE | re.MULTILINE
            )
            
            for line in text_after_header.split('\n'):
                line_cleaned = line.strip()
                if not line_cleaned or any(keyword in line_cleaned.upper() 
                                         for keyword in ['PREPARED BY:', 'AUTHORIZED BY:', 'TOTAL']):
                    continue
                
                line_match = product_line_pattern.match(line_cleaned)
                if line_match:
                    try:
                        extracted['product_name'] = line_match.group(1).strip()
                        quantity_str = line_match.group(2).strip().replace(',', '')
                        unit = line_match.group(3).strip().upper()
                        compartment_str = line_match.group(4).strip() if line_match.group(4) else ""
                        
                        quantity_val = Decimal(quantity_str)
                        if unit == 'M³':
                            extracted['total_quantity_litres'] = quantity_val * Decimal('1000')
                        else:
                            extracted['total_quantity_litres'] = quantity_val
                        
                        # Parse compartment quantities
                        if compartment_str:
                            compartment_quantities = []
                            raw_values = re.split(r'[:\s]+', compartment_str)
                            for val_str in raw_values:
                                val_str = val_str.strip()
                                if val_str:
                                    try:
                                        comp_qty = Decimal(val_str.replace(',', '')) * Decimal('1000')
                                        compartment_quantities.append(comp_qty)
                                    except (ValueError, InvalidOperation):
                                        continue
                            
                            if compartment_quantities:
                                extracted['compartment_quantities_litres'] = compartment_quantities
                        
                        break
                        
                    except (ValueError, InvalidOperation) as e:
                        logger.warning(f"Error parsing product line '{line_cleaned}': {e}")
                        continue
        
        # Fallback total quantity parsing
        if extracted.get('total_quantity_litres', Decimal('0.00')) <= Decimal('0.00'):
            total_qty_match = re.search(
                r"TOTAL\s+QUANTITY\s*[:\-]?\s*([\d\.,]+)\s*(M³|LITRES|L)",
                text_content, re.IGNORECASE | re.DOTALL
            )
            if total_qty_match:
                try:
                    qty_str = total_qty_match.group(1).strip().replace(',', '')
                    unit = total_qty_match.group(2).strip().upper()
                    total_qty_val = Decimal(qty_str)
                    
                    if unit == 'M³':
                        extracted['total_quantity_litres'] = total_qty_val * Decimal('1000')
                    else:
                        extracted['total_quantity_litres'] = total_qty_val
                except (ValueError, InvalidOperation):
                    pass
        
    except Exception as e:
        logger.error(f"Error during PDF field parsing: {e}")
    
    return extracted

# --- Upload Loading Authority View ---
@login_required
@permission_required('shipments.add_trip', raise_exception=True)
@require_http_methods(["GET", "POST"])
@csrf_protect
def upload_loading_authority_view(request):
    """Handle upload and processing of loading authority PDFs."""
    if request.method == 'POST':
        form = PdfLoadingAuthorityUploadForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                pdf_file = request.FILES['pdf_file']
                
                # Additional security validation
                if not pdf_file.name.lower().endswith('.pdf'):
                    messages.error(request, "Invalid file type. Please upload a PDF.")
                    return render(request, 'shipments/upload_loading_authority.html', {'form': form})
                
                # Parse PDF
                parsed_data = parse_loading_authority_pdf(pdf_file, request)
                if not parsed_data:
                    return render(request, 'shipments/upload_loading_authority.html', {'form': form})
                
                # Create trip with transaction
                with transaction.atomic():
                    trip_instance = create_trip_from_parsed_data(parsed_data, request, pdf_file.name)
                    if trip_instance:
                        messages.success(
                            request, 
                            f"Trip for KPC Order No '{trip_instance.kpc_order_number}' "
                            f"(ID: {trip_instance.id}) created from PDF."
                        )
                        return redirect(reverse('shipments:trip-detail', kwargs={'pk': trip_instance.pk}))
                
            except ValidationError as e:
                messages.error(request, f"Data validation error: {str(e)}")
                logger.warning(f"Validation error in PDF upload: {e}")
            except IntegrityError as e:
                messages.error(request, "A trip with this order number already exists.")
                logger.warning(f"Integrity error in PDF upload: {e}")
            except Exception as e:
                messages.error(request, f"An unexpected error occurred: {str(e)}")
                logger.error(f"Unexpected error in PDF upload: {e}", exc_info=True)
    else:
        form = PdfLoadingAuthorityUploadForm()
    
    return render(request, 'shipments/upload_loading_authority.html', {'form': form})

def create_trip_from_parsed_data(parsed_data, request, filename):
    """Create trip and related objects from parsed PDF data."""
    try:
        # Extract and validate required data
        required_fields = ['product_name', 'customer_name', 'truck_plate', 'destination_name', 'order_number']
        for field in required_fields:
            if not parsed_data.get(field):
                raise ValidationError(f"Missing required field: {field}")
        
        # Get or create related objects
        product, _ = Product.objects.get_or_create(
            name__iexact=parsed_data['product_name'],
            defaults={'name': parsed_data['product_name'].upper()}
        )
        
        customer, _ = Customer.objects.get_or_create(
            name__iexact=parsed_data['customer_name'],
            defaults={'name': parsed_data['customer_name']}
        )
        
        vehicle, _ = Vehicle.objects.get_or_create(
            plate_number__iexact=parsed_data['truck_plate'],
            defaults={'plate_number': parsed_data['truck_plate'].upper()}
        )
        
        # Update trailer if provided
        if parsed_data.get('trailer_number') and vehicle.trailer_number != parsed_data['trailer_number']:
            vehicle.trailer_number = parsed_data['trailer_number']
            vehicle.save(update_fields=['trailer_number'])
        
        destination, _ = Destination.objects.get_or_create(
            name__iexact=parsed_data['destination_name'],
            defaults={'name': parsed_data['destination_name']}
        )
        
        # Check for existing trip
        kpc_order_no = parsed_data['order_number']
        if Trip.objects.filter(kpc_order_number__iexact=kpc_order_no).exists():
            existing_trip = Trip.objects.get(kpc_order_number__iexact=kpc_order_no)
            messages.warning(
                request, 
                f"Trip with KPC Order No '{kpc_order_no}' already exists (ID: {existing_trip.id})."
            )
            return existing_trip
        
        # Create trip
        trip_instance = Trip(
            user=request.user,
            vehicle=vehicle,
            customer=customer,
            product=product,
            destination=destination,
            loading_date=parsed_data.get('loading_date', timezone.now().date()),
            loading_time=parsed_data.get('loading_time', datetime.time(0, 0)),
            kpc_order_number=kpc_order_no,
            status='PENDING',
            notes=f"Created from PDF: {filename}",
        )
        trip_instance.save(stdout=DefaultCommandOutput())
        
        # Create compartments
        compartment_quantities = parsed_data.get('compartment_quantities_litres', [])
        total_qty = parsed_data.get('total_quantity_litres', Decimal('0.00'))
        
        if compartment_quantities:
            for i, qty in enumerate(compartment_quantities):
                if qty > 0:
                    LoadingCompartment.objects.create(
                        trip=trip_instance,
                        compartment_number=i + 1,
                        quantity_requested_litres=qty
                    )
        elif total_qty > 0:
            LoadingCompartment.objects.create(
                trip=trip_instance,
                compartment_number=1,
                quantity_requested_litres=total_qty
            )
        
        logger.info(f"Successfully created trip {trip_instance.id} from PDF {filename}")
        return trip_instance
        
    except Exception as e:
        logger.error(f"Error creating trip from parsed data: {e}", exc_info=True)
        raise

# --- Shipment Views with Enhanced Security ---
@login_required
@permission_required('shipments.view_shipment', raise_exception=True)
def shipment_list_view(request):
    """List shipments with filtering and pagination."""
    try:
        # Get user-accessible shipments
        queryset = get_user_accessible_shipments(request.user)
        
        # Apply filters with validation
        queryset = apply_shipment_filters(queryset, request.GET)
        
        # Add pagination
        paginator = Paginator(queryset.order_by('-import_date', '-created_at'), 25)
        page = request.GET.get('page')
        
        try:
            shipments = paginator.page(page)
        except PageNotAnInteger:
            shipments = paginator.page(1)
        except EmptyPage:
            shipments = paginator.page(paginator.num_pages)
        
        # Get filter options
        products_for_filter = Product.objects.all().order_by('name')
        
        context = {
            'shipments': shipments,
            'products': products_for_filter,
            'product_filter_value': request.GET.get('product', ''),
            'supplier_filter_value': request.GET.get('supplier_name', ''),
            'start_date_filter_value': request.GET.get('start_date', ''),
            'end_date_filter_value': request.GET.get('end_date', ''),
            'can_add_shipment': request.user.has_perm('shipments.add_shipment'),
            'can_change_shipment': request.user.has_perm('shipments.change_shipment'),
            'can_delete_shipment': request.user.has_perm('shipments.delete_shipment'),
        }
        
        return render(request, 'shipments/shipment_list.html', context)
        
    except Exception as e:
        logger.error(f"Error in shipment_list_view: {e}", exc_info=True)
        messages.error(request, "An error occurred while loading shipments.")
        return render(request, 'shipments/shipment_list.html', {
            'shipments': [],
            'products': [],
            'can_add_shipment': request.user.has_perm('shipments.add_shipment'),
            'can_change_shipment': request.user.has_perm('shipments.change_shipment'),
            'can_delete_shipment': request.user.has_perm('shipments.delete_shipment'),
        })

def apply_shipment_filters(queryset, get_params):
    """Apply filters to shipment queryset with validation."""
    try:
        # Product filter
        product_filter = get_params.get('product', '').strip()
        if product_filter and product_filter.isdigit():
            queryset = queryset.filter(product__pk=int(product_filter))
        
        # Supplier filter with SQL injection protection
        supplier_filter = get_params.get('supplier_name', '').strip()
        if supplier_filter:
            # Limit length and escape
            supplier_filter = supplier_filter[:100]
            queryset = queryset.filter(supplier_name__icontains=supplier_filter)
        
        # Date filters with validation
        start_date_str = get_params.get('start_date', '').strip()
        if start_date_str:
            try:
                start_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d').date()
                queryset = queryset.filter(import_date__gte=start_date)
            except ValueError:
                logger.warning(f"Invalid start date format: {start_date_str}")
        
        end_date_str = get_params.get('end_date', '').strip()
        if end_date_str:
            try:
                end_date = datetime.datetime.strptime(end_date_str, '%Y-%m-%d').date()
                queryset = queryset.filter(import_date__lte=end_date)
            except ValueError:
                logger.warning(f"Invalid end date format: {end_date_str}")
        
    except Exception as e:
        logger.error(f"Error applying shipment filters: {e}")
    
    return queryset

@login_required
@permission_required('shipments.add_trip', raise_exception=True)
@require_http_methods(["GET", "POST"])
@csrf_protect
def trip_add_view(request):
    """Add new trip with compartments."""
    if request.method == 'POST':
        trip_form = TripForm(request.POST) 
        compartment_formset = LoadingCompartmentFormSet(request.POST, prefix='compartments')
        
        if trip_form.is_valid() and compartment_formset.is_valid():
            try:
                with transaction.atomic():
                    trip_instance = trip_form.save(commit=False)
                    trip_instance.user = request.user
                    trip_instance.full_clean()
                    trip_instance.save(stdout=DefaultCommandOutput()) 
                    
                    compartment_formset.instance = trip_instance
                    for form in compartment_formset:
                        if form.cleaned_data and not form.cleaned_data.get('DELETE', False):
                            form.instance.full_clean()
                    compartment_formset.save()
                    
                    messages.success(request, f'Loading for KPC Order {trip_instance.kpc_order_number} recorded. Status: {trip_instance.get_status_display()}.')
                    return redirect(reverse('shipments:trip-detail', kwargs={'pk': trip_instance.pk}))
            except ValidationError as e:
                error_dict = e.message_dict if hasattr(e, 'message_dict') else {'__all__': getattr(e, 'messages', [str(e)])}
                for field, errors_list in error_dict.items():
                    for error_item in errors_list:
                        if field == '__all__' or not hasattr(trip_form, field) or field == 'status': 
                            messages.error(request, error_item) 
                        elif hasattr(trip_form, field): 
                            trip_form.add_error(field, error_item)
                        else: 
                            messages.error(request, f"Error: {error_item}")
            except IntegrityError as e:
                if 'kpc_order_number' in str(e):
                    trip_form.add_error('kpc_order_number', 'A trip with this KPC Order Number already exists.')
                else:
                    messages.error(request, "Database error occurred.")
                logger.error(f"IntegrityError in trip creation: {e}")
            except Exception as e: 
                messages.error(request, f'An unexpected error occurred: {str(e)}')
                logger.error(f"Unexpected error in trip creation: {e}", exc_info=True)
        else: 
            messages.error(request, 'Please correct the form errors (main or compartments).')
    else: 
        trip_form = TripForm()
        initial_compartments = [{'compartment_number': i+1} for i in range(3)]
        compartment_formset = LoadingCompartmentFormSet(prefix='compartments', initial=initial_compartments)
    
    context = {
        'trip_form': trip_form, 
        'compartment_formset': compartment_formset, 
        'page_title': 'Record New Loading'
    }
    return render(request, 'shipments/trip_form.html', context)

@login_required
@require_http_methods(["GET", "POST"])
@csrf_protect
def trip_edit_view(request, pk):
    """Edit trip with proper permissions."""
    if is_admin_or_superuser(request.user): 
        trip_instance = get_object_or_404(Trip, pk=pk)
    else: 
        trip_instance = get_object_or_404(Trip.objects.filter(user=request.user), pk=pk)
    
    if not request.user.has_perm('shipments.change_trip'): 
        return HttpResponseForbidden("You do not have permission to change loadings.")

    if request.method == 'POST':
        trip_form = TripForm(request.POST, instance=trip_instance) 
        compartment_formset = LoadingCompartmentFormSet(request.POST, instance=trip_instance, prefix='compartments')
        
        if trip_form.is_valid() and compartment_formset.is_valid():
            try:
                with transaction.atomic():
                    updated_trip_instance = trip_form.save(commit=False)
                    updated_trip_instance.full_clean()
                    updated_trip_instance.save(stdout=DefaultCommandOutput())
                    
                    compartment_formset.instance = updated_trip_instance
                    for form in compartment_formset:
                        if form.cleaned_data and not form.cleaned_data.get('DELETE', False):
                            form.instance.full_clean()
                    compartment_formset.save()
                    
                    messages.success(request, f"Loading '{updated_trip_instance.kpc_order_number}' updated successfully!")
                    return redirect(reverse('shipments:trip-detail', kwargs={'pk': updated_trip_instance.pk}))
            except ValidationError as e:
                error_dict = e.message_dict if hasattr(e, 'message_dict') else {'__all__': getattr(e, 'messages', [str(e)])}
                for field, errors_list in error_dict.items():
                    for error_item in errors_list:
                        if field == '__all__' or not hasattr(trip_form, field) or field == 'status': 
                            messages.error(request, error_item) 
                        elif hasattr(trip_form, field): 
                            trip_form.add_error(field, error_item)
                        else: 
                            messages.error(request, f"Error: {error_item}")
            except Exception as e: 
                messages.error(request, f'An unexpected error during update: {str(e)}')
                logger.error(f"Error updating trip {pk}: {e}", exc_info=True)
        else: 
            messages.error(request, 'Please correct the form errors (main or compartments).')
    else: 
        trip_form = TripForm(instance=trip_instance)
        compartment_formset = LoadingCompartmentFormSet(instance=trip_instance, prefix='compartments')
    
    context = { 
        'trip_form': trip_form, 
        'compartment_formset': compartment_formset, 
        'page_title': f'Edit Loading: {trip_instance.kpc_order_number or f"Trip {trip_instance.id}"}', 
        'trip': trip_instance 
    }
    return render(request, 'shipments/trip_form.html', context)

@login_required
@require_http_methods(["GET", "POST"])
@csrf_protect
def trip_delete_view(request, pk):
    """Delete trip with stock reversal."""
    if is_admin_or_superuser(request.user): 
        trip_instance = get_object_or_404(Trip, pk=pk)
    else: 
        trip_instance = get_object_or_404(Trip.objects.filter(user=request.user), pk=pk)
    
    if not request.user.has_perm('shipments.delete_trip'): 
        return HttpResponseForbidden("You do not have permission to delete loadings.")
    
    if request.method == 'POST':
        try:
            with transaction.atomic():
                if not trip_instance.reverse_stock_depletion():
                    messages.error(request, "Failed to reverse stock depletions. Deletion aborted.")
                    return redirect(reverse('shipments:trip-detail', kwargs={'pk': pk}))
                
                trip_desc = str(trip_instance)
                trip_instance.delete()
                messages.success(request, f"Loading '{trip_desc}' and depletions (if any) deleted successfully!")
                return redirect(reverse('shipments:trip-list'))
        except Exception as e: 
            messages.error(request, f"Error during deletion: {str(e)}")
            logger.error(f"Error deleting trip {pk}: {e}", exc_info=True)
            return redirect(reverse('shipments:trip-detail', kwargs={'pk': pk}))
    
    context = {
        'trip': trip_instance, 
        'page_title': f'Confirm Delete Loading: {trip_instance.kpc_order_number or f"Trip {trip_instance.id}"}'
    }
    return render(request, 'shipments/trip_confirm_delete.html', context)

@login_required
@permission_required('shipments.view_trip', raise_exception=True)
def trip_detail_view(request, pk):
    """View trip details with compartments and depletions."""
    if is_viewer_or_admin_or_superuser(request.user):
        trip = get_object_or_404(
            Trip.objects.select_related('user', 'vehicle', 'customer', 'product', 'destination')
            .prefetch_related('requested_compartments', 'depletions_for_trip__shipment_batch__product'), 
            pk=pk
        )
    else:
        trip = get_object_or_404(
            Trip.objects.filter(user=request.user)
            .select_related('user', 'vehicle', 'customer', 'product', 'destination')
            .prefetch_related('requested_compartments', 'depletions_for_trip__shipment_batch__product'), 
            pk=pk
        )
    
    requested_compartments_qs = trip.requested_compartments.all().order_by('compartment_number')
    actual_depletions_qs = trip.depletions_for_trip.all().order_by('created_at', 'shipment_batch__import_date')
    
    context = {
        'trip': trip, 
        'compartments': requested_compartments_qs, 
        'actual_depletions': actual_depletions_qs, 
        'page_title': f'Loading Details: {trip.kpc_order_number or f"Trip {trip.id}"}', 
        'can_change_trip': request.user.has_perm('shipments.change_trip') and (is_admin_or_superuser(request.user) or trip.user == request.user),
        'can_delete_trip': request.user.has_perm('shipments.delete_trip') and (is_admin_or_superuser(request.user) or trip.user == request.user),
    }
    return render(request, 'shipments/trip_detail.html', context)

# --- Dashboard and Reporting Views ---
@login_required
@permission_required('shipments.view_trip', raise_exception=True) 
@permission_required('shipments.view_shipment', raise_exception=True)
def truck_activity_dashboard_view(request):
    """Truck activity dashboard with filtering."""
    try:
        show_global_data = is_viewer_or_admin_or_superuser(request.user)
        if show_global_data: 
            base_queryset = Trip.objects.all()
        else: 
            base_queryset = Trip.objects.filter(user=request.user)
        
        # Apply filters
        base_queryset = apply_trip_filters(base_queryset, request.GET)

        truck_activities = defaultdict(lambda: {'trips': [], 'total_quantity': Decimal('0.00'), 'trip_count': 0})
        filtered_trips = base_queryset.select_related('vehicle', 'product', 'customer', 'user', 'destination').prefetch_related('depletions_for_trip')
        
        for trip in filtered_trips:
            vehicle_obj = trip.vehicle
            truck_activities[vehicle_obj]['trips'].append(trip)
            truck_activities[vehicle_obj]['total_quantity'] += trip.total_loaded
            truck_activities[vehicle_obj]['trip_count'] += 1
        
        sorted_truck_activities = dict(sorted(truck_activities.items(), key=lambda item: item[0].plate_number))
        
        products_for_filter = Product.objects.all().order_by('name')
        customers_for_filter = Customer.objects.all().order_by('name')
        vehicles_for_filter = Vehicle.objects.all().order_by('plate_number')
        status_choices_for_filter = Trip.STATUS_CHOICES
        
        context = { 
            'truck_activities': sorted_truck_activities, 
            'products': products_for_filter, 
            'customers': customers_for_filter, 
            'vehicles': vehicles_for_filter, 
            'status_choices': status_choices_for_filter, 
            'product_filter_value': request.GET.get('product', ''), 
            'customer_filter_value': request.GET.get('customer', ''), 
            'vehicle_filter_value': request.GET.get('vehicle', ''), 
            'status_filter_value': request.GET.get('status', ''), 
            'start_date_filter_value': request.GET.get('start_date', ''), 
            'end_date_filter_value': request.GET.get('end_date', ''), 
            'page_title': 'Truck Activity Dashboard'
        }
        return render(request, 'shipments/truck_activity_dashboard.html', context)
        
    except Exception as e:
        logger.error(f"Error in truck_activity_dashboard_view: {e}", exc_info=True)
        messages.error(request, "An error occurred while loading truck activity data.")
        return render(request, 'shipments/truck_activity_dashboard.html', {
            'truck_activities': {},
            'products': [],
            'customers': [],
            'vehicles': [],
            'status_choices': [],
            'page_title': 'Truck Activity Dashboard'
        })

@login_required
@permission_required('shipments.view_shipment', raise_exception=True)
@permission_required('shipments.view_trip', raise_exception=True) 
def monthly_stock_summary_view(request):
    """Monthly stock summary report."""
    try:
        show_global_data = is_viewer_or_admin_or_superuser(request.user)
        
        # Get available years from both shipments and trips
        shipment_years = Shipment.objects.dates('import_date', 'year', order='DESC')
        trip_years = Trip.objects.dates('loading_date', 'year', order='DESC')
        all_years = sorted(list(set([d.year for d in shipment_years] + [d.year for d in trip_years])), reverse=True)
        if not all_years: 
            all_years.append(datetime.date.today().year)
        
        months_for_dropdown = [(i, datetime.date(2000, i, 1).strftime('%B')) for i in range(1, 13)]
        current_year = datetime.date.today().year
        current_month = datetime.date.today().month
        
        selected_year_str = request.GET.get('year', str(current_year))
        selected_month_str = request.GET.get('month', str(current_month))
        
        try:
            selected_year = int(selected_year_str)
            selected_month = int(selected_month_str)
            if not (1 <= selected_month <= 12 and 1900 < selected_year < 2200): 
                raise ValueError("Invalid year or month")
        except (ValueError, TypeError): 
            messages.error(request, "Invalid year or month. Defaulting to current period.")
            selected_year = current_year
            selected_month = current_month
        
        start_of_selected_month = datetime.date(selected_year, selected_month, 1)
        num_days_in_month = monthrange(selected_year, selected_month)[1]
        end_of_selected_month = datetime.date(selected_year, selected_month, num_days_in_month)
        
        summary_data = []
        all_products = Product.objects.all().order_by('name')
        
        for product_obj in all_products:
            try:
                shipments_product_qs = Shipment.objects.filter(product=product_obj)
                depletions_product_qs = ShipmentDepletion.objects.filter(shipment_batch__product=product_obj)
                
                if not show_global_data:
                    shipments_product_qs = shipments_product_qs.filter(user=request.user)
                    depletions_product_qs = depletions_product_qs.filter(trip__user=request.user)
                
                total_shipped_before_month = shipments_product_qs.filter(
                    import_date__lt=start_of_selected_month
                ).aggregate(s=Sum('quantity_litres'))['s'] or Decimal('0.00')
                
                total_depleted_before_month = depletions_product_qs.filter(
                    created_at__date__lt=start_of_selected_month
                ).aggregate(s=Sum('quantity_depleted'))['s'] or Decimal('0.00')
                
                opening_stock = total_shipped_before_month - total_depleted_before_month
                
                stock_in_month = shipments_product_qs.filter(
                    import_date__gte=start_of_selected_month, 
                    import_date__lte=end_of_selected_month
                ).aggregate(s=Sum('quantity_litres'))['s'] or Decimal('0.00')
                
                stock_out_month = depletions_product_qs.filter(
                    created_at__date__gte=start_of_selected_month, 
                    created_at__date__lte=end_of_selected_month
                ).aggregate(s=Sum('quantity_depleted'))['s'] or Decimal('0.00')
                
                closing_stock = opening_stock + stock_in_month - stock_out_month
                
                # Only include products with activity
                if opening_stock != 0 or stock_in_month != 0 or stock_out_month != 0 or closing_stock != 0:
                    summary_data.append({
                        'product_name': product_obj.name, 
                        'opening_stock': opening_stock, 
                        'stock_in_month': stock_in_month, 
                        'stock_out_month': stock_out_month, 
                        'closing_stock': closing_stock
                    })
                    
            except Exception as e:
                logger.error(f"Error calculating monthly summary for product {product_obj.name}: {e}")
                continue
        
        context = {
            'summary_data': summary_data, 
            'selected_year': selected_year, 
            'selected_month': selected_month, 
            'month_name_display': datetime.date(1900, selected_month, 1).strftime('%B'), 
            'available_years': [str(year) for year in all_years],  # Convert to strings 
            'months_for_dropdown': months_for_dropdown, 
            'page_title': f'Monthly Stock Summary - {datetime.date(1900, selected_month, 1).strftime("%B")} {selected_year}'
        }
        return render(request, 'shipments/monthly_stock_summary.html', context)
        
    except Exception as e:
        logger.error(f"Error in monthly_stock_summary_view: {e}", exc_info=True)
        messages.error(request, "An error occurred while generating the monthly report.")
        return render(request, 'shipments/monthly_stock_summary.html', {
            'summary_data': [],
            'selected_year': datetime.date.today().year,
            'selected_month': datetime.date.today().month,
            'available_years': [datetime.date.today().year],
            'months_for_dropdown': [(i, datetime.date(2000, i, 1).strftime('%B')) for i in range(1, 13)],
            'page_title': 'Monthly Stock Summary'
        })

# --- User Management ---
@require_http_methods(["GET", "POST"])
@csrf_protect
def signup_view(request):
    """User registration with automatic group assignment."""
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid(): 
            try:
                with transaction.atomic():
                    user = form.save()
                    auth_login(request, user)
                    messages.success(request, 'Account created successfully! You are now logged in.')
                    
                    # Assign to Viewer group by default
                    try:
                        viewer_group, created = Group.objects.get_or_create(name='Viewer')
                        user.groups.add(viewer_group)
                        logger.info(f"New user {user.username} assigned to Viewer group")
                    except Exception as e: 
                        messages.warning(request, f"Could not assign default group: {e}")
                        logger.warning(f"Failed to assign group to user {user.username}: {e}")
                    
                    return redirect(reverse('shipments:home'))
            except Exception as e:
                messages.error(request, f"An error occurred during registration: {e}")
                logger.error(f"Error during user registration: {e}", exc_info=True)
    else: 
        form = UserCreationForm()
    
    context = {'form': form, 'page_title': 'Sign Up'}
    return render(request, 'registration/signup.html', context)

# --- API-like Views (for AJAX calls) ---
@login_required
@require_http_methods(["GET"])
def get_vehicle_capacity_ajax(request):
    """AJAX endpoint to get vehicle capacity for a product (if needed in future)."""
    vehicle_id = request.GET.get('vehicle_id')
    product_id = request.GET.get('product_id')
    
    if not vehicle_id or not product_id:
        return JsonResponse({'error': 'Missing vehicle_id or product_id'}, status=400)
    
    try:
        vehicle = Vehicle.objects.get(pk=vehicle_id)
        product = Product.objects.get(pk=product_id)
        
        # For now, return a generic capacity since we removed product-specific capacities
        # This could be enhanced later with more sophisticated capacity calculations
        capacity = 30000  # Default 30,000L capacity
        
        return JsonResponse({
            'capacity': capacity,
            'vehicle': vehicle.plate_number,
            'product': product.name
        })
        
    except (Vehicle.DoesNotExist, Product.DoesNotExist):
        return JsonResponse({'error': 'Vehicle or Product not found'}, status=404)
    except Exception as e:
        logger.error(f"Error in get_vehicle_capacity_ajax: {e}")
        return JsonResponse({'error': 'Server error'}, status=500)

# --- Error Handlers ---
def handler404(request, exception):
    """Custom 404 handler."""
    return render(request, '404.html', status=404)

def handler500(request):
    """Custom 500 handler."""
    return render(request, '500.html', status=500)

def handler403(request, exception):
    """Custom 403 handler."""
    return render(request, '403.html', status=403)

# --- Health Check View ---
@require_http_methods(["GET"])
def health_check(request):
    """Simple health check endpoint."""
    try:
        # Test database connection
        Product.objects.count()
        return JsonResponse({
            'status': 'healthy',
            'timestamp': timezone.now().isoformat(),
            'database': 'connected'
        })
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JsonResponse({
            'status': 'unhealthy',
            'timestamp': timezone.now().isoformat(),
            'error': str(e)
        }, status=503)

@login_required
@permission_required('shipments.add_shipment', raise_exception=True)
@require_http_methods(["GET", "POST"])
@csrf_protect
def shipment_add_view(request):
    """Add new shipment with validation."""
    if request.method == 'POST':
        form = ShipmentForm(request.POST) 
        if form.is_valid():
            try:
                with transaction.atomic():
                    shipment_instance = form.save(commit=False)
                    shipment_instance.user = request.user
                    shipment_instance.full_clean()  # Ensure model validation
                    shipment_instance.save()
                    messages.success(request, 'Shipment added successfully!')
                    return redirect(reverse('shipments:shipment-detail', kwargs={'pk': shipment_instance.pk}))
            except ValidationError as e:
                for field, errors in e.message_dict.items():
                    for error in errors:
                        if field == '__all__':
                            messages.error(request, error)
                        else:
                            form.add_error(field, error)
            except IntegrityError as e:
                if 'vessel_id_tag' in str(e):
                    form.add_error('vessel_id_tag', 'A shipment with this Vessel ID already exists.')
                else:
                    messages.error(request, "Database error occurred.")
                logger.error(f"IntegrityError in shipment creation: {e}")
            except Exception as e: 
                messages.error(request, f"An unexpected error occurred: {e}")
                logger.error(f"Unexpected error in shipment creation: {e}", exc_info=True)
    else: 
        form = ShipmentForm()
    
    context = {'form': form, 'page_title': 'Add New Shipment'}
    return render(request, 'shipments/shipment_form.html', context)

@login_required
@require_http_methods(["GET", "POST"])
@csrf_protect
def shipment_edit_view(request, pk):
    """Edit shipment with proper permissions and validation."""
    if is_admin_or_superuser(request.user): 
        shipment_instance = get_object_or_404(Shipment, pk=pk)
    else: 
        shipment_instance = get_object_or_404(Shipment.objects.filter(user=request.user), pk=pk)
    
    if not request.user.has_perm('shipments.change_shipment'): 
        return HttpResponseForbidden("You do not have permission to change shipments.")
    
    depleted_quantity = ShipmentDepletion.objects.filter(shipment_batch=shipment_instance).aggregate(
        total_depleted=Sum('quantity_depleted')
    )['total_depleted'] or Decimal('0.00')
    
    if request.method == 'POST':
        form = ShipmentForm(request.POST, instance=shipment_instance) 
        if form.is_valid():
            new_quantity_litres = form.cleaned_data.get('quantity_litres', shipment_instance.quantity_litres)
            if new_quantity_litres < depleted_quantity:
                form.add_error('quantity_litres', 
                    f"Cannot reduce total quantity ({new_quantity_litres}L) below what has already been depleted ({depleted_quantity}L).")
            else:
                try:
                    with transaction.atomic():
                        shipment_to_save = form.save(commit=False)
                        shipment_to_save.quantity_remaining = new_quantity_litres - depleted_quantity
                        shipment_to_save.full_clean()
                        shipment_to_save.save()
                        messages.success(request, f"Shipment '{shipment_to_save.vessel_id_tag}' updated successfully!")
                        return redirect(reverse('shipments:shipment-detail', kwargs={'pk': shipment_to_save.pk}))
                except ValidationError as e:
                    for field, errors in e.message_dict.items():
                        for error in errors:
                            if field == '__all__':
                                messages.error(request, error)
                            else:
                                form.add_error(field, error)
                except Exception as e: 
                    messages.error(request, f"An unexpected error: {e}")
                    logger.error(f"Error updating shipment {pk}: {e}", exc_info=True)
    else: 
        form = ShipmentForm(instance=shipment_instance)
    
    if depleted_quantity > 0: 
        messages.info(request, f"Note: {depleted_quantity}L from this shipment has been used. Total quantity cannot be set lower.")
    
    context = {
        'form': form, 
        'page_title': f'Edit Shipment: {shipment_instance.vessel_id_tag}',
        'shipment_instance': shipment_instance, 
        'depleted_quantity': depleted_quantity,
    }
    return render(request, 'shipments/shipment_form.html', context)

@login_required
@require_http_methods(["GET", "POST"])
@csrf_protect
def shipment_delete_view(request, pk):
    """Delete shipment with proper validation."""
    if is_admin_or_superuser(request.user): 
        shipment_instance = get_object_or_404(Shipment, pk=pk)
    else: 
        shipment_instance = get_object_or_404(Shipment.objects.filter(user=request.user), pk=pk)
    
    if not request.user.has_perm('shipments.delete_shipment'): 
        return HttpResponseForbidden("You do not have permission to delete shipments.")
    
    if ShipmentDepletion.objects.filter(shipment_batch=shipment_instance).exists():
        messages.error(request, f"Cannot delete Shipment '{shipment_instance.vessel_id_tag}'. It has associated loadings. Adjust loadings first.")
        return redirect(reverse('shipments:shipment-detail', kwargs={'pk': shipment_instance.pk}))
    
    if request.method == 'POST':
        try:
            shipment_tag = shipment_instance.vessel_id_tag
            shipment_instance.delete()
            messages.success(request, f"Shipment '{shipment_tag}' deleted successfully!")
            return redirect(reverse('shipments:shipment-list'))
        except Exception as e: 
            messages.error(request, f"Error deleting: {e}")
            logger.error(f"Error deleting shipment {pk}: {e}", exc_info=True)
            return redirect(reverse('shipments:shipment-detail', kwargs={'pk': pk}))
    
    context = {
        'shipment': shipment_instance, 
        'page_title': f'Confirm Delete: {shipment_instance.vessel_id_tag}',
        'can_delete_shipment': True  # User reached here, so they have permission
    }
    return render(request, 'shipments/shipment_confirm_delete.html', context)

@login_required
@permission_required('shipments.view_shipment', raise_exception=True)
def shipment_detail_view(request, pk):
    """View shipment details with proper permissions."""
    if is_viewer_or_admin_or_superuser(request.user): 
        shipment = get_object_or_404(Shipment.objects.select_related('user', 'product', 'destination'), pk=pk)
    else: 
        shipment = get_object_or_404(Shipment.objects.filter(user=request.user).select_related('user', 'product', 'destination'), pk=pk)
    
    context = {
        'shipment': shipment, 
        'page_title': f'Shipment Details: {shipment.vessel_id_tag}',
        'can_change_shipment': request.user.has_perm('shipments.change_shipment') and (is_admin_or_superuser(request.user) or shipment.user == request.user),
        'can_delete_shipment': request.user.has_perm('shipments.delete_shipment') and (is_admin_or_superuser(request.user) or shipment.user == request.user),
    }
    return render(request, 'shipments/shipment_detail.html', context)

# --- Trip Views ---
@login_required
@permission_required('shipments.view_trip', raise_exception=True)
def trip_list_view(request):
    """List trips with filtering and pagination."""
    try:
        if is_viewer_or_admin_or_superuser(request.user): 
            queryset = Trip.objects.all()
        else: 
            queryset = Trip.objects.filter(user=request.user)
        
        # Apply filters
        queryset = apply_trip_filters(queryset, request.GET)
        
        # Calculate summary stats for filtered results
        filtered_trip_count = queryset.count()
        filtered_total_loaded_val = sum(trip.total_loaded for trip in queryset.prefetch_related('depletions_for_trip'))

        # Add pagination
        paginator = Paginator(queryset.select_related('vehicle', 'customer', 'product', 'destination', 'user').order_by('-loading_date', '-loading_time'), 25)
        page = request.GET.get('page')
        
        try:
            trips = paginator.page(page)
        except PageNotAnInteger:
            trips = paginator.page(1)
        except EmptyPage:
            trips = paginator.page(paginator.num_pages)
        
        # Get filter options
        products_for_filter = Product.objects.all().order_by('name')
        customers_for_filter = Customer.objects.all().order_by('name')
        vehicles_for_filter = Vehicle.objects.all().order_by('plate_number')
        status_choices_for_filter = Trip.STATUS_CHOICES
        
        context = { 
            'trips': trips, 
            'products': products_for_filter, 
            'customers': customers_for_filter, 
            'vehicles': vehicles_for_filter, 
            'status_choices': status_choices_for_filter, 
            'product_filter_value': request.GET.get('product', ''), 
            'customer_filter_value': request.GET.get('customer', ''), 
            'vehicle_filter_value': request.GET.get('vehicle', ''), 
            'status_filter_value': request.GET.get('status', ''), 
            'start_date_filter_value': request.GET.get('start_date', ''), 
            'end_date_filter_value': request.GET.get('end_date', ''), 
            'filtered_trip_count': filtered_trip_count, 
            'filtered_total_loaded': filtered_total_loaded_val, 
            'can_add_trip': request.user.has_perm('shipments.add_trip'), 
            'can_change_trip': request.user.has_perm('shipments.change_trip'), 
            'can_delete_trip': request.user.has_perm('shipments.delete_trip'),
        }
        return render(request, 'shipments/trip_list.html', context)
        
    except Exception as e:
        logger.error(f"Error in trip_list_view: {e}", exc_info=True)
        messages.error(request, "An error occurred while loading trips.")
        return render(request, 'shipments/trip_list.html', {
            'trips': [],
            'products': [],
            'customers': [],
            'vehicles': [],
            'status_choices': [],
            'filtered_trip_count': 0,
            'filtered_total_loaded': 0,
            'can_add_trip': request.user.has_perm('shipments.add_trip'),
            'can_change_trip': request.user.has_perm('shipments.change_trip'),
            'can_delete_trip': request.user.has_perm('shipments.delete_trip'),
        })

def apply_trip_filters(queryset, get_params):
    """Apply filters to trip queryset with validation."""
    try:
        # Product filter
        product_filter_pk = get_params.get('product', '').strip()
        if product_filter_pk and product_filter_pk.isdigit():
            queryset = queryset.filter(product__pk=int(product_filter_pk))
        
        # Customer filter
        customer_filter_pk = get_params.get('customer', '').strip()
        if customer_filter_pk and customer_filter_pk.isdigit():
            queryset = queryset.filter(customer__pk=int(customer_filter_pk))
        
        # Vehicle filter
        vehicle_filter_pk = get_params.get('vehicle', '').strip()
        if vehicle_filter_pk and vehicle_filter_pk.isdigit():
            queryset = queryset.filter(vehicle__pk=int(vehicle_filter_pk))
        
        # Status filter
        status_filter = get_params.get('status', '').strip()
        if status_filter:
            # Validate status is in choices
            valid_statuses = [choice[0] for choice in Trip.STATUS_CHOICES]
            if status_filter in valid_statuses:
                queryset = queryset.filter(status=status_filter)
        
        # Date filters
        start_date_str = get_params.get('start_date', '').strip()
        if start_date_str:
            try:
                start_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d').date()
                queryset = queryset.filter(loading_date__gte=start_date)
            except ValueError:
                logger.warning(f"Invalid start date format for trips: {start_date_str}")
        
        end_date_str = get_params.get('end_date', '').strip()
        if end_date_str:
            try:
                end_date = datetime.datetime.strptime(end_date_str, '%Y-%m-%d').date()
                queryset = queryset.filter(loading_date__lte=end_date)
            except ValueError:
                logger.warning(f"Invalid end date format for trips: {end_date_str}")
        
    except Exception as e:
        logger.error(f"Error applying trip filters: {e}")
    
    return queryset