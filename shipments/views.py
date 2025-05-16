# shipments/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import login as auth_login
from django.db.models import Sum, Count, F, ExpressionWrapper, DecimalField, Max
from django.utils import timezone
import datetime
from django.db import transaction
from django.http import JsonResponse, HttpResponseForbidden
from django import forms
from calendar import monthrange
from collections import defaultdict
from django.core.exceptions import ValidationError

from .models import Shipment, Product, Customer, Vehicle, Trip, LoadingCompartment, ShipmentDepletion
from .forms import ShipmentForm, TripForm, LoadingCompartmentFormSet


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
def home_view(request):
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
        
        total_shipments = 0; total_quantity_shipments = 0; total_value_shipments = 0
        product_shipment_summary = []
        total_trips = 0; total_quantity_loaded = 0
        trip_quantity_by_product = None
        stock_by_product = {}
        aging_stock_notifications = [] 
        inactive_product_notifications = []
        utilized_shipment_notifications = [] 

        shipments_qs_base = Shipment.objects.all() if show_global_data else Shipment.objects.filter(user=request.user)
        trips_qs_base = Trip.objects.all() if show_global_data else Trip.objects.filter(user=request.user)
        depletions_qs_base = ShipmentDepletion.objects.all() if show_global_data else ShipmentDepletion.objects.filter(trip__user=request.user)

        if can_view_shipments:
            total_shipments = shipments_qs_base.count()
            total_quantity_shipments_agg = shipments_qs_base.aggregate(Sum('quantity_litres'))
            total_quantity_shipments = total_quantity_shipments_agg.get('quantity_litres__sum', 0)
            total_cost_expression = ExpressionWrapper(F('quantity_litres') * F('price_per_litre'), output_field=DecimalField())
            shipments_data_for_avg_price = shipments_qs_base.values('product__name').annotate(
                total_litres_shipped=Sum('quantity_litres'),
                total_monetary_value=Sum(total_cost_expression)
            ).order_by('product__name')
            total_value_shipments = 0
            for item in shipments_data_for_avg_price:
                avg_price = 0
                if item['total_monetary_value'] is not None: total_value_shipments += item['total_monetary_value']
                if item['total_litres_shipped'] and item['total_litres_shipped'] > 0 and item['total_monetary_value'] is not None:
                    avg_price = item['total_monetary_value'] / item['total_litres_shipped']
                product_shipment_summary.append({'name': item['product__name'], 'total_litres': item['total_litres_shipped'], 'avg_price': avg_price})
        
        if can_view_trip:
            total_trips = trips_qs_base.count()
            total_quantity_loaded_agg = depletions_qs_base.filter(trip__in=trips_qs_base).aggregate(total_loaded_sum=Sum('quantity_depleted'))
            total_quantity_loaded = total_quantity_loaded_agg.get('total_loaded_sum', 0)
            trip_quantity_by_product = trips_qs_base.values('product__name').annotate(
                total_litres=Sum('depletions_for_trip__quantity_depleted')
            ).order_by('product__name')

        context.update({
            'message': f'Welcome back, {request.user.username}!',
            'description': 'Here is a summary of company fuel data.' if show_global_data else 'Here is a summary of your fuel data.',
            'total_shipments': total_shipments,
            'total_quantity_shipments': total_quantity_shipments,
            'total_value_shipments': total_value_shipments,
            'product_shipment_summary': product_shipment_summary,
            'total_trips': total_trips,
            'total_quantity_loaded': total_quantity_loaded,
            'trip_quantity_by_product': trip_quantity_by_product,
        })

        if can_view_shipments and can_view_trip: 
            available_stock_agg = shipments_qs_base.values('product__name').annotate(available=Sum('quantity_remaining')).order_by('product__name')
            shipment_sums_dict = {item['name']: item['total_litres'] for item in product_shipment_summary}
            trip_sums_dict = {item['product__name']: item['total_litres'] for item in trip_quantity_by_product} if trip_quantity_by_product else {}
            all_product_names_for_stock = set(shipment_sums_dict.keys()).union(set(trip_sums_dict.keys()))
            
            today = timezone.now().date()
            age_threshold_days = 25
            inactivity_threshold_days = 5

            for product_name in sorted(list(all_product_names_for_stock)):
                shipped = shipment_sums_dict.get(product_name, 0); 
                loaded_from_trips = trip_sums_dict.get(product_name, 0)
                current_available_for_product = 0
                for item_stock in available_stock_agg:
                    if item_stock['product__name'] == product_name:
                        current_available_for_product = item_stock['available'] or 0
                        break
                stock_by_product[product_name] = {
                    'shipped': shipped, 'loaded': loaded_from_trips, 'available': current_available_for_product
                }

                if current_available_for_product > 0:
                    # Aging Stock Notification Logic
                    product_shipments_for_aging = shipments_qs_base.filter(product__name=product_name, quantity_remaining__gt=0).order_by('import_date')
                    remaining_available_for_aging = current_available_for_product
                    oldest_contributing_shipment_date = None
                    for sh_aging in product_shipments_for_aging:
                        if remaining_available_for_aging <= 0: break
                        if sh_aging.quantity_remaining >= remaining_available_for_aging:
                            oldest_contributing_shipment_date = sh_aging.import_date
                            remaining_available_for_aging = 0; break
                        else:
                            oldest_contributing_shipment_date = sh_aging.import_date
                            remaining_available_for_aging -= sh_aging.quantity_remaining
                    if oldest_contributing_shipment_date:
                        days_old = (today - oldest_contributing_shipment_date).days
                        if days_old > age_threshold_days:
                            aging_stock_notifications.append(
                                f"Product '{product_name}' has stock where the oldest batch is from {oldest_contributing_shipment_date.strftime('%Y-%m-%d')} ({days_old} days old)."
                            )
                    
                    # Product Inactivity Notification Logic
                    product_depletions_qs = depletions_qs_base.filter(shipment_batch__product__name=product_name)
                    most_recent_depletion = product_depletions_qs.aggregate(latest_depletion_date=Max('created_at'))
                    latest_depletion_date_val = most_recent_depletion.get('latest_depletion_date')
                    main_inactivity_message_for_product = None
                    if latest_depletion_date_val: 
                        days_since_last_depletion = (today - latest_depletion_date_val.date()).days
                        if days_since_last_depletion > inactivity_threshold_days:
                            main_inactivity_message_for_product = f"Product '{product_name}' has available stock but no overall dispatch in {days_since_last_depletion} days (last product dispatch: {latest_depletion_date_val.date().strftime('%Y-%m-%d')})."
                    else: 
                        oldest_shipment_with_stock = shipments_qs_base.filter(product__name=product_name, quantity_remaining__gt=0).order_by('import_date').first()
                        if oldest_shipment_with_stock:
                            days_since_available = (today - oldest_shipment_with_stock.import_date).days
                            if days_since_available > inactivity_threshold_days:
                                 main_inactivity_message_for_product = f"Product '{product_name}' has available stock and has never been dispatched (available for {days_since_available} days)."
                    if main_inactivity_message_for_product:
                        inactive_shipment_batches_details = []
                        contributing_shipments = shipments_qs_base.filter(
                            product__name=product_name, 
                            quantity_remaining__gt=0
                        ).order_by('import_date')
                        for sh_batch in contributing_shipments:
                            inactive_shipment_batches_details.append({
                                'id': sh_batch.id, 'import_date': sh_batch.import_date,
                                'supplier_name': sh_batch.supplier_name, 'quantity_remaining': sh_batch.quantity_remaining
                            })
                        if inactive_shipment_batches_details:
                            inactive_product_notifications.append({
                                'product_name': product_name, 'message': main_inactivity_message_for_product,
                                'shipments': inactive_shipment_batches_details
                            })
            
            # --- Utilized Shipment Notification Logic ---
            fully_utilized_shipments = shipments_qs_base.filter(
                quantity_remaining__lte=0, 
                updated_at__date__lt=today 
            ).select_related('product').prefetch_related('depletions_from_batch').order_by('-updated_at')

            for sh_utilized in fully_utilized_shipments:
                latest_depletion_for_this_batch = sh_utilized.depletions_from_batch.aggregate(
                    max_date=Max('created_at')
                )['max_date']
                
                date_it_became_utilized = sh_utilized.updated_at.date() 
                if latest_depletion_for_this_batch: 
                    date_it_became_utilized = latest_depletion_for_this_batch.date()
                
                days_since_utilized = (today - date_it_became_utilized).days
                
                if days_since_utilized >= 1: 
                    utilized_shipment_notifications.append({
                        'id': sh_utilized.id,
                        'product_name': sh_utilized.product.name,
                        'supplier_name': sh_utilized.supplier_name,
                        'import_date': sh_utilized.import_date,
                        'utilized_date': date_it_became_utilized, 
                        'days_since_utilized': days_since_utilized
                    })
            utilized_shipment_notifications.sort(key=lambda x: x['utilized_date'], reverse=True)
            # --- End Utilized Shipment Notification Logic ---

            context['stock_by_product'] = stock_by_product
            context['aging_stock_notifications'] = aging_stock_notifications
            context['inactive_product_notifications'] = inactive_product_notifications
            context['utilized_shipment_notifications'] = utilized_shipment_notifications
        
        if not (can_view_shipments or can_view_trip or context.get('can_view_product') or context.get('can_view_customer') or context.get('can_view_vehicle')):
             context['description'] = 'You do not have permission to view fuel data. Contact admin for access.'
    return render(request, 'shipments/home.html', context)

# --- Shipment Views (Stock In) ---
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
        'shipments': shipments, 'product_filter_value': product_filter_pk,
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
            shipment_instance.quantity_remaining = form.cleaned_data['quantity_litres'] 
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
    depleted_quantity = ShipmentDepletion.objects.filter(shipment_batch=shipment_instance).aggregate(Sum('quantity_depleted'))['quantity_depleted__sum'] or 0
    if request.method == 'POST':
        form = ShipmentForm(request.POST, instance=shipment_instance)
        if form.is_valid():
            new_quantity_litres = form.cleaned_data['quantity_litres']
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
    if is_admin_or_superuser(request.user):
        shipment_instance = get_object_or_404(Shipment, pk=pk)
    else:
        shipment_instance = get_object_or_404(Shipment.objects.filter(user=request.user), pk=pk)
    can_delete = request.user.has_perm('shipments.delete_shipment')
    is_owner_or_admin = is_admin_or_superuser(request.user) or shipment_instance.user == request.user
    if not (can_delete and is_owner_or_admin):
        return HttpResponseForbidden("You do not have permission to delete this shipment.")
    if ShipmentDepletion.objects.filter(shipment_batch=shipment_instance).exists():
        messages.error(request, f"Cannot delete Shipment '{shipment_instance}'. It has already been used. Adjust loadings first.")
        return redirect(reverse('shipments:shipment-list'))
    if request.method == 'POST':
        shipment_name = shipment_instance.product.name
        shipment_instance.delete()
        messages.success(request, f"Shipment '{shipment_name}' deleted successfully!")
        return redirect(reverse('shipments:shipment-list'))
    context = {'shipment': shipment_instance, 'page_title': f'Confirm Delete: {shipment_instance.product.name}'}
    return render(request, 'shipments/shipment_confirm_delete.html', context)

@login_required
def shipment_detail_view(request, pk):
    if is_viewer_or_admin_or_superuser(request.user):
        shipment = get_object_or_404(Shipment, pk=pk)
    else:
        shipment = get_object_or_404(Shipment.objects.filter(user=request.user), pk=pk)
    if not request.user.has_perm('shipments.view_shipment'):
         if not (is_viewer_or_admin_or_superuser(request.user) or shipment.user == request.user):
             return HttpResponseForbidden("You do not have permission to view this shipment.")
         elif not request.user.has_perm('shipments.view_shipment'):
             return HttpResponseForbidden("You do not have permission to view shipments.")
    context = {
        'shipment': shipment, 'page_title': f'Shipment Details: {shipment.product.name}',
        'can_change_shipment': request.user.has_perm('shipments.change_shipment'),
        'can_delete_shipment': request.user.has_perm('shipments.delete_shipment'),}
    return render(request, 'shipments/shipment_detail.html', context)

# --- Trip Views (Stock Out / Loadings) ---
@login_required
@permission_required('shipments.view_trip', raise_exception=True)
def trip_list_view(request):
    if is_viewer_or_admin_or_superuser(request.user): queryset = Trip.objects.all()
    else: queryset = Trip.objects.filter(user=request.user)
    product_filter_pk = request.GET.get('product', '').strip()
    customer_filter_pk = request.GET.get('customer', '').strip()
    vehicle_filter_pk = request.GET.get('vehicle', '').strip()
    status_filter = request.GET.get('status', '').strip()
    start_date_str = request.GET.get('start_date', '').strip()
    end_date_str = request.GET.get('end_date', '').strip()
    if product_filter_pk: queryset = queryset.filter(product__pk=product_filter_pk)
    if customer_filter_pk: queryset = queryset.filter(customer__pk=customer_filter_pk)
    if vehicle_filter_pk: queryset = queryset.filter(vehicle__pk=vehicle_filter_pk)
    if status_filter: queryset = queryset.filter(status=status_filter)
    start_date = None
    if start_date_str:
        try:
            start_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d').date()
            queryset = queryset.filter(loading_date__gte=start_date)
        except ValueError: messages.error(request, "Invalid start date format for trips.")
    end_date = None
    if end_date_str:
        try:
            end_date = datetime.datetime.strptime(end_date_str, '%Y-%m-%d').date()
            queryset = queryset.filter(loading_date__lte=end_date)
        except ValueError: messages.error(request, "Invalid end date format for trips.")
    filtered_trip_count = queryset.count()
    filtered_total_loaded_agg = ShipmentDepletion.objects.filter(trip__in=queryset).aggregate(total_loaded_sum=Sum('quantity_depleted'))
    filtered_total_loaded = filtered_total_loaded_agg.get('total_loaded_sum', 0)
    trips = queryset.order_by('-loading_date', '-loading_time')
    products = Product.objects.all().order_by('name')
    customers = Customer.objects.all().order_by('name')
    vehicles = Vehicle.objects.all().order_by('plate_number')
    status_choices = Trip.STATUS_CHOICES
    context = {
        'trips': trips, 'products': products, 'customers': customers, 'vehicles': vehicles,
        'status_choices': status_choices, 'product_filter_value': product_filter_pk,
        'customer_filter_value': customer_filter_pk, 'vehicle_filter_value': vehicle_filter_pk,
        'status_filter_value': status_filter, 'start_date_filter_value': start_date_str,
        'end_date_filter_value': end_date_str,
        'filtered_trip_count': filtered_trip_count, 'filtered_total_loaded': filtered_total_loaded,
        'can_add_trip': request.user.has_perm('shipments.add_trip'),
        'can_change_trip': request.user.has_perm('shipments.change_trip'),
        'can_delete_trip': request.user.has_perm('shipments.delete_trip'),
        'can_view_trip': request.user.has_perm('shipments.view_trip'),
    }
    return render(request, 'shipments/trip_list.html', context)

@login_required
@permission_required('shipments.add_trip', raise_exception=True)
def trip_add_view(request):
    if request.method == 'POST':
        trip_form = TripForm(request.POST)
        compartment_formset = LoadingCompartmentFormSet(request.POST, prefix='compartments')
        if trip_form.is_valid() and compartment_formset.is_valid():
            vehicle = trip_form.cleaned_data.get('vehicle')
            product_for_trip = trip_form.cleaned_data.get('product')
            vehicle_capacity = None
            if vehicle and product_for_trip:
                if product_for_trip.name.upper() == 'PMS': vehicle_capacity = vehicle.capacity_pms
                elif product_for_trip.name.upper() == 'AGO': vehicle_capacity = vehicle.capacity_ago
            requested_total_loading = 0
            for form_comp in compartment_formset.cleaned_data: 
                if form_comp and not form_comp.get('DELETE', False):
                    quantity = form_comp.get('quantity_requested_litres')
                    if quantity is not None: requested_total_loading += quantity
            if vehicle_capacity is not None and requested_total_loading > vehicle_capacity:
                trip_form.add_error(None, forms.ValidationError(
                    f"Total requested quantity ({requested_total_loading}L) exceeds vehicle capacity for {product_for_trip.name} ({vehicle_capacity}L)."
                ))
            else: 
                try:
                    with transaction.atomic():
                        trip_instance = trip_form.save(commit=False)
                        trip_instance.user = request.user
                        trip_instance.save() 
                        compartment_formset.instance = trip_instance
                        saved_compartments = compartment_formset.save() 
                        total_quantity_to_deplete_for_trip = requested_total_loading
                        available_shipments = Shipment.objects.filter(
                            product=product_for_trip, 
                            quantity_remaining__gt=0
                        ).order_by('import_date', 'created_at')
                        depleted_amount_for_trip = 0
                        for batch in available_shipments:
                            if depleted_amount_for_trip >= total_quantity_to_deplete_for_trip: break 
                            can_take_from_batch = batch.quantity_remaining
                            take_this_much = min(can_take_from_batch, total_quantity_to_deplete_for_trip - depleted_amount_for_trip)
                            if take_this_much > 0:
                                ShipmentDepletion.objects.create(
                                    trip=trip_instance, shipment_batch=batch, quantity_depleted=take_this_much
                                )
                                batch.quantity_remaining -= take_this_much
                                batch.save(update_fields=['quantity_remaining'])
                                depleted_amount_for_trip += take_this_much
                        if depleted_amount_for_trip < total_quantity_to_deplete_for_trip:
                            raise ValidationError(f"Not enough stock available for {product_for_trip.name} to fulfill {total_quantity_to_deplete_for_trip}L. Only {depleted_amount_for_trip}L could be sourced.")
                        messages.success(request, 'Loading recorded successfully with FIFO stock depletion!')
                        return redirect(reverse('shipments:trip-list'))
                except ValidationError as e:
                    trip_form.add_error(None, e) 
                    messages.error(request, f'Error processing loading: {e.args[0] if e.args else str(e)}')
                except Exception as e:
                    messages.error(request, f'An unexpected error occurred: {str(e)}')
        else: messages.error(request, 'Please correct the form errors below.')
    else: # GET Request
        trip_form = TripForm()
        initial_compartments = [{'compartment_number': 1},{'compartment_number': 2},{'compartment_number': 3},]
        compartment_formset = LoadingCompartmentFormSet(prefix='compartments', initial=initial_compartments)
    context = {
        'trip_form': trip_form, 'compartment_formset': compartment_formset,
        'page_title': 'Record New Loading', 'can_add_trip': request.user.has_perm('shipments.add_trip'),
    }
    return render(request, 'shipments/trip_form.html', context)

@login_required
def trip_edit_view(request, pk):
    if is_admin_or_superuser(request.user):
        trip_instance = get_object_or_404(Trip, pk=pk)
    else:
        trip_instance = get_object_or_404(Trip.objects.filter(user=request.user), pk=pk)
    if not request.user.has_perm('shipments.change_trip'):
        if not (is_admin_or_superuser(request.user) or trip_instance.user == request.user):
            return HttpResponseForbidden("You do not have permission to edit this loading.")
        elif not request.user.has_perm('shipments.change_trip'):
             return HttpResponseForbidden("You do not have permission to edit loadings.")
    if request.method == 'POST':
        trip_form = TripForm(request.POST, instance=trip_instance)
        compartment_formset = LoadingCompartmentFormSet(request.POST, instance=trip_instance, prefix='compartments')
        if trip_form.is_valid() and compartment_formset.is_valid():
            vehicle = trip_form.cleaned_data.get('vehicle')
            product_for_trip = trip_form.cleaned_data.get('product')
            vehicle_capacity = None
            if vehicle and product_for_trip:
                if product_for_trip.name.upper() == 'PMS': vehicle_capacity = vehicle.capacity_pms
                elif product_for_trip.name.upper() == 'AGO': vehicle_capacity = vehicle.capacity_ago
            requested_total_loading = 0
            for form_comp in compartment_formset.cleaned_data:
                if form_comp and not form_comp.get('DELETE', False):
                    quantity = form_comp.get('quantity_requested_litres')
                    if quantity is not None: requested_total_loading += quantity
            if vehicle_capacity is not None and requested_total_loading > vehicle_capacity:
                trip_form.add_error(None, forms.ValidationError(
                    f"Total requested quantity ({requested_total_loading}L) exceeds vehicle capacity for {product_for_trip.name} ({vehicle_capacity}L)."
                ))
            else:
                try:
                    with transaction.atomic():
                        old_depletions = ShipmentDepletion.objects.filter(trip=trip_instance)
                        for dep in old_depletions:
                            dep.shipment_batch.quantity_remaining += dep.quantity_depleted
                            dep.shipment_batch.save(update_fields=['quantity_remaining'])
                        old_depletions.delete()
                        updated_trip = trip_form.save()
                        compartment_formset.instance = updated_trip
                        compartment_formset.save()
                        total_quantity_to_deplete_for_trip = requested_total_loading
                        available_shipments = Shipment.objects.filter(
                            product=product_for_trip, quantity_remaining__gt=0
                        ).order_by('import_date', 'created_at')
                        depleted_amount_for_trip = 0
                        for batch in available_shipments:
                            if depleted_amount_for_trip >= total_quantity_to_deplete_for_trip: break
                            can_take_from_batch = batch.quantity_remaining
                            take_this_much = min(can_take_from_batch, total_quantity_to_deplete_for_trip - depleted_amount_for_trip)
                            if take_this_much > 0:
                                ShipmentDepletion.objects.create(
                                    trip=updated_trip, shipment_batch=batch, quantity_depleted=take_this_much
                                )
                                batch.quantity_remaining -= take_this_much
                                batch.save(update_fields=['quantity_remaining'])
                                depleted_amount_for_trip += take_this_much
                        if depleted_amount_for_trip < total_quantity_to_deplete_for_trip:
                            raise ValidationError(f"Not enough stock available for {product_for_trip.name} to fulfill updated loading of {total_quantity_to_deplete_for_trip}L. Only {depleted_amount_for_trip}L could be sourced. Edit reverted.")
                        messages.success(request, f"Loading '{updated_trip}' updated successfully with FIFO stock adjustment!")
                        return redirect(reverse('shipments:trip-list'))
                except ValidationError as e:
                    trip_form.add_error(None, e)
                    messages.error(request, f'Error processing loading update: {e}')
                except Exception as e:
                    messages.error(request, f'An unexpected error occurred during update: {str(e)}')
        else: messages.error(request, 'Please correct the form errors below.')
    else: # GET Request
        trip_form = TripForm(instance=trip_instance)
        compartment_formset = LoadingCompartmentFormSet(instance=trip_instance, prefix='compartments')
    context = {
        'trip_form': trip_form, 'compartment_formset': compartment_formset,
        'page_title': f'Edit Loading: Trip {trip_instance.id}',
        'can_change_trip': request.user.has_perm('shipments.change_trip'),
    }
    return render(request, 'shipments/trip_form.html', context)

@login_required
def trip_delete_view(request, pk):
    if is_admin_or_superuser(request.user):
        trip_instance = get_object_or_404(Trip, pk=pk)
    else:
        trip_instance = get_object_or_404(Trip.objects.filter(user=request.user), pk=pk)
    if not request.user.has_perm('shipments.delete_trip'):
        if not (is_admin_or_superuser(request.user) or trip_instance.user == request.user):
            return HttpResponseForbidden("You do not have permission to delete this loading.")
        elif not request.user.has_perm('shipments.delete_trip'):
            return HttpResponseForbidden("You do not have permission to delete loadings.")
    if request.method == 'POST':
        try:
            with transaction.atomic():
                depletions_to_revert = ShipmentDepletion.objects.filter(trip=trip_instance)
                for depletion in depletions_to_revert:
                    shipment_batch = depletion.shipment_batch
                    shipment_batch.quantity_remaining += depletion.quantity_depleted
                    shipment_batch.save(update_fields=['quantity_remaining'])
                trip_desc = str(trip_instance)
                trip_instance.delete()
                messages.success(request, f"Loading '{trip_desc}' and its stock depletions reverted and deleted successfully!")
                return redirect(reverse('shipments:trip-list'))
        except Exception as e:
            messages.error(request, f"Error during deletion: {str(e)}")
            return redirect(reverse('shipments:trip-delete', kwargs={'pk': pk}))
    context = {
        'trip': trip_instance,
        'page_title': f'Confirm Delete Loading: {trip_instance}'
    }
    return render(request, 'shipments/trip_confirm_delete.html', context)

@login_required
def trip_detail_view(request, pk):
    if is_viewer_or_admin_or_superuser(request.user):
        trip = get_object_or_404(Trip, pk=pk)
    else:
        trip = get_object_or_404(Trip.objects.filter(user=request.user), pk=pk)
    if not request.user.has_perm('shipments.view_trip'):
         if not (is_viewer_or_admin_or_superuser(request.user) or trip.user == request.user):
             return HttpResponseForbidden("You do not have permission to view this loading.")
         elif not request.user.has_perm('shipments.view_trip'):
             return HttpResponseForbidden("You do not have permission to view loadings.")
    requested_compartments = trip.requested_compartments.all().order_by('compartment_number')
    actual_depletions = trip.depletions_for_trip.select_related('shipment_batch', 'shipment_batch__product').order_by('shipment_batch__import_date', 'pk')
    context = {
        'trip': trip, 'compartments': requested_compartments,
        'actual_depletions': actual_depletions,
        'page_title': f'Loading Details: Trip {trip.id}',
        'can_change_trip': request.user.has_perm('shipments.change_trip'),
        'can_delete_trip': request.user.has_perm('shipments.delete_trip'),
    }
    return render(request, 'shipments/trip_detail.html', context)

@login_required
@permission_required('shipments.view_trip', raise_exception=True)
def truck_activity_dashboard_view(request):
    show_global_data = is_viewer_or_admin_or_superuser(request.user)
    if show_global_data: base_queryset = Trip.objects.all()
    else: base_queryset = Trip.objects.filter(user=request.user)
    product_filter_pk = request.GET.get('product', '').strip()
    customer_filter_pk = request.GET.get('customer', '').strip()
    vehicle_filter_pk = request.GET.get('vehicle', '').strip()
    status_filter = request.GET.get('status', '').strip()
    start_date_str = request.GET.get('start_date', '').strip()
    end_date_str = request.GET.get('end_date', '').strip()
    if product_filter_pk: base_queryset = base_queryset.filter(product__pk=product_filter_pk)
    if customer_filter_pk: base_queryset = base_queryset.filter(customer__pk=customer_filter_pk)
    if vehicle_filter_pk: base_queryset = base_queryset.filter(vehicle__pk=vehicle_filter_pk)
    if status_filter: base_queryset = base_queryset.filter(status=status_filter)
    if start_date_str:
        try:
            start_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d').date()
            base_queryset = base_queryset.filter(loading_date__gte=start_date)
        except ValueError: messages.error(request, "Invalid start date format.")
    if end_date_str:
        try:
            end_date = datetime.datetime.strptime(end_date_str, '%Y-%m-%d').date()
            base_queryset = base_queryset.filter(loading_date__lte=end_date)
        except ValueError: messages.error(request, "Invalid end date format.")
    truck_activities = defaultdict(lambda: {'trips': [], 'total_quantity': 0, 'trip_count': 0})
    filtered_trips = base_queryset.select_related('vehicle', 'product', 'customer', 'user').prefetch_related('depletions_for_trip')
    for trip in filtered_trips:
        vehicle_obj = trip.vehicle
        truck_activities[vehicle_obj]['trips'].append(trip)
        current_trip_total_depleted = sum(dep.quantity_depleted for dep in trip.depletions_for_trip.all())
        truck_activities[vehicle_obj]['total_quantity'] += current_trip_total_depleted
        truck_activities[vehicle_obj]['trip_count'] += 1
    sorted_truck_activities = dict(sorted(truck_activities.items(), key=lambda item: item[0].plate_number))
    products = Product.objects.all().order_by('name')
    customers = Customer.objects.all().order_by('name')
    vehicles = Vehicle.objects.all().order_by('plate_number')
    status_choices = Trip.STATUS_CHOICES
    context = {
        'truck_activities': sorted_truck_activities, 'products': products, 'customers': customers,
        'vehicles': vehicles, 'status_choices': status_choices, 'product_filter_value': product_filter_pk,
        'customer_filter_value': customer_filter_pk, 'vehicle_filter_value': vehicle_filter_pk,
        'status_filter_value': status_filter, 'start_date_filter_value': start_date_str,
        'end_date_filter_value': end_date_str, 'page_title': 'Truck Activity Dashboard'
    }
    return render(request, 'shipments/truck_activity_dashboard.html', context)

@login_required
def get_vehicle_capacity_for_product_view(request):
    vehicle_id = request.GET.get('vehicle_id')
    product_id = request.GET.get('product_id')
    capacity = None; status = 'error'; message = 'Vehicle or Product not selected, or capacity not defined.'
    if vehicle_id and product_id:
        try:
            vehicle = Vehicle.objects.get(pk=vehicle_id)
            product = Product.objects.get(pk=product_id)
            if product.name.upper() == 'PMS': capacity = vehicle.capacity_pms
            elif product.name.upper() == 'AGO': capacity = vehicle.capacity_ago
            if capacity is not None:
                status = 'success'; message = f'Capacity for {product.name}: {capacity} L'
            else: message = f'Capacity not defined for {vehicle.plate_number} with {product.name}.'
        except Vehicle.DoesNotExist: message = 'Selected vehicle not found.'
        except Product.DoesNotExist: message = 'Selected product not found.'
        except Exception as e: message = f'An error occurred: {str(e)}'
    data = {'status': status, 'capacity': capacity, 'message': message}
    return JsonResponse(data)

def signup_view(request):
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            auth_login(request, user)
            messages.success(request, 'Account created successfully! You are now logged in.')
            return redirect(reverse('shipments:home'))
    else: form = UserCreationForm()
    context = { 'form': form, 'page_title': 'Sign Up'}
    return render(request, 'registration/signup.html', context)

@login_required
@permission_required('shipments.view_shipment', raise_exception=True)
@permission_required('shipments.view_trip', raise_exception=True)
def monthly_stock_summary_view(request):
    show_global_data = is_viewer_or_admin_or_superuser(request.user)
    years_with_data_q1 = Shipment.objects.dates('import_date', 'year', order='DESC')
    years_with_data_q2 = Trip.objects.dates('loading_date', 'year', order='DESC')
    available_years = sorted(list(set( [d.year for d in years_with_data_q1] + [d.year for d in years_with_data_q2] )), reverse=True)
    if not available_years: available_years.append(datetime.date.today().year)
    months_for_dropdown = []
    for i in range(1, 13): months_for_dropdown.append((i, datetime.date(2000, i, 1).strftime('%B')))
    current_year = datetime.date.today().year; current_month = datetime.date.today().month
    selected_year_str = request.GET.get('year', str(current_year))
    selected_month_str = request.GET.get('month', str(current_month))
    try:
        selected_year = int(selected_year_str); selected_month = int(selected_month_str)
        if not (1 <= selected_month <= 12 and 1900 < selected_year < 2200): raise ValueError("Invalid year or month")
    except (ValueError, TypeError):
        messages.error(request, "Invalid year or month selected. Defaulting to current period.")
        selected_year = current_year; selected_month = current_month
    start_of_selected_month = datetime.date(selected_year, selected_month, 1)
    num_days_in_month = monthrange(selected_year, selected_month)[1]
    end_of_selected_month = datetime.date(selected_year, selected_month, num_days_in_month)
    summary_data = []
    all_products = Product.objects.all().order_by('name')
    for product_obj in all_products:
        base_shipments_qs_product = Shipment.objects.filter(product=product_obj)
        base_depletions_qs_product = ShipmentDepletion.objects.filter(shipment_batch__product=product_obj)
        if not show_global_data:
            base_shipments_qs_product = base_shipments_qs_product.filter(user=request.user)
            base_depletions_qs_product = base_depletions_qs_product.filter(trip__user=request.user)
        total_shipped_before = base_shipments_qs_product.filter(import_date__lt=start_of_selected_month).aggregate(s=Sum('quantity_litres'))['s'] or 0
        total_depleted_before = base_depletions_qs_product.filter(created_at__date__lt=start_of_selected_month).aggregate(s=Sum('quantity_depleted'))['s'] or 0
        opening_stock = total_shipped_before - total_depleted_before
        stock_in_month = base_shipments_qs_product.filter(import_date__gte=start_of_selected_month, import_date__lte=end_of_selected_month).aggregate(s=Sum('quantity_litres'))['s'] or 0
        stock_out_month = base_depletions_qs_product.filter(created_at__date__gte=start_of_selected_month, created_at__date__lte=end_of_selected_month).aggregate(s=Sum('quantity_depleted'))['s'] or 0
        closing_stock = opening_stock + stock_in_month - stock_out_month
        summary_data.append({
            'product_name': product_obj.name, 'opening_stock': opening_stock,
            'stock_in_month': stock_in_month, 'stock_out_month': stock_out_month,
            'closing_stock': closing_stock,
        })
    context = {
        'summary_data': summary_data, 'selected_year': selected_year,
        'selected_month': selected_month, 'month_name_display': datetime.date(1900, selected_month, 1).strftime('%B'),
        'available_years': available_years, 'months_for_dropdown': months_for_dropdown,
        'page_title': f'Monthly Stock Summary - {datetime.date(1900, selected_month, 1).strftime("%B")} {selected_year}'
    }
    return render(request, 'shipments/monthly_stock_summary.html', context)