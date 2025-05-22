# shipments/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import login as auth_login, get_user_model
from django.contrib.auth.models import Group # For signup default group
from django.db.models import Sum, Count, F, ExpressionWrapper, DecimalField as DjangoDecimalField, Max, Q, Value
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

User = get_user_model()

# --- Helper Functions for Permissions (used for data scoping) ---
def is_viewer_or_admin_or_superuser(user):
    if not user.is_authenticated: return False
    if user.is_superuser: return True
    if user.groups.filter(name__in=['Admin', 'Viewer']).exists(): return True
    return False

def is_admin_or_superuser(user):
    if not user.is_authenticated: return False
    if user.is_superuser: return True
    if user.groups.filter(name='Admin').exists(): return True
    return False

# --- Home View (Dashboard) ---
@login_required
def home_view(request):
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
         'can_add_shipment': can_add_shipment, # For "Add New Shipment" button
         'can_view_trip': can_view_trip,
         # Adding other view perms needed for nav links, if any
         'can_view_product': request.user.has_perm('shipments.view_product'), 
         'can_view_customer': request.user.has_perm('shipments.view_customer'), 
         'can_view_vehicle': request.user.has_perm('shipments.view_vehicle'),
         'can_add_trip': request.user.has_perm('shipments.add_trip') # For "Add New Loading" button
    })

    show_global_data = is_viewer_or_admin_or_superuser(request.user)
    # Base querysets: if not global view, filter by user. Adjust if data ownership is different.
    shipments_qs_base = Shipment.objects.all() if show_global_data else Shipment.objects.filter(user=request.user)
    # Eager load related fields for trips_qs_base to optimize dashboard queries
    trips_qs_base = Trip.objects.select_related(
        'product', 'destination', 'vehicle', 'customer', 'user'
    ).prefetch_related(
        'requested_compartments', 
        'depletions_for_trip__shipment_batch__product' # For total_loaded property and other summaries
    ).all() if show_global_data else Trip.objects.select_related(
        'product', 'destination', 'vehicle', 'customer', 'user'
    ).prefetch_related(
        'requested_compartments', 
        'depletions_for_trip__shipment_batch__product'
    ).filter(user=request.user)


    # Overall Received Stats
    total_shipments_val = 0
    total_quantity_shipments_val = Decimal('0.00')
    total_value_shipments_val = Decimal('0.00')
    if can_view_shipments:
        total_shipments_val = shipments_qs_base.count()
        total_quantity_shipments_agg = shipments_qs_base.aggregate(Sum('quantity_litres'))
        total_quantity_shipments_val = total_quantity_shipments_agg.get('quantity_litres__sum') or Decimal('0.00')
        total_value_shipments_val = shipments_qs_base.annotate(
            calculated_total_cost=F('quantity_litres') * F('price_per_litre')
        ).aggregate(total_value=Sum('calculated_total_cost'))['total_value'] or Decimal('0.00')

    # Overall Dispatched Stats (Final customer delivery)
    total_trips_dispatched_final = 0
    total_quantity_loaded_final_dispatch = Decimal('0.00')
    trip_quantity_by_product_final_dispatch = None # This is specifically for the "Total Dispatched by Product" card
    if can_view_trip:
        final_dispatched_trips_qs = trips_qs_base.filter(status='DELIVERED')
        total_trips_dispatched_final = final_dispatched_trips_qs.count()
        
        current_total_qty_final_dispatch = Decimal('0.00')
        for trip_fd in final_dispatched_trips_qs: # Iterate over pre-fetched trips
            current_total_qty_final_dispatch += trip_fd.total_loaded # Use the property
        total_quantity_loaded_final_dispatch = current_total_qty_final_dispatch
        
        # For the "Total Dispatched by Product" summary card (uses ShipmentDepletion for delivered trips)
        # This specific aggregation might be okay as is, or could also leverage pre-fetched data if complex
        trip_quantity_by_product_final_dispatch = ShipmentDepletion.objects.filter(
            trip__in=final_dispatched_trips_qs 
        ).values('shipment_batch__product__name').annotate(
            total_litres=Sum('quantity_depleted')
        ).order_by('shipment_batch__product__name')
        context['trip_quantity_by_product'] = trip_quantity_by_product_final_dispatch

    context.update({
        'total_shipments': total_shipments_val,
        'total_quantity_shipments': total_quantity_shipments_val,
        'total_value_shipments': total_value_shipments_val,
        'total_trips': total_trips_dispatched_final, 
        'total_quantity_loaded': total_quantity_loaded_final_dispatch,
    })

    # Product Stock Summary section
    stock_by_product_detailed = {}
    all_products_for_stock_calc = Product.objects.all().order_by('name')
    
    committed_not_delivered_statuses = ['PENDING', 'KPC_APPROVED', 'LOADING', 'LOADED', 'GATEPASSED', 'TRANSIT'] 

    for product_obj in all_products_for_stock_calc:
        total_received_for_product = shipments_qs_base.filter(product=product_obj).aggregate(
            total_qty=Sum('quantity_litres')
        )['total_qty'] or Decimal('0.00')

        total_delivered_for_product_val = Decimal('0.00')
        if trip_quantity_by_product_final_dispatch: 
            for item in trip_quantity_by_product_final_dispatch:
                if item['shipment_batch__product__name'] == product_obj.name:
                    total_delivered_for_product_val = item['total_litres'] or Decimal('0.00')
                    break
        
        physical_stock_calc = total_received_for_product - total_delivered_for_product_val

        current_committed_stock_for_product = Decimal('0.00')
        relevant_trips_for_product = trips_qs_base.filter(product=product_obj, status__in=committed_not_delivered_statuses)
        
        for trip_item in relevant_trips_for_product: # Iterate over already fetched & filtered trips
            if trip_item.status == 'LOADED':
                if trip_item.total_actual_l20_from_compartments > Decimal('0.00'):
                     current_committed_stock_for_product += trip_item.total_actual_l20_from_compartments
                elif trip_item.total_loaded > Decimal('0.00'): 
                     current_committed_stock_for_product += trip_item.total_loaded
                else: 
                     current_committed_stock_for_product += trip_item.total_requested_from_compartments
            elif trip_item.status in ['GATEPASSED', 'TRANSIT']:
                 current_committed_stock_for_product += trip_item.total_loaded 
            else: 
                current_committed_stock_for_product += trip_item.total_requested_from_compartments
        
        net_available_stock_for_product = physical_stock_calc - current_committed_stock_for_product

        total_cost_for_product = shipments_qs_base.filter(product=product_obj).annotate(
            line_total=F('quantity_litres') * F('price_per_litre')
        ).aggregate(total_value=Sum('line_total'))['total_value'] or Decimal('0.00')
        avg_price_val = total_cost_for_product / total_received_for_product if total_received_for_product > 0 else Decimal('0.000')

        if total_received_for_product > 0 or total_delivered_for_product_val > 0 or physical_stock_calc != 0 or current_committed_stock_for_product != 0 :
            stock_by_product_detailed[product_obj.name] = {
                'shipped': total_received_for_product,
                'dispatched': total_delivered_for_product_val, 
                'physical_stock': physical_stock_calc,
                'booked_stock': current_committed_stock_for_product, 
                'net_available': net_available_stock_for_product,
                'avg_price': avg_price_val
            }
    context['stock_by_product_detailed'] = stock_by_product_detailed
    
    # --- Notifications Logic ---
    aging_stock_notifications = []
    inactive_product_notifications = []
    utilized_shipment_notifications = []
    if can_view_shipments: # Assuming notifications are primarily related to shipments
        today_date_for_notifications = timezone.now().date()
        age_threshold_days = 25 
        inactivity_threshold_days = 5
        utilized_threshold_days = 7

        aging_shipments = shipments_qs_base.filter(
            quantity_remaining__gt=0,
            import_date__lte=today_date_for_notifications - datetime.timedelta(days=age_threshold_days)
        ).select_related('product', 'destination').order_by('import_date')
        for sh in aging_shipments:
            days_old = (today_date_for_notifications - sh.import_date).days
            dest_info = f" for {sh.destination.name}" if sh.destination else ""
            aging_stock_notifications.append(
                f"Shipment '{sh.vessel_id_tag}' ({sh.product.name}{dest_info}) "
                f"imported on {sh.import_date.strftime('%Y-%m-%d')} ({days_old} days old) "
                f"still has {sh.quantity_remaining}L remaining."
            )

        for product_obj_notify in Product.objects.all():
            product_shipments_with_stock_qs = shipments_qs_base.filter(product=product_obj_notify, quantity_remaining__gt=0)
            if product_shipments_with_stock_qs.exists():
                last_depletion_date_for_product = ShipmentDepletion.objects.filter(
                    shipment_batch__product=product_obj_notify,
                    **( {'trip__user': request.user} if not show_global_data else {} ) 
                ).aggregate(max_date=Max('created_at'))['max_date']

                is_inactive_notify = False; days_inactive_notify = 0; message_notify = ""
                if last_depletion_date_for_product:
                    days_inactive_notify = (today_date_for_notifications - last_depletion_date_for_product.date()).days
                    if days_inactive_notify > inactivity_threshold_days:
                        is_inactive_notify = True
                        message_notify = (f"Product '{product_obj_notify.name}' has stock "
                                   f"but no dispatch in {days_inactive_notify} days (last: {last_depletion_date_for_product.date().strftime('%Y-%m-%d')}).")
                else: 
                    oldest_stock_date_notify = product_shipments_with_stock_qs.order_by('import_date').first().import_date
                    days_inactive_notify = (today_date_for_notifications - oldest_stock_date_notify).days
                    if days_inactive_notify > inactivity_threshold_days:
                        is_inactive_notify = True
                        message_notify = (f"Product '{product_obj_notify.name}' has stock (oldest from {oldest_stock_date_notify.strftime('%Y-%m-%d')}) "
                                   f"and no dispatch in {days_inactive_notify} days (or ever).")
                if is_inactive_notify:
                    idle_batches_info_notify = []
                    for batch_notify in product_shipments_with_stock_qs.order_by('import_date'): # Use the filtered queryset
                        idle_batches_info_notify.append({
                            'id': batch_notify.id, 'vessel_id_tag': batch_notify.vessel_id_tag,
                            'import_date': batch_notify.import_date, 'supplier_name': batch_notify.supplier_name,
                            'quantity_remaining': batch_notify.quantity_remaining,
                            'destination_name': batch_notify.destination.name if batch_notify.destination else "Any"
                        })
                    inactive_product_notifications.append({'message': message_notify, 'shipments': idle_batches_info_notify})

        potentially_utilized_shipments = shipments_qs_base.filter(
            quantity_remaining__lte=Decimal('0.00'),
            updated_at__date__lte=today_date_for_notifications - datetime.timedelta(days=utilized_threshold_days)
        ).select_related('product').order_by('updated_at')
        for sh_utilized in potentially_utilized_shipments:
            last_depletion_for_batch = ShipmentDepletion.objects.filter(shipment_batch=sh_utilized).aggregate(max_date=Max('created_at'))['max_date']
            date_it_became_utilized = sh_utilized.updated_at.date()
            if last_depletion_for_batch and last_depletion_for_batch.date() > date_it_became_utilized :
                date_it_became_utilized = last_depletion_for_batch.date()
            days_since_utilized = (today_date_for_notifications - date_it_became_utilized).days
            if days_since_utilized >= utilized_threshold_days:
                 utilized_shipment_notifications.append({
                     'id': sh_utilized.id, 'vessel_id_tag': sh_utilized.vessel_id_tag,
                     'product_name': sh_utilized.product.name, 'supplier_name': sh_utilized.supplier_name,
                     'import_date': sh_utilized.import_date, 'utilized_date': date_it_became_utilized,
                     'days_since_utilized': days_since_utilized })
        if utilized_shipment_notifications:
            utilized_shipment_notifications.sort(key=lambda x: x['utilized_date'], reverse=False) 

    context['aging_stock_notifications'] = aging_stock_notifications
    context['inactive_product_notifications'] = inactive_product_notifications
    context['utilized_shipment_notifications'] = utilized_shipment_notifications
    
    loadings_chart_labels = []; pms_loadings_data = []; ago_loadings_data = []
    if can_view_trip:
        chart_today_date = timezone.now().date()
        chart_trip_statuses = ['LOADED', 'GATEPASSED', 'TRANSIT', 'DELIVERED'] 
        
        # Use the pre-fetched trips_qs_base for chart data calculation for efficiency
        trips_for_chart = trips_qs_base.filter(status__in=chart_trip_statuses)

        daily_totals_pms = defaultdict(Decimal)
        daily_totals_ago = defaultdict(Decimal)

        for trip_chart in trips_for_chart:
            # Iterate through its depletions (which are pre-fetched if done right)
            # For the chart, we want the date the depletion effectively happened,
            # which is often tied to trip.loading_date for simplicity or depletion.created_at.date()
            chart_date = trip_chart.loading_date # Or depletion.created_at.date() if more granular
            
            # Check if date is within last 30 days
            if (chart_today_date - chart_date).days < 30:
                # Sum depletions for this trip
                trip_total_depleted_for_chart = trip_chart.total_loaded # Uses pre-fetched depletions

                if trip_chart.product.name.upper() == 'PMS':
                    daily_totals_pms[chart_date] += trip_total_depleted_for_chart
                elif trip_chart.product.name.upper() == 'AGO':
                    daily_totals_ago[chart_date] += trip_total_depleted_for_chart
        
        for i in range(29, -1, -1): 
            current_day_for_chart = chart_today_date - datetime.timedelta(days=i)
            loadings_chart_labels.append(current_day_for_chart.strftime("%b %d")) 
            pms_loadings_data.append(float(daily_totals_pms.get(current_day_for_chart, Decimal('0.00'))))
            ago_loadings_data.append(float(daily_totals_ago.get(current_day_for_chart, Decimal('0.00'))))
            
    context['loadings_chart_labels'] = loadings_chart_labels
    context['pms_loadings_data'] = pms_loadings_data
    context['ago_loadings_data'] = ago_loadings_data
    
    if not (can_view_shipments or can_view_trip or context.get('can_view_product') or context.get('can_view_customer') or context.get('can_view_vehicle')):
         context['description'] = 'You do not have permission to view fuel data. Contact admin for access.'

    return render(request, 'shipments/home.html', context)

# --- PDF Parsing Helper Function ---
def parse_loading_authority_pdf(pdf_file_obj, request_for_messages=None):
    # (This function should be the complete version from file #19 / our previous full exchange)
    # (Ensure it's the one with detailed regex and error handling)
    extracted_data = {'compartment_quantities_litres': [], 'total_quantity_litres': Decimal('0.00')}
    full_text = ""
    try:
        with pdfplumber.open(pdf_file_obj) as pdf:
            if not pdf.pages:
                if request_for_messages: messages.error(request_for_messages, "PDF is empty or unreadable.")
                return None
            for page_num, page in enumerate(pdf.pages):
                page_text = page.extract_text_simple(x_tolerance=1.5, y_tolerance=1.5) 
                if page_text:
                    full_text += page_text + "\n"
        if not full_text.strip():
            if request_for_messages: messages.error(request_for_messages, "No text could be extracted from the PDF.")
            return None
        
        order_match = re.search(r"ORDER\s+NUMBER\s*[:\-]?\s*(S\d+)", full_text, re.IGNORECASE)
        if order_match: extracted_data['order_number'] = order_match.group(1).strip().upper()
        
        date_match = re.search(r"DATE\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{4})", full_text, re.IGNORECASE)
        if date_match:
            try: extracted_data['loading_date'] = datetime.datetime.strptime(date_match.group(1).strip(), '%d/%m/%Y').date()
            except ValueError:
                try: extracted_data['loading_date'] = datetime.datetime.strptime(date_match.group(1).strip(), '%m/%d/%Y').date()
                except ValueError: print(f"DEBUG PDF PARSE (Auth): Could not parse date: {date_match.group(1)}")
        
        dest_match = re.search(r"DESTINATION\s*[:\-]?\s*(.*?)(?=\s*ID NO:|\s*TRUCK\s*NO:|\s*TRAILER\s*NO:|\s*DRIVER:|$)", full_text, re.IGNORECASE | re.DOTALL)
        if dest_match: extracted_data['destination_name'] = dest_match.group(1).replace('\n', ' ').strip()

        truck_match = re.search(r"TRUCK\s*(?:NO|PLATE)?\s*[:\-]?\s*([A-Z0-9\s\-]+?)(?=\s*TRAILER|\s*DEPOT|\s*DRIVER|$)", full_text, re.IGNORECASE | re.DOTALL)
        if truck_match: extracted_data['truck_plate'] = truck_match.group(1).replace('\n', ' ').strip().upper()

        trailer_match = re.search(r"TRAILER\s*(?:NO|PLATE)?\s*[:\-]?\s*([A-Z0-9\s\-]*?)(?=\s*DRIVER|\s*ID NO|\s*DEPOT|$)", full_text, re.IGNORECASE | re.DOTALL)
        if trailer_match and trailer_match.group(1).strip(): extracted_data['trailer_number'] = trailer_match.group(1).replace('\n', ' ').strip().upper()

        client_match = re.search(r"CLIENT\s*[:\-]?\s*(.*?)(?=\s*TRANSPORTER|\s*DEPOT|\s*PRODUCT\s*DESCRIPTION|$)", full_text, re.IGNORECASE | re.DOTALL)
        if client_match: extracted_data['customer_name'] = client_match.group(1).replace('\n', ' ').strip()

        header_match = re.search(r"DESCRIPTION\s+QUANTITY\s+(?:UNIT\s+OF\s+MEASURE|UOM)\s+COMPARTMENT", full_text, re.IGNORECASE)
        if header_match:
            text_after_header = full_text[header_match.end():]
            product_line_pattern = re.compile(
                r"^\s*(.+?)\s{2,}([\d\.,]+)\s+(m³|litres|l|ltrs)\s*(?:m³\s*)?([\d:\s]*?)\s*$",
                re.IGNORECASE | re.MULTILINE
            )
            found_product_line = False
            for line in text_after_header.split('\n'):
                line_cleaned = line.strip()
                if not line_cleaned or "Prepared by:" in line_cleaned or "Authorized by:" in line_cleaned or "TOTAL" in line_cleaned.upper():
                    if found_product_line: break
                    continue
                line_match = product_line_pattern.match(line_cleaned)
                if line_match:
                    extracted_data['product_name'] = line_match.group(1).strip()
                    quantity_val_str = line_match.group(2).strip().replace(',', '')
                    unit_of_measure = line_match.group(3).strip().upper()
                    compartment_str = line_match.group(4).strip() if line_match.group(4) else ""
                    try:
                        quantity_val = Decimal(quantity_val_str)
                        if unit_of_measure == 'M³': extracted_data['total_quantity_litres'] = quantity_val * Decimal('1000')
                        elif unit_of_measure in ['L', 'LITRES', 'LTRS', 'LTR']: extracted_data['total_quantity_litres'] = quantity_val
                        else: extracted_data['total_quantity_litres'] = quantity_val
                    except Exception as e: print(f"Error converting quantity '{quantity_val_str}': {e}")
                    if compartment_str:
                        temp_compartment_quantities = []
                        raw_compartment_values = re.split(r'[:\s]+', compartment_str)
                        for val_str_comp in raw_compartment_values:
                            val_str_cleaned_comp = val_str_comp.strip()
                            if val_str_cleaned_comp:
                                try:
                                    comp_qty_litres = Decimal(val_str_cleaned_comp.replace(',', '')) * Decimal('1000')
                                    temp_compartment_quantities.append(comp_qty_litres)
                                except Exception as e: print(f"Could not parse compartment value: '{val_str_cleaned_comp}' due to {e}")
                        if temp_compartment_quantities: extracted_data['compartment_quantities_litres'] = temp_compartment_quantities
                    found_product_line = True; break 
        
        if extracted_data.get('total_quantity_litres', Decimal('0.00')) <= Decimal('0.00'):
            total_qty_match = re.search(r"TOTAL\s+QUANTITY\s*[:\-]?\s*([\d\.,]+)\s*(M³|LITRES|L)", full_text, re.IGNORECASE | re.DOTALL)
            if total_qty_match:
                qty_str = total_qty_match.group(1).strip().replace(',', ''); unit = total_qty_match.group(2).strip().upper()
                try:
                    total_qty_val = Decimal(qty_str)
                    if unit == 'M³': extracted_data['total_quantity_litres'] = total_qty_val * Decimal('1000')
                    else: extracted_data['total_quantity_litres'] = total_qty_val
                except: pass
        
        driver_match = re.search(r"DRIVER\s*[:\-]?\s*(.*?)(?=\s*ID NO:|\n|$)", full_text, re.IGNORECASE | re.DOTALL)
        if driver_match: extracted_data['driver_name'] = driver_match.group(1).replace('\n',' ').strip()
        driver_id_match = re.search(r"ID NO\s*[:\-]?\s*([A-Z0-9\s\/\-]+?)(?=\s*DEPOT:|\s*PRODUCT\s*DESCRIPTION:|\n|$)", full_text, re.IGNORECASE | re.DOTALL)
        if driver_id_match: extracted_data['driver_id'] = driver_id_match.group(1).replace('\n',' ').strip()
        depot_match = re.search(r"DEPOT\s*[:\-]?\s*(.*?)(?=\s*SEAL NOS:|\s*DRIVER:|\n|$)", full_text, re.IGNORECASE | re.DOTALL)
        if depot_match: extracted_data['depot_name'] = depot_match.group(1).replace('\n',' ').strip()

    except Exception as e:
        if request_for_messages: messages.error(request_for_messages, f"Error processing PDF: {e}")
        return None
    
    required_fields_for_trip = ['order_number', 'loading_date', 'destination_name', 'truck_plate', 'customer_name', 'product_name']
    missing_fields = [field for field in required_fields_for_trip if not extracted_data.get(field)]
    if extracted_data.get('total_quantity_litres', Decimal('0.00')) <= Decimal('0.00') and not extracted_data.get('compartment_quantities_litres'):
        if 'total_quantity_litres' not in missing_fields: missing_fields.append('total_quantity_litres (must be > 0 or have compartment data)')
    if missing_fields:
        if request_for_messages: messages.error(request_for_messages, f"Essential data missing from PDF: {', '.join(missing_fields)}. Trip not created.")
        return None
    return extracted_data


@login_required
@permission_required('shipments.add_trip', raise_exception=True)
def upload_loading_authority_view(request):
    if request.method == 'POST':
        form = PdfLoadingAuthorityUploadForm(request.POST, request.FILES)
        if form.is_valid():
            pdf_file = request.FILES['pdf_file']
            if not pdf_file.name.lower().endswith('.pdf'):
                messages.error(request, "Invalid file type. Please upload a PDF.")
            else:
                parsed_data = parse_loading_authority_pdf(pdf_file, request)
                if parsed_data:
                    try:
                        with transaction.atomic():
                            product_name_parsed = parsed_data.get('product_name')
                            customer_name_parsed = parsed_data.get('customer_name')
                            truck_plate_parsed = parsed_data.get('truck_plate')
                            destination_name_parsed = parsed_data.get('destination_name')
                            kpc_order_no_parsed = parsed_data.get('order_number')

                            if not all([product_name_parsed, customer_name_parsed, truck_plate_parsed, destination_name_parsed, kpc_order_no_parsed]):
                                raise ValidationError("One or more essential fields (Product, Customer, Truck, Destination, KPC Order No) were not found in the PDF.")

                            product, _ = Product.objects.get_or_create(name__iexact=product_name_parsed, defaults={'name': product_name_parsed.upper()})
                            customer, _ = Customer.objects.get_or_create(name__iexact=customer_name_parsed, defaults={'name': customer_name_parsed})
                            vehicle, _ = Vehicle.objects.get_or_create(plate_number__iexact=truck_plate_parsed, defaults={'plate_number': truck_plate_parsed.upper()})
                            if parsed_data.get('trailer_number') and vehicle.trailer_number != parsed_data.get('trailer_number'):
                                vehicle.trailer_number = parsed_data.get('trailer_number'); vehicle.save(update_fields=['trailer_number'])
                            destination, _ = Destination.objects.get_or_create(name__iexact=destination_name_parsed, defaults={'name': destination_name_parsed})
                            
                            if Trip.objects.filter(kpc_order_number__iexact=kpc_order_no_parsed).exists():
                                existing_trip = Trip.objects.get(kpc_order_number__iexact=kpc_order_no_parsed)
                                messages.warning(request, f"Trip with KPC Order No '{kpc_order_no_parsed}' already exists (ID: {existing_trip.id}). PDF not processed again.")
                                return redirect(reverse('shipments:trip-detail', kwargs={'pk': existing_trip.pk}))

                            trip_instance = Trip( # Use Trip() constructor then save()
                                user=request.user, vehicle=vehicle, customer=customer, product=product, destination=destination,
                                loading_date=parsed_data.get('loading_date', timezone.now().date()),
                                loading_time=parsed_data.get('loading_time', datetime.time(0,0)), 
                                kpc_order_number=kpc_order_no_parsed,
                                status='PENDING', 
                                notes=f"Created from PDF: {pdf_file.name}. Driver: {parsed_data.get('driver_name', 'N/A')}",
                                kpc_comments=f"Depot: {parsed_data.get('depot_name','N/A')}. Driver ID: {parsed_data.get('driver_id','N/A')}" 
                            )
                            # Pass stdout from the view to the model's save method if needed for detailed logging
                            # For now, model's save will use its default PrintLikeCommandOutput
                            trip_instance.save(stdout=DefaultCommandOutput()) # Pass a dummy stdout if needed or make it optional in save

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
    if start_date_str:
        try: queryset = queryset.filter(import_date__gte=datetime.datetime.strptime(start_date_str, '%Y-%m-%d').date())
        except ValueError: messages.error(request, "Invalid start date format.")
    if end_date_str:
         try: queryset = queryset.filter(import_date__lte=datetime.datetime.strptime(end_date_str, '%Y-%m-%d').date())
         except ValueError: messages.error(request, "Invalid end date format.")
    shipments = queryset.select_related('product', 'destination', 'user').order_by('-import_date', '-created_at')
    products_for_filter = Product.objects.all().order_by('name')
    context = {
        'shipments': shipments, 'products': products_for_filter,
        'product_filter_value': product_filter_pk, 'supplier_filter_value': supplier_filter,
        'start_date_filter_value': start_date_str, 'end_date_filter_value': end_date_str,
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
            try:
                with transaction.atomic():
                    shipment_instance = form.save(commit=False)
                    shipment_instance.user = request.user
                    shipment_instance.save() # Model's save() handles initial quantity_remaining
                    messages.success(request, 'Shipment added successfully!')
                    return redirect(reverse('shipments:shipment-detail', kwargs={'pk': shipment_instance.pk}))
            except ValidationError as e: form.add_error(None, e); messages.error(request, "Error saving shipment.")
            except Exception as e: messages.error(request, f"An unexpected error occurred: {e}")
    else: form = ShipmentForm()
    context = { 'form': form, 'page_title': 'Add New Shipment'}
    return render(request, 'shipments/shipment_form.html', context)

@login_required
def shipment_edit_view(request, pk):
    if is_admin_or_superuser(request.user): shipment_instance = get_object_or_404(Shipment, pk=pk)
    else: shipment_instance = get_object_or_404(Shipment.objects.filter(user=request.user), pk=pk)
    if not request.user.has_perm('shipments.change_shipment'): return HttpResponseForbidden("You do not have permission to change shipments.")
    
    depleted_quantity = ShipmentDepletion.objects.filter(shipment_batch=shipment_instance).aggregate(total_depleted=Sum('quantity_depleted'))['total_depleted'] or Decimal('0.00')
    if request.method == 'POST':
        form = ShipmentForm(request.POST, instance=shipment_instance) 
        if form.is_valid():
            new_quantity_litres = form.cleaned_data.get('quantity_litres', shipment_instance.quantity_litres)
            if new_quantity_litres < depleted_quantity:
                form.add_error('quantity_litres', f"Cannot reduce total quantity ({new_quantity_litres}L) below what has already been depleted ({depleted_quantity}L).")
            else:
                try:
                    with transaction.atomic():
                        shipment_to_save = form.save(commit=False)
                        shipment_to_save.quantity_remaining = new_quantity_litres - depleted_quantity
                        shipment_to_save.save()
                        messages.success(request, f"Shipment '{shipment_to_save.vessel_id_tag}' updated successfully!")
                        return redirect(reverse('shipments:shipment-detail', kwargs={'pk': shipment_to_save.pk}))
                except ValidationError as e: form.add_error(None, e); messages.error(request, "Error updating shipment.")
                except Exception as e: messages.error(request, f"An unexpected error: {e}")
    else: form = ShipmentForm(instance=shipment_instance)
    if depleted_quantity > 0: messages.info(request, f"Note: {depleted_quantity}L from this shipment has been used. Total quantity cannot be set lower.")
    context = {
        'form': form, 'page_title': f'Edit Shipment: {shipment_instance.vessel_id_tag}',
        'shipment_instance': shipment_instance, 'depleted_quantity': depleted_quantity,
        'can_delete_shipment': request.user.has_perm('shipments.delete_shipment') and (is_admin_or_superuser(request.user) or shipment_instance.user == request.user),
    }
    return render(request, 'shipments/shipment_form.html', context)

@login_required
def shipment_delete_view(request, pk):
    if is_admin_or_superuser(request.user): shipment_instance = get_object_or_404(Shipment, pk=pk)
    else: shipment_instance = get_object_or_404(Shipment.objects.filter(user=request.user), pk=pk)
    if not request.user.has_perm('shipments.delete_shipment'): return HttpResponseForbidden("You do not have permission to delete shipments.")
    if ShipmentDepletion.objects.filter(shipment_batch=shipment_instance).exists():
        messages.error(request, f"Cannot delete Shipment '{shipment_instance.vessel_id_tag}'. It has associated loadings. Adjust loadings first.")
        return redirect(reverse('shipments:shipment-detail', kwargs={'pk': shipment_instance.pk}))
    if request.method == 'POST':
        try:
            shipment_tag = shipment_instance.vessel_id_tag; shipment_instance.delete()
            messages.success(request, f"Shipment '{shipment_tag}' deleted successfully!")
            return redirect(reverse('shipments:shipment-list'))
        except Exception as e: messages.error(request, f"Error deleting: {e}"); return redirect(reverse('shipments:shipment-detail', kwargs={'pk': pk})) 
    context = {'shipment': shipment_instance, 'page_title': f'Confirm Delete: {shipment_instance.vessel_id_tag}'}
    return render(request, 'shipments/shipment_confirm_delete.html', context)

@login_required
def shipment_detail_view(request, pk):
    if is_viewer_or_admin_or_superuser(request.user): shipment = get_object_or_404(Shipment.objects.select_related('user', 'product', 'destination'), pk=pk)
    else: shipment = get_object_or_404(Shipment.objects.filter(user=request.user).select_related('user', 'product', 'destination'), pk=pk)
    if not request.user.has_perm('shipments.view_shipment') and not (is_viewer_or_admin_or_superuser(request.user) or shipment.user == request.user):
        return HttpResponseForbidden("You do not have permission to view this shipment.")
    context = {
        'shipment': shipment, 'page_title': f'Shipment Details: {shipment.vessel_id_tag}',
        'can_change_shipment': request.user.has_perm('shipments.change_shipment') and (is_admin_or_superuser(request.user) or shipment.user == request.user),
        'can_delete_shipment': request.user.has_perm('shipments.delete_shipment') and (is_admin_or_superuser(request.user) or shipment.user == request.user),
    }
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
    # Calculate total loaded by iterating through the filtered queryset and using the `total_loaded` property
    filtered_total_loaded_val = sum(trip.total_loaded for trip in queryset.prefetch_related('depletions_for_trip'))

    trips = queryset.select_related('vehicle', 'customer', 'product', 'destination', 'user').order_by('-loading_date', '-loading_time')
    
    products_for_filter = Product.objects.all().order_by('name'); customers_for_filter = Customer.objects.all().order_by('name')
    vehicles_for_filter = Vehicle.objects.all().order_by('plate_number'); status_choices_for_filter = Trip.STATUS_CHOICES
    context = { 
        'trips': trips, 'products': products_for_filter, 'customers': customers_for_filter, 
        'vehicles': vehicles_for_filter, 'status_choices': status_choices_for_filter, 
        'product_filter_value': product_filter_pk, 'customer_filter_value': customer_filter_pk, 
        'vehicle_filter_value': vehicle_filter_pk, 'status_filter_value': status_filter, 
        'start_date_filter_value': start_date_str, 'end_date_filter_value': end_date_str, 
        'filtered_trip_count': filtered_trip_count, 'filtered_total_loaded': filtered_total_loaded_val, 
        'can_add_trip': request.user.has_perm('shipments.add_trip'), 
        'can_change_trip': request.user.has_perm('shipments.change_trip'), 
        'can_delete_trip': request.user.has_perm('shipments.delete_trip'),
    }
    return render(request, 'shipments/trip_list.html', context)

class DefaultCommandOutput: # For passing to model save method from views
    def write(self, msg, style_func=None): print(msg)
    class style: SUCCESS = lambda x: x; ERROR = lambda x: x; WARNING = lambda x: x; NOTICE = lambda x: x
    style = style()

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
                    # Pass a simple stdout to the model's save method
                    trip_instance.save(stdout=DefaultCommandOutput()) 
                    
                    compartment_formset.instance = trip_instance
                    compartment_formset.save()
                    messages.success(request, f'Loading for KPC Order {trip_instance.kpc_order_number} recorded. Status: {trip_instance.get_status_display()}.')
                    return redirect(reverse('shipments:trip-detail', kwargs={'pk': trip_instance.pk}))
            except ValidationError as e:
                error_dict = e.message_dict if hasattr(e, 'message_dict') else {'__all__': getattr(e, 'messages', [str(e)])}
                for field, errors_list in error_dict.items():
                    for error_item in errors_list:
                        if field == '__all__' or not hasattr(trip_form, field) or field == 'status': messages.error(request, error_item) 
                        elif hasattr(trip_form, field): trip_form.add_error(field, error_item)
                        else: messages.error(request, f"Error: {error_item}")
                messages.error(request, 'Please correct the errors highlighted.')
            except Exception as e: messages.error(request, f'An unexpected error occurred: {str(e)}')
        else: messages.error(request, 'Please correct the form errors (main or compartments).')
    else: 
        trip_form = TripForm()
        initial_compartments = [{'compartment_number': i+1} for i in range(3)]
        compartment_formset = LoadingCompartmentFormSet(prefix='compartments', initial=initial_compartments)
    context = { 'trip_form': trip_form, 'compartment_formset': compartment_formset, 'page_title': 'Record New Loading'}
    return render(request, 'shipments/trip_form.html', context)

@login_required
def trip_edit_view(request, pk):
    if is_admin_or_superuser(request.user): trip_instance = get_object_or_404(Trip, pk=pk)
    else: trip_instance = get_object_or_404(Trip.objects.filter(user=request.user), pk=pk)
    if not request.user.has_perm('shipments.change_trip'): return HttpResponseForbidden("You do not have permission to change loadings.")

    if request.method == 'POST':
        trip_form = TripForm(request.POST, instance=trip_instance) 
        compartment_formset = LoadingCompartmentFormSet(request.POST, instance=trip_instance, prefix='compartments')
        if trip_form.is_valid() and compartment_formset.is_valid():
            try:
                with transaction.atomic():
                    # No need to manually call reverse_stock_depletion here; Trip.save() will handle it.
                    updated_trip_instance = trip_form.save(commit=False)
                    updated_trip_instance.save(stdout=DefaultCommandOutput()) # This triggers complex save logic in model
                    
                    compartment_formset.instance = updated_trip_instance # Ensure formset is linked to the potentially refreshed instance
                    compartment_formset.save()
                    messages.success(request, f"Loading '{updated_trip_instance.kpc_order_number}' updated successfully!")
                    return redirect(reverse('shipments:trip-detail', kwargs={'pk': updated_trip_instance.pk}))
            except ValidationError as e:
                error_dict = e.message_dict if hasattr(e, 'message_dict') else {'__all__': getattr(e, 'messages', [str(e)])}
                for field, errors_list in error_dict.items():
                    for error_item in errors_list:
                        if field == '__all__' or not hasattr(trip_form, field) or field == 'status': messages.error(request, error_item) 
                        elif hasattr(trip_form, field): trip_form.add_error(field, error_item)
                        else: messages.error(request, f"Error: {error_item}")
                messages.error(request, 'Please correct the errors highlighted.')
            except Exception as e: messages.error(request, f'An unexpected error during update: {str(e)}')
        else: messages.error(request, 'Please correct the form errors (main or compartments).')
    else: 
        trip_form = TripForm(instance=trip_instance)
        compartment_formset = LoadingCompartmentFormSet(instance=trip_instance, prefix='compartments')
    context = { 
        'trip_form': trip_form, 'compartment_formset': compartment_formset, 
        'page_title': f'Edit Loading: {trip_instance.kpc_order_number or f"Trip {trip_instance.id}"}', 
        'trip': trip_instance 
    }
    return render(request, 'shipments/trip_form.html', context)

@login_required
def trip_delete_view(request, pk):
    if is_admin_or_superuser(request.user): trip_instance = get_object_or_404(Trip, pk=pk)
    else: trip_instance = get_object_or_404(Trip.objects.filter(user=request.user), pk=pk)
    if not request.user.has_perm('shipments.delete_trip'): return HttpResponseForbidden("You do not have permission to delete loadings.")
    if request.method == 'POST':
        try:
            with transaction.atomic():
                # Pass stdout to ensure model method can log
                if not trip_instance.reverse_stock_depletion(): # Model method uses its own stdout now
                    messages.error(request, "Failed to reverse stock depletions. Deletion aborted.")
                    return redirect(reverse('shipments:trip-detail', kwargs={'pk': pk}))
                trip_desc = str(trip_instance); trip_instance.delete()
                messages.success(request, f"Loading '{trip_desc}' and depletions (if any) deleted successfully!")
                return redirect(reverse('shipments:trip-list'))
        except Exception as e: messages.error(request, f"Error during deletion: {str(e)}"); return redirect(reverse('shipments:trip-detail', kwargs={'pk': pk})) 
    context = {'trip': trip_instance, 'page_title': f'Confirm Delete Loading: {trip_instance.kpc_order_number or f"Trip {trip_instance.id}"}'}
    return render(request, 'shipments/trip_confirm_delete.html', context)

@login_required
def trip_detail_view(request, pk):
    if is_viewer_or_admin_or_superuser(request.user):
        trip = get_object_or_404(Trip.objects.select_related('user', 'vehicle', 'customer', 'product', 'destination').prefetch_related('requested_compartments', 'depletions_for_trip__shipment_batch__product'), pk=pk)
    else:
        trip = get_object_or_404(Trip.objects.filter(user=request.user).select_related('user', 'vehicle', 'customer', 'product', 'destination').prefetch_related('requested_compartments', 'depletions_for_trip__shipment_batch__product'), pk=pk)
    if not request.user.has_perm('shipments.view_trip') and not (is_viewer_or_admin_or_superuser(request.user) or trip.user == request.user):
        return HttpResponseForbidden("You do not have permission to view this loading.")
    requested_compartments_qs = trip.requested_compartments.all().order_by('compartment_number')
    actual_depletions_qs = trip.depletions_for_trip.all().order_by('created_at', 'shipment_batch__import_date')
    context = {
        'trip': trip, 'compartments': requested_compartments_qs, 'actual_depletions': actual_depletions_qs, 
        'page_title': f'Loading Details: {trip.kpc_order_number or f"Trip {trip.id}"}', 
        'can_change_trip': request.user.has_perm('shipments.change_trip') and (is_admin_or_superuser(request.user) or trip.user == request.user),
        'can_delete_trip': request.user.has_perm('shipments.delete_trip') and (is_admin_or_superuser(request.user) or trip.user == request.user),
    }
    return render(request, 'shipments/trip_detail.html', context)

@login_required
@permission_required('shipments.view_trip', raise_exception=True) 
@permission_required('shipments.view_shipment', raise_exception=True)
def truck_activity_dashboard_view(request):
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

    truck_activities = defaultdict(lambda: {'trips': [], 'total_quantity': Decimal('0.00'), 'trip_count': 0})
    filtered_trips = base_queryset.select_related('vehicle', 'product', 'customer', 'user', 'destination').prefetch_related('depletions_for_trip')
    for trip in filtered_trips:
        vehicle_obj = trip.vehicle; 
        truck_activities[vehicle_obj]['trips'].append(trip)
        truck_activities[vehicle_obj]['total_quantity'] += trip.total_loaded # Use property
        truck_activities[vehicle_obj]['trip_count'] += 1
    sorted_truck_activities = dict(sorted(truck_activities.items(), key=lambda item: item[0].plate_number))
    
    products_for_filter = Product.objects.all().order_by('name'); customers_for_filter = Customer.objects.all().order_by('name')
    vehicles_for_filter = Vehicle.objects.all().order_by('plate_number'); status_choices_for_filter = Trip.STATUS_CHOICES
    context = { 
        'truck_activities': sorted_truck_activities, 'products': products_for_filter, 
        'customers': customers_for_filter, 'vehicles': vehicles_for_filter, 
        'status_choices': status_choices_for_filter, 'product_filter_value': product_filter_pk, 
        'customer_filter_value': customer_filter_pk, 'vehicle_filter_value': vehicle_filter_pk, 
        'status_filter_value': status_filter, 'start_date_filter_value': start_date_str, 
        'end_date_filter_value': end_date_str, 'page_title': 'Truck Activity Dashboard'
    }
    return render(request, 'shipments/truck_activity_dashboard.html', context)

@login_required
@permission_required('shipments.view_shipment', raise_exception=True)
@permission_required('shipments.view_trip', raise_exception=True) 
def monthly_stock_summary_view(request):
    show_global_data = is_viewer_or_admin_or_superuser(request.user)
    shipment_years = Shipment.objects.dates('import_date', 'year', order='DESC')
    trip_years = Trip.objects.dates('loading_date', 'year', order='DESC')
    all_years = sorted(list(set( [d.year for d in shipment_years] + [d.year for d in trip_years] )), reverse=True)
    if not all_years: all_years.append(datetime.date.today().year)
    months_for_dropdown = [(i, datetime.date(2000, i, 1).strftime('%B')) for i in range(1, 13)]
    current_year = datetime.date.today().year; current_month = datetime.date.today().month
    selected_year_str = request.GET.get('year', str(current_year)); selected_month_str = request.GET.get('month', str(current_month))
    try:
        selected_year = int(selected_year_str); selected_month = int(selected_month_str)
        if not (1 <= selected_month <= 12 and 1900 < selected_year < 2200): raise ValueError("Invalid year or month")
    except (ValueError, TypeError): 
        messages.error(request, "Invalid year or month. Defaulting to current period."); 
        selected_year = current_year; selected_month = current_month
    start_of_selected_month = datetime.date(selected_year, selected_month, 1)
    num_days_in_month = monthrange(selected_year, selected_month)[1]
    end_of_selected_month = datetime.date(selected_year, selected_month, num_days_in_month); summary_data = []
    all_products = Product.objects.all().order_by('name')
    for product_obj in all_products:
        shipments_product_qs = Shipment.objects.filter(product=product_obj)
        depletions_product_qs = ShipmentDepletion.objects.filter(shipment_batch__product=product_obj)
        if not show_global_data:
            shipments_product_qs = shipments_product_qs.filter(user=request.user)
            depletions_product_qs = depletions_product_qs.filter(trip__user=request.user)
        
        total_shipped_before_month = shipments_product_qs.filter(import_date__lt=start_of_selected_month).aggregate(s=Sum('quantity_litres'))['s'] or Decimal('0.00')
        total_depleted_before_month = depletions_product_qs.filter(created_at__date__lt=start_of_selected_month).aggregate(s=Sum('quantity_depleted'))['s'] or Decimal('0.00')
        opening_stock = total_shipped_before_month - total_depleted_before_month
        stock_in_month = shipments_product_qs.filter(import_date__gte=start_of_selected_month, import_date__lte=end_of_selected_month).aggregate(s=Sum('quantity_litres'))['s'] or Decimal('0.00')
        stock_out_month = depletions_product_qs.filter(created_at__date__gte=start_of_selected_month, created_at__date__lte=end_of_selected_month).aggregate(s=Sum('quantity_depleted'))['s'] or Decimal('0.00')
        closing_stock = opening_stock + stock_in_month - stock_out_month
        if opening_stock !=0 or stock_in_month !=0 or stock_out_month !=0 or closing_stock !=0: # Only show products with activity or stock
            summary_data.append({'product_name': product_obj.name, 'opening_stock': opening_stock, 'stock_in_month': stock_in_month, 'stock_out_month': stock_out_month, 'closing_stock': closing_stock})
    context = {
        'summary_data': summary_data, 'selected_year': selected_year, 'selected_month': selected_month, 
        'month_name_display': datetime.date(1900, selected_month, 1).strftime('%B'), 
        'available_years': all_years, 'months_for_dropdown': months_for_dropdown, 
        'page_title': f'Monthly Stock Summary - {datetime.date(1900, selected_month, 1).strftime("%B")} {selected_year}'
    }
    return render(request, 'shipments/monthly_stock_summary.html', context)

def signup_view(request):
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid(): 
            user = form.save()
            auth_login(request, user)
            messages.success(request, 'Account created successfully! You are now logged in.')
            try: # Assign to 'Viewer' group by default
                viewer_group, created = Group.objects.get_or_create(name='Viewer')
                user.groups.add(viewer_group)
            except Exception as e: messages.warning(request, f"Could not assign default group: {e}")
            return redirect(reverse('shipments:home'))
    else: form = UserCreationForm()
    context = { 'form': form, 'page_title': 'Sign Up'}
    return render(request, 'registration/signup.html', context)