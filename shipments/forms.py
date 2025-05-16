# shipments/forms.py
from django import forms
from django.forms.models import inlineformset_factory
# Import the models needed for forms
from .models import Shipment, Product, Trip, LoadingCompartment, Customer, Vehicle


# Form for Shipments (Stock In)
class ShipmentForm(forms.ModelForm):
    class Meta:
        model = Shipment
        fields = [
            'import_date', 'supplier_name', 'product',
            'quantity_litres', 'price_per_litre', 'notes'
        ]
        widgets = {
            'import_date': forms.DateInput(attrs={'type': 'date', 'class': ''}),
            'supplier_name': forms.TextInput(attrs={'class': '', 'placeholder': 'e.g., FuelCorp Inc.'}),
            'product': forms.Select(attrs={'class': ''}),
            'quantity_litres': forms.NumberInput(attrs={'class': '', 'step': '0.01'}),
            'price_per_litre': forms.NumberInput(attrs={'class': '', 'step': '0.001'}),
            'notes': forms.Textarea(attrs={'class': '', 'rows': 3, 'placeholder': 'Optional notes about the shipment...'}),
        }


# --- Forms and Formset for Trips (Stock Out) ---

# Form for the main Trip details
class TripForm(forms.ModelForm):
    class Meta:
        model = Trip
        fields = ['vehicle', 'customer', 'product', 'loading_date', 'loading_time', 'bol_number', 'status', 'notes']
        widgets = {
            'vehicle': forms.Select(attrs={'class': ''}),
            'customer': forms.Select(attrs={'class': ''}),
            'product': forms.Select(attrs={'class': ''}),
            'loading_date': forms.DateInput(attrs={'type': 'date', 'class': ''}),
            'loading_time': forms.TimeInput(attrs={'type': 'time', 'class': ''}),
            'bol_number': forms.TextInput(attrs={'class': '', 'placeholder': 'Bill of Lading Number'}),
            'status': forms.Select(attrs={'class': ''}),
            'notes': forms.Textarea(attrs={'class': '', 'rows': 3, 'placeholder': 'Notes for this loading...'}),
        }
        labels = {
            'loading_date': 'BOL Date',
            'loading_time': 'BOL Time',
            'bol_number': 'BOL Number',
            'status': 'Loading Status',
        }


# Form for individual Loading Compartment details
class LoadingCompartmentForm(forms.ModelForm):
    class Meta:
        model = LoadingCompartment
        # Ensure BOTH fields are listed here
        fields = ['compartment_number', 'quantity_requested_litres'] 
        widgets = {
            'compartment_number': forms.NumberInput(attrs={
                'class': 'w-20 inline-block mr-2 bg-gray-100 text-gray-700 border-gray-300', # Styled as read-only
                'readonly': True, 
                'tabindex': '-1', # Remove from tab order
            }),
            # This widget should be for 'quantity_requested_litres'
            'quantity_requested_litres': forms.NumberInput(attrs={'class': 'w-40 inline-block', 'step': '0.01', 'placeholder': 'Quantity (L)', 'min': '0'}),
        }
        labels = {
            'compartment_number': 'Comp. #',
            'quantity_requested_litres': 'Qty Requested (L)', # Correct label for the quantity field
        }


# Formset to manage multiple LoadingCompartment forms under a single Trip form
LoadingCompartmentFormSet = inlineformset_factory(
    Trip,
    LoadingCompartment,
    form=LoadingCompartmentForm,
    # Ensure BOTH fields are listed here if you want the formset to manage them
    fields=['compartment_number', 'quantity_requested_litres'], 
    extra=3,
    min_num=3,
    validate_min=True,
    max_num=3,
    can_delete=False 
)