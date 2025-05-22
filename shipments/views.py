# shipments/views.py
# (Keep all existing imports: django.shortcuts, ..., pdfplumber, re, .models, .forms)
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import login as auth_login
from django.db.models import Sum, Count, F, ExpressionWrapper, DecimalField, Max, Q 
from django.utils import timezone
import datetime 
from decimal import Decimal 
from django.db import transaction
from django.http import JsonResponse, HttpResponseForbidden
from django import forms
from calendar import monthrange
from collections import defaultdict
from django.core.exceptions import ValidationError

import pdfplumber
import re 

from .models import Shipment, Product, Customer, Vehicle, Trip, LoadingCompartment, ShipmentDepletion, Destination
from .forms import ShipmentForm, TripForm, LoadingCompartmentFormSet, PdfLoadingAuthorityUploadForm


def is_viewer_or_admin_or_superuser(user): # Unchanged
    if not user.is_authenticated: return False
    if user.is_superuser: return True
    if user.groups.filter(name__in=['Admin', 'Viewer']).exists(): return True
    return False

def is_admin_or_superuser(user): # Unchanged
    if not user.is_authenticated: return False
    if user.is_superuser: return True
    if user.groups.filter(name='Admin').exists(): return True
    return False

# --- Home View (Dashboard) ---
def home_view(request): # Unchanged from last working version
    # ... (The entire home_view with chart and booked stock logic) ...
    context = {
        'message': 'Welcome to the Fuel Tracker MVP!',
        'description': 'We are building this with Django and Tailwind CSS.',
        'is_authenticated_user': request.user.is_authenticated
    }

    if request.user.is_authenticated:
        can_view_shipments = request.user.has_perm('shipments.view_shipment')
        can_add_shipment = request.user.has_perm('shipments.add_shipment')
        can_change_shipment = request.user.has_perm('shipments.change_shipment')
        can_delete_shipment = request.user.has_perm('shipments.delete_shipment')
        can_view_product = request.user.has_perm('shipments.view_product')
        can_view_customer = request.user.has_perm('shipments.view_customer')
        can_view_vehicle = request.user.has_perm('shipments.view_vehicle')
        can_view_trip = request.user.has_perm('shipments.view_trip') 
        can_add_product = request.user.has_perm('shipments.add_product')
        can_add_customer = request.user.has_perm('shipments.add_customer')
        can_add_vehicle = request.user.has_perm('shipments.add_vehicle')
        can_add_trip = request.user.has_perm('shipments.add_trip')
        
        context.update({
             'can_view_shipments': can_view_shipments, 'can_add_shipment': can_add_shipment,
             'can_change_shipment': can_change_shipment, 'can_delete_shipment': can_delete_shipment,
             'can_view_product': can_view_product, 'can_view_customer': can_view_customer,
             'can_view_vehicle': can_view_vehicle, 'can_view_trip': can_view_trip,
             'can_add_product': can_add_product, 'can_add_customer': can_add_customer,
             'can_add_vehicle': can_add_vehicle, 'can_add_trip': can_add_trip,
        })

        show_global_data = is_viewer_or_admin_or_superuser(request.user)
        
        total_shipments_val = 0
        total_quantity_shipments_val = Decimal('0.00') 
        total_value_shipments_val = Decimal('0.00')   
        product_shipment_summary_for_avg_price = []
        total_trips_val = 0 
        total_quantity_loaded_val = Decimal('0.00')     
        trip_quantity_by_product_for_dispatch_summary = None
        
        aging_stock_notifications = [] 
        inactive_product_notifications = []
        utilized_shipment_notifications = [] 

        shipments_qs_base = Shipment.objects.all() if show_global_data else Shipment.objects.filter(user=request.user)
        trips_qs_base = Trip.objects.all() if show_global_data else Trip.objects.filter(user=request.user)
        depletions_qs_base = ShipmentDepletion.objects.all() if show_global_data else ShipmentDepletion.objects.filter(trip__user=request.user)

        if can_view_shipments:
            total_shipments_val = shipments_qs_base.count()
            total_quantity_shipments_agg = shipments_qs_base.aggregate(Sum('quantity_litres'))
            total_quantity_shipments_val = total_quantity_shipments_agg.get('quantity_litres__sum') or Decimal('0.00')
            
            current_total_value_shipments = Decimal('0.00')
            all_prods_for_avg = Product.objects.all().order_by('name')
            for prod in all_prods_for_avg:
                prod_shipments = shipments_qs_base.filter(product=prod)
                prod_total_litres = prod_shipments.aggregate(total_l=Sum('quantity_litres'))['total_l'] or Decimal('0.00')
                prod_total_cost_for_this_prod = Decimal('0.00') 
                for s_item_loop in prod_shipments: 
                    prod_total_cost_for_this_prod += s_item_loop.total_cost 
                current_total_value_shipments += prod_total_cost_for_this_prod
                avg_price = prod_total_cost_for_this_prod / prod_total_litres if prod_total_litres > 0 else Decimal('0.00')
                if prod_total_litres > 0 : 
                     product_shipment_summary_for_avg_price.append({'name': prod.name, 
                                                                    'total_litres': prod_total_litres, 
                                                                    'avg_price': avg_price})
            total_value_shipments_val = current_total_value_shipments

        if can_view_trip:
            delivered_trips_qs = trips_qs_base.filter(status='DELIVERED')
            total_trips_val = delivered_trips_qs.count()
            current_total_quantity_loaded_dispatch = Decimal('0.00')
            for trip_obj in delivered_trips_qs:
                current_total_quantity_loaded_dispatch += trip_obj.total_loaded
            total_quantity_loaded_val = current_total_quantity_loaded_dispatch
            
            trip_quantity_by_product_for_dispatch_summary = delivered_trips_qs.values('product__name').annotate(
                total_litres=Sum('depletions_for_trip__quantity_depleted')
            ).order_by('product__name')

        context.update({
            'total_shipments': total_shipments_val,
            'total_quantity_shipments': total_quantity_shipments_val,
            'total_value_shipments': total_value_shipments_val,
            'product_shipment_summary': product_shipment_summary_for_avg_price, 
            'total_trips': total_trips_val,
            'total_quantity_loaded': total_quantity_loaded_val,
            'trip_quantity_by_product': trip_quantity_by_product_for_dispatch_summary,
        })

        stock_by_product_detailed = {} 
        all_products_for_stock_calc = Product.objects.all().order_by('name')
        booked_statuses = ['PENDING', 'KPC_APPROVED', 'LOADING', 'LOADED'] 

        for product in all_products_for_stock_calc:
            shipped_val = Decimal('0.00')
            avg_price_val = Decimal('0.000')
            for summary_item in product_shipment_summary_for_avg_price:
                if summary_item['name'] == product.name:
                    shipped_val = summary_item['total_litres']
                    avg_price_val = summary_item['avg_price']
                    break
            
            dispatched_val = Decimal('0.00')
            if trip_quantity_by_product_for_dispatch_summary:
                for item in trip_quantity_by_product_for_dispatch_summary:
                    if item['product__name'] == product.name:
                        dispatched_val = item['total_litres'] or Decimal('0.00')
                        break
            
            physical_stock_for_display = shipped_val - dispatched_val

            booked_trips_for_product = trips_qs_base.filter(product=product, status__in=booked_statuses)
            current_booked_stock_for_product = Decimal('0.00')
            for trip_item in booked_trips_for_product:
                requested_for_trip = trip_item.requested_compartments.aggregate(
                    total_requested=Sum('quantity_requested_litres')
                )['total_requested'] or Decimal('0.00')
                current_booked_stock_for_product += requested_for_trip
            
            actual_physical_stock_from_db = shipments_qs_base.filter(product=product).aggregate(
                total_remaining=Sum('quantity_remaining')
            )['total_remaining'] or Decimal('0.00')
            
            net_available_stock_for_product = physical_stock_for_display - current_booked_stock_for_product 
            
            if physical_stock_for_display > 0 or current_booked_stock_for_product > 0 or shipped_val > 0 or dispatched_val > 0 or actual_physical_stock_from_db > 0:
                stock_by_product_detailed[product.name] = {
                    'shipped': shipped_val,             
                    'dispatched': dispatched_val,         
                    'physical_stock': physical_stock_for_display, 
                    'actual_physical_db': actual_physical_stock_from_db,
                    'booked_stock': current_booked_stock_for_product,
                    'net_available': net_available_stock_for_product,
                    'avg_price': avg_price_val     
                }
        context['stock_by_product_detailed'] = stock_by_product_detailed
        
        if can_view_shipments and can_view_trip: # Notification logic
            today_date_for_notifications = timezone.now().date(); age_threshold_days = 25; inactivity_threshold_days = 5
            for product_name_iter, stock_data in stock_by_product_detailed.items():
                if stock_data.get('actual_physical_db', Decimal('0.00')) > 0:
                    product_shipments_for_aging = shipments_qs_base.filter(product__name=product_name_iter, quantity_remaining__gt=0).order_by('import_date')
                    oldest_contributing_shipment_date = None; remaining_available_for_aging = stock_data.get('actual_physical_db', Decimal('0.00')) 
                    for sh_aging in product_shipments_for_aging:
                        if remaining_available_for_aging <= 0: break
                        if sh_aging.quantity_remaining >= remaining_available_for_aging: oldest_contributing_shipment_date = sh_aging.import_date; remaining_available_for_aging = Decimal('0.00'); break
                        else: oldest_contributing_shipment_date = sh_aging.import_date; remaining_available_for_aging -= sh_aging.quantity_remaining
                    if oldest_contributing_shipment_date:
                        days_old = (today_date_for_notifications - oldest_contributing_shipment_date).days
                        if days_old > age_threshold_days: aging_stock_notifications.append(f"Product '{product_name_iter}' has stock where the oldest batch is from {oldest_contributing_shipment_date.strftime('%Y-%m-%d')} ({days_old} days old).")
                    
                    product_depletions_qs = depletions_qs_base.filter(shipment_batch__product__name=product_name_iter)
                    most_recent_depletion = product_depletions_qs.aggregate(latest_depletion_date=Max('created_at'))
                    latest_depletion_date_val = most_recent_depletion.get('latest_depletion_date')
                    main_inactivity_message_for_product = None
                    if latest_depletion_date_val: 
                        days_since_last_depletion = (today_date_for_notifications - latest_depletion_date_val.date()).days
                        if days_since_last_depletion > inactivity_threshold_days: main_inactivity_message_for_product = f"Product '{product_name_iter}' has available stock but no overall dispatch in {days_since_last_depletion} days (last product dispatch: {latest_depletion_date_val.date().strftime('%Y-%m-%d')})."
                    else: 
                        oldest_shipment_with_stock = shipments_qs_base.filter(product__name=product_name_iter, quantity_remaining__gt=0).order_by('import_date').first()
                        if oldest_shipment_with_stock:
                            days_since_available = (today_date_for_notifications - oldest_shipment_with_stock.import_date).days
                            if days_since_available > inactivity_threshold_days: main_inactivity_message_for_product = f"Product '{product_name_iter}' has available stock and has never been dispatched (available for {days_since_available} days)."
                    if main_inactivity_message_for_product:
                        inactive_shipment_batches_details = []; contributing_shipments = shipments_qs_base.filter(product__name=product_name_iter, quantity_remaining__gt=0).order_by('import_date')
                        for sh_batch in contributing_shipments: inactive_shipment_batches_details.append({'id': sh_batch.id, 'import_date': sh_batch.import_date, 'supplier_name': sh_batch.supplier_name, 'quantity_remaining': sh_batch.quantity_remaining})
                        if inactive_shipment_batches_details: inactive_product_notifications.append({'product_name': product_name_iter, 'message': main_inactivity_message_for_product, 'shipments': inactive_shipment_batches_details})
            
            fully_utilized_shipments = shipments_qs_base.filter(quantity_remaining__lte=0, updated_at__date__lt=today_date_for_notifications).select_related('product').prefetch_related('depletions_from_batch').order_by('-updated_at')
            for sh_utilized in fully_utilized_shipments:
                latest_depletion_for_this_batch = sh_utilized.depletions_from_batch.aggregate(max_date=Max('created_at'))['max_date']
                date_it_became_utilized = sh_utilized.updated_at.date() 
                if latest_depletion_for_this_batch: date_it_became_utilized = latest_depletion_for_this_batch.date()
                days_since_utilized = (today_date_for_notifications - date_it_became_utilized).days
                if days_since_utilized >= 1: utilized_shipment_notifications.append({'id': sh_utilized.id, 'vessel_id_tag': getattr(sh_utilized, 'vessel_id_tag', '(No Tag)'), 'product_name': sh_utilized.product.name, 'supplier_name': sh_utilized.supplier_name, 'import_date': sh_utilized.import_date, 'utilized_date': date_it_became_utilized, 'days_since_utilized': days_since_utilized})
            if utilized_shipment_notifications: utilized_shipment_notifications.sort(key=lambda x: x['utilized_date'], reverse=True)
        
        context['aging_stock_notifications'] = aging_stock_notifications
        context['inactive_product_notifications'] = inactive_product_notifications
        context['utilized_shipment_notifications'] = utilized_shipment_notifications
        
        if not (can_view_shipments or can_view_trip or context.get('can_view_product') or context.get('can_view_customer') or context.get('can_view_vehicle')):
             context['description'] = 'You do not have permission to view fuel data. Contact admin for access.'
        
        loadings_chart_labels = []; pms_loadings_data = []; ago_loadings_data = []
        chart_today_date = timezone.now().date(); chart_trip_statuses = ['LOADED', 'GATEPASSED', 'TRANSIT', 'DELIVERED']
        for i in range(29, -1, -1): 
            current_day_for_chart = chart_today_date - datetime.timedelta(days=i)
            loadings_chart_labels.append(current_day_for_chart.strftime("%b %d"))
            daily_pms_total = Decimal('0.00'); daily_ago_total = Decimal('0.00')
            for trip_item in trips_qs_base.filter(loading_date=current_day_for_chart, status__in=chart_trip_statuses, product__name__iexact='PMS'): daily_pms_total += trip_item.total_loaded
            for trip_item in trips_qs_base.filter(loading_date=current_day_for_chart, status__in=chart_trip_statuses, product__name__iexact='AGO'): daily_ago_total += trip_item.total_loaded
            pms_loadings_data.append(float(daily_pms_total)); ago_loadings_data.append(float(daily_ago_total))
        context['loadings_chart_labels'] = loadings_chart_labels
        context['pms_loadings_data'] = pms_loadings_data; context['ago_loadings_data'] = ago_loadings_data
        
    return render(request, 'shipments/home.html', context)

# --- PDF Parsing Helper Function ---
def parse_loading_authority_pdf(pdf_file_obj, request_for_messages):
    # ... (This function remains unchanged from the last version) ...
    extracted_data = {'compartment_quantities_litres': [], 'total_quantity_litres': Decimal('0.00')}
    full_text = ""
    try:
        with pdfplumber.open(pdf_file_obj) as pdf:
            if not pdf.pages:
                if request_for_messages: messages.error(request_for_messages, "PDF is empty or unreadable.")
                return None
            for page_num, page in enumerate(pdf.pages):
                page_text = page.extract_text_simple(x_tolerance=1, y_tolerance=1) 
                if page_text: full_text += page_text + "\n"
        if not full_text.strip():
            if request_for_messages: messages.error(request_for_messages, "No text could be extracted from the PDF.")
            return None
        # print(f"--- Raw PDF Text (First 1000 chars) ---\n{full_text[:1000]}\n-----------------------------") # Keep for debugging if needed
        order_match = re.search(r"ORDER NUMBER\s*[:\-]?\s*([S\d]+)", full_text, re.IGNORECASE)
        if order_match: extracted_data['order_number'] = order_match.group(1).strip()
        else: print("DEBUG: Order Number not found")
        date_match = re.search(r"DATE\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{4})", full_text, re.IGNORECASE)
        if date_match:
            try: extracted_data['loading_date'] = datetime.datetime.strptime(date_match.group(1).strip(), '%m/%d/%Y').date()
            except ValueError: print(f"Could not parse date: {date_match.group(1)}")
        else: print("DEBUG: Date not found")
        dest_match = re.search(r"DESTINATION\s*[:\-]?\s*(.*?)(?=\s*ID NO:|\s*TRUCK:|\s*TRAILER:|\s*DRIVER:|$)", full_text, re.IGNORECASE | re.DOTALL)
        if dest_match: extracted_data['destination_name'] = dest_match.group(1).strip().split('\n')[0].strip()
        else: print("DEBUG: Destination not found")
        truck_match = re.search(r"TRUCK\s*[:\-]?\s*(.*?)(?=\s*TRAILER:|\s*DEPOT:|$)", full_text, re.IGNORECASE | re.DOTALL)
        if truck_match: extracted_data['truck_plate'] = truck_match.group(1).strip().split('\n')[0].strip()
        else: print("DEBUG: Truck plate not found")
        trailer_match = re.search(r"TRAILER\s*[:\-]?\s*(.*?)(?=\s*DRIVER:|\s*ID NO:|$)", full_text, re.IGNORECASE | re.DOTALL)
        if trailer_match: extracted_data['trailer_number'] = trailer_match.group(1).strip().split('\n')[0].strip()
        client_match = re.search(r"CLIENT\s*[:\-]?\s*(.*?)(?=\s*TRANSPORTER:|\s*DEPOT:|$)", full_text, re.IGNORECASE | re.DOTALL)
        if client_match: extracted_data['customer_name'] = client_match.group(1).strip().split('\n')[0].strip()
        else: print("DEBUG: Client/Customer not found")
        driver_match = re.search(r"DRIVER\s*[:\-]?\s*(.*?)(?=\s*ID NO:|\n|$)", full_text, re.IGNORECASE | re.DOTALL)
        if driver_match: extracted_data['driver_name'] = driver_match.group(1).strip().split('\n')[0].strip()
        driver_id_match = re.search(r"ID NO\s*[:\-]?\s*([+\d\sA-Z]+)(?=\n|$)", full_text, re.IGNORECASE | re.DOTALL)
        if driver_id_match: extracted_data['driver_id'] = driver_id_match.group(1).strip().split('\n')[0].strip()
        depot_match = re.search(r"DEPOT\s*[:\-]?\s*(.*?)(?=\s*DRIVER:|$)", full_text, re.IGNORECASE | re.DOTALL)
        if depot_match: extracted_data['depot_name'] = depot_match.group(1).strip().split('\n')[0].strip()
        header_match = re.search(r"DESCRIPTION\s+QUANTITY\s+UNIT OF MEASURE\s+COMPARTMENT", full_text, re.IGNORECASE)
        if header_match:
            # print("DEBUG: Table header found.") # Already confirmed working
            text_after_header = full_text[header_match.end():]
            product_line_pattern = re.compile(r"^\s*([A-Z\s\/-]+?)\s+([\d\.]+)\s+(m³|LITRES|L)\s*(?:m³\s*)?([\d:]*)\s*$", re.IGNORECASE | re.MULTILINE)
            found_product_line = False
            for line in text_after_header.split('\n'):
                line = line.strip()
                if not line or "Prepared by:" in line or "Authorized by:" in line:
                    if found_product_line: break
                    continue
                line_match = product_line_pattern.match(line)
                if line_match:
                    # print(f"DEBUG: Table line match on '{line}': Groups -> {line_match.groups()}") # Confirmed working
                    extracted_data['product_name'] = line_match.group(1).strip()
                    quantity_val_str = line_match.group(2).strip(); unit_of_measure = line_match.group(3).strip().upper()
                    compartment_str = line_match.group(4).strip() if line_match.group(4) else "" 
                    # print(f"DEBUG: Extracted compartment_str: '{compartment_str}'") # Confirmed working
                    try:
                        quantity_val = Decimal(quantity_val_str)
                        if unit_of_measure == 'M³': extracted_data['total_quantity_litres'] = quantity_val * Decimal('1000')
                        elif unit_of_measure in ['L', 'LITRES', 'LTRS', 'LTR']: extracted_data['total_quantity_litres'] = quantity_val
                        else: extracted_data['total_quantity_litres'] = quantity_val 
                    except Exception as e: print(f"Error converting quantity '{quantity_val_str}': {e}")
                    if compartment_str:
                        temp_compartment_quantities = []
                        for val_str in compartment_str.split(':'):
                            val_str_cleaned = val_str.strip()
                            if val_str_cleaned:
                                try: temp_compartment_quantities.append(Decimal(val_str_cleaned) * Decimal('1000'))
                                except Exception as e: print(f"Could not parse compartment value: '{val_str_cleaned}' due to {e}")
                        extracted_data['compartment_quantities_litres'] = temp_compartment_quantities
                    found_product_line = True; break 
    except Exception as e:
        if request_for_messages: messages.error(request_for_messages, f"Error processing PDF: {e}")
        return None
    required_fields = ['order_number', 'loading_date', 'destination_name', 'truck_plate', 'customer_name', 'product_name']
    missing_fields = [field for field in required_fields if not extracted_data.get(field)]
    if extracted_data.get('total_quantity_litres', Decimal('0.00')) <= Decimal('0.00') :
        if 'total_quantity_litres' not in missing_fields: missing_fields.append('total_quantity_litres (must be > 0)')
    if missing_fields:
        if request_for_messages: messages.error(request_for_messages, f"Essential data missing from PDF: {', '.join(missing_fields)}. Trip not created.")
        return None
    # print("--- Successfully Parsed PDF Data (or attempted) ---"); [print(f"  {key}: {value}") for key, value in extracted_data.items()]; print("------------------------------------") # Keep for debugging
    return extracted_data

@login_required
@permission_required('shipments.add_trip')
def upload_loading_authority_view(request):
    if request.method == 'POST':
        form = PdfLoadingAuthorityUploadForm(request.POST, request.FILES)
        if form.is_valid():
            pdf_file = request.FILES['pdf_file']
            if not pdf_file.name.lower().endswith('.pdf'): messages.error(request, "Invalid file type. Please upload a PDF.")
            else:
                parsed_data = parse_loading_authority_pdf(pdf_file, request)
                if parsed_data:
                    try:
                        with transaction.atomic():
                            product_name_parsed = parsed_data.get('product_name'); customer_name_parsed = parsed_data.get('customer_name')
                            truck_plate_parsed = parsed_data.get('truck_plate'); destination_name_parsed = parsed_data.get('destination_name')
                            kpc_order_no_parsed = parsed_data.get('order_number') # Use this for kpc_order_number field

                            # Ensure essential fields for get_or_create are present
                            if not product_name_parsed: raise ValidationError("Product name missing from PDF for Product model.")
                            if not customer_name_parsed: raise ValidationError("Customer name missing from PDF for Customer model.")
                            if not truck_plate_parsed: raise ValidationError("Truck plate missing from PDF for Vehicle model.")
                            if not destination_name_parsed: raise ValidationError("Destination missing from PDF for Destination model.")
                            if not kpc_order_no_parsed: raise ValidationError("KPC Order Number (Sxxxxx) not found in PDF.")

                            product, _ = Product.objects.get_or_create(name__iexact=product_name_parsed, defaults={'name': product_name_parsed.upper()})
                            customer, _ = Customer.objects.get_or_create(name__iexact=customer_name_parsed, defaults={'name': customer_name_parsed})
                            vehicle, _ = Vehicle.objects.get_or_create(plate_number__iexact=truck_plate_parsed, defaults={'plate_number': truck_plate_parsed.upper()})
                            if parsed_data.get('trailer_number') and vehicle.trailer_number != parsed_data.get('trailer_number'): # Update trailer if different
                                vehicle.trailer_number = parsed_data.get('trailer_number'); vehicle.save(update_fields=['trailer_number'])
                            destination, _ = Destination.objects.get_or_create(name__iexact=destination_name_parsed, defaults={'name': destination_name_parsed})
                            
                            # Check for existing Trip by KPC Order Number
                            if Trip.objects.filter(kpc_order_number__iexact=kpc_order_no_parsed).exists():
                                existing_trip = Trip.objects.get(kpc_order_number__iexact=kpc_order_no_parsed)
                                messages.warning(request, f"Trip with KPC Order No '{kpc_order_no_parsed}' already exists (ID: {existing_trip.id}). PDF not processed again.")
                                return redirect(reverse('shipments:trip-detail', kwargs={'pk': existing_trip.pk}))

                            trip_instance = Trip.objects.create(
                                user=request.user, vehicle=vehicle, customer=customer, product=product, destination=destination,
                                loading_date=parsed_data.get('loading_date', timezone.now().date()),
                                loading_time=parsed_data.get('loading_time', datetime.time(0,0)), 
                                kpc_order_number=kpc_order_no_parsed, # CORRECTED: Store Sxxxx in kpc_order_number
                                bol_number=None, # Final BoL number is initially None
                                status='PENDING', 
                                notes=f"Created from PDF: {pdf_file.name}. Driver: {parsed_data.get('driver_name', 'N/A')}",
                                kpc_comments=f"Depot: {parsed_data.get('depot_name','N/A')}. Driver ID: {parsed_data.get('driver_id','N/A')}" 
                            )

                            compartment_quantities = parsed_data.get('compartment_quantities_litres', [])
                            pdf_total_qty_litres = parsed_data.get('total_quantity_litres', Decimal('0.00'))
                            if compartment_quantities:
                                for i, qty_l in enumerate(compartment_quantities):
                                    if qty_l > 0: LoadingCompartment.objects.create(trip=trip_instance, compartment_number=i + 1, quantity_requested_litres=qty_l)
                                sum_comp_qty = sum(compartment_quantities)
                                if abs(sum_comp_qty - pdf_total_qty_litres) > Decimal('1.0'): messages.warning(request, f"For Trip {kpc_order_no_parsed}: Sum of compartment quantities ({sum_comp_qty}L) differs from total quantity in PDF ({pdf_total_qty_litres}L). Check PDF data or compartment parsing.")
                            elif pdf_total_qty_litres > 0 : 
                                 LoadingCompartment.objects.create(trip=trip_instance, compartment_number=1, quantity_requested_litres=pdf_total_qty_litres)
                                 messages.info(request, f"For Trip {kpc_order_no_parsed}: No compartment breakdown in PDF. Total quantity {pdf_total_qty_litres}L assigned to Compartment 1.")
                            
                            messages.success(request, f"Trip for KPC Order No '{kpc_order_no_parsed}' (ID: {trip_instance.id}) created from PDF.")
                            return redirect(reverse('shipments:trip-detail', kwargs={'pk': trip_instance.pk}))
                    except ValidationError as e: messages.error(request, f"Data validation error creating Trip: {e.args[0] if hasattr(e, 'args') and e.args else str(e)}")
                    except Exception as e: messages.error(request, f"An unexpected error occurred while creating Trip: {str(e)}")
    else: form = PdfLoadingAuthorityUploadForm()
    return render(request, 'shipments/upload_loading_authority.html', {'form': form, 'page_title': 'Upload Loading Authority'})

# --- (The rest of your views.py from file #13 - shipment_list_view onwards - goes here, UNCHANGED) ---
# ... (Make sure to paste the full original content of the remaining views)
@login_required
@permission_required('shipments.view_shipment', raise_exception=True)
def shipment_list_view(request):
    if is_viewer_or_admin_or_superuser(request.user): queryset = Shipment.objects.all()
    else: queryset = Shipment.objects.filter(user=request.user)
    product_filter_pk = request.GET.get('product', '').strip()
    supplier_filter = request.GET.get('supplier_name', '').strip()
    start_date_str = request.GET.get('start_date', '').strip()
    end_date_str = request.GET.get('end_date', '').strip()
    if product_filter_pk: queryset = queryset.filter(product__pk=product_filter_pk)
    if supplier_filter: queryset = queryset.filter(supplier_name__icontains=supplier_filter)
    start_date = None; end_date = None
    if start_date_str:
        try:
            start_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d').date()
            queryset = queryset.filter(import_date__gte=start_date)
        except ValueError: messages.error(request, "Invalid start date format.")
    if end_date_str:
         try:
            end_date = datetime.datetime.strptime(end_date_str, '%Y-%m-%d').date()
            queryset = queryset.filter(import_date__lte=end_date)
         except ValueError: messages.error(request, "Invalid end date format.")
    shipments = queryset.order_by('import_date', 'created_at') 
    products = Product.objects.all().order_by('name')
    context = {
        'shipments': shipments, 
        'product_filter_value': product_filter_pk,
        'supplier_filter_value': supplier_filter, 'start_date_filter_value': start_date_str,
        'end_date_filter_value': end_date_str, 'products': products,
        'can_add_shipment': request.user.has_perm('shipments.add_shipment'),
        'can_change_shipment': request.user.has_perm('shipments.change_shipment'),
        'can_delete_shipment': request.user.has_perm('shipments.delete_shipment'),
    }
    return render(request, 'shipments/shipment_list.html', context)

@login_required
@permission_required('shipments.add_shipment', raise_exception=True)
def shipment_add_view(request):
    if request.method == 'POST':
        form = ShipmentForm(request.POST) 
        if form.is_valid():
            shipment_instance = form.save(commit=False)
            shipment_instance.user = request.user
            # quantity_remaining set by model save method
            shipment_instance.save()
            messages.success(request, 'Shipment added successfully!')
            return redirect(reverse('shipments:shipment-list'))
    else: form = ShipmentForm()
    context = { 'form': form, 'page_title': 'Add New Shipment', 'can_add_shipment': request.user.has_perm('shipments.add_shipment'),}
    return render(request, 'shipments/shipment_form.html', context)

@login_required
def shipment_edit_view(request, pk):
    if is_admin_or_superuser(request.user):
        shipment_instance = get_object_or_404(Shipment, pk=pk)
    else:
        shipment_instance = get_object_or_404(Shipment.objects.filter(user=request.user), pk=pk)
    can_change = request.user.has_perm('shipments.change_shipment')
    is_owner_or_admin = is_admin_or_superuser(request.user) or shipment_instance.user == request.user
    if not (can_change and is_owner_or_admin):
         return HttpResponseForbidden("You do not have permission to edit this shipment.")
    depleted_quantity = ShipmentDepletion.objects.filter(shipment_batch=shipment_instance).aggregate(Sum('quantity_depleted'))['quantity_depleted__sum'] or Decimal('0.00')
    if request.method == 'POST':
        form = ShipmentForm(request.POST, instance=shipment_instance) 
        if form.is_valid():
            new_quantity_litres = form.cleaned_data.get('quantity_litres', Decimal('0.00')) 
            if new_quantity_litres < depleted_quantity:
                form.add_error('quantity_litres', f"Cannot reduce quantity below what has already been depleted ({depleted_quantity}L).")
            else:
                shipment_to_save = form.save(commit=False)
                shipment_to_save.quantity_remaining = new_quantity_litres - depleted_quantity
                shipment_to_save.save()
                messages.success(request, f"Shipment '{shipment_instance.product.name}' updated successfully!")
                return redirect(reverse('shipments:shipment-list'))
    else: form = ShipmentForm(instance=shipment_instance)
    if depleted_quantity > 0:
         messages.warning(request, f"Warning: {depleted_quantity}L from this shipment has already been used. Reducing total quantity may be restricted.")
    context = {
        'form': form, 'page_title': f'Edit Shipment: {shipment_instance.product.name}',
        'shipment_instance': shipment_instance, 
        'depleted_quantity': depleted_quantity,
        'can_change_shipment': can_change,
        'can_delete_shipment': request.user.has_perm('shipments.delete_shipment'),}
    return render(request, 'shipments/shipment_form.html', context)

@login_required
def shipment_delete_view(request, pk):
    if is_admin_or_superuser(request.user): shipment_instance = get_object_or_404(Shipment, pk=pk)
    else: shipment_instance = get_object_or_404(Shipment.objects.filter(user=request.user), pk=pk)
    can_delete = request.user.has_perm('shipments.delete_shipment')
    is_owner_or_admin = is_admin_or_superuser(request.user) or shipment_instance.user == request.user
    if not (can_delete and is_owner_or_admin): return HttpResponseForbidden("You do not have permission to delete this shipment.")
    if ShipmentDepletion.objects.filter(shipment_batch=shipment_instance).exists():
        messages.error(request, f"Cannot delete Shipment '{shipment_instance}'. It has already been used. Adjust loadings first.")
        return redirect(reverse('shipments:shipment-list'))
    if request.method == 'POST':
        shipment_name = shipment_instance.product.name; shipment_instance.delete()
        messages.success(request, f"Shipment '{shipment_name}' deleted successfully!")
        return redirect(reverse('shipments:shipment-list'))
    context = {'shipment': shipment_instance, 'page_title': f'Confirm Delete: {shipment_instance.product.name}'}
    return render(request, 'shipments/shipment_confirm_delete.html', context)

@login_required
def shipment_detail_view(request, pk):
    if is_viewer_or_admin_or_superuser(request.user): shipment = get_object_or_404(Shipment, pk=pk)
    else: shipment = get_object_or_404(Shipment.objects.filter(user=request.user), pk=pk)
    if not request.user.has_perm('shipments.view_shipment'):
         if not (is_viewer_or_admin_or_superuser(request.user) or shipment.user == request.user): return HttpResponseForbidden("You do not have permission to view this shipment.")
         elif not request.user.has_perm('shipments.view_shipment'): return HttpResponseForbidden("You do not have permission to view shipments.")
    context = {'shipment': shipment, 'page_title': f'Shipment Details: {shipment.product.name}', 'can_change_shipment': request.user.has_perm('shipments.change_shipment'), 'can_delete_shipment': request.user.has_perm('shipments.delete_shipment'),}
    return render(request, 'shipments/shipment_detail.html', context)

@login_required
@permission_required('shipments.view_trip', raise_exception=True)
def trip_list_view(request):
    if is_viewer_or_admin_or_superuser(request.user): queryset = Trip.objects.all()
    else: queryset = Trip.objects.filter(user=request.user)
    product_filter_pk = request.GET.get('product', '').strip(); customer_filter_pk = request.GET.get('customer', '').strip()
    vehicle_filter_pk = request.GET.get('vehicle', '').strip(); status_filter = request.GET.get('status', '').strip()
    start_date_str = request.GET.get('start_date', '').strip(); end_date_str = request.GET.get('end_date', '').strip()
    if product_filter_pk: queryset = queryset.filter(product__pk=product_filter_pk)
    if customer_filter_pk: queryset = queryset.filter(customer__pk=customer_filter_pk)
    if vehicle_filter_pk: queryset = queryset.filter(vehicle__pk=vehicle_filter_pk)
    if status_filter: queryset = queryset.filter(status=status_filter)
    if start_date_str:
        try: queryset = queryset.filter(loading_date__gte=datetime.datetime.strptime(start_date_str, '%Y-%m-%d').date())
        except ValueError: messages.error(request, "Invalid start date format for trips.")
    if end_date_str:
        try: queryset = queryset.filter(loading_date__lte=datetime.datetime.strptime(end_date_str, '%Y-%m-%d').date())
        except ValueError: messages.error(request, "Invalid end date format for trips.")
    filtered_trip_count = queryset.count()
    filtered_total_loaded_agg = ShipmentDepletion.objects.filter(trip__in=queryset).aggregate(total_loaded_sum=Sum('quantity_depleted'))
    filtered_total_loaded = filtered_total_loaded_agg.get('total_loaded_sum') or Decimal('0.00')
    trips = queryset.order_by('-loading_date', '-loading_time')
    products = Product.objects.all().order_by('name'); customers = Customer.objects.all().order_by('name')
    vehicles = Vehicle.objects.all().order_by('plate_number'); status_choices = Trip.STATUS_CHOICES
    context = { 'trips': trips, 'products': products, 'customers': customers, 'vehicles': vehicles, 'status_choices': status_choices, 'product_filter_value': product_filter_pk, 'customer_filter_value': customer_filter_pk, 'vehicle_filter_value': vehicle_filter_pk, 'status_filter_value': status_filter, 'start_date_filter_value': start_date_str, 'end_date_filter_value': end_date_str, 'filtered_trip_count': filtered_trip_count, 'filtered_total_loaded': filtered_total_loaded, 'can_add_trip': request.user.has_perm('shipments.add_trip'), 'can_change_trip': request.user.has_perm('shipments.change_trip'), 'can_delete_trip': request.user.has_perm('shipments.delete_trip'), 'can_view_trip': request.user.has_perm('shipments.view_trip'),}
    return render(request, 'shipments/trip_list.html', context)

@login_required
@permission_required('shipments.add_trip', raise_exception=True)
def trip_add_view(request):
    if request.method == 'POST':
        trip_form = TripForm(request.POST) 
        compartment_formset = LoadingCompartmentFormSet(request.POST, prefix='compartments')
        if trip_form.is_valid() and compartment_formset.is_valid():
            try:
                with transaction.atomic():
                    trip_instance = trip_form.save(commit=False)
                    trip_instance.user = request.user
                    trip_instance.save() 
                    compartment_formset.instance = trip_instance
                    compartment_formset.save()
                    messages.success(request, f'Loading {trip_instance.bol_number or trip_instance.kpc_order_number} successfully recorded. Status: {trip_instance.get_status_display()}.')
                    return redirect(reverse('shipments:trip-detail', kwargs={'pk': trip_instance.pk}))
            except ValidationError as e:
                error_dict = e.message_dict if hasattr(e, 'message_dict') else {'__all__': getattr(e, 'messages', [str(e)])}
                for field, errors_list in error_dict.items():
                    for error_item in errors_list:
                        if field == '__all__' or not hasattr(trip_form, field) or field == 'status': messages.error(request, error_item) 
                        elif hasattr(trip_form, field): trip_form.add_error(field, error_item)
                        else: messages.error(request, f"Error: {error_item}")
                messages.error(request, 'Please correct the errors highlighted.')
            except Exception as e:
                messages.error(request, f'An unexpected error occurred: {str(e)}')
        else: 
            messages.error(request, 'Please correct the form errors below (main form or compartments).')
            # print("Trip Form Errors (Add View):", trip_form.errors.as_json(escape_html=True)) # Keep for debug
            # print("Compartment Formset Errors (Add View):", compartment_formset.errors)
    else: 
        trip_form = TripForm()
        initial_compartments = [{'compartment_number': i+1} for i in range(3)]
        compartment_formset = LoadingCompartmentFormSet(prefix='compartments', initial=initial_compartments)
    context = { 'trip_form': trip_form, 'compartment_formset': compartment_formset, 'page_title': 'Record New Loading', 'can_add_trip': request.user.has_perm('shipments.add_trip'),}
    return render(request, 'shipments/trip_form.html', context)

@login_required
def trip_edit_view(request, pk):
    if is_admin_or_superuser(request.user): trip_instance = get_object_or_404(Trip, pk=pk)
    else: trip_instance = get_object_or_404(Trip.objects.filter(user=request.user), pk=pk)
    if not request.user.has_perm('shipments.change_trip'):
        if not (is_admin_or_superuser(request.user) or trip_instance.user == request.user): return HttpResponseForbidden("You do not have permission to edit this loading.")
        elif not request.user.has_perm('shipments.change_trip'): return HttpResponseForbidden("You do not have permission to edit loadings.")

    original_product = trip_instance.product
    original_destination = trip_instance.destination
    original_total_requested = trip_instance.total_requested_from_compartments 
    original_status_from_db = trip_instance.status 
    
    if request.method == 'POST':
        trip_form = TripForm(request.POST, instance=trip_instance) 
        compartment_formset = LoadingCompartmentFormSet(request.POST, instance=trip_instance, prefix='compartments')
        
        if trip_form.is_valid() and compartment_formset.is_valid():
            try:
                with transaction.atomic():
                    new_product = trip_form.cleaned_data.get('product')
                    new_destination = trip_form.cleaned_data.get('destination')
                    newly_calculated_total_requested = Decimal('0.00')
                    for form_comp in compartment_formset.cleaned_data:
                        if form_comp and not form_comp.get('DELETE', False):
                            newly_calculated_total_requested += form_comp.get('quantity_requested_litres', Decimal('0.00'))
                    
                    critical_change_affecting_stock = (new_product != original_product or new_destination != original_destination or newly_calculated_total_requested != original_total_requested)

                    if critical_change_affecting_stock and original_status_from_db in Trip.DEPLETED_STOCK_STATUSES:
                        print(f"Trip {pk}: Critical fields/quantities changed. Reversing previous depletions before update.")
                        if not trip_instance.reverse_stock_depletion():
                            raise ValidationError("Failed to reverse existing stock depletions. Edit cannot proceed safely.")
                    
                    updated_trip_instance = trip_form.save(commit=False)
                    updated_trip_instance.user = request.user 
                    updated_trip_instance.save() 
                    
                    compartment_formset.instance = updated_trip_instance
                    compartment_formset.save()

                    messages.success(request, f"Loading '{updated_trip_instance}' updated successfully!")
                    return redirect(reverse('shipments:trip-detail', kwargs={'pk': updated_trip_instance.pk}))
            except ValidationError as e:
                error_dict = e.message_dict if hasattr(e, 'message_dict') else {'__all__': getattr(e, 'messages', [str(e)])}
                for field, errors_list in error_dict.items():
                    for error_item in errors_list:
                        if field == '__all__' or not hasattr(trip_form, field) or field == 'status': messages.error(request, error_item) 
                        elif hasattr(trip_form, field): trip_form.add_error(field, error_item)
                        else: messages.error(request, f"Error: {error_item}")
                messages.error(request, 'Please correct the errors highlighted.')
            except Exception as e:
                messages.error(request, f'An unexpected error occurred during update: {str(e)}')
        else: 
            messages.error(request, 'Please correct the form errors below (main form or compartments).')
            # print("Trip Form Errors (Edit View):", trip_form.errors.as_json(escape_html=True)) # Keep for debug
            # print("Compartment Formset Errors (Edit View):", compartment_formset.errors)
    else: 
        trip_form = TripForm(instance=trip_instance)
        compartment_formset = LoadingCompartmentFormSet(instance=trip_instance, prefix='compartments')
    context = { 'trip_form': trip_form, 'compartment_formset': compartment_formset, 'page_title': f'Edit Loading: Trip {trip_instance.id}', 'trip': trip_instance, 'can_change_trip': request.user.has_perm('shipments.change_trip'),}
    return render(request, 'shipments/trip_form.html', context)

@login_required
def trip_delete_view(request, pk):
    # ... (Your original trip_delete_view from file #13, ensuring trip_instance.reverse_stock_depletion() is called) ...
    if is_admin_or_superuser(request.user): trip_instance = get_object_or_404(Trip, pk=pk)
    else: trip_instance = get_object_or_404(Trip.objects.filter(user=request.user), pk=pk)
    if not request.user.has_perm('shipments.delete_trip'):
        if not (is_admin_or_superuser(request.user) or trip_instance.user == request.user): return HttpResponseForbidden("You do not have permission to delete this loading.")
        elif not request.user.has_perm('shipments.delete_trip'): return HttpResponseForbidden("You do not have permission to delete loadings.")
    if request.method == 'POST':
        try:
            with transaction.atomic():
                trip_instance.reverse_stock_depletion() 
                trip_desc = str(trip_instance); trip_instance.delete()
                messages.success(request, f"Loading '{trip_desc}' and its stock depletions reverted and deleted successfully!")
                return redirect(reverse('shipments:trip-list'))
        except Exception as e: messages.error(request, f"Error during deletion: {str(e)}"); return redirect(reverse('shipments:trip-detail', kwargs={'pk': pk})) 
    context = {'trip': trip_instance, 'page_title': f'Confirm Delete Loading: {trip_instance}'}
    return render(request, 'shipments/trip_confirm_delete.html', context)

@login_required
def trip_detail_view(request, pk):
    # ... (Your original trip_detail_view from file #13) ...
    if is_viewer_or_admin_or_superuser(request.user): trip = get_object_or_404(Trip, pk=pk)
    else: trip = get_object_or_404(Trip.objects.filter(user=request.user), pk=pk)
    if not request.user.has_perm('shipments.view_trip'):
         if not (is_viewer_or_admin_or_superuser(request.user) or trip.user == request.user): return HttpResponseForbidden("You do not have permission to view this loading.")
         elif not request.user.has_perm('shipments.view_trip'): return HttpResponseForbidden("You do not have permission to view loadings.")
    requested_compartments = trip.requested_compartments.all().order_by('compartment_number')
    actual_depletions = trip.depletions_for_trip.select_related('shipment_batch', 'shipment_batch__product').order_by('shipment_batch__import_date', 'pk')
    context = {'trip': trip, 'compartments': requested_compartments, 'actual_depletions': actual_depletions, 'page_title': f'Loading Details: Trip {trip.id}', 'can_change_trip': request.user.has_perm('shipments.change_trip'), 'can_delete_trip': request.user.has_perm('shipments.delete_trip'),}
    return render(request, 'shipments/trip_detail.html', context)

@login_required
@permission_required('shipments.view_trip', raise_exception=True)
def truck_activity_dashboard_view(request):
    # ... (Your original truck_activity_dashboard_view from file #13) ...
    show_global_data = is_viewer_or_admin_or_superuser(request.user)
    if show_global_data: base_queryset = Trip.objects.all()
    else: base_queryset = Trip.objects.filter(user=request.user)
    product_filter_pk = request.GET.get('product', '').strip(); customer_filter_pk = request.GET.get('customer', '').strip()
    vehicle_filter_pk = request.GET.get('vehicle', '').strip(); status_filter = request.GET.get('status', '').strip()
    start_date_str = request.GET.get('start_date', '').strip(); end_date_str = request.GET.get('end_date', '').strip()
    if product_filter_pk: base_queryset = base_queryset.filter(product__pk=product_filter_pk)
    if customer_filter_pk: base_queryset = base_queryset.filter(customer__pk=customer_filter_pk)
    if vehicle_filter_pk: base_queryset = base_queryset.filter(vehicle__pk=vehicle_filter_pk)
    if status_filter: base_queryset = base_queryset.filter(status=status_filter)
    if start_date_str:
        try: base_queryset = base_queryset.filter(loading_date__gte=datetime.datetime.strptime(start_date_str, '%Y-%m-%d').date())
        except ValueError: messages.error(request, "Invalid start date format.")
    if end_date_str:
        try: base_queryset = base_queryset.filter(loading_date__lte=datetime.datetime.strptime(end_date_str, '%Y-%m-%d').date())
        except ValueError: messages.error(request, "Invalid end date format.")
    truck_activities = defaultdict(lambda: {'trips': [], 'total_quantity': 0, 'trip_count': 0})
    filtered_trips = base_queryset.select_related('vehicle', 'product', 'customer', 'user').prefetch_related('depletions_for_trip')
    for trip in filtered_trips:
        vehicle_obj = trip.vehicle; truck_activities[vehicle_obj]['trips'].append(trip)
        current_trip_total_depleted = sum(dep.quantity_depleted for dep in trip.depletions_for_trip.all())
        truck_activities[vehicle_obj]['total_quantity'] += current_trip_total_depleted
        truck_activities[vehicle_obj]['trip_count'] += 1
    sorted_truck_activities = dict(sorted(truck_activities.items(), key=lambda item: item[0].plate_number))
    products = Product.objects.all().order_by('name'); customers = Customer.objects.all().order_by('name')
    vehicles = Vehicle.objects.all().order_by('plate_number'); status_choices = Trip.STATUS_CHOICES
    context = { 'truck_activities': sorted_truck_activities, 'products': products, 'customers': customers, 'vehicles': vehicles, 'status_choices': status_choices, 'product_filter_value': product_filter_pk, 'customer_filter_value': customer_filter_pk, 'vehicle_filter_value': vehicle_filter_pk, 'status_filter_value': status_filter, 'start_date_filter_value': start_date_str, 'end_date_filter_value': end_date_str, 'page_title': 'Truck Activity Dashboard'}
    return render(request, 'shipments/truck_activity_dashboard.html', context)

def signup_view(request):
    # ... (Your original signup_view from file #13) ...
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid(): user = form.save(); auth_login(request, user); messages.success(request, 'Account created successfully! You are now logged in.'); return redirect(reverse('shipments:home'))
    else: form = UserCreationForm()
    context = { 'form': form, 'page_title': 'Sign Up'}
    return render(request, 'registration/signup.html', context)

@login_required
@permission_required('shipments.view_shipment', raise_exception=True)
@permission_required('shipments.view_trip', raise_exception=True)
def monthly_stock_summary_view(request):
    # ... (Your original monthly_stock_summary_view from file #13) ...
    show_global_data = is_viewer_or_admin_or_superuser(request.user)
    years_with_data_q1 = Shipment.objects.dates('import_date', 'year', order='DESC'); years_with_data_q2 = Trip.objects.dates('loading_date', 'year', order='DESC')
    available_years = sorted(list(set( [d.year for d in years_with_data_q1] + [d.year for d in years_with_data_q2] )), reverse=True)
    if not available_years: available_years.append(datetime.date.today().year)
    months_for_dropdown = []
    for i in range(1, 13): months_for_dropdown.append((i, datetime.date(2000, i, 1).strftime('%B')))
    current_year = datetime.date.today().year; current_month = datetime.date.today().month
    selected_year_str = request.GET.get('year', str(current_year)); selected_month_str = request.GET.get('month', str(current_month))
    try:
        selected_year = int(selected_year_str); selected_month = int(selected_month_str)
        if not (1 <= selected_month <= 12 and 1900 < selected_year < 2200): raise ValueError("Invalid year or month")
    except (ValueError, TypeError): messages.error(request, "Invalid year or month selected. Defaulting to current period."); selected_year = current_year; selected_month = current_month
    start_of_selected_month = datetime.date(selected_year, selected_month, 1); num_days_in_month = monthrange(selected_year, selected_month)[1]
    end_of_selected_month = datetime.date(selected_year, selected_month, num_days_in_month); summary_data = []
    all_products = Product.objects.all().order_by('name')
    for product_obj in all_products:
        base_shipments_qs_product = Shipment.objects.filter(product=product_obj); base_depletions_qs_product = ShipmentDepletion.objects.filter(shipment_batch__product=product_obj)
        if not show_global_data: base_shipments_qs_product = base_shipments_qs_product.filter(user=request.user); base_depletions_qs_product = base_depletions_qs_product.filter(trip__user=request.user)
        total_shipped_before = base_shipments_qs_product.filter(import_date__lt=start_of_selected_month).aggregate(s=Sum('quantity_litres'))['s'] or 0
        total_depleted_before = base_depletions_qs_product.filter(created_at__date__lt=start_of_selected_month).aggregate(s=Sum('quantity_depleted'))['s'] or 0
        opening_stock = total_shipped_before - total_depleted_before
        stock_in_month = base_shipments_qs_product.filter(import_date__gte=start_of_selected_month, import_date__lte=end_of_selected_month).aggregate(s=Sum('quantity_litres'))['s'] or 0
        stock_out_month = base_depletions_qs_product.filter(created_at__date__gte=start_of_selected_month, created_at__date__lte=end_of_selected_month).aggregate(s=Sum('quantity_depleted'))['s'] or 0
        closing_stock = opening_stock + stock_in_month - stock_out_month
        summary_data.append({'product_name': product_obj.name, 'opening_stock': opening_stock, 'stock_in_month': stock_in_month, 'stock_out_month': stock_out_month, 'closing_stock': closing_stock,})
    context = {'summary_data': summary_data, 'selected_year': selected_year, 'selected_month': selected_month, 'month_name_display': datetime.date(1900, selected_month, 1).strftime('%B'), 'available_years': available_years, 'months_for_dropdown': months_for_dropdown, 'page_title': f'Monthly Stock Summary - {datetime.date(1900, selected_month, 1).strftime("%B")} {selected_year}'}
    return render(request, 'shipments/monthly_stock_summary.html', context)