# shipments/views.py
import datetime
import logging
import os
import re
import tempfile
from calendar import monthrange
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from django.utils.timesince import timesince # Add this import

import pdfplumber
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login as auth_login
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Max, Q, Sum, Value
from django.http import Http404, HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods, require_POST

from .forms import LoadingCompartmentFormSet, PdfLoadingAuthorityUploadForm, ShipmentForm, TripForm
from .models import (
    Customer, Destination, LoadingCompartment, Product, 
    Shipment, ShipmentDepletion, Trip, Vehicle
)

# Initialize logger
logger = logging.getLogger('shipments')
User = get_user_model()

# --- Constants ---
TRUCK_CAPACITIES = {
    'PMS': Decimal('40000'),
    'AGO': Decimal('36000'),
}

MAX_PDF_SIZE_MB = 10
ALLOWED_PDF_EXTENSIONS = ['.pdf']
# Adjusted: PENDING removed as per requirement
COMMITTED_TRIP_STATUSES = ['KPC_APPROVED', 'LOADING']
CHART_TRIP_STATUSES = ['LOADED', 'GATEPASSED', 'TRANSIT', 'DELIVERED']

# Date ranges for validation
MIN_DATE = datetime.date(2000, 1, 1)
MAX_DATE = datetime.date.today() + datetime.timedelta(days=365)
# --- Helper Functions ---
def is_viewer_or_admin_or_superuser(user):
    """Check if user has viewer, admin, or superuser privileges."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=['Admin', 'Viewer']).exists()


def is_admin_or_superuser(user):
    """Check if user has admin or superuser privileges."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name='Admin').exists()


def get_user_accessible_shipments(user):
    """Get shipments accessible to the user based on permissions."""
    base_qs = Shipment.objects.select_related('product', 'destination', 'user')
    return base_qs if is_viewer_or_admin_or_superuser(user) else base_qs.filter(user=user)


def get_user_accessible_trips(user):
    """Get trips accessible to the user based on permissions."""
    base_qs = Trip.objects.select_related(
        'product', 'destination', 'vehicle', 'customer', 'user'
    ).prefetch_related(
        'requested_compartments', 
        'depletions_for_trip'
    )
    return base_qs if is_viewer_or_admin_or_superuser(user) else base_qs.filter(user=user)


def validate_file_upload(uploaded_file, max_size_mb=None, allowed_extensions=None):
    """Validate uploaded file for security and size constraints."""
    if max_size_mb is None:
        max_size_mb = getattr(settings, 'FUEL_TRACKER_SETTINGS', {}).get('MAX_FILE_UPLOAD_SIZE_MB', MAX_PDF_SIZE_MB)
    
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


class DefaultCommandOutput:
    """Default output handler for model operations that expect stdout."""
    
    def write(self, msg, style_func=None): 
        logger.info(msg)
    
    class Style: 
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
    
    style = Style()
REASONABLE_YEAR_RANGE = (1900, 2200)
# --- PDF Parsing Functions ---
def parse_pdf_fields(text_content):
    """Parse PDF fields with enhanced error handling and validation."""
    extracted = {}
    
    if not text_content or not text_content.strip():
        logger.warning("Empty text content provided for PDF parsing")
        return extracted
        
    logger.info("Starting PDF field parsing for loading authority...")
    
    # Clean text but preserve structure
    text_content = re.sub(r'\s+', ' ', text_content.strip())
    logger.debug(f"PDF content to parse: {text_content[:500]}...")
    
    try:
        # Parse ORDER NUMBER
        order_patterns = [
            r"ORDER\s+NUMBER\s*[:\-]?\s*(S\d+)",
            r"ORDER\s*[:\-]?\s*(S\d+)",
            r"ORDER\s+NO\s*[:\-]?\s*(S\d+)",
            r"\bORDER.*?(S\d{4,6})\b",
            r"\b(S\d{5})\b",
            r"S(\d{5})",
        ]
        
        for i, pattern in enumerate(order_patterns):
            matches = re.findall(pattern, text_content, re.IGNORECASE)
            if matches:
                order_num = matches[0] if isinstance(matches[0], str) else matches[0]
                if not order_num.upper().startswith('S'):
                    order_num = 'S' + order_num
                extracted['order_number'] = order_num.upper()
                logger.info(f"Found order number: {extracted['order_number']}")
                break
        
        # Fallback for order number
        if 'order_number' not in extracted:
            all_s_numbers = re.findall(r'S\d+', text_content, re.IGNORECASE)
            if all_s_numbers:
                extracted['order_number'] = all_s_numbers[0].upper()
                logger.info(f"Using fallback order number: {extracted['order_number']}")

        # Parse DATE
        date_patterns = [
            r"DATE\s*[:\-]?\s*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{4})",
            r"Delivery Date\s*[:\-]?\s*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{4})", # More specific for BoL
            r"(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{4})",
        ]
        
        # Define the order of date formats to try
        # You mentioned wanting MM/DD/YYYY for loading authorities.
        # BoL shows DD.MM.YYYY. Let's make the list comprehensive.
        date_formats_to_try = [
            '%m/%d/%Y',  # MM/DD/YYYY (e.g., 06/11/2025 for June 11) - Your preferred for LA
            '%d.%m.%Y',  # DD.MM.YYYY (e.g., 11.06.2025 for June 11) - Seen on BoL
            '%d/%m/%Y',  # DD/MM/YYYY (e.g., 11/06/2025 for June 11)
            '%m.%d.%Y',  # MM.DD.YYYY
            '%m-%d-%Y',  # MM-DD-YYYY
            '%d-%m-%Y',  # DD-MM-YYYY
            '%Y-%m-%d',  # YYYY-MM-DD (ISO)
        ]
        
        extracted_date_str = None # To store the string that successfully matched a pattern

        for pattern in date_patterns:
            match = re.search(pattern, text_content, re.IGNORECASE)
            if match:
                # A pattern might have multiple groups, we usually want the one with the date.
                # If the pattern is just (\d{1,2}...), group(1) will be the date.
                # If it's "DATE\s*:\s*(\d{1,2}...), group(1) is also the date.
                # Iterate through groups if necessary, or make regex more specific.
                # For simplicity, assuming the first relevant group contains the date string.
                if len(match.groups()) > 0:
                    extracted_date_str = match.group(1) # Get the captured date string
                    logger.info(f"Date pattern '{pattern}' matched. Extracted date string: '{extracted_date_str}'")
                    break # Found a date string with a pattern
        
        if extracted_date_str:
            parsed_successfully = False
            for fmt in date_formats_to_try:
                try:
                    parsed_date_obj = datetime.datetime.strptime(extracted_date_str, fmt).date()
                    
                    # MIN_DATE and MAX_DATE validation
                    # MAX_DATE = datetime.date.today() + datetime.timedelta(days=365*2) # Allow up to 2 years from today for MAX_DATE
                    # MIN_DATE = datetime.date(2000, 1, 1)
                    if MIN_DATE <= parsed_date_obj <= MAX_DATE: # Ensure MIN_DATE and MAX_DATE are defined
                        extracted['loading_date'] = parsed_date_obj
                        logger.info(f"Successfully parsed date: {extracted['loading_date']} using format {fmt} from string '{extracted_date_str}'")
                        parsed_successfully = True
                        break # Break from inner loop (formats)
                    else:
                        logger.warning(f"Date '{extracted_date_str}' with format {fmt} parsed to {parsed_date_obj}, but it's outside MIN/MAX range ({MIN_DATE} to {MAX_DATE}).")
                except ValueError:
                    continue 
            if not parsed_successfully:
                 logger.error(f"Could not parse the extracted date string '{extracted_date_str}' with any of the known formats.")

        if 'loading_date' not in extracted:
            logger.warning(f"Could not parse a valid date from PDF content. Text snippet for date search: {text_content[:300]}")

        # Parse PRODUCT
        product_mapping = {
            'PMS': 'PMS',
            'AGO': 'AGO',
            'DIESEL': 'AGO'
        }
        
        for keyword, product_name in product_mapping.items():
            if keyword in text_content.upper():
                extracted['product_name'] = product_name
                logger.info(f"Found product: {product_name} (from {keyword})")
                break

        # Parse CUSTOMER/CLIENT
        client_patterns = [
            r"CLIENT\s*[:\-]?\s*([A-Z][A-Z\s\-&\.]+?)(?=\s+TRANSPORTER)",
            r"CLIENT\s*[:\-]?\s*([A-Z][A-Z\s\-&\.]+?)(?=\s+ORDER)",
            r"CLIENT\s*[:\-]?\s*([A-Z][A-Z\s\-&\.]+?)(?=\s+DEPOT)",
            r"CLIENT\s*[:\-]?\s*([A-Z][A-Z\s\-&\.]+?)(?=\s*$)",
            r"CLIENT\s*[:\-]?\s*([A-Z\s\-&\.]{3,40})",
            r"CUSTOMER\s*[:\-]?\s*([A-Z][A-Z\s\-&\.]+?)(?=\s+TRANSPORTER)",
            r"CUSTOMER\s*[:\-]?\s*([A-Z\s\-&\.]{3,40})",
        ]
        
        for pattern in client_patterns:
            matches = re.findall(pattern, text_content, re.IGNORECASE)
            if matches:
                client = matches[0].strip()
                client = re.sub(r'\s+', ' ', client)
                if len(client) >= 3:
                    extracted['customer_name'] = client.upper()
                    logger.info(f"Found customer: {extracted['customer_name']}")
                    break
        
        # Fallback for customer
        if 'customer_name' not in extracted:
            company_patterns = ['BRIDGE', 'PETROLEUM', 'ENERGY', 'OIL', 'GAS']
            for company in company_patterns:
                if company in text_content.upper():
                    company_match = re.search(rf'{company}[A-Z\s]*', text_content, re.IGNORECASE)
                    if company_match:
                        extracted['customer_name'] = company_match.group(0).strip().upper()
                        logger.info(f"Found company pattern: {extracted['customer_name']}")
                        break

        # Parse QUANTITY AND COMPARTMENTS
        _parse_quantity_and_compartments(text_content, extracted)

        # Parse DESTINATION
        dest_patterns = [
            r"DESTINATION\s*[:\-]?\s*(SOUTH\s+SUDAN)",
            r"DESTINATION\s*[:\-]?\s*(DRC\s+CONGO)",
            r"DESTINATION\s*[:\-]?\s*([A-Z][A-Z\s\-]+?)(?=\s+ID\s+NO)",
            r"DESTINATION\s*[:\-]?\s*([A-Z\s\-]{3,25})(?=\s+ID|\s+TRUCK)",
        ]
        
        for pattern in dest_patterns:
            match = re.search(pattern, text_content, re.IGNORECASE)
            if match:
                dest = match.group(1).strip()
                dest = re.sub(r'\s+', ' ', dest)
                if len(dest) >= 3:
                    extracted['destination_name'] = dest.upper()
                    logger.info(f"Found destination: '{extracted['destination_name']}'")
                    break

        # Parse TRUCK
        truck_patterns = [
            r"TRUCK\s*[:\-]?\s*([A-Z0-9]+)",
            r"TRUCK\s*[:\-]?\s*([A-Z]{3}\d{3}[A-Z]?)",
            r"VEHICLE\s*[:\-]?\s*([A-Z0-9]+)",
        ]
        
        for pattern in truck_patterns:
            match = re.search(pattern, text_content, re.IGNORECASE)
            if match:
                truck = match.group(1).strip().upper()
                if len(truck) >= 4:
                    extracted['truck_plate'] = truck
                    logger.info(f"Found truck: {extracted['truck_plate']}")
                    break

    except Exception as e:
        logger.error(f"Error in PDF parsing: {e}", exc_info=True)

    logger.info(f"Final extracted data: {extracted}")
    return extracted


def _parse_quantity_and_compartments(text_content, extracted):
    """Helper function to parse quantity and compartment data."""
    quantity_patterns = [
        r"(?:PMS|AGO|DIESEL)\s+([\d\.]+)\s*m³",
        r"QUANTITY\s+([\d\.]+)\s*m³",
        r"TOTAL\s+([\d\.]+)\s*m³",
        r"([\d\.]+)\s*m³"
    ]
    
    quantity_found = False
    for pattern in quantity_patterns:
        match = re.search(pattern, text_content, re.IGNORECASE)
        if match:
            try:
                qty_m3 = Decimal(match.group(1))
                if Decimal('1') <= qty_m3 <= Decimal('100'):
                    extracted['total_quantity_litres'] = qty_m3 * Decimal('1000')
                    logger.info(f"Found quantity: {qty_m3} m³ = {extracted['total_quantity_litres']} litres")
                    quantity_found = True
                    break
            except (ValueError, InvalidOperation):
                continue
    
    # Look for compartment breakdown
    compartment_match = re.search(r"COMPARTMENT.*?(\d{1,2}:\d{1,2}:\d{1,2})", text_content, re.IGNORECASE)
    if compartment_match:
        compartment_str = compartment_match.group(1)
        logger.info(f"Found compartment string: {compartment_str}")
        
        try:
            compartment_parts = compartment_str.split(':')
            compartment_volumes = []
            
            for part in compartment_parts:
                if part.strip().isdigit():
                    vol = Decimal(part.strip())
                    if Decimal('1') <= vol <= Decimal('50'):
                        compartment_volumes.append(vol * Decimal('1000'))
            
            if compartment_volumes:
                extracted['compartment_quantities_litres'] = compartment_volumes
                compartment_total = sum(compartment_volumes)
                logger.info(f"Found compartments: {compartment_parts} m³ = {compartment_volumes} litres")
                
                if not quantity_found:
                    extracted['total_quantity_litres'] = compartment_total
                    logger.info(f"Using compartment total: {compartment_total} litres")
            
        except (ValueError, InvalidOperation) as e:
            logger.warning(f"Error parsing compartments '{compartment_str}': {e}")


def parse_loading_authority_pdf(pdf_file_obj, request_for_messages=None):
    """Parse loading authority PDF with enhanced validation and error handling."""
    try:
        validate_file_upload(pdf_file_obj, allowed_extensions=ALLOWED_PDF_EXTENSIONS)
        
        extracted_data = {
            'compartment_quantities_litres': [],
            'total_quantity_litres': Decimal('0.00')
        }
        
        full_text = _extract_pdf_text(pdf_file_obj)
        if not full_text:
            if request_for_messages:
                messages.error(request_for_messages, "No text could be extracted from the PDF.")
            logger.warning("No text could be extracted from the PDF.")
            return None
        
        # Parse fields
        parsing_results = parse_pdf_fields(full_text)
        extracted_data.update(parsing_results)
        
        # Validate and set defaults
        if not _validate_and_set_defaults(extracted_data, request_for_messages):
            return None
        
        logger.info(f"Final validated data: {extracted_data}")
        return extracted_data
        
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


def _extract_pdf_text(pdf_file_obj):
    """Extract text from PDF file."""
    full_text = ""
    temp_file_path = None
    
    try:
        # Create temporary file for PDF processing
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as temp_file:
            for chunk in pdf_file_obj.chunks():
                temp_file.write(chunk)
            temp_file_path = temp_file.name
        
        # Process PDF with pdfplumber
        with pdfplumber.open(temp_file_path) as pdf:
            if not pdf.pages:
                return None
            
            for page_num, page in enumerate(pdf.pages):
                try:
                    page_text = page.extract_text_simple(x_tolerance=1.5, y_tolerance=1.5)
                    if not page_text:
                        page_text = page.extract_text()
                    if page_text:
                        full_text += page_text + "\n"
                except Exception as e:
                    logger.warning(f"Error extracting text from page {page_num + 1}: {e}")
                    continue
        
        return full_text.strip() if full_text.strip() else None
        
    finally:
        # Clean up temporary file
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
            except OSError as e:
                logger.warning(f"Could not delete temporary file {temp_file_path}: {e}")


def _validate_and_set_defaults(extracted_data, request_for_messages):
    """Validate critical fields and set defaults for optional fields."""
    # Validate critical fields
    critical_fields = ['order_number', 'product_name', 'total_quantity_litres']
    missing_critical = []
    
    for field in critical_fields:
        if field == 'total_quantity_litres':
            if extracted_data.get(field, Decimal('0.00')) <= Decimal('0.00'):
                missing_critical.append(field)
        else:
            if not extracted_data.get(field):
                missing_critical.append(field)
    
    if missing_critical:
        error_msg = f"Critical data missing from PDF: {', '.join(missing_critical)}. Trip not created."
        if request_for_messages:
            messages.error(request_for_messages, error_msg)
        logger.error(f"Parsing failed - missing critical fields: {missing_critical}")
        return False
    
    # Set defaults for missing optional fields
    missing_optional = []
    defaults = {
        'loading_date': timezone.now().date(),
        'destination_name': "Not specified",
        'truck_plate': "Not specified",
        'customer_name': "Not specified"
    }
    
    for field, default_value in defaults.items():
        if not extracted_data.get(field):
            extracted_data[field] = default_value
            missing_optional.append(field)
    
    if missing_optional and request_for_messages:
        messages.warning(request_for_messages, f"Used default values for: {', '.join(missing_optional)}")
    return True

def create_trip_from_parsed_data(parsed_data, request, filename):
    """Create trip and related objects from parsed PDF data with improved error handling."""
    try:
        # Validate required fields
        required_fields = ['product_name', 'customer_name', 'truck_plate', 'destination_name', 'order_number']
        for field in required_fields:
            if not parsed_data.get(field):
                raise ValidationError(f"Missing required field: {field}")
        
        # Get or create related objects
        product = _get_or_create_product(parsed_data['product_name'])
        customer = _get_or_create_customer(parsed_data['customer_name'])
        vehicle = _get_or_create_vehicle(parsed_data['truck_plate'], parsed_data.get('trailer_number'))
        destination = _get_or_create_destination(parsed_data['destination_name'])
        
        # Check for existing trip
        kpc_order_no = parsed_data['order_number']
        logger.info(f"Checking for existing trip with order number: '{kpc_order_no}'")
        
        try:
            existing_trip = Trip.objects.get(kpc_order_number__iexact=kpc_order_no)
            messages.warning(
                request, 
                f"Trip with KPC Order No '{kpc_order_no}' already exists (ID: {existing_trip.id})."
            )
            return existing_trip
        except Trip.DoesNotExist:
            pass  # Expected for new trips
        
        # Create new trip
        trip_instance = Trip.objects.create(
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
        
        logger.info(f"Successfully created trip with ID: {trip_instance.id}")
        
        # Create compartments
        _create_trip_compartments(trip_instance, parsed_data)
        
        logger.info(f"Successfully created trip {trip_instance.id} from PDF {filename}")
        return trip_instance
        
    except ValidationError:
        raise  # Re-raise validation errors
    except Exception as e:
        logger.error(f"Unexpected error creating trip from parsed data: {e}", exc_info=True)
        raise ValidationError(f"Unexpected error creating trip: {e}")


def _get_or_create_product(product_name):
    """Get or create product with error handling."""
    try:
        product, created = Product.objects.get_or_create(
            name__iexact=product_name,
            defaults={'name': product_name.upper()}
        )
        if created:
            logger.info(f"Created new product: {product.name}")
        return product
    except Exception as e:
        logger.error(f"Error creating/getting product: {e}")
        raise ValidationError(f"Error with product '{product_name}': {e}")


def _get_or_create_customer(customer_name):
    """Get or create customer with error handling."""
    try:
        if isinstance(customer_name, str):
            customer_name_cleaned = customer_name.strip().upper()
            if not customer_name_cleaned:
                customer_name_cleaned = "UNKNOWN CUSTOMER"
        else:
            customer_name_cleaned = "UNKNOWN CUSTOMER"
            logger.warning(f"Customer name was not a string: {customer_name}. Using '{customer_name_cleaned}'.")

        customer, created = Customer.objects.get_or_create(
            name__iexact=customer_name_cleaned,
            defaults={'name': customer_name_cleaned}
        )
        if created:
            logger.info(f"Created new customer: {customer.name}")
        return customer
    except Exception as e:
        logger.error(f"Error creating/getting customer for '{customer_name_cleaned}': {e}", exc_info=True)
        raise ValidationError(f"Error with customer '{customer_name_cleaned}': {e}")


def _get_or_create_vehicle(truck_plate, trailer_number=None):
    """Get or create vehicle with error handling."""
    try:
        if isinstance(truck_plate, str):
            truck_plate_cleaned = truck_plate.strip().upper().replace(" ", "")
            if not truck_plate_cleaned :
                 truck_plate_cleaned = "UNKNOWNVEHICLE"
        else:
            truck_plate_cleaned = "UNKNOWNVEHICLE"
            logger.warning(f"Truck plate was not a string: {truck_plate}. Using '{truck_plate_cleaned}'.")


        vehicle, created = Vehicle.objects.get_or_create(
            plate_number__iexact=truck_plate_cleaned,
            defaults={'plate_number': truck_plate_cleaned}
        )
        if created:
            logger.info(f"Created new vehicle: {vehicle.plate_number}")
        
        if trailer_number and isinstance(trailer_number, str):
            trailer_number_cleaned = trailer_number.strip().upper()
            if trailer_number_cleaned and vehicle.trailer_number != trailer_number_cleaned:
                vehicle.trailer_number = trailer_number_cleaned
                vehicle.save(update_fields=['trailer_number'])
            
        return vehicle
    except Exception as e:
        logger.error(f"Error creating/getting vehicle for '{truck_plate_cleaned}': {e}", exc_info=True)
        raise ValidationError(f"Error with vehicle '{truck_plate_cleaned}': {e}")


def _get_or_create_destination(destination_name):
    """Get or create destination with improved matching and robustness."""
    if not isinstance(destination_name, str):
        logger.warning(f"Destination name received was not a string: {destination_name}. Using 'Not Specified'.")
        destination_name = "Not Specified"
    else:
        destination_name = destination_name.strip()
        if not destination_name:
            logger.warning("Empty destination name received. Using 'Not Specified'.")
            destination_name = "Not Specified"

    try:
        destination = Destination.objects.get(name__iexact=destination_name)
        logger.info(f"Found exact destination match for '{destination_name}': {destination.name}")
        return destination
    except Destination.DoesNotExist:
        pass
    except Destination.MultipleObjectsReturned:
        logger.error(f"Multiple destinations found for name__iexact='{destination_name}'. Returning the first one.")
        return Destination.objects.filter(name__iexact=destination_name).first()

    partial_matches = {
        'SOUTH': 'SOUTH SUDAN', 'CONGO': 'CONGO', 'DRC': 'CONGO'
    }
    upper_destination_name = destination_name.upper()

    for keyword, canonical_name in partial_matches.items():
        if keyword in upper_destination_name:
            try:
                destination = Destination.objects.get(name__iexact=canonical_name)
                logger.info(f"Found partial match: input '{destination_name}' mapped to canonical '{canonical_name}' ({destination.name})")
                return destination
            except Destination.DoesNotExist:
                logger.info(f"Keyword '{keyword}' in '{destination_name}', but canonical '{canonical_name}' not found. Will create original.")
            except Destination.MultipleObjectsReturned:
                logger.warning(f"Multiple canonical destinations for '{canonical_name}' (from '{destination_name}'). Skipping rule.")

    try:
        destination_to_create = destination_name.upper()
        destination, created = Destination.objects.get_or_create(
            name__iexact=destination_to_create,
            defaults={'name': destination_to_create}
        )
        if created:
            logger.info(f"Created new destination: {destination.name} (from input: '{destination_name}')")
        else:
            logger.info(f"Found existing destination via get_or_create: {destination.name} (from input: '{destination_name}')")
        return destination
    except Exception as e:
        logger.error(f"Error creating/getting destination for input '{destination_name}': {e}", exc_info=True)
        raise ValidationError(f"Error processing destination '{destination_name}': {e}")


def _create_trip_compartments(trip_instance, parsed_data):
    """Create compartments for the trip."""
    try:
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
                    logger.debug(f"Created compartment {i+1} with {qty}L for trip {trip_instance.id}")
        elif total_qty > 0:
            LoadingCompartment.objects.create(
                trip=trip_instance,
                compartment_number=1,
                quantity_requested_litres=total_qty
            )
            logger.debug(f"Created single compartment with {total_qty}L for trip {trip_instance.id}")
        else:
            logger.warning(f"No compartment quantities or positive total quantity for trip {trip_instance.id}. No compartments created.")
            
    except Exception as e:
        logger.error(f"Error creating compartments for trip {trip_instance.id}: {e}", exc_info=True)
        logger.warning(f"Trip {trip_instance.id} created but compartment setup failed: {e}")
    return True
# --- Filter Helper Functions ---
# --- Helper Functions --- (Add get_recent_activity here or group with other helpers)

SVG_PATHS = {
    'shipment_received': '<path d="M3 4a1 1 0 011-1h12a1 1 0 011 1v2a1 1 0 01-1 1H4a1 1 0 01-1-1V4z"/><path d="M3 10a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H4a1 1 0 01-1-1v-6z"/>',
    'trip_delivered': '<path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"/>',
    'trip_pending_approval': '<path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/>',
    'trip_generic_update': '<path d="M8 16.5a1.5 1.5 0 11-3 0 1.5 1.5 0 013 0zM15 16.5a1.5 1.5 0 11-3 0 1.5 1.5 0 013 0z"/><path d="M3 4a1 1 0 00-1 1v10a1 1 0 001 1h1.05a2.5 2.5 0 014.9 0H10a1 1 0 001-1V5a1 1 0 00-1-1H3z"/>', # Truck icon
    'trip_loaded': '<path d="M3 10a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H4a1 1 0 01-1-1v-6z"/>', # Box icon for loaded, similar to shipment
}

TRIP_STATUS_STYLES = {
    'DELIVERED': {'icon_bg': 'bg-green-100', 'icon_text': 'text-green-600', 'svg_key': 'trip_delivered'},
    'LOADED': {'icon_bg': 'bg-purple-100', 'icon_text': 'text-purple-600', 'svg_key': 'trip_loaded'},
    'GATEPASSED': {'icon_bg': 'bg-blue-100', 'icon_text': 'text-blue-600', 'svg_key': 'trip_generic_update'},
    'TRANSIT': {'icon_bg': 'bg-blue-100', 'icon_text': 'text-blue-600', 'svg_key': 'trip_generic_update'},
    'KPC_APPROVED': {'icon_bg': 'bg-yellow-100', 'icon_text': 'text-yellow-600', 'svg_key': 'trip_pending_approval'},
    'LOADING': {'icon_bg': 'bg-yellow-100', 'icon_text': 'text-yellow-600', 'svg_key': 'trip_pending_approval'},
    'PENDING': {'icon_bg': 'bg-yellow-100', 'icon_text': 'text-yellow-600', 'svg_key': 'trip_pending_approval'},
    'KPC_REJECTED': {'icon_bg': 'bg-red-100', 'icon_text': 'text-red-600', 'svg_key': 'trip_pending_approval'}, # Using warning icon for rejected too
    'CANCELLED': {'icon_bg': 'bg-red-100', 'icon_text': 'text-red-600', 'svg_key': 'trip_pending_approval'}, # And for cancelled
    'DEFAULT': {'icon_bg': 'bg-gray-100', 'icon_text': 'text-gray-600', 'svg_key': 'trip_generic_update'}
}

logger = logging.getLogger(__name__)

def get_recent_activity(user, limit=3):
    logger.info(f"--- get_recent_activity called for user: {user.username} ---") # <<<<<<< DEBUG LOG
    all_activity = []
    now = timezone.now()
    fetch_limit = limit * 5 # Fetch more initially to ensure we get enough distinct activities

    # 1. Fetch recent Shipment creations
    if user.has_perm('shipments.view_shipment'):
        # Assuming get_user_accessible_shipments is defined and works
        recent_shipments_qs = get_user_accessible_shipments(user).select_related('product', 'user').order_by('-created_at')[:fetch_limit]
        logger.info(f"Found {recent_shipments_qs.count()} recent shipments for user {user.username}.") # <<<<<<< DEBUG LOG
        for shipment in recent_shipments_qs:
            logger.info(f"Processing shipment ID: {shipment.id}, Created: {shipment.created_at}, Vessel: {shipment.vessel_id_tag}") # <<<<<<< DEBUG LOG
            all_activity.append({
                'type': 'new_shipment',
                'timestamp': shipment.created_at,
                'title': f"New shipment {shipment.vessel_id_tag or f'ID {shipment.id}'} received",
                'details': f"{shipment.quantity_litres:,.0f}L {shipment.product.name} from {shipment.supplier_name}",
                'time_ago': timesince(shipment.created_at, now).split(',')[0] + " ago",
                'icon_bg_color': 'bg-blue-100',
                'icon_text_color': 'text-blue-600',
                'icon_svg_path': SVG_PATHS['shipment_received'],
                'link': reverse('shipments:shipment-detail', args=[shipment.pk])
            })

    # 2. Fetch recent Trip history (creations and status changes)
    if user.has_perm('shipments.view_trip'):
        HistoricalTrip = Trip.history.model 
        logger.info("Attempting to fetch historical trips.") # <<<<<<< DEBUG LOG
        
        historical_trips_base_qs = HistoricalTrip.objects
        if not is_viewer_or_admin_or_superuser(user):
            user_trip_ids = Trip.objects.filter(user=user).values_list('id', flat=True)
            logger.info(f"Non-admin user {user.username}, filtering trip history for trip_ids: {list(user_trip_ids)}") # <<<<<<< DEBUG LOG
            historical_trips_base_qs = historical_trips_base_qs.filter(instance_id__in=user_trip_ids)


        recent_trip_history_qs = historical_trips_base_qs.select_related(
            'instance__vehicle', 
            'instance__customer', 
            'instance__product',
            'instance__user' 
        ).order_by('-history_date')[:fetch_limit * 2] 
        
        logger.info(f"Found {recent_trip_history_qs.count()} historical trip records after initial fetch for user {user.username}.") # <<<<<<< DEBUG LOG

        processed_trip_creations = set() 

        for history_record in recent_trip_history_qs:
            logger.info(f"Processing history_record ID: {history_record.history_id}, Type: {history_record.history_type}, Date: {history_record.history_date}, Instance PK: {history_record.instance_id}") # <<<<<<< DEBUG LOG
            trip_instance = history_record.instance
            if not trip_instance: 
                logger.warning(f"Skipping history_record {history_record.history_id} because instance is None.") # <<<<<<< DEBUG LOG
                continue

            if not (is_viewer_or_admin_or_superuser(user) or trip_instance.user == user):
                logger.info(f"Skipping history for trip {trip_instance.pk} (owner: {trip_instance.user.username if trip_instance.user else 'N/A'}) due to permission for user {user.username}") # <<<<<<< DEBUG LOG
                continue

            activity_item = None
            style_info = TRIP_STATUS_STYLES.get(history_record.status, TRIP_STATUS_STYLES['DEFAULT'])

            if history_record.history_type == '+': 
                if trip_instance.pk not in processed_trip_creations:
                    title = f"Loading {history_record.status.lower()} initiated" 
                    if history_record.status == 'PENDING' or history_record.status == 'KPC_APPROVED' or history_record.status == 'LOADING':
                        title = f"Loading approval {history_record.get_status_display().lower()}"                    
                    details = f"Trip {trip_instance.kpc_order_number or f'ID {trip_instance.pk}'} ({trip_instance.product.name})"
                    if history_record.status == 'PENDING' or history_record.status == 'KPC_APPROVED':
                         details += " waiting for KPC authorization"
                    activity_item = {
                        'type': 'trip_created',
                        'title': title,
                        'details': details,
                        'icon_svg_path': SVG_PATHS[style_info['svg_key']],
                    }
                    processed_trip_creations.add(trip_instance.pk)
            elif history_record.history_type == '~': 
                prev_history_record = history_record.prev_record
                if prev_history_record and history_record.status != prev_history_record.status:
                    title = f"Trip {trip_instance.kpc_order_number or f'ID {trip_instance.pk}'} status changed"
                    details = f"From '{prev_history_record.get_status_display()}' to '{history_record.get_status_display()}'"
                    if history_record.status == 'DELIVERED':
                        title = f"Shipment {trip_instance.bol_number or trip_instance.kpc_order_number or f'ID {trip_instance.pk}'} delivered"
                        details = f"{trip_instance.total_loaded:,.0f}L to {trip_instance.customer.name}"
                    activity_item = {
                        'type': 'trip_status_change',
                        'title': title,
                        'details': details,
                        'icon_svg_path': SVG_PATHS[style_info['svg_key']],
                    }
            
            if activity_item:
                activity_item.update({
                    'timestamp': history_record.history_date,
                    'time_ago': timesince(history_record.history_date, now).split(',')[0] + " ago",
                    'icon_bg_color': style_info['icon_bg'],
                    'icon_text_color': style_info['icon_text'],
                    'link': reverse('shipments:trip-detail', args=[trip_instance.pk])
                })
                all_activity.append(activity_item)
                logger.info(f"Added activity: {activity_item['title']}") # <<<<<<< DEBUG LOG
            else:
                logger.info(f"No qualifying activity_item created for history_record {history_record.history_id} (type {history_record.history_type})")


    logger.info(f"Total activities collected before sort: {len(all_activity)}") # <<<<<<< DEBUG LOG
    all_activity.sort(key=lambda x: x['timestamp'], reverse=True)
    
    final_activities = []
    seen_activities_keys = set()
    for act in all_activity:
        activity_key = (act['title'], act.get('link', 'no_link')) # Ensure link is handled if missing
        if activity_key not in seen_activities_keys:
            final_activities.append(act)
            seen_activities_keys.add(activity_key)
            if len(final_activities) >= limit:
                break
                
    logger.info(f"Final activities to be returned (limit {limit}): {len(final_activities)}") # <<<<<<< DEBUG LOG
    if final_activities:
        for fa_idx, fa_item in enumerate(final_activities):
            logger.info(f"Final item {fa_idx + 1}: {fa_item['title']}")

    return final_activities[:limit]

# ... (other view functions like home_view)

@login_required
def home_view(request):
    """Main dashboard view with stock summary and notifications."""
    try:
        context = {
            'message': 'Welcome to Sakina Gas Fuel Tracker', # Changed message to match screenshot
            'description': 'Manage your fuel inventory efficiently.',
            'is_authenticated_user': request.user.is_authenticated
        }

        permissions = {
            'can_view_shipments': request.user.has_perm('shipments.view_shipment'),
            'can_add_shipment': request.user.has_perm('shipments.add_shipment'),
            'can_view_trip': request.user.has_perm('shipments.view_trip'),
            'can_view_product': request.user.has_perm('shipments.view_product'), 
            'can_view_customer': request.user.has_perm('shipments.view_customer'), 
            'can_view_vehicle': request.user.has_perm('shipments.view_vehicle'),
            'can_add_trip': request.user.has_perm('shipments.add_trip')
        }
        context.update(permissions)

        shipments_qs = get_user_accessible_shipments(request.user)
        trips_qs = get_user_accessible_trips(request.user)

        context.update(_calculate_dashboard_stats(shipments_qs, trips_qs, request.user, permissions))

        cache_key = f"dashboard_stock_summary_{request.user.id}"
        stock_summary = cache.get(cache_key)
        
        if stock_summary is None:
            try:
                stock_summary = calculate_product_stock_summary(shipments_qs, trips_qs, request.user)
                cache.set(cache_key, stock_summary, 300)
            except Exception as e:
                logger.error(f"Error calculating stock summary: {e}", exc_info=True)
                stock_summary = {}
        
        context['stock_by_product_detailed'] = stock_summary

        if permissions['can_view_trip']:
            context.update(calculate_chart_data(trips_qs))

        if permissions['can_view_shipments'] or permissions['can_view_trip']: # Ensure notifications are calculated if either perm exists
            context.update(calculate_notifications(shipments_qs, request.user)) # Aging stock depends on shipments
            context['recent_activity'] = get_recent_activity(request.user) # Add recent activity

        if permissions['can_view_trip']:
            context.update(_calculate_trip_quantities_by_product(trips_qs))

        if not (permissions['can_view_shipments'] or permissions['can_view_trip']):
            context['description'] = 'You do not have permission to view fuel data. Contact admin for access.'

        return render(request, 'shipments/home.html', context)
        
    except Exception as e:
        logger.exception(f"Unexpected error in home_view: {e}")
        messages.error(request, "An error occurred while loading the dashboard. Please try again.")
        return render(request, 'shipments/home.html', {
            'message': 'Welcome to Sakina Gas Fuel Tracker',
            'description': 'An error occurred while loading dashboard data.',
            'is_authenticated_user': request.user.is_authenticated,
            'stock_by_product_detailed': {}, 
            'loadings_chart_labels': [], 'pms_loadings_data': [], 'ago_loadings_data': [],
            'aging_stock_notifications': [], 'inactive_product_notifications': [], 'utilized_shipment_notifications': [],
            'recent_activity': [], # Add empty list on error
            'trip_quantity_by_product': []
        })
    
def apply_shipment_filters(queryset, get_params):
    """Apply filters to shipment queryset with enhanced validation."""
    try:
        product_filter = get_params.get('product', '').strip()
        if product_filter and product_filter.isdigit():
            product_id = int(product_filter)
            if Product.objects.filter(pk=product_id).exists():
                queryset = queryset.filter(product__pk=product_id)
            else:
                logger.warning(f"Product with ID {product_id} does not exist")
        
        supplier_filter = get_params.get('supplier_name', '').strip()
        if supplier_filter:
            supplier_filter = supplier_filter[:100]
            queryset = queryset.filter(supplier_name__icontains=supplier_filter)
        
        start_date_str = get_params.get('start_date', '').strip()
        if start_date_str:
            try:
                start_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d').date()
                if MIN_DATE <= start_date <= MAX_DATE:
                    queryset = queryset.filter(import_date__gte=start_date)
                else:
                    logger.warning(f"Start date out of reasonable range: {start_date}")
            except ValueError:
                logger.warning(f"Invalid start date format: {start_date_str}")
        
        end_date_str = get_params.get('end_date', '').strip()
        if end_date_str:
            try:
                end_date = datetime.datetime.strptime(end_date_str, '%Y-%m-%d').date()
                if MIN_DATE <= end_date <= MAX_DATE:
                    queryset = queryset.filter(import_date__lte=end_date)
                else:
                    logger.warning(f"End date out of reasonable range: {end_date}")
            except ValueError:
                logger.warning(f"Invalid end date format: {end_date_str}")
        
    except Exception as e:
        logger.error(f"Error applying shipment filters: {e}")
    
    return queryset


def apply_trip_filters(queryset, get_params):
    """Apply filters to trip queryset with enhanced validation."""
    try:
        product_filter = get_params.get('product', '').strip()
        if product_filter and product_filter.isdigit():
            product_id = int(product_filter)
            if Product.objects.filter(pk=product_id).exists():
                queryset = queryset.filter(product__pk=product_id)
            else:
                logger.warning(f"Product with ID {product_id} does not exist")
        
        customer_filter = get_params.get('customer', '').strip()
        if customer_filter and customer_filter.isdigit():
            customer_id = int(customer_filter)
            if Customer.objects.filter(pk=customer_id).exists():
                queryset = queryset.filter(customer__pk=customer_id)
            else:
                logger.warning(f"Customer with ID {customer_id} does not exist")
        
        vehicle_filter = get_params.get('vehicle', '').strip()
        if vehicle_filter and vehicle_filter.isdigit():
            vehicle_id = int(vehicle_filter)
            if Vehicle.objects.filter(pk=vehicle_id).exists():
                queryset = queryset.filter(vehicle__pk=vehicle_id)
            else:
                logger.warning(f"Vehicle with ID {vehicle_id} does not exist")
        
        status_filter = get_params.get('status', '').strip()
        if status_filter:
            valid_statuses = [choice[0] for choice in Trip.STATUS_CHOICES]
            if status_filter in valid_statuses:
                queryset = queryset.filter(status=status_filter)
            else:
                logger.warning(f"Invalid status filter: {status_filter}")
        
        start_date_str = get_params.get('start_date', '').strip()
        if start_date_str:
            try:
                start_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d').date()
                if MIN_DATE <= start_date <= MAX_DATE:
                    queryset = queryset.filter(loading_date__gte=start_date)
                else:
                    logger.warning(f"Start date out of reasonable range: {start_date}")
            except ValueError:
                logger.warning(f"Invalid start date format for trips: {start_date_str}")
        
        end_date_str = get_params.get('end_date', '').strip()
        if end_date_str:
            try:
                end_date = datetime.datetime.strptime(end_date_str, '%Y-%m-%d').date()
                if MIN_DATE <= end_date <= MAX_DATE:
                    queryset = queryset.filter(loading_date__lte=end_date)
                else:
                    logger.warning(f"End date out of reasonable range: {end_date}")
            except ValueError:
                logger.warning(f"Invalid end date format for trips: {end_date_str}")
        
    except Exception as e:
        logger.error(f"Error applying trip filters: {e}")
    
    return queryset
# --- Dashboard Calculation Functions ---
def calculate_product_stock_summary(shipments_qs, trips_qs, user):
    """Calculate detailed stock summary by product and destination with better error handling."""
    stock_by_product_destination = {}
    show_global_data = is_viewer_or_admin_or_superuser(user)
    
    try:
        product_dest_combinations = _get_product_destination_combinations(shipments_qs, trips_qs, user, show_global_data)
        
        for product_obj, destination_obj in product_dest_combinations:
            try:
                stock_data = _calculate_stock_for_product_destination(
                    product_obj, destination_obj, shipments_qs, trips_qs, user, show_global_data
                )
                
                if _has_stock_activity(stock_data):
                    key = f"{product_obj.name} - {destination_obj.name}"
                    stock_by_product_destination[key] = stock_data
                    
            except Exception as e:
                logger.error(f"Error calculating stock for {product_obj.name} - {destination_obj.name}: {e}")
                continue
    
    except Exception as e:
        logger.error(f"Error in calculate_product_stock_summary: {e}")
        return {}
    
    return dict(sorted(stock_by_product_destination.items(), 
                      key=lambda x: (x[1]['product_name'], x[1]['destination_name'])))


def _get_product_destination_combinations(shipments_qs, trips_qs, user, show_global_data):
    """Get all product-destination combinations that have activity."""
    product_dest_combinations = set()
    
    for shipment in shipments_qs.select_related('product', 'destination'):
        if shipment.destination:
            product_dest_combinations.add((shipment.product, shipment.destination))
    
    trip_filter_qs = trips_qs
    for trip in trip_filter_qs.select_related('product', 'destination'):
        if trip.destination:
            product_dest_combinations.add((trip.product, trip.destination))
    
    return product_dest_combinations


def _calculate_stock_for_product_destination(product_obj, destination_obj, shipments_qs, trips_qs, user, show_global_data):
    """Calculate stock data for a specific product-destination combination."""
    # ... existing code for total_received and total_delivered ...
    
    specific_shipments_qs = shipments_qs.filter(
        product=product_obj, 
        destination=destination_obj
    )
    total_received = specific_shipments_qs.aggregate(total=Sum('quantity_litres'))['total'] or Decimal('0.00')
    
    delivered_depletions_qs = ShipmentDepletion.objects.filter(
        shipment_batch__product=product_obj,
        shipment_batch__destination=destination_obj,
        trip__status='DELIVERED'
    )
    if not show_global_data:
         delivered_depletions_qs = delivered_depletions_qs.filter(trip__user=user)

    total_delivered = delivered_depletions_qs.aggregate(
        total=Sum('quantity_depleted')
    )['total'] or Decimal('0.00')
    
    physical_stock = total_received - total_delivered
    
    # FIXED: Only include trips that are approved but not yet loaded
    # Trips in LOADED+ status have already been depleted and shouldn't be "committed"
    committed_trips_qs = trips_qs.filter(
        product=product_obj, 
        destination=destination_obj,
        status__in=['KPC_APPROVED', 'LOADING']  # Removed LOADED, GATEPASSED, TRANSIT
    )
    total_committed = _calculate_committed_stock(committed_trips_qs)
    
    net_available = physical_stock - total_committed
    
    # ... rest of the calculation remains the same ...
    total_cost = specific_shipments_qs.aggregate(
        total=Sum(F('quantity_litres') * F('price_per_litre'))
    )['total'] or Decimal('0.00')
    avg_price = total_cost / total_received if total_received > 0 else Decimal('0.000')
    
    truck_capacity = TRUCK_CAPACITIES.get(product_obj.name.upper(), Decimal('40000'))
    trucks_available = net_available / truck_capacity if truck_capacity > 0 and net_available > 0 else Decimal('0.00')
    
    return {
        'product_name': product_obj.name,
        'destination_name': destination_obj.name,
        'shipped': total_received,
        'dispatched': total_delivered,
        'physical_stock': physical_stock,
        'booked_stock': total_committed,
        'net_available': net_available,
        'avg_price': avg_price,
        'truck_capacity': truck_capacity,
        'trucks_available': trucks_available,
        'trucks_full': int(trucks_available),
        'trucks_partial': trucks_available - int(trucks_available),
    }


def _calculate_committed_stock(committed_trips_qs):
    """
    Calculate total committed stock from a queryset of trips.
    
    Committed stock represents approved loadings that haven't been physically loaded yet.
    Once a trip reaches LOADED status (after BoL processing), the stock is actually 
    depleted via FIFO and should no longer be counted as "committed".
    """
    total_committed = Decimal('0.00')
    
    for trip in committed_trips_qs:
        try:
            quantity_to_commit = Decimal('0.00')
            
            # Only count trips that are approved but not yet physically loaded
            if trip.status == 'KPC_APPROVED':
                # Use requested quantities from the loading authority
                quantity_to_commit = trip.total_requested_from_compartments
            elif trip.status == 'LOADING':
                # Trip is actively being loaded, still count as committed until BoL received
                quantity_to_commit = trip.total_requested_from_compartments
            # Note: LOADED, GATEPASSED, TRANSIT, DELIVERED trips are not included
            # because their stock has already been depleted via actual BoL processing
            
            total_committed += quantity_to_commit
            
        except Exception as e:
            logger.warning(f"Error calculating committed quantity for trip {trip.id} ({trip.kpc_order_number}): {e}")
            try:
                # Fallback: only for non-loaded trips
                if trip.status in ['KPC_APPROVED', 'LOADING']:
                    total_committed += trip.total_requested_from_compartments
            except Exception as fallback_e:
                logger.error(f"Fallback calculation also failed for trip {trip.id}: {fallback_e}")
            continue
            
    return total_committed


def _has_stock_activity(stock_data):
    """Check if stock data shows any activity."""
    return any([
        stock_data['shipped'] > 0,
        stock_data['dispatched'] > 0,
        stock_data['physical_stock'] != 0,
        stock_data['booked_stock'] != 0
    ])


def calculate_chart_data(trips_qs):
    """Calculate chart data for the last 30 days with better error handling."""
    try:
        today = timezone.now().date()
        daily_totals_pms = defaultdict(Decimal)
        daily_totals_ago = defaultdict(Decimal)
        
        trips_last_30_days = trips_qs.filter(
            status__in=CHART_TRIP_STATUSES,
            loading_date__gte=today - datetime.timedelta(days=30),
            loading_date__lte=today 
        ).select_related('product').prefetch_related('depletions_for_trip')
        
        for trip in trips_last_30_days:
            try:
                chart_date = trip.loading_date
                trip_total = trip.total_loaded 
                
                if trip_total and trip_total > Decimal('0.00'):
                    if trip.product.name.upper() == 'PMS':
                        daily_totals_pms[chart_date] += trip_total
                    elif trip.product.name.upper() == 'AGO':
                        daily_totals_ago[chart_date] += trip_total
            except Exception as e:
                logger.warning(f"Error processing trip {trip.id} for chart data: {e}")
                continue
        
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
    
    except Exception as e:
        logger.error(f"Error calculating chart data: {e}", exc_info=True)
        return {
            'loadings_chart_labels': [], 'pms_loadings_data': [], 'ago_loadings_data': [],
        }


def calculate_notifications(shipments_qs, user):
    """Calculate various notifications for the dashboard with enhanced error handling."""
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
        _calculate_aging_stock_notifications(shipments_qs, notifications, today, age_threshold)
        _calculate_product_inactivity_notifications(shipments_qs, notifications, user, show_global_data, today, inactivity_threshold)
        _calculate_utilized_shipment_notifications(shipments_qs, notifications, today, utilized_threshold)
        
    except Exception as e:
        logger.error(f"Error calculating notifications: {e}", exc_info=True)
    
    return notifications


def _calculate_aging_stock_notifications(shipments_qs, notifications, today, age_threshold):
    """Calculate aging stock notifications."""
    try:
        aging_shipments = shipments_qs.filter(
            quantity_remaining__gt=0,
            import_date__lte=today - datetime.timedelta(days=age_threshold)
        ).select_related('product', 'destination').order_by('import_date')
        
        for shipment in aging_shipments:
            try:
                days_old = (today - shipment.import_date).days
                dest_info = f" for {shipment.destination.name}" if shipment.destination else ""
                notifications['aging_stock_notifications'].append(
                    f"Shipment '{shipment.vessel_id_tag}' ({shipment.product.name}{dest_info}) "
                    f"imported on {shipment.import_date.strftime('%Y-%m-%d')} ({days_old} days old) "
                    f"still has {shipment.quantity_remaining:,.2f}L remaining."
                )
            except Exception as e:
                logger.warning(f"Error processing aging shipment {shipment.id}: {e}")
                continue
    except Exception as e:
        logger.error(f"Error calculating aging stock notifications: {e}", exc_info=True)


def _calculate_product_inactivity_notifications(shipments_qs, notifications, user, show_global_data, today, inactivity_threshold):
    """Calculate product inactivity notifications."""
    try:
        for product in Product.objects.all().order_by('name'):
            try:
                product_shipments_with_stock = shipments_qs.filter(product=product, quantity_remaining__gt=0)
                
                if not product_shipments_with_stock.exists():
                    continue
                
                depletion_filter = {'shipment_batch__product': product}
                if not show_global_data:
                    depletion_filter['trip__user'] = user 
                
                last_depletion_date = ShipmentDepletion.objects.filter(
                    **depletion_filter
                ).aggregate(max_date=Max('created_at'))['max_date']
                
                if last_depletion_date and isinstance(last_depletion_date, datetime.datetime):
                    last_depletion_date = last_depletion_date.date()

                message = _get_inactivity_message(product, last_depletion_date, product_shipments_with_stock, today, inactivity_threshold)
                
                if message:
                    idle_batches_info = []
                    for batch in product_shipments_with_stock.order_by('import_date'):
                        idle_batches_info.append({
                            'id': batch.id,
                            'vessel_id_tag': batch.vessel_id_tag,
                            'import_date': batch.import_date.strftime('%Y-%m-%d'),
                            'supplier_name': batch.supplier_name,
                            'quantity_remaining': f"{batch.quantity_remaining:,.2f}L",
                        })
                    
                    notifications['inactive_product_notifications'].append({
                        'message': message,
                        'product_name': product.name,
                        'shipments': idle_batches_info
                    })
            except Exception as e:
                logger.warning(f"Error processing product inactivity for {product.name}: {e}", exc_info=True)
                continue
    except Exception as e:
        logger.error(f"Error calculating product inactivity notifications: {e}", exc_info=True)


def _get_inactivity_message(product, last_depletion_date, product_shipments_with_stock, today, inactivity_threshold):
    """Get inactivity message for a product."""
    if last_depletion_date:
        days_inactive = (today - last_depletion_date).days
        if days_inactive > inactivity_threshold:
            return (f"Product '{product.name}' has stock but no dispatch "
                   f"in {days_inactive} days (last dispatch: {last_depletion_date.strftime('%Y-%m-%d')}).")
    else:
        oldest_stock_batch = product_shipments_with_stock.order_by('import_date').first()
        if oldest_stock_batch:
            days_inactive_since_import = (today - oldest_stock_batch.import_date).days
            if days_inactive_since_import > inactivity_threshold:
                return (f"Product '{product.name}' has stock (oldest batch from "
                       f"{oldest_stock_batch.import_date.strftime('%Y-%m-%d')}) "
                       f"but no dispatches recorded in the last {days_inactive_since_import} days (or ever).")
    return None


def _calculate_utilized_shipment_notifications(shipments_qs, notifications, today, utilized_threshold):
    """Calculate utilized shipment notifications."""
    try:
        utilized_shipments_candidates = shipments_qs.filter(
            quantity_remaining__lte=Decimal('0.00') 
        ).select_related('product').order_by('updated_at')

        for shipment in utilized_shipments_candidates:
            try:
                last_depletion_from_batch = ShipmentDepletion.objects.filter(
                    shipment_batch=shipment
                ).aggregate(max_date=Max('created_at'))['max_date']

                effective_utilized_date = shipment.updated_at.date()
                if last_depletion_from_batch:
                    last_depletion_date_only = last_depletion_from_batch.date() if isinstance(last_depletion_from_batch, datetime.datetime) else last_depletion_from_batch
                    if last_depletion_date_only and last_depletion_date_only > effective_utilized_date:
                        effective_utilized_date = last_depletion_date_only
                
                days_since_utilized = (today - effective_utilized_date).days
                
                if days_since_utilized >= utilized_threshold:
                    notifications['utilized_shipment_notifications'].append({
                        'id': shipment.id,
                        'vessel_id_tag': shipment.vessel_id_tag,
                        'product_name': shipment.product.name,
                        'supplier_name': shipment.supplier_name,
                        'import_date': shipment.import_date.strftime('%Y-%m-%d'),
                        'utilized_date': effective_utilized_date.strftime('%Y-%m-%d'),
                        'days_since_utilized': days_since_utilized
                    })
            except Exception as e:
                logger.warning(f"Error processing utilized shipment {shipment.id}: {e}", exc_info=True)
                continue
        
        notifications['utilized_shipment_notifications'].sort(key=lambda x: datetime.datetime.strptime(x['utilized_date'], '%Y-%m-%d').date())
        
    except Exception as e:
        logger.error(f"Error calculating utilized shipment notifications: {e}", exc_info=True)
# --- Main Views ---
@login_required
def home_view(request):
    """Main dashboard view with stock summary and notifications."""
    try:
        context = {
            'message': 'Welcome to Sakina Gas Company',
            'description': 'Manage your fuel inventory efficiently.',
            'is_authenticated_user': request.user.is_authenticated
        }

        permissions = {
            'can_view_shipments': request.user.has_perm('shipments.view_shipment'),
            'can_add_shipment': request.user.has_perm('shipments.add_shipment'),
            'can_view_trip': request.user.has_perm('shipments.view_trip'),
            'can_view_product': request.user.has_perm('shipments.view_product'), 
            'can_view_customer': request.user.has_perm('shipments.view_customer'), 
            'can_view_vehicle': request.user.has_perm('shipments.view_vehicle'),
            'can_add_trip': request.user.has_perm('shipments.add_trip')
        }
        context.update(permissions)

        shipments_qs = get_user_accessible_shipments(request.user)
        trips_qs = get_user_accessible_trips(request.user)

        context.update(_calculate_dashboard_stats(shipments_qs, trips_qs, request.user, permissions))

        cache_key = f"dashboard_stock_summary_{request.user.id}"
        stock_summary = cache.get(cache_key)
        
        if stock_summary is None:
            try:
                stock_summary = calculate_product_stock_summary(shipments_qs, trips_qs, request.user)
                cache.set(cache_key, stock_summary, 300)
            except Exception as e:
                logger.error(f"Error calculating stock summary: {e}", exc_info=True)
                stock_summary = {}
        
        context['stock_by_product_detailed'] = stock_summary

        if permissions['can_view_trip']:
            context.update(calculate_chart_data(trips_qs))

        if permissions['can_view_shipments']:
            context.update(calculate_notifications(shipments_qs, request.user))

        if permissions['can_view_trip']:
            context.update(_calculate_trip_quantities_by_product(trips_qs))

        if not (permissions['can_view_shipments'] or permissions['can_view_trip']):
            context['description'] = 'You do not have permission to view fuel data. Contact admin for access.'

        return render(request, 'shipments/home.html', context)
        
    except Exception as e:
        logger.exception(f"Unexpected error in home_view: {e}")
        messages.error(request, "An error occurred while loading the dashboard. Please try again.")
        return render(request, 'shipments/home.html', {
            'message': 'Welcome to Sakina Gas Company',
            'description': 'An error occurred while loading dashboard data.',
            'is_authenticated_user': request.user.is_authenticated,
            'stock_by_product_detailed': {}, 
            'loadings_chart_labels': [], 'pms_loadings_data': [], 'ago_loadings_data': [],
            'aging_stock_notifications': [], 'inactive_product_notifications': [], 'utilized_shipment_notifications': [],
            'trip_quantity_by_product': []
        })


def _calculate_dashboard_stats(shipments_qs, trips_qs, user, permissions):
    """Calculate overall dashboard statistics, respecting user permissions."""
    stats = {
        'total_shipments': 0, 'total_quantity_shipments': Decimal('0.00'),
        'total_value_shipments': Decimal('0.00'), 'total_trips_delivered': 0, 
        'total_quantity_loaded_delivered': Decimal('0.00'), 'turnover_rate': 0.0,
    }
    
    try:
        if permissions['can_view_shipments']:
            stats['total_shipments'] = shipments_qs.count()
            if stats['total_shipments'] > 0:
                aggregation = shipments_qs.aggregate(
                    total_qty=Sum('quantity_litres'),
                    total_value=Sum(F('quantity_litres') * F('price_per_litre'))
                )
                stats['total_quantity_shipments'] = aggregation.get('total_qty') or Decimal('0.00')
                stats['total_value_shipments'] = aggregation.get('total_value') or Decimal('0.00')

        if permissions['can_view_trip']:
            # Count all trips regardless of status for total trips
            stats['total_trips'] = trips_qs.count()
            
            # Count only delivered trips for the delivered metric
            delivered_trips_qs = trips_qs.filter(status='DELIVERED')
            stats['total_trips_delivered'] = delivered_trips_qs.count()
            
            # Calculate total quantity for ALL completed trips (not just delivered)
            # Include LOADED, GATEPASSED, TRANSIT, DELIVERED statuses
            completed_statuses = ['LOADED', 'GATEPASSED', 'TRANSIT', 'DELIVERED']
            completed_trips_qs = trips_qs.filter(status__in=completed_statuses)
            
            if completed_trips_qs.exists():
                # Use actual depletions for accurate calculation
                total_depleted = ShipmentDepletion.objects.filter(
                    trip__in=completed_trips_qs
                ).aggregate(
                    total=Sum('quantity_depleted')
                )['total'] or Decimal('0.00')
                
                stats['total_quantity_loaded_delivered'] = total_depleted
            else:
                stats['total_quantity_loaded_delivered'] = Decimal('0.00')
            
            if stats['total_quantity_shipments'] > 0 and stats['total_quantity_loaded_delivered'] > 0:
                turnover_rate_decimal = (stats['total_quantity_loaded_delivered'] / stats['total_quantity_shipments']) * 100
                stats['turnover_rate'] = round(float(turnover_rate_decimal), 1)
        return stats
    except Exception as e:
        logger.error(f"Error calculating dashboard stats: {e}", exc_info=True)
        return stats


def _calculate_trip_quantities_by_product(trips_qs):
    """Calculate trip quantities by product for delivered trips. trips_qs is permission-aware."""
    try:
        delivered_trips_qs = trips_qs.filter(status='DELIVERED')
        
        trip_quantity_by_product = ShipmentDepletion.objects.filter(
            trip__in=delivered_trips_qs
        ).values(
            'shipment_batch__product__name'
        ).annotate(
            total_litres=Sum('quantity_depleted')
        ).order_by(
            'shipment_batch__product__name'
        )
        return {'trip_quantity_by_product': list(trip_quantity_by_product)}
    except Exception as e:
        logger.error(f"Error calculating trip quantities by product: {e}", exc_info=True)
        return {'trip_quantity_by_product': []}


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
                
                if not pdf_file.name.lower().endswith(tuple(ALLOWED_PDF_EXTENSIONS)):
                    messages.error(request, f"Invalid file type. Please upload a PDF ({', '.join(ALLOWED_PDF_EXTENSIONS)}).")
                    return render(request, 'shipments/upload_loading_authority.html', {'form': form})
                
                parsed_data = parse_loading_authority_pdf(pdf_file, request)
                if not parsed_data:
                    return render(request, 'shipments/upload_loading_authority.html', {'form': form})
                
                with transaction.atomic():
                    trip_instance = create_trip_from_parsed_data(parsed_data, request, pdf_file.name)
                    if trip_instance:
                        is_new_creation = not messages.get_messages(request) 
                        if is_new_creation:
                             messages.success(
                                request, 
                                f"Trip for KPC Order No '{trip_instance.kpc_order_number}' "
                                f"(ID: {trip_instance.id}) processed successfully from PDF."
                             )
                        return redirect(reverse('shipments:trip-detail', kwargs={'pk': trip_instance.pk}))
                
            except ValidationError as e:
                messages.error(request, f"Data validation error: {str(e)}")
                logger.warning(f"Validation error in PDF upload: {e}", exc_info=True)
            except IntegrityError as e:
                error_str = str(e).lower()
                if 'kpc_order_number' in error_str and ('trip' in error_str or 'loadingauthority' in error_str) :
                    messages.error(request, "A trip with this KPC Order Number already exists. Cannot create a duplicate.")
                else:
                    messages.error(request, f"Database integrity error: {str(e)}. Please check your data.")
                logger.error(f"Integrity error during PDF upload processing: {e}", exc_info=True)
            except Exception as e:
                messages.error(request, f"An unexpected error occurred while processing the PDF: {str(e)}")
                logger.error(f"Unexpected error in PDF upload (upload_loading_authority_view): {e}", exc_info=True)
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    if field == '__all__':
                        messages.error(request, error)
                    else:
                        messages.error(request, f"{form.fields[field].label if field in form.fields else field.replace('_', ' ').capitalize()}: {error}")
    else:
        form = PdfLoadingAuthorityUploadForm()
        
    return render(request, 'shipments/upload_loading_authority.html', {'form': form})
# --- Shipment Views ---
@login_required
@permission_required('shipments.view_shipment', raise_exception=True)
def shipment_list_view(request):
    """List shipments with filtering and pagination."""
    try:
        queryset = get_user_accessible_shipments(request.user)
        queryset = apply_shipment_filters(queryset, request.GET)
        
        ordered_queryset = queryset.select_related('product', 'destination', 'user').order_by('-import_date', '-created_at')
        
        paginator = Paginator(ordered_queryset, 25)
        page_number = request.GET.get('page', 1)
        
        try:
            page_obj = paginator.page(page_number)
        except PageNotAnInteger:
            page_obj = paginator.page(1)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)
        
        products_for_filter = Product.objects.all().order_by('name')
        
        context = {
            'page_obj': page_obj,
            'shipments': page_obj, 
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
            'page_obj': None,
            'shipments': None,
            'products': Product.objects.all().order_by('name'),
            'can_add_shipment': request.user.has_perm('shipments.add_shipment'),
            'can_change_shipment': request.user.has_perm('shipments.change_shipment'),
            'can_delete_shipment': request.user.has_perm('shipments.delete_shipment'),
        })


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
                    shipment_instance.full_clean()
                    shipment_instance.save()
                    messages.success(request, 'Shipment added successfully!')
                    return redirect(reverse('shipments:shipment-detail', kwargs={'pk': shipment_instance.pk}))
            except ValidationError as e:
                _handle_validation_errors(e, form, messages, request)
            except IntegrityError as e:
                _handle_shipment_integrity_error(e, form, logger)
                if not form.errors: messages.error(request, "A database error occurred. Please check unique fields like Vessel ID.")
            except Exception as e: 
                messages.error(request, f"An unexpected error occurred: {e}")
                logger.error(f"Unexpected error in shipment creation: {e}", exc_info=True)
        else:
             _handle_validation_errors(form.errors, form, messages, request)
    else: 
        form = ShipmentForm()
    
    context = {'form': form, 'page_title': 'Add New Shipment'}
    return render(request, 'shipments/shipment_form.html', context)


@login_required
@require_http_methods(["GET", "POST"])
@csrf_protect
def shipment_edit_view(request, pk):
    """Edit shipment with proper permissions and validation."""
    try:
        shipment_instance = _get_shipment_for_user(request.user, pk)
        
        if not request.user.has_perm('shipments.change_shipment'): 
            return HttpResponseForbidden("You do not have permission to change shipments.")
        
        depleted_quantity = ShipmentDepletion.objects.filter(
            shipment_batch=shipment_instance
        ).aggregate(total_depleted=Sum('quantity_depleted'))['total_depleted'] or Decimal('0.00')
        
        if request.method == 'POST':
            form = ShipmentForm(request.POST, instance=shipment_instance) 
            if form.is_valid():
                new_quantity_litres = form.cleaned_data.get('quantity_litres', shipment_instance.quantity_litres)
                if new_quantity_litres < depleted_quantity:
                    form.add_error('quantity_litres', 
                        f"Cannot reduce total quantity ({new_quantity_litres:,.2f}L) below what has already been depleted ({depleted_quantity:,.2f}L).")
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
                        _handle_validation_errors(e, form, messages, request)
                    except Exception as e: 
                        messages.error(request, f"An unexpected error occurred during update: {e}")
                        logger.error(f"Error updating shipment {pk}: {e}", exc_info=True)
            else:
                 _handle_validation_errors(form.errors, form, messages, request)
        else: 
            form = ShipmentForm(instance=shipment_instance)
        
        if depleted_quantity > 0: 
            messages.info(request, f"Note: {depleted_quantity:,.2f}L from this shipment has been used. Total quantity cannot be set lower than this amount.")
        
        context = {
            'form': form, 
            'page_title': f'Edit Shipment: {shipment_instance.vessel_id_tag}',
            'shipment_instance': shipment_instance, 
            'depleted_quantity': depleted_quantity,
        }
        return render(request, 'shipments/shipment_form.html', context)
    
    except Http404:
        messages.error(request, "Shipment not found.")
        return redirect(reverse('shipments:shipment-list'))
    except PermissionDenied:
        return HttpResponseForbidden("You do not have permission to access this shipment.")
    except Exception as e:
        logger.error(f"Error in shipment_edit_view for pk {pk}: {e}", exc_info=True)
        messages.error(request, "An error occurred while loading the shipment for editing.")
        return redirect(reverse('shipments:shipment-list'))


@login_required
@require_http_methods(["GET", "POST"])
@csrf_protect
def shipment_delete_view(request, pk):
    """Delete shipment with proper validation."""
    try:
        shipment_instance = _get_shipment_for_user(request.user, pk)
        
        can_delete = request.user.has_perm('shipments.delete_shipment')
        if not can_delete:
             return HttpResponseForbidden("You do not have permission to delete shipments.")

        if ShipmentDepletion.objects.filter(shipment_batch=shipment_instance).exists():
            messages.error(request, 
                f"Cannot delete Shipment '{shipment_instance.vessel_id_tag}'. "
                f"It has associated loadings/depletions. Please remove or reassign these loadings first.")
            return redirect(reverse('shipments:shipment-detail', kwargs={'pk': shipment_instance.pk}))
        
        if request.method == 'POST':
            try:
                with transaction.atomic():
                    shipment_tag = shipment_instance.vessel_id_tag
                    shipment_instance.delete()
                    messages.success(request, f"Shipment '{shipment_tag}' deleted successfully!")
                    return redirect(reverse('shipments:shipment-list'))
            except Exception as e: 
                messages.error(request, f"Error deleting shipment: {e}")
                logger.error(f"Error deleting shipment {pk}: {e}", exc_info=True)
                return redirect(reverse('shipments:shipment-detail', kwargs={'pk': pk}))
        
        context = {
            'shipment': shipment_instance, 
            'page_title': f'Confirm Delete: {shipment_instance.vessel_id_tag}',
            'can_delete_shipment': can_delete
        }
        return render(request, 'shipments/shipment_confirm_delete.html', context)
    
    except Http404:
        messages.error(request, "Shipment not found for deletion.")
        return redirect(reverse('shipments:shipment-list'))
    except PermissionDenied:
        return HttpResponseForbidden("You do not have permission to access this shipment for deletion.")
    except Exception as e:
        logger.error(f"Error in shipment_delete_view for pk {pk}: {e}", exc_info=True)
        messages.error(request, "An error occurred while processing the deletion request.")
        return redirect(reverse('shipments:shipment-list'))


@login_required
@permission_required('shipments.view_shipment', raise_exception=True)
def shipment_detail_view(request, pk):
    """View shipment details with proper permissions."""
    try:
        shipment = _get_shipment_for_user(request.user, pk, for_detail=True)
        
        related_depletions = ShipmentDepletion.objects.filter(
            shipment_batch=shipment
        ).select_related('trip__vehicle', 'trip__customer', 'trip__user').order_by('-created_at')[:10]
        
        can_change = request.user.has_perm('shipments.change_shipment')
        if not is_admin_or_superuser(request.user) and shipment.user != request.user:
            can_change = False

        can_delete = request.user.has_perm('shipments.delete_shipment')
        if not is_admin_or_superuser(request.user) and shipment.user != request.user:
            can_delete = False

        context = {
            'shipment': shipment, 
            'related_depletions': related_depletions,
            'page_title': f'Shipment Details: {shipment.vessel_id_tag}',
            'can_change_shipment': can_change,
            'can_delete_shipment': can_delete,
        }
        return render(request, 'shipments/shipment_detail.html', context)
    
    except Http404:
        messages.error(request, "Shipment not found or you don't have permission to view it.")
        return redirect(reverse('shipments:shipment-list'))
    except PermissionDenied:
        return HttpResponseForbidden("You do not have permission to view this shipment.")
    except Exception as e:
        logger.error(f"Error in shipment_detail_view for pk {pk}: {e}", exc_info=True)
        messages.error(request, "An error occurred while loading shipment details.")
        return redirect(reverse('shipments:shipment-list'))


# --- Shipment Helper Functions ---
def _get_shipment_for_user(user, pk, for_detail=False):
    """Get shipment instance based on user permissions. Raises Http404 if not found or not permitted."""
    base_qs = Shipment.objects.all()
    if for_detail:
        base_qs = base_qs.select_related('user', 'product', 'destination')

    if is_viewer_or_admin_or_superuser(user):
        if not for_detail and not is_admin_or_superuser(user):
            raise Http404("Viewers do not have permission to modify this resource.")
        try:
            return base_qs.get(pk=pk)
        except Shipment.DoesNotExist:
            raise Http404("Shipment not found.")
    else:
        try:
            return base_qs.get(pk=pk, user=user)
        except Shipment.DoesNotExist:
            raise Http404("Shipment not found or you do not have permission to access it.")


def _handle_validation_errors(validation_error_or_dict, form, messages_obj, request):
    """Handle Django ValidationErrors or form.errors dict consistently."""
    error_dict = {}
    if isinstance(validation_error_or_dict, ValidationError):
        if hasattr(validation_error_or_dict, 'message_dict'):
            error_dict = validation_error_or_dict.message_dict
        elif hasattr(validation_error_or_dict, 'messages'):
            error_dict = {'__all__': validation_error_or_dict.messages}
        else:
            error_dict = {'__all__': [str(validation_error_or_dict)]}
    elif isinstance(validation_error_or_dict, dict):
        error_dict = validation_error_or_dict

    for field, errors in error_dict.items():
        for error in errors:
            if field == '__all__':
                messages_obj.error(request, error)
            else:
                if form and hasattr(form, 'add_error') and field in form.fields :
                    form.add_error(field, error)
                else:
                    field_name = field.replace('_', ' ').capitalize()
                    messages_obj.error(request, f"{field_name}: {error}")


def _handle_shipment_integrity_error(integrity_error, form, logger_obj):
    """Handle shipment-specific integrity errors by adding to form errors."""
    error_str = str(integrity_error).lower()
    if 'vessel_id_tag' in error_str and ('shipment' in error_str or 'unique' in error_str):
        if form and hasattr(form, 'add_error'):
            form.add_error('vessel_id_tag', 'A shipment with this Vessel ID already exists.')
        else:
            logger_obj.error(f"IntegrityError for vessel_id_tag but no form to add error: {integrity_error}")
    else:
        logger_obj.error(f"Unhandled IntegrityError in shipment operation: {integrity_error}", exc_info=True)
        if form and hasattr(form, 'add_error'):
            form.add_error(None, "A database error occurred. Please ensure all unique fields are correctly entered.")
# --- Trip Views ---
@login_required
@permission_required('shipments.add_trip', raise_exception=True)
@require_http_methods(["GET", "POST"])
@csrf_protect
def trip_add_view(request):
    """Add new trip with compartments."""
    if request.method == 'POST':
        trip_form = TripForm(request.POST) 
        compartment_formset = LoadingCompartmentFormSet(request.POST, prefix='compartments', instance=None)
        
        if trip_form.is_valid() and compartment_formset.is_valid():
            try:
                with transaction.atomic():
                    trip_instance = trip_form.save(commit=False)
                    trip_instance.user = request.user
                    trip_instance.full_clean()
                    trip_instance.save()
                    logger.info(f"Trip {trip_instance.id} saved with KPC Order Number: {trip_instance.kpc_order_number}") 
                    
                    compartment_formset.instance = trip_instance
                    for form_in_fs in compartment_formset:
                        if form_in_fs.has_changed() and not form_in_fs.cleaned_data.get('DELETE', False):
                            form_in_fs.instance.trip = trip_instance
                            form_in_fs.instance.full_clean()
                    compartment_formset.save()
                    
                    messages.success(request, 
                        f'Loading for KPC Order {trip_instance.kpc_order_number} recorded. '
                        f'Status: {trip_instance.get_status_display()}.')
                    return redirect(reverse('shipments:trip-detail', kwargs={'pk': trip_instance.pk}))
            except ValidationError as e:
                _handle_validation_errors(e, trip_form, messages, request)
            except IntegrityError as e:
                _handle_trip_integrity_error(e, trip_form, logger)
                if not trip_form.errors: messages.error(request, "A database error occurred. Check KPC Order Number uniqueness.")
            except Exception as e: 
                messages.error(request, f'An unexpected error occurred: {str(e)}')
                logger.error(f"Unexpected error in trip creation: {e}", exc_info=True)
        else: 
            messages.error(request, 'Please correct the form errors (main trip form or compartments section).')
            if trip_form.errors: logger.warning(f"Trip form errors: {trip_form.errors.as_json()}")
            if compartment_formset.errors: logger.warning(f"Compartment formset errors: {compartment_formset.errors}")
            if compartment_formset.non_form_errors(): logger.warning(f"Compartment formset non-form errors: {compartment_formset.non_form_errors()}")
    else: 
        trip_form = TripForm()
        initial_compartments = [{'compartment_number': i+1, 'quantity_requested_litres': None} for i in range(3)]
        compartment_formset = LoadingCompartmentFormSet(prefix='compartments', initial=initial_compartments, instance=None)
    
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
    try:
        trip_instance = _get_trip_for_user(request.user, pk)
        
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
                        updated_trip_instance.save()
                        logger.info(f"Trip {updated_trip_instance.id} updated with KPC Order Number: {updated_trip_instance.kpc_order_number}")
                        
                        for form_in_fs in compartment_formset:
                            if form_in_fs.has_changed() and not form_in_fs.cleaned_data.get('DELETE', False):
                                form_in_fs.instance.trip = updated_trip_instance
                                form_in_fs.instance.full_clean()
                        compartment_formset.save()
                        
                        messages.success(request, f"Loading '{updated_trip_instance.kpc_order_number}' updated successfully!")
                        return redirect(reverse('shipments:trip-detail', kwargs={'pk': updated_trip_instance.pk}))
                except ValidationError as e:
                    _handle_validation_errors(e, trip_form, messages, request)
                except IntegrityError as e:
                    _handle_trip_integrity_error(e, trip_form, logger)
                    if not trip_form.errors: messages.error(request, "A database integrity error occurred during update.")
                except Exception as e: 
                    messages.error(request, f'An unexpected error during update: {str(e)}')
                    logger.error(f"Error updating trip {pk}: {e}", exc_info=True)
            else: 
                messages.error(request, 'Please correct the form errors (main trip form or compartments section).')
                if trip_form.errors: logger.warning(f"Trip edit form errors: {trip_form.errors.as_json()}")
                if compartment_formset.errors: logger.warning(f"Compartment edit formset errors: {compartment_formset.errors}")
                if compartment_formset.non_form_errors(): logger.warning(f"Compartment edit formset non-form errors: {compartment_formset.non_form_errors()}")
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
    
    except Http404:
        messages.error(request, "Loading/Trip not found.")
        return redirect(reverse('shipments:trip-list'))
    except PermissionDenied:
        return HttpResponseForbidden("You do not have permission to access this loading/trip.")
    except Exception as e:
        logger.error(f"Error in trip_edit_view for pk {pk}: {e}", exc_info=True)
        messages.error(request, "An error occurred while loading the trip for editing.")
        return redirect(reverse('shipments:trip-list'))


@login_required
@require_http_methods(["GET", "POST"])
@csrf_protect
def trip_delete_view(request, pk):
    """Delete trip with stock reversal."""
    try:
        trip_instance = _get_trip_for_user(request.user, pk)
        
        if not request.user.has_perm('shipments.delete_trip'): 
            return HttpResponseForbidden("You do not have permission to delete loadings.")
        
        if request.method == 'POST':
            try:
                with transaction.atomic():
                    reversal_successful, reversal_message = trip_instance.reverse_stock_depletion()
                    if not reversal_successful:
                        messages.error(request, f"Failed to reverse stock depletions: {reversal_message}. Deletion aborted.")
                        return redirect(reverse('shipments:trip-detail', kwargs={'pk': pk}))
                    
                    trip_desc = trip_instance.kpc_order_number or f"Trip {trip_instance.id}"
                    trip_instance.delete()
                    messages.success(request, f"Loading '{trip_desc}' and associated depletions (if any) deleted successfully!")
                    if reversal_message : messages.info(request, reversal_message)
                    return redirect(reverse('shipments:trip-list'))
            except Exception as e:
                messages.error(request, f"Error during deletion: {str(e)}")
                logger.error(f"Error deleting trip {pk} after attempting stock reversal: {e}", exc_info=True)
                return redirect(reverse('shipments:trip-detail', kwargs={'pk': pk}))
        
        context = {
            'trip': trip_instance, 
            'page_title': f'Confirm Delete Loading: {trip_instance.kpc_order_number or f"Trip {trip_instance.id}"}'
        }
        return render(request, 'shipments/trip_confirm_delete.html', context)
    
    except Http404:
        messages.error(request, "Loading/Trip not found for deletion.")
        return redirect(reverse('shipments:trip-list'))
    except PermissionDenied:
        return HttpResponseForbidden("You do not have permission to access this loading/trip for deletion.")
    except Exception as e:
        logger.error(f"Error in trip_delete_view for pk {pk}: {e}", exc_info=True)
        messages.error(request, "An error occurred while processing the deletion request.")
        return redirect(reverse('shipments:trip-list'))


@login_required
@permission_required('shipments.view_trip', raise_exception=True)
def trip_detail_view(request, pk):
    """View trip details with compartments and depletions."""
    try:
        trip = _get_trip_for_user(request.user, pk, for_detail=True)
        
        requested_compartments = trip.requested_compartments.all().order_by('compartment_number')
        actual_depletions = trip.depletions_for_trip.select_related(
            'shipment_batch__product', 'shipment_batch__destination'
        ).order_by('created_at', 'shipment_batch__import_date')
        
        can_change = request.user.has_perm('shipments.change_trip')
        if not is_admin_or_superuser(request.user) and trip.user != request.user:
            can_change = False

        can_delete = request.user.has_perm('shipments.delete_trip')
        if not is_admin_or_superuser(request.user) and trip.user != request.user:
            can_delete = False
        
        context = {
            'trip': trip, 
            'compartments': requested_compartments, 
            'actual_depletions': actual_depletions, 
            'page_title': f'Loading Details: {trip.kpc_order_number or f"Trip {trip.id}"}', 
            'can_change_trip': can_change,
            'can_delete_trip': can_delete,
        }
        return render(request, 'shipments/trip_detail.html', context)
    
    except Http404:
        messages.error(request, "Loading/Trip not found or you don't have permission to view it.")
        return redirect(reverse('shipments:trip-list'))
    except PermissionDenied:
        return HttpResponseForbidden("You do not have permission to view this loading/trip.")
    except Exception as e:
        logger.error(f"Error in trip_detail_view for pk {pk}: {e}", exc_info=True)
        messages.error(request, "An error occurred while loading trip details.")
        return redirect(reverse('shipments:trip-list'))


@login_required
@permission_required('shipments.view_trip', raise_exception=True)
def trip_list_view(request):
    """List trips with filtering and pagination."""
    try:
        queryset = get_user_accessible_trips(request.user)
        queryset = apply_trip_filters(queryset, request.GET)
        
        distinct_queryset_for_sum = queryset.distinct()
        filtered_total_loaded = _calculate_filtered_total_loaded(distinct_queryset_for_sum)

        ordered_queryset = distinct_queryset_for_sum.select_related(
            'vehicle', 'customer', 'product', 'destination', 'user'
        ).prefetch_related(
            'requested_compartments', 'depletions_for_trip'
        ).order_by('-loading_date', '-loading_time', '-created_at')
         
        paginator = Paginator(ordered_queryset, 25)
        page_number = request.GET.get('page', 1)
        
        try:
            page_obj = paginator.page(page_number)
        except PageNotAnInteger:
            page_obj = paginator.page(1)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)
        
        filter_options = _get_trip_filter_options()
        
        context = { 
            'page_obj': page_obj,
            'trips': page_obj, 
            'filtered_trip_count': page_obj.paginator.count,
            'filtered_total_loaded': filtered_total_loaded, 
            'can_add_trip': request.user.has_perm('shipments.add_trip'), 
            'can_change_trip_globally': request.user.has_perm('shipments.change_trip'), 
            'can_delete_trip_globally': request.user.has_perm('shipments.delete_trip'),
            **filter_options,
            **_get_filter_values(request.GET)
        }
        return render(request, 'shipments/trip_list.html', context)
        
    except Exception as e:
        logger.error(f"Error in trip_list_view: {e}", exc_info=True)
        messages.error(request, "An error occurred while loading trips.")
        return render(request, 'shipments/trip_list.html', {
            'page_obj': None,
            'trips': None,
            'filtered_trip_count': 0,
            'filtered_total_loaded': Decimal('0.00'),
            'can_add_trip': request.user.has_perm('shipments.add_trip'),
            'can_change_trip_globally': request.user.has_perm('shipments.change_trip'),
            'can_delete_trip_globally': request.user.has_perm('shipments.delete_trip'),
            **_get_trip_filter_options(),
            **_get_filter_values(request.GET)
        })


# --- Trip Helper Functions ---
def _get_trip_for_user(user, pk, for_detail=False):
    """Get trip instance based on user permissions. Raises Http404 if not found or not permitted."""
    base_qs = Trip.objects.all()
    if for_detail:
        base_qs = base_qs.select_related(
            'user', 'vehicle', 'customer', 'product', 'destination'
        ).prefetch_related(
            'requested_compartments', 
            'depletions_for_trip__shipment_batch__product', 
            'depletions_for_trip__shipment_batch__destination'
        )

    if is_viewer_or_admin_or_superuser(user):
        if not for_detail and not is_admin_or_superuser(user): 
            raise Http404("Viewers do not have permission to modify this resource.")
        try:
            return base_qs.get(pk=pk)
        except Trip.DoesNotExist:
            raise Http404("Trip not found.")
    else:
        try:
            return base_qs.get(pk=pk, user=user)
        except Trip.DoesNotExist:
            raise Http404("Trip not found or you do not have permission to access it.")


def _handle_trip_validation_errors(validation_error, trip_form, messages_obj, request):
    """Handle trip validation errors by adding to form or global messages."""
    error_dict = {}
    if hasattr(validation_error, 'message_dict'):
        error_dict = validation_error.message_dict
    elif hasattr(validation_error, 'messages'):
        error_dict = {'__all__': validation_error.messages}
    else:
        error_dict = {'__all__': [str(validation_error)]}

    for field, errors_list in error_dict.items():
        for error_item in errors_list:
            if field == '__all__' or not hasattr(trip_form, 'add_error') or field not in trip_form.fields: 
                messages_obj.error(request, error_item) 
            elif hasattr(trip_form, 'add_error'):
                trip_form.add_error(field, error_item)
            else:
                messages_obj.error(request, f"Error on field '{field}': {error_item}")


def _handle_trip_integrity_error(integrity_error, trip_form, logger_obj):
    """Handle trip-specific integrity errors by adding to form errors."""
    error_str = str(integrity_error).lower()
    if 'kpc_order_number' in error_str and ('trip' in error_str or 'loadingauthority' in error_str or 'unique' in error_str):
        if trip_form and hasattr(trip_form, 'add_error'):
            trip_form.add_error('kpc_order_number', 'A trip with this KPC Order Number already exists.')
        else:
             logger_obj.error(f"IntegrityError for kpc_order_number but no form to add error: {integrity_error}")
    else:
        logger_obj.error(f"Unhandled IntegrityError in trip operation: {integrity_error}", exc_info=True)
        if trip_form and hasattr(trip_form, 'add_error'):
            trip_form.add_error(None, "A database error occurred. Please ensure all unique fields are correctly entered.")


def _calculate_filtered_total_loaded(queryset):
    """Calculate total loaded quantity for a given queryset of trips."""
    filtered_total_loaded = Decimal('0.00')
    trips_for_total = queryset.prefetch_related('requested_compartments', 'depletions_for_trip')

    for trip in trips_for_total:
        try:
            filtered_total_loaded += trip.total_loaded
        except Exception as e:
            logger.warning(f"Error calculating total_loaded for trip {trip.id} ({trip.kpc_order_number}): {e}")
            try: 
                filtered_total_loaded += trip.total_requested_from_compartments
            except Exception as fallback_e:
                 logger.error(f"Fallback for total_loaded also failed for trip {trip.id}: {fallback_e}")
            continue
    
    return filtered_total_loaded


def _get_trip_filter_options():
    """Get filter options for trip list view dropdowns."""
    return {
        'products': Product.objects.all().order_by('name'),
        'customers': Customer.objects.all().order_by('name'),
        'vehicles': Vehicle.objects.all().order_by('plate_number'),
        'status_choices': Trip.STATUS_CHOICES,
    }


def _get_filter_values(get_params):
    """Extract and return current filter values from GET parameters for form repopulation."""
    return {
        'product_filter_value': get_params.get('product', ''),
        'customer_filter_value': get_params.get('customer', ''), 
        'vehicle_filter_value': get_params.get('vehicle', ''), 
        'status_filter_value': get_params.get('status', ''), 
        'start_date_filter_value': get_params.get('start_date', ''), 
        'end_date_filter_value': get_params.get('end_date', ''),
    }
# --- Reporting and Dashboard Views ---
@login_required
@permission_required('shipments.view_trip', raise_exception=True) 
@permission_required('shipments.view_shipment', raise_exception=True)
def truck_activity_dashboard_view(request):
    """Truck activity dashboard with filtering by trip parameters."""
    try:
        base_queryset = get_user_accessible_trips(request.user)
        filtered_trips_qs = apply_trip_filters(base_queryset, request.GET).distinct()
        truck_activities = _calculate_truck_activities(filtered_trips_qs)
        filter_options = _get_trip_filter_options()
        
        context = { 
            'truck_activities': truck_activities,
            'page_title': 'Truck Activity Dashboard',
            **filter_options,
            **_get_filter_values(request.GET)
        }
        return render(request, 'shipments/truck_activity_dashboard.html', context)
        
    except Exception as e:
        logger.error(f"Error in truck_activity_dashboard_view: {e}", exc_info=True)
        messages.error(request, "An error occurred while loading truck activity data.")
        return render(request, 'shipments/truck_activity_dashboard.html', {
            'truck_activities': {},
            'page_title': 'Truck Activity Dashboard',
            **_get_trip_filter_options(),
            **_get_filter_values(request.GET)
        })


@login_required
@permission_required('shipments.view_shipment', raise_exception=True)
@permission_required('shipments.view_trip', raise_exception=True) 
def monthly_stock_summary_view(request):
    """Monthly stock summary report showing opening, in, out, and closing stock per product."""
    try:
        show_global_data = is_viewer_or_admin_or_superuser(request.user)
        all_years = _get_available_years()
        months_for_dropdown = [(i, datetime.date(2000, i, 1).strftime('%B')) for i in range(1, 13)]
        selected_year, selected_month = _parse_date_parameters(request.GET)
        start_date_of_month, end_date_of_month = _get_month_date_range(selected_year, selected_month)
        summary_data = _calculate_monthly_summary_data(start_date_of_month, end_date_of_month, show_global_data, request.user)
        
        context = {
            'summary_data': summary_data, 
            'selected_year': selected_year, 
            'selected_month': selected_month, 
            'month_name_display': datetime.date(1900, selected_month, 1).strftime('%B'),
            'available_years': [str(year) for year in all_years],
            'months_for_dropdown': months_for_dropdown,
            'page_title': f'Monthly Stock Summary - {datetime.date(1900, selected_month, 1).strftime("%B")} {selected_year}'
        }
        return render(request, 'shipments/monthly_stock_summary.html', context)
        
    except Exception as e:
        logger.error(f"Error in monthly_stock_summary_view: {e}", exc_info=True)
        messages.error(request, "An error occurred while generating the monthly report.")
        current_year = datetime.date.today().year
        current_month = datetime.date.today().month
        return render(request, 'shipments/monthly_stock_summary.html', {
            'summary_data': [],
            'selected_year': current_year,
            'selected_month': current_month,
            'month_name_display': datetime.date(1900, current_month, 1).strftime('%B'),
            'available_years': [str(yr) for yr in _get_available_years()] or [str(current_year)],
            'months_for_dropdown': [(i, datetime.date(2000, i, 1).strftime('%B')) for i in range(1, 13)],
            'page_title': 'Monthly Stock Summary'
        })


# --- Reporting Helper Functions ---
def _calculate_truck_activities(filtered_trips_qs):
    """Calculate and group truck activities from a pre-filtered queryset of trips."""
    truck_activities = defaultdict(lambda: {'trips': [], 'total_quantity': Decimal('0.00'), 'trip_count': 0})
    
    trips_with_relations = filtered_trips_qs.select_related(
        'vehicle', 'product', 'customer', 'user', 'destination'
    ).prefetch_related(
        'requested_compartments', 'depletions_for_trip' 
    ) 
    
    for trip in trips_with_relations:
        try:
            vehicle_obj = trip.vehicle
            if vehicle_obj:
                truck_activities[vehicle_obj]['trips'].append(trip)
                truck_activities[vehicle_obj]['total_quantity'] += trip.total_loaded
                truck_activities[vehicle_obj]['trip_count'] += 1
            else:
                logger.warning(f"Trip {trip.id} has no associated vehicle. Skipping for truck activity.")
        except Exception as e:
            logger.warning(f"Error processing trip {trip.id} ({trip.kpc_order_number}) for truck activity: {e}")
            continue
    
    return dict(sorted(truck_activities.items(), key=lambda item: item[0].plate_number if item[0] else ""))


def _get_available_years():
    """Get unique years from both shipments (import_date) and trips (loading_date)."""
    shipment_years_qs = Shipment.objects.dates('import_date', 'year', order='DESC')
    trip_years_qs = Trip.objects.dates('loading_date', 'year', order='DESC')
    
    all_years_set = set()
    for date_obj in shipment_years_qs: all_years_set.add(date_obj.year)
    for date_obj in trip_years_qs: all_years_set.add(date_obj.year)
        
    sorted_years = sorted(list(all_years_set), reverse=True)
    
    return sorted_years if sorted_years else [datetime.date.today().year]


def _parse_date_parameters(get_params):
    """Parse year and month from GET parameters, defaulting to current year/month."""
    today = datetime.date.today()
    current_year = today.year
    current_month = today.month
    
    try:
        selected_year = int(get_params.get('year', str(current_year)))
        selected_month = int(get_params.get('month', str(current_month)))
        
        if not (REASONABLE_YEAR_RANGE[0] <= selected_year <= REASONABLE_YEAR_RANGE[1]):
            logger.warning(f"Year {selected_year} out of reasonable range. Defaulting to {current_year}.")
            selected_year = current_year
        if not (1 <= selected_month <= 12):
            logger.warning(f"Month {selected_month} out of range (1-12). Defaulting to {current_month}.")
            selected_month = current_month
            
        return selected_year, selected_month
    except (ValueError, TypeError): 
        logger.warning(f"Invalid year/month parameters: year='{get_params.get('year')}', month='{get_params.get('month')}'. Defaulting.")
        return current_year, current_month


def _get_month_date_range(year, month):
    """Return the first and last date of a given year and month."""
    try:
        start_date = datetime.date(year, month, 1)
        _, num_days_in_month = monthrange(year, month)
        end_date = datetime.date(year, month, num_days_in_month)
        return start_date, end_date
    except ValueError as e:
        logger.error(f"Invalid year ({year}) or month ({month}) for date range: {e}")
        today = datetime.date.today()
        return _get_month_date_range(today.year, today.month)


def _calculate_monthly_summary_data(start_date_of_month, end_date_of_month, show_global_data, user_obj):
    """Calculate monthly stock summary for all products, respecting user permissions."""
    summary_data_list = []
    
    for product_obj in Product.objects.all().order_by('name'):
        try:
            shipments_for_product_qs = Shipment.objects.filter(product=product_obj)
            depletions_for_product_qs = ShipmentDepletion.objects.filter(shipment_batch__product=product_obj)
            
            if not show_global_data:
                shipments_for_product_qs = shipments_for_product_qs.filter(user=user_obj)
                depletions_for_product_qs = depletions_for_product_qs.filter(trip__user=user_obj)
            
            total_shipped_before_month = shipments_for_product_qs.filter(
                import_date__lt=start_date_of_month
            ).aggregate(total_qty=Sum('quantity_litres'))['total_qty'] or Decimal('0.00')
            
            total_depleted_before_month = depletions_for_product_qs.filter(
                created_at__date__lt=start_date_of_month
            ).aggregate(total_qty=Sum('quantity_depleted'))['total_qty'] or Decimal('0.00')
            
            opening_stock_qty = total_shipped_before_month - total_depleted_before_month
            
            stock_in_during_month_qty = shipments_for_product_qs.filter(
                import_date__gte=start_date_of_month,
                import_date__lte=end_date_of_month
            ).aggregate(total_qty=Sum('quantity_litres'))['total_qty'] or Decimal('0.00')
            
            stock_out_during_month_qty = depletions_for_product_qs.filter(
                created_at__date__gte=start_date_of_month, 
                created_at__date__lte=end_date_of_month
            ).aggregate(total_qty=Sum('quantity_depleted'))['total_qty'] or Decimal('0.00')
            
            closing_stock_qty = opening_stock_qty + stock_in_during_month_qty - stock_out_during_month_qty
            
            if any([opening_stock_qty != 0, stock_in_during_month_qty != 0, 
                    stock_out_during_month_qty != 0, closing_stock_qty != 0]):
                summary_data_list.append({
                    'product_name': product_obj.name, 
                    'opening_stock': opening_stock_qty, 
                    'stock_in_month': stock_in_during_month_qty, 
                    'stock_out_month': stock_out_during_month_qty, 
                    'closing_stock': closing_stock_qty
                })
        except Exception as e:
            logger.error(f"Error calculating monthly summary for product {product_obj.name}: {e}", exc_info=True)
            continue
    return summary_data_list
# --- User Management ---
@require_http_methods(["GET", "POST"])
@csrf_protect
def signup_view(request):
    """User registration with automatic assignment to 'Viewer' group."""
    if request.user.is_authenticated:
        messages.info(request, "You are already logged in.")
        return redirect(reverse('shipments:home'))

    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid(): 
            try:
                with transaction.atomic():
                    user = form.save()
                    try:
                        viewer_group, created = Group.objects.get_or_create(name='Viewer')
                        user.groups.add(viewer_group)
                        if created: logger.info(f"'Viewer' group created and user {user.username} assigned.")
                        else: logger.info(f"User {user.username} assigned to existing 'Viewer' group.")
                    except Exception as e: 
                        messages.warning(request, "Account created, but could not assign default user group. Please contact admin.")
                        logger.warning(f"Failed to assign 'Viewer' group to new user {user.username}: {e}", exc_info=True)
                    
                    auth_login(request, user)
                    messages.success(request, 'Account created successfully! You are now logged in.')
                    return redirect(reverse('shipments:home'))
            except Exception as e:
                messages.error(request, f"An error occurred during registration: {e}")
                logger.error(f"Error during user registration for {form.cleaned_data.get('username')}: {e}", exc_info=True)
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    if field == '__all__': messages.error(request, error)
                    else:
                        field_label = form.fields[field].label if field in form.fields and form.fields[field].label else field.replace('_', ' ').capitalize()
                        messages.error(request, f"{field_label}: {error}")
    else:
        form = UserCreationForm()
    
    context = {'form': form, 'page_title': 'Sign Up'}
    return render(request, 'registration/signup.html', context)


# --- API Views ---
@login_required
@require_http_methods(["GET"])
def get_vehicle_capacity_ajax(request):
    """AJAX endpoint to get default vehicle capacity for a given product type."""
    vehicle_id_str = request.GET.get('vehicle_id')
    product_id_str = request.GET.get('product_id')
    
    if not product_id_str:
        return JsonResponse({'error': 'Missing product_id'}, status=400)
    
    try:
        product_id = int(product_id_str)
        product = get_object_or_404(Product, pk=product_id)
        
        default_capacity_if_not_found = Decimal('30000')
        capacity = TRUCK_CAPACITIES.get(product.name.upper(), default_capacity_if_not_found)
        
        response_data = {
            'capacity': float(capacity),
            'product_name': product.name
        }

        if vehicle_id_str:
            try:
                vehicle_id = int(vehicle_id_str)
                vehicle = get_object_or_404(Vehicle, pk=vehicle_id)
                response_data['vehicle_plate'] = vehicle.plate_number
            except (ValueError, TypeError):
                 logger.warning(f"Invalid vehicle_id format in get_vehicle_capacity_ajax: {vehicle_id_str}")
            except Http404:
                 logger.warning(f"Vehicle not found for vehicle_id in get_vehicle_capacity_ajax: {vehicle_id_str}")

        return JsonResponse(response_data)
        
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Invalid product_id format. Must be an integer.'}, status=400)
    except Http404:
        return JsonResponse({'error': 'Product not found for the given product_id.'}, status=404)
    except Exception as e:
        logger.error(f"Error in get_vehicle_capacity_ajax: {e}", exc_info=True)
        return JsonResponse({'error': 'An unexpected server error occurred.'}, status=500)


@login_required
@permission_required('shipments.view_shipment', raise_exception=True)
@require_http_methods(["GET"])
def shipment_search_ajax(request):
    """AJAX endpoint for dynamic shipment search."""
    query = request.GET.get('q', '').strip()
    
    if len(query) < 2:
        return JsonResponse({'results': [], 'message': 'Query too short.'})
    
    try:
        accessible_shipments = get_user_accessible_shipments(request.user)
        matching_shipments = accessible_shipments.filter(
            Q(vessel_id_tag__icontains=query) | 
            Q(supplier_name__icontains=query)
        ).select_related('product').distinct()[:10]
        
        results = []
        for shipment in matching_shipments:
            results.append({
                'id': shipment.id,
                'text': f"{shipment.vessel_id_tag} ({shipment.product.name}) - Sup: {shipment.supplier_name} - Rem: {shipment.quantity_remaining:,.0f}L",
                'vessel_id_tag': shipment.vessel_id_tag,
                'supplier_name': shipment.supplier_name,
                'product_name': shipment.product.name,
                'quantity_remaining': float(shipment.quantity_remaining)
            })
        return JsonResponse({'results': results})
    except Exception as e:
        logger.error(f"Error in shipment_search_ajax for query '{query}': {e}", exc_info=True)
        return JsonResponse({'error': 'Shipment search failed due to a server error.'}, status=500)


@login_required
@permission_required('shipments.view_trip', raise_exception=True)
@require_http_methods(["GET"])
def trip_search_ajax(request):
    """AJAX endpoint for dynamic trip search."""
    query = request.GET.get('q', '').strip()
    
    if len(query) < 2:
        return JsonResponse({'results': [], 'message': 'Query too short.'})
    
    try:
        accessible_trips = get_user_accessible_trips(request.user)
        matching_trips = accessible_trips.filter(
            Q(kpc_order_number__icontains=query) | 
            Q(vehicle__plate_number__icontains=query) |
            Q(customer__name__icontains=query)
        ).select_related('vehicle', 'customer', 'product').distinct()[:10]
        
        results = []
        for trip in matching_trips:
            results.append({
                'id': trip.id,
                'text': f"{trip.kpc_order_number or f'Trip ID {trip.id}'} - V: {trip.vehicle.plate_number} - C: {trip.customer.name} ({trip.product.name}) - {trip.get_status_display()}",
                'kpc_order_number': trip.kpc_order_number,
                'vehicle_plate_number': trip.vehicle.plate_number,
                'customer_name': trip.customer.name,
                'product_name': trip.product.name,
                'status': trip.status,
                'status_display': trip.get_status_display()
            })
        return JsonResponse({'results': results})
    except Exception as e:
        logger.error(f"Error in trip_search_ajax for query '{query}': {e}", exc_info=True)
        return JsonResponse({'error': 'Trip search failed due to a server error.'}, status=500)


# --- Health Check and Error Handlers ---
@require_http_methods(["GET"])
def health_check(request):
    """Simple health check endpoint to verify application and database status."""
    try:
        db_ok = Product.objects.exists()
        return JsonResponse({
            'status': 'healthy',
            'timestamp': timezone.now().isoformat(),
            'database_connection': 'ok' if db_ok else 'issues_detected_but_no_exception', 
        })
    except Exception as e:
        logger.error(f"Health check failed: {e}", exc_info=True)
        return JsonResponse({
            'status': 'unhealthy',
            'timestamp': timezone.now().isoformat(),
            'error_message': str(e),
            'details': 'Failed to connect to database or perform essential checks.'
        }, status=503)


def handler404(request, exception):
    """Custom 404 error handler."""
    logger.warning(f"404 Not Found: Path='{request.path}', User='{request.user}', Exception='{exception}'")
    return render(request, '404.html', {'exception': exception}, status=404)


def handler500(request):
    """Custom 500 internal server error handler."""
    logger.error(f"500 Internal Server Error: Path='{request.path}', User='{request.user}'", exc_info=True)
    return render(request, '500.html', status=500)


def handler403(request, exception):
    """Custom 403 permission denied error handler."""
    logger.warning(f"403 Permission Denied: Path='{request.path}', User='{request.user}', Exception='{exception}'")
    return render(request, '403.html', {'exception': exception}, status=403)