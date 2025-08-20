# shipments/forms.py
from django import forms
from django.forms import inlineformset_factory
from django.core.validators import FileExtensionValidator
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
            'vessel_id_tag': forms.TextInput(attrs={
                'class': 'form-control', 
                'placeholder': 'Unique vessel/batch ID'
            }),
            'import_date': forms.DateInput(attrs={
                'type': 'date', 
                'class': 'form-control'
            }),
            'supplier_name': forms.TextInput(attrs={
                'class': 'form-control', 
                'placeholder': 'Supplier company name'
            }),
            'product': forms.Select(attrs={'class': 'form-control'}),
            'destination': forms.Select(attrs={'class': 'form-control'}),
            'quantity_litres': forms.NumberInput(attrs={
                'class': 'form-control', 
                'step': '0.01', 
                'placeholder': 'Quantity in litres'
            }),
            'price_per_litre': forms.NumberInput(attrs={
                'class': 'form-control', 
                'step': '0.001', 
                'placeholder': 'Price per litre in USD'
            }),
            'notes': forms.Textarea(attrs={
                'class': 'form-control', 
                'rows': 3, 
                'placeholder': 'Additional notes or comments'
            }),
        }

    def clean_quantity_litres(self):
        quantity = self.cleaned_data.get('quantity_litres')
        if quantity is not None and quantity <= 0:
            raise forms.ValidationError("Quantity must be greater than zero.")
        return quantity

    def clean_price_per_litre(self):
        price = self.cleaned_data.get('price_per_litre')
        if price is not None and price < 0:
            raise forms.ValidationError("Price cannot be negative.")
        return price


# --- Forms for Trips (Stock Out) ---
class TripForm(forms.ModelForm):
    class Meta:
        model = Trip
        fields = [
            'kpc_order_number', 'bol_number', 'loading_date', 'loading_time',
            'customer', 'destination', 'vehicle', 'product', 'status', 'notes'
        ]
        widgets = {
            'kpc_order_number': forms.TextInput(attrs={
                'class': 'form-control', 
                'placeholder': 'KPC Loading Order No (e.g., S02106)'
            }),
            'bol_number': forms.TextInput(attrs={
                'class': 'form-control', 
                'placeholder': 'Final BoL Number (optional)'
            }),
            'loading_date': forms.DateInput(attrs={
                'type': 'date', 
                'class': 'form-control'
            }),
            'loading_time': forms.TimeInput(attrs={
                'type': 'time', 
                'class': 'form-control'
            }),
            'customer': forms.Select(attrs={'class': 'form-control'}),
            'destination': forms.Select(attrs={'class': 'form-control'}),
            'vehicle': forms.Select(attrs={'class': 'form-control'}),
            'product': forms.Select(attrs={'class': 'form-control'}),
            'status': forms.Select(attrs={'class': 'form-control'}),
            'notes': forms.Textarea(attrs={
                'class': 'form-control', 
                'rows': 3, 
                'placeholder': 'Trip notes or special instructions'
            }),
        }

    def clean_kpc_order_number(self):
        kpc_order = self.cleaned_data.get('kpc_order_number')
        if kpc_order:
            kpc_order = kpc_order.strip().upper()
            if len(kpc_order) < 3:
                raise forms.ValidationError("KPC Order Number must be at least 3 characters.")
        return kpc_order


# FormSet for Loading Compartments
LoadingCompartmentFormSet = inlineformset_factory(
    Trip, LoadingCompartment,
    fields=['compartment_number', 'quantity_requested_litres'],
    extra=1,
    can_delete=True,
    widgets={
        'compartment_number': forms.NumberInput(attrs={
            'class': 'form-control',
            'min': '1',
            'max': '10',
            'placeholder': 'Compartment number (1-10)'
        }),
        'quantity_requested_litres': forms.NumberInput(attrs={
            'class': 'form-control', 
            'step': '0.01', 
            'placeholder': 'Quantity in litres'
        }),
    }
)


# FORM FOR SINGLE PDF UPLOAD (Loading Authority - Trips)
class PdfLoadingAuthorityUploadForm(forms.Form):
    pdf_file = forms.FileField(
        label="Upload Export Loading Authority PDF",
        help_text="Upload a PDF file containing the export loading authority document. Maximum file size: 10MB.",
        validators=[FileExtensionValidator(allowed_extensions=['pdf'])],
        widget=forms.FileInput(attrs={
            'accept': '.pdf', 
            'class': 'form-control'
        })
    )

    def clean_pdf_file(self):
        pdf_file = self.cleaned_data.get('pdf_file')
        
        if pdf_file:
            # Check file size (10MB limit)
            if pdf_file.size > 10 * 1024 * 1024:
                raise forms.ValidationError("File size cannot exceed 10MB.")
            
            # Check file type
            if not pdf_file.name.lower().endswith('.pdf'):
                raise forms.ValidationError("Only PDF files are allowed.")
            
            # Check content type
            if pdf_file.content_type != 'application/pdf':
                raise forms.ValidationError("Invalid file type. Please upload a PDF file.")
        
        return pdf_file


# FORM FOR BULK PDF UPLOAD (Loading Authority - Trips)
class BulkPdfLoadingAuthorityUploadForm(forms.Form):
    pdf_files = MultipleFileField(
        label="Upload Multiple Export Loading Authority PDFs",
        help_text="Select multiple PDF files to upload at once. Maximum 10 files, 10MB each.",
        widget=MultipleFileInput(attrs={
            'accept': '.pdf', 
            'class': 'form-control'
        })
    )

    def clean_pdf_files(self):
        files = self.cleaned_data.get('pdf_files', [])
        
        if not files:
            raise forms.ValidationError("Please select at least one PDF file.")

        if len(files) > 10:
            raise forms.ValidationError("Maximum 10 files allowed per upload.")

        for file in files:
            # Check file extension
            if not file.name.lower().endswith('.pdf'):
                raise forms.ValidationError(f"File '{file.name}' is not a PDF.")
            
            # Check file size
            if file.size > 10 * 1024 * 1024:  # 10MB limit
                raise forms.ValidationError(f"File '{file.name}' is too large. Maximum size is 10MB.")
            
            # Check content type
            if file.content_type != 'application/pdf':
                raise forms.ValidationError(f"File '{file.name}' has invalid content type.")

        return files


# FORM FOR TR830 PDF UPLOAD (Shipments)
class TR830UploadForm(forms.Form):
    """Enhanced TR830 upload form with better validation and user experience"""

    tr830_pdf = forms.FileField(
        label="Upload TR830 PDF Document",
        help_text="Upload the KRA TR830 PDF document containing received entries per product/destination. Maximum file size: 10MB.",
        validators=[FileExtensionValidator(allowed_extensions=['pdf'])],
        widget=forms.FileInput(attrs={
            'accept': '.pdf',
            'class': 'form-control file-input',
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
            'class': 'form-control form-input',
            'placeholder': 'e.g., Kuwait Petroleum Corporation'
        })
    )

    default_price_per_litre = forms.DecimalField(
        max_digits=10,
        decimal_places=3,
        required=False,
        initial=Decimal('0.000'),
        label="Default Price per Litre (USD)",
        help_text="Price per litre if not found in PDF. Use 0.000 if unknown.",
        widget=forms.NumberInput(attrs={
            'class': 'form-control form-input',
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
            pdf_file.seek(0)  # Reset file pointer
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
        help_text="Select multiple TR830 PDF files to upload at once. Maximum 10 files, 10MB each.",
        widget=MultipleFileInput(attrs={
            'accept': '.pdf',
            'class': 'form-control',
            'multiple': True,
            'id': 'bulk-tr830-files'
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
        max_digits=10,
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
        """Validate uploaded files"""
        files = self.files.getlist('tr830_files')
        
        if not files:
            raise forms.ValidationError("Please select at least one PDF file.")
        
        if len(files) > 10:
            raise forms.ValidationError("Maximum 10 files allowed per upload.")
        
        for file in files:
            # Check file size
            if file.size > 10 * 1024 * 1024:
                raise forms.ValidationError(f"File '{file.name}' exceeds 10MB limit.")
            
            # Check file type
            if not file.name.lower().endswith('.pdf'):
                raise forms.ValidationError(f"File '{file.name}' is not a PDF.")
            
            # Check content type
            if file.content_type != 'application/pdf':
                raise forms.ValidationError(f"File '{file.name}' has invalid content type.")
            
            # Basic PDF validation
            try:
                file.seek(0)
                header = file.read(5)
                if header != b'%PDF-':
                    raise forms.ValidationError(f"File '{file.name}' is not a valid PDF.")
                file.seek(0)  # Reset file pointer
            except Exception:
                raise forms.ValidationError(f"Unable to read PDF file '{file.name}'.")
        
        return files

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


# --- Search and Filter Forms ---
class ShipmentSearchForm(forms.Form):
    """Form for searching and filtering shipments"""
    
    search = forms.CharField(
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Search by vessel ID, supplier, or notes...'
        })
    )
    
    product = forms.ModelChoiceField(
        queryset=Product.objects.all(),
        required=False,
        empty_label="All Products",
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    
    destination = forms.ModelChoiceField(
        queryset=Destination.objects.all(),
        required=False,
        empty_label="All Destinations",
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    
    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-control'
        })
    )
    
    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-control'
        })
    )


class TripSearchForm(forms.Form):
    """Form for searching and filtering trips"""
    
    search = forms.CharField(
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Search by KPC order number, BoL number, or notes...'
        })
    )
    
    customer = forms.ModelChoiceField(
        queryset=Customer.objects.all(),
        required=False,
        empty_label="All Customers",
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    
    destination = forms.ModelChoiceField(
        queryset=Destination.objects.all(),
        required=False,
        empty_label="All Destinations",
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    
    vehicle = forms.ModelChoiceField(
        queryset=Vehicle.objects.all(),
        required=False,
        empty_label="All Vehicles",
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    
    product = forms.ModelChoiceField(
        queryset=Product.objects.all(),
        required=False,
        empty_label="All Products",
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    
    status = forms.ChoiceField(
        choices=[('', 'All Statuses')] + Trip.STATUS_CHOICES,
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    
    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-control'
        })
    )
    
    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-control'
        })
    )


# --- Quick Action Forms ---
class QuickShipmentStatusForm(forms.Form):
    """Quick form to update shipment - simplified since Shipment doesn't have status choices"""
    
    notes = forms.CharField(
        max_length=500,
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 2,
            'placeholder': 'Update shipment notes...'
        })
    )


class QuickTripStatusForm(forms.Form):
    """Quick form to update trip status"""
    
    status = forms.ChoiceField(
        choices=Trip.STATUS_CHOICES,
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    
    notes = forms.CharField(
        max_length=500,
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 2,
            'placeholder': 'Optional status update notes...'
        })
    )


# --- Utility Forms ---
class DateRangeForm(forms.Form):
    """Form for selecting date ranges in reports"""
    
    start_date = forms.DateField(
        label="Start Date",
        widget=forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-control'
        })
    )
    
    end_date = forms.DateField(
        label="End Date",
        widget=forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-control'
        })
    )
    
    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')
        
        if start_date and end_date and start_date > end_date:
            raise forms.ValidationError("Start date cannot be after end date.")
        
        return cleaned_data