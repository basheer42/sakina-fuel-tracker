# Sakina Gas Fuel Tracker

A comprehensive Django-based fuel inventory management system designed for Sakina Gas Company to track fuel shipments, vehicle loadings, and stock management across East Africa.

![Django](https://img.shields.io/badge/Django-5.2.1-green.svg)
![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)
![License](https://img.shields.io/badge/License-Proprietary-red.svg)

## 🚀 Features

### Core Functionality
- **Shipment Management**: Track fuel imports with vessel IDs, quantities, pricing, and destinations
- **Loading Operations**: Manage truck loadings with KPC order integration and compartment details
- **Stock Tracking**: Real-time inventory management with FIFO depletion system
- **Email Integration**: Automated processing of KPC status updates and Bill of Lading PDFs
- **PDF Parsing**: Intelligent extraction of loading authority data from KPC documents

### Advanced Features
- **Multi-Destination Support**: Handle fuel distribution to South Sudan, DRC, and local markets
- **Truck Activity Dashboard**: Monitor vehicle utilization and loading patterns
- **Monthly Reporting**: Comprehensive stock movement analysis
- **Aging Stock Alerts**: Proactive notifications for inventory optimization
- **User Role Management**: Admin/Viewer permissions with data isolation

### Technical Features
- **Real-time Stock Calculation**: Automatic depletion tracking with status-based triggers
- **Data Validation**: Comprehensive input validation and error handling
- **Caching System**: Optimized performance for dashboard calculations
- **Audit Trails**: Complete tracking of stock movements and user actions
- **Security**: CSRF protection, SQL injection prevention, file upload validation

## 🏗️ System Architecture

### Technology Stack
- **Backend**: Django 5.2.1 with Python 3.8+
- **Database**: PostgreSQL (production) / SQLite (development)
- **Frontend**: TailwindCSS with responsive design
- **Charts**: Chart.js for data visualization
- **PDF Processing**: pdfplumber for document parsing
- **Email**: IMAP integration for automated processing

### Key Models
- **Shipment**: Fuel imports and inventory batches
- **Trip**: Loading operations and truck dispatches
- **LoadingCompartment**: Compartment-level quantity tracking
- **ShipmentDepletion**: Stock usage tracking with FIFO logic
- **Vehicle/Customer/Product**: Master data management

## 📋 Prerequisites

- Python 3.8 or higher
- pip (Python package manager)
- PostgreSQL (for production) or SQLite (for development)
- Virtual environment tool (venv, virtualenv, or conda)

## 🚀 Installation

### 1. Clone the Repository
```bash
git clone <repository-url>
cd fuel_tracker
```

### 2. Create Virtual Environment
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Environment Configuration
```bash
cp .env.example .env
```

Edit `.env` file with your settings:
```env
SECRET_KEY=your-secret-key-here
DEBUG=True
DATABASE_ENGINE=sqlite  # or postgresql
DB_NAME=fuel_tracker_db
DB_USER=your_db_user
DB_PASSWORD=your_db_password

# Email settings for KPC integration
EMAIL_PROCESSING_ENABLED=False
EMAIL_PROCESSING_HOST=imap.gmail.com
EMAIL_PROCESSING_USER=your-email@company.com
EMAIL_PROCESSING_PASSWORD=your-app-password
```

### 5. Database Setup
```bash
python manage.py migrate
python manage.py createsuperuser
```

### 6. Load Initial Data (Optional)
```bash
python manage.py shell
```
```python
from shipments.models import Product, Destination
Product.objects.create(name='PMS')
Product.objects.create(name='AGO')
Destination.objects.create(name='South Sudan')
Destination.objects.create(name='DRC')
Destination.objects.create(name='Local Nairobi')
```

### 7. Run Development Server
```bash
python manage.py runserver
```

Visit `http://localhost:8000` to access the application.

## 🔧 Configuration

### User Groups Setup
Create user groups with appropriate permissions:

1. **Admin Group**: Full access to all features
2. **Viewer Group**: Read-only access to data

```bash
python manage.py shell
```
```python
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from shipments.models import Shipment, Trip

# Create Admin group with all permissions
admin_group = Group.objects.create(name='Admin')
permissions = Permission.objects.filter(content_type__app_label='shipments')
admin_group.permissions.set(permissions)

# Create Viewer group with view-only permissions
viewer_group = Group.objects.create(name='Viewer')
view_permissions = permissions.filter(codename__startswith='view_')
viewer_group.permissions.set(view_permissions)
```

### Email Processing Setup
For automated KPC integration:

1. Enable email processing in `.env`:
```env
EMAIL_PROCESSING_ENABLED=True
EMAIL_STATUS_UPDATE_SENDER_FILTER=notifications@kpc.co.ke
EMAIL_BOL_SENDER_FILTER=bol@kpc.co.ke
```

2. Set up scheduled tasks:
```bash
# Add to crontab for regular processing
*/15 * * * * cd /path/to/project && python manage.py process_status_emails
*/30 * * * * cd /path/to/project && python manage.py process_bol_emails
```

## 📖 Usage Guide

### Adding Shipments
1. Navigate to **Shipments** → **Add New Shipment**
2. Fill in vessel ID, supplier, product, destination, and quantities
3. System automatically calculates total cost and sets quantity remaining

### Recording Loadings
1. **Method 1**: Upload KPC Loading Authority PDF
   - Go to **Upload Loading Authority**
   - System automatically extracts order details and creates trip
   
2. **Method 2**: Manual Entry
   - Navigate to **Loadings** → **Add New Loading**
   - Enter KPC order number, vehicle, customer details
   - Add compartment quantities (exactly 3 required)

### Stock Management
- Stock is automatically depleted when trips reach `KPC_APPROVED` status
- L20 actuals from BoL PDFs trigger re-calculation when status becomes `LOADED`
- FIFO system ensures oldest stock is used first
- Real-time stock levels shown on dashboard

### Email Integration
The system processes two types of emails:
- **Status Updates**: Auto-update trip status from KPC notifications
- **BoL PDFs**: Extract actual loading quantities and reconcile stock

## 🛠️ Management Commands

### Email Processing
```bash
# Process KPC status update emails
python manage.py process_status_emails

# Process BoL PDF emails
python manage.py process_bol_emails
```

### Batch Operations
```bash
# Run both email processors
./run_email_processors.bat  # Windows
```

## 📊 Dashboard Features

### Stock Summary
- Real-time inventory by product and destination
- Available vs. committed stock calculation
- Truck capacity analysis (PMS: 40,000L, AGO: 36,000L)

### Activity Charts
- 30-day loading trends by product
- Delivery statistics and turnover rates

### Smart Notifications
- **Aging Stock**: Items older than 25 days
- **Inactive Products**: No movement for 5+ days
- **Utilized Shipments**: Completed batches ready for archival

## 🔒 Security Features

- CSRF protection on all forms
- SQL injection prevention
- File upload validation (PDF type checking)
- User-based data isolation (non-admin users see only their data)
- Permission-based access control
- Audit logging for all stock movements

## 🚀 Production Deployment

### 1. Environment Setup
```env
DEBUG=False
DATABASE_ENGINE=postgresql
SECURE_SSL_REDIRECT=True
ALLOWED_HOSTS=your-domain.com
```

### 2. Static Files
```bash
python manage.py collectstatic
```

### 3. Database Migration
```bash
python manage.py migrate
```

### 4. Web Server Configuration
Use Gunicorn + Nginx for production:
```bash
pip install gunicorn
gunicorn fuel_project.wsgi:application --bind 0.0.0.0:8000
```

## 🤝 API Integration

### KPC Integration Points
- **IMAP Email Processing**: Automated status updates
- **PDF Document Parsing**: Loading authority and BoL processing
- **Order Number Matching**: Links email updates to existing trips

### Extensibility
The system is designed for easy integration with:
- ERP systems via Django REST framework (future enhancement)
- Mobile applications for field operations
- Third-party logistics platforms

## 📝 Troubleshooting

### Common Issues

**PDF Parsing Failures**
```bash
# Check PDF content manually
python manage.py shell
from shipments.views import parse_loading_authority_pdf
# Debug parsing with sample files
```

**Email Processing Issues**
```bash
# Test email connection
python manage.py shell
import imaplib
mail = imaplib.IMAP4_SSL('imap.gmail.com', 993)
mail.login('user', 'password')
```

**Stock Inconsistencies**
```bash
# Recalculate stock levels
python manage.py shell
from shipments.models import Shipment
for shipment in Shipment.objects.all():
    shipment.save()  # Triggers recalculation
```

### Performance Optimization
- Database indexes are pre-configured for common queries
- Caching enabled for dashboard calculations
- Pagination implemented for large datasets

## 📄 License

Copyright (c) 2025 Sakina Gas Company. All rights reserved.

This software is proprietary and confidential. Unauthorized copying, modification, or distribution is prohibited.

## 🆘 Support

For technical support or feature requests, contact the development team at:
- Email: support@sakinagas.com
- Internal: IT Department

## 🔄 Version History

- **v1.0.0**: Initial release with core functionality
- **v1.1.0**: Added PDF parsing and email integration
- **v1.2.0**: Enhanced dashboard and reporting features
- **v1.3.0**: Multi-destination support and advanced stock tracking

---

**Built with ❤️ for Sakina Gas Company - Powering East Africa with Quality Fuel**
