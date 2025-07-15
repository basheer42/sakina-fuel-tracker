# shipments/forms.py
from django import forms
from django.forms import inlineformset_factory
from .models import Shipment, Trip, LoadingCompartment, Product, Customer, Vehicle, Destination
from decimal import Decimal


# Custom widget for multiple file uploads
class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True

    def __init__(self, attrs=None):
        if attrs is None:
            attrs = {}
        attrs['multiple'] = True
        super().__init__(attrs)

    def value_from_datadict(self, data, files, name):
        upload = files.getlist(name)
        if not upload:
            return None
        return upload


# Custom field for multiple file uploads
class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_file_clean = super().clean
        if isinstance(data, (list, tuple)):
            result = [single_file_clean(d, initial) for d in data]
        else:
            result = single_file_clean(data, initial)
        return result


# --- Forms for Shipments (Stock In) ---
class ShipmentForm(forms.ModelForm):
    class Meta:
        model = Shipment
        fields = [
            'vessel_id_tag', 'import_date', 'supplier_name',
            'product', 'destination', 'quantity_litres',
            'price_per_litre', 'notes'
        ]
        widgets = {
            'vessel_id_tag': forms.TextInput(attrs={'class': '', 'placeholder': 'Unique vessel/batch ID'}),
            'import_date': forms.DateInput(attrs={'type': 'date', 'class': ''}),
            'supplier_name': forms.TextInput(attrs={'class': '', 'placeholder': 'Supplier company name'}),
            'product': forms.Select(attrs={'class': ''}),
            'destination': forms.Select(attrs={'class': ''}),
            'quantity_litres': forms.NumberInput(attrs={'class': '', 'step': '0.01', 'placeholder': 'Quantity in litres'}),
            'price_per_litre': forms.NumberInput(attrs={'class': '', 'step': '0.001', 'placeholder': 'Price per litre (USD)'}),
            'notes': forms.Textarea(attrs={'class': '', 'rows': 3, 'placeholder': 'Additional notes about this shipment...'}),
        }
        labels = {
            'vessel_id_tag': 'Vessel/Batch ID',
            'import_date': 'Import Date',
            'supplier_name': 'Supplier Name',
            'quantity_litres': 'Quantity (Litres)',
            'price_per_litre': 'Price per Litre (USD)',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'product' in self.fields:
            self.fields['product'].queryset = Product.objects.all().order_by('name')
        if 'destination' in self.fields:
            self.fields['destination'].queryset = Destination.objects.all().order_by('name')

    def clean_quantity_litres(self):
        quantity = self.cleaned_data.get('quantity_litres')
        if quantity and quantity <= 0:
            raise forms.ValidationError("Quantity must be greater than zero.")
        return quantity

    def clean_price_per_litre(self):
        price = self.cleaned_data.get('price_per_litre')
        if price and price <= 0:
            raise forms.ValidationError("Price per litre must be greater than zero.")
        return price


# --- Forms for Trips (Stock Out) ---
class TripForm(forms.ModelForm):
    class Meta:
        model = Trip
        fields = [
            'loading_date', 'loading_time', 'vehicle', 'customer',
            'product', 'destination', 'kpc_order_number', 'status', 'notes', 'kpc_comments'
        ]
        widgets = {
            'loading_date': forms.DateInput(attrs={'type': 'date', 'class': ''}),
            'loading_time': forms.TimeInput(attrs={'type': 'time', 'class': ''}),
            'vehicle': forms.Select(attrs={'class': ''}),
            'customer': forms.Select(attrs={'class': ''}),
            'product': forms.Select(attrs={'class': ''}),
            'destination': forms.Select(attrs={'class': ''}),
            'kpc_order_number': forms.TextInput(attrs={'class': '', 'placeholder': 'Loading Order No. (e.g. S0xxxx)'}),
            'status': forms.Select(attrs={'class': ''}),
            'notes': forms.Textarea(attrs={'class': '', 'rows': 3, 'placeholder': 'Notes for this loading...'}),
            'kpc_comments': forms.Textarea(attrs={'class': '', 'rows': 2, 'placeholder': 'Comments from KPC updates...'}),
        }
        labels = {
            'loading_date': 'Authority/BOL Date',
            'loading_time': 'Authority/BOL Time',
            'kpc_order_number': 'KPC Order No. / Initial BoL',
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

    def clean_kpc_order_number(self):
        kpc_order_val = self.cleaned_data.get('kpc_order_number')
        if kpc_order_val and not kpc_order_val.startswith('S'):
            raise forms.ValidationError("KPC Loading Order Number must start with 'S'.")
        return kpc_order_val.upper() if kpc_order_val else kmp_order_val


class LoadingCompartmentForm(forms.ModelForm):
    class Meta:
        model = LoadingCompartment
        fields = ['compartment_number', 'quantity_requested_litres']
        widgets = {
            'compartment_number': forms.NumberInput(attrs={
                'class': 'w-20 inline-block mr-2 bg-gray-100 text-gray-700 border-gray-300',
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

# FORM FOR PDF UPLOAD
class PdfLoadingAuthorityUploadForm(forms.Form):
    pdf_file = forms.FileField(
        label="Upload Export Loading Authority PDF",
        help_text="Upload the PDF document containing the loading authority details.",
        widget=forms.FileInput(attrs={
            'class': 'mt-1 block w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded-md file:border-0 file:text-sm file:font-semibold file:bg-blue-100 file:text-blue-700 hover:file:bg-blue-200 cursor-pointer',
            'accept': '.pdf'
        })
    )

# FORM FOR BULK PDF UPLOAD - ✅ FIXED
class BulkPdfLoadingAuthorityUploadForm(forms.Form):
    pdf_files = MultipleFileField(
        label="Upload Multiple Loading Authority PDFs",
        help_text="Select multiple PDF documents containing loading authority details. Hold Ctrl/Cmd to select multiple files.",
        widget=MultipleFileInput(attrs={
            'class': 'mt-1 block w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded-md file:border-0 file:text-sm file:font-semibold file:bg-blue-100 file:text-blue-700 hover:file:bg-blue-200 cursor-pointer',
            'accept': '.pdf'
        })
    )

    def clean_pdf_files(self):
        files = self.cleaned_data.get('pdf_files')
        
        if not files:
            raise forms.ValidationError("Please select at least one PDF file.")

        # Validate each file
        for file in files:
            if not file.name.lower().endswith('.pdf'):
                raise forms.ValidationError(f"File '{file.name}' is not a PDF. Please upload only PDF files.")

            # Check file size (limit to 10MB per file)
            if file.size > 10 * 1024 * 1024:
                raise forms.ValidationError(f"File '{file.name}' is too large. Maximum size is 10MB per file.")

        return files