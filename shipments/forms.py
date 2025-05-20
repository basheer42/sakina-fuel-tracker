# shipments/forms.py
from django import forms
from django.forms.models import inlineformset_factory
# Import the models needed for forms, including Destination
from .models import Shipment, Product, Trip, LoadingCompartment, Customer, Vehicle, Destination


# Form for Shipments (Stock In)
class ShipmentForm(forms.ModelForm):
    # Explicitly define destination to ensure it uses ModelChoiceField and can be ordered
    destination = forms.ModelChoiceField(
        queryset=Destination.objects.all().order_by('name'), 
        required=False, # Consistent with model's null=True, blank=True for Shipment
        widget=forms.Select(attrs={'class': ''}) # Add your Tailwind classes here if needed
    )

    class Meta:
        model = Shipment
        fields = [
            'vessel_id_tag',     # Added in a previous step for duplicate check
            'import_date', 
            'supplier_name', 
            'product',
            'destination',       # NEWLY ADDED FIELD to the form
            'quantity_litres', 
            'price_per_litre', 
            'notes'
        ]
        widgets = {
            'vessel_id_tag': forms.TextInput(attrs={'class': '', 'placeholder': 'e.g., AGO KG09/2025'}),
            'import_date': forms.DateInput(attrs={'type': 'date', 'class': ''}),
            'supplier_name': forms.TextInput(attrs={'class': '', 'placeholder': 'e.g., FuelCorp Inc.'}),
            'product': forms.Select(attrs={'class': ''}),
            # 'destination' widget is handled by the explicit field definition above
            'quantity_litres': forms.NumberInput(attrs={'class': '', 'step': '0.01'}),
            'price_per_litre': forms.NumberInput(attrs={'class': '', 'step': '0.001'}),
            'notes': forms.Textarea(attrs={'class': '', 'rows': 3, 'placeholder': 'Optional notes about the shipment...'}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'product' in self.fields:
            self.fields['product'].queryset = Product.objects.all().order_by('name')
        # Queryset for destination is set in its explicit field definition


# --- Forms and Formset for Trips (Stock Out) ---
class TripForm(forms.ModelForm):
    # Explicitly define destination
    destination = forms.ModelChoiceField(
        queryset=Destination.objects.all().order_by('name'),
        required=True, # As per Trip model (blank=False for destination)
        widget=forms.Select(attrs={'class': ''})
    )

    class Meta:
        model = Trip
        fields = [
            'vehicle', 'customer', 'product', 
            'destination', # ADDED destination field
            'loading_date', 'loading_time', 
            'bol_number', # This will initially store KPC Order No (e.g., Sxxxxx)
            'status', 
            'notes',
            'kpc_comments' # Added from model update
        ]
        widgets = {
            'vehicle': forms.Select(attrs={'class': ''}),
            'customer': forms.Select(attrs={'class': ''}),
            'product': forms.Select(attrs={'class': ''}),
            # 'destination' widget handled by explicit field
            'loading_date': forms.DateInput(attrs={'type': 'date', 'class': ''}),
            'loading_time': forms.TimeInput(attrs={'type': 'time', 'class': ''}),
            'bol_number': forms.TextInput(attrs={'class': '', 'placeholder': 'KPC Order No. (e.g. S0xxxx)'}),
            'status': forms.Select(attrs={'class': ''}),
            'notes': forms.Textarea(attrs={'class': '', 'rows': 3, 'placeholder': 'Notes for this loading...'}),
            'kpc_comments': forms.Textarea(attrs={'class': '', 'rows': 2, 'placeholder': 'Comments from KPC updates...'}),
        }
        labels = {
            'loading_date': 'Authority/BOL Date',
            'loading_time': 'Authority/BOL Time',
            'bol_number': 'KPC Order No / Final BOL', 
            'status': 'Loading Status',
            'kpc_comments': 'KPC Comments'
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'vehicle' in self.fields:
            self.fields['vehicle'].queryset = Vehicle.objects.all().order_by('plate_number')
        if 'customer' in self.fields:
            self.fields['customer'].queryset = Customer.objects.all().order_by('name')
        if 'product' in self.fields:
            self.fields['product'].queryset = Product.objects.all().order_by('name')


class LoadingCompartmentForm(forms.ModelForm):
    class Meta:
        model = LoadingCompartment
        fields = ['compartment_number', 'quantity_requested_litres'] 
        widgets = {
            'compartment_number': forms.NumberInput(attrs={
                'class': 'w-20 inline-block mr-2 bg-gray-100 text-gray-700 border-gray-300', # Your original classes
                'readonly': True, 
                'tabindex': '-1',
            }),
            'quantity_requested_litres': forms.NumberInput(attrs={'class': 'w-40 inline-block', 'step': '0.01', 'placeholder': 'Quantity (L)', 'min': '0'}),
        }
        labels = {
            'compartment_number': 'Comp. #',
            'quantity_requested_litres': 'Qty Requested (L)', 
        }

LoadingCompartmentFormSet = inlineformset_factory(
    Trip, LoadingCompartment, form=LoadingCompartmentForm,
    fields=['compartment_number', 'quantity_requested_litres'], 
    extra=3, min_num=3, validate_min=True, max_num=3, can_delete=False
)

# NEW FORM FOR PDF UPLOAD
class PdfLoadingAuthorityUploadForm(forms.Form):
    pdf_file = forms.FileField(
        label="Upload Export Loading Authority PDF",
        help_text="Upload the PDF document containing the loading authority details.", # Corrected
        widget=forms.ClearableFileInput(attrs={
            'class': 'mt-1 block w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded-md file:border-0 file:text-sm file:font-semibold file:bg-blue-100 file:text-blue-700 hover:file:bg-blue-200 cursor-pointer',
            'accept': '.pdf'
            })
    )
    # Example of adding an initial status if needed, can be set in the view too
    # initial_status = forms.ChoiceField(
    #     choices=Trip.STATUS_CHOICES, # Make sure Trip model is accessible or choices are defined here
    #     initial='PENDING', 
    #     required=False,
    #     widget=forms.HiddenInput() # Or a Select if user should choose
    # )