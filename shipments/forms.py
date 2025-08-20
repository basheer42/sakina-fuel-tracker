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
        return kpc_order_val.upper() if kpc_order_val else kpc_order_val


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

# FORM FOR PDF UPLOAD (Loading Authority - Trips)
class PdfLoadingAuthorityUploadForm(forms.Form):
    pdf_file = forms.FileField(
        label="Upload Export Loading Authority PDF",
        help_text="Upload the PDF document containing the loading authority details.",
        widget=forms.FileInput(attrs={'accept': '.pdf', 'class': 'form-control'})
    )

# FORM FOR BULK PDF UPLOAD (Loading Authority - Trips)
class BulkPdfLoadingAuthorityUploadForm(forms.Form):
    pdf_files = MultipleFileField(
        label="Upload Multiple Export Loading Authority PDFs",
        help_text="Select multiple PDF files to upload at once. Maximum 10 files.",
        widget=MultipleFileInput(attrs={'accept': '.pdf', 'class': 'form-control'})
    )

    def clean_pdf_files(self):
        files = self.cleaned_data.get('pdf_files', [])
        if not files:
            raise forms.ValidationError("Please select at least one PDF file.")

        if len(files) > 10:
            raise forms.ValidationError("Maximum 10 files allowed.")

        for file in files:
            if not file.name.lower().endswith('.pdf'):
                raise forms.ValidationError(f"File '{file.name}' is not a PDF.")
            if file.size > 10 * 1024 * 1024:  # 10MB limit
                raise forms.ValidationError(f"File '{file.name}' is too large. Maximum size is 10MB.")

        return files


# FORM FOR TR830 PDF UPLOAD (Shipments)
class TR830UploadForm(forms.Form):
    """Enhanced TR830 upload form with better validation and user experience"""

    tr830_pdf = forms.FileField(
        label="Upload TR830 PDF Document",
        help_text="Upload the KRA TR830 PDF document containing received entries per product/destination. Maximum file size: 10MB.",
        widget=forms.FileInput(attrs={
            'accept': '.pdf',
            'class': 'form-control',
            'id': 'tr830-file-input'
        })
    )

    default_supplier = forms.CharField(
        max_length=200,
        required=False,
        initial="KPC",
        label="Default Supplier Name",
        help_text="Supplier name to use if not found in PDF. Defaults to 'KPC'.",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g., Kuwait Petroleum Corporation'
        })
    )

    default_price_per_litre = forms.DecimalField(
        max_digits=7,
        decimal_places=3,
        required=False,
        initial=Decimal('0.000'),
        label="Default Price per Litre (USD)",
        help_text="Price per litre if not found in PDF. Use 0.000 if unknown.",
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'step': '0.001',
            'placeholder': '0.000',
            'min': '0'
        })
    )

    def clean_tr830_pdf(self):
        """Enhanced PDF validation"""
        pdf_file = self.cleaned_data.get('tr830_pdf')

        if not pdf_file:
            raise forms.ValidationError("Please select a PDF file.")

        # File extension validation
        if not pdf_file.name.lower().endswith('.pdf'):
            raise forms.ValidationError("Only PDF files are allowed.")

        # File size validation (10MB limit)
        if pdf_file.size > 10 * 1024 * 1024:
            raise forms.ValidationError("File size must be less than 10MB.")

        # Basic PDF content validation
        try:
            pdf_file.seek(0)
            header = pdf_file.read(5)
            if header != b'%PDF-':
                raise forms.ValidationError("Invalid PDF file format.")
            pdf_file.seek(0)
        except Exception:
            raise forms.ValidationError("Unable to read PDF file.")

        return pdf_file

    def clean_default_supplier(self):
        """Validate supplier name"""
        supplier = self.cleaned_data.get('default_supplier', '').strip()
        if supplier and len(supplier) < 2:
            raise forms.ValidationError("Supplier name must be at least 2 characters long.")
        return supplier or "KPC"

    def clean_default_price_per_litre(self):
        """Validate price"""
        price = self.cleaned_data.get('default_price_per_litre')
        if price is not None and price < 0:
            raise forms.ValidationError("Price cannot be negative.")
        return price or Decimal('0.000')


# FORM FOR BULK TR830 PDF UPLOAD
class BulkTR830UploadForm(forms.Form):
    """Enhanced bulk TR830 upload form"""

    tr830_files = MultipleFileField(
        label="Upload Multiple TR830 PDF Documents",
        help_text="Select multiple TR830 PDF files to upload at once. Maximum 5 files, 10MB each.",
        widget=MultipleFileInput(attrs={
            'accept': '.pdf',
            'class': 'form-control',
            'multiple': True
        })
    )

    default_supplier = forms.CharField(
        max_length=200,
        required=False,
        initial="KPC",
        label="Default Supplier Name",
        help_text="Fallback supplier name for all uploads if not found in PDFs.",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g., Kuwait Petroleum Corporation'
        })
    )

    default_price_per_litre = forms.DecimalField(
        max_digits=7,
        decimal_places=3,
        required=False,
        initial=Decimal('0.000'),
        label="Default Price per Litre (USD)",
        help_text="Default price for all uploads if not found in PDFs.",
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'step': '0.001',
            'placeholder': '0.000',
            'min': '0'
        })
    )

    def clean_tr830_files(self):
        """Enhanced file validation for bulk upload"""
        files = self.cleaned_data.get('tr830_files', [])

        if not files:
            raise forms.ValidationError("Please select at least one PDF file.")

        if len(files) > 5:
            raise forms.ValidationError("Maximum 5 files allowed for TR830 bulk upload.")

        for file in files:
            # Extension check
            if not file.name.lower().endswith('.pdf'):
                raise forms.ValidationError(f"File '{file.name}' is not a PDF.")

            # Size check
            if file.size > 10 * 1024 * 1024:
                raise forms.ValidationError(f"File '{file.name}' is too large. Maximum size is 10MB.")

            # Basic content check
            try:
                file.seek(0)
                header = file.read(5)
                if header != b'%PDF-':
                    raise forms.ValidationError(f"File '{file.name}' is not a valid PDF.")
                file.seek(0)
            except Exception:
                raise forms.ValidationError(f"Unable to read file '{file.name}'.")

        return files

    def clean_default_supplier(self):
        """Validate supplier name"""
        supplier = self.cleaned_data.get('default_supplier', '').strip()
        return supplier or "KPC"

    def clean_default_price_per_litre(self):
        """Validate price"""
        price = self.cleaned_data.get('default_price_per_litre')
        if price is not None and price < 0:
            raise forms.ValidationError("Price cannot be negative.")
        return price or Decimal('0.000')