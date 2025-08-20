# Management command to test TR830 integration with Telegram bot
# File: shipments/management/commands/test_tr830_telegram.py

from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from shipments.telegram_bot import TelegramBot
from shipments.tr830_parser import TR830Parser, TR830Entry
from shipments.models import Shipment, Product
import tempfile
import os
from decimal import Decimal
from datetime import datetime

class Command(BaseCommand):
    help = 'Test TR830 integration with Telegram bot'

    def add_arguments(self, parser):
        parser.add_argument(
            '--test-parser',
            action='store_true',
            help='Test TR830 parser with sample data'
        )
        parser.add_argument(
            '--test-bot',
            action='store_true',
            help='Test Telegram bot TR830 processing'
        )
        parser.add_argument(
            '--test-file',
            type=str,
            help='Test with actual TR830 PDF file'
        )
        parser.add_argument(
            '--create-sample-pdf',
            action='store_true',
            help='Create a sample TR830 PDF for testing'
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed output'
        )

    def handle(self, *args, **options):
        self.stdout.write("🧪 Testing TR830 Telegram Integration...")
        self.stdout.write("=" * 60)
        
        try:
            if options['test_parser']:
                self._test_tr830_parser(options.get('verbose', False))
            
            if options['test_bot']:
                self._test_telegram_bot_tr830(options.get('verbose', False))
            
            if options['test_file']:
                self._test_file_processing(options['test_file'], options.get('verbose', False))
            
            if options['create_sample_pdf']:
                self._create_sample_tr830_pdf()
            
            if not any([options['test_parser'], options['test_bot'], options['test_file'], options['create_sample_pdf']]):
                self._show_usage()
                
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Error: {e}"))
            import traceback
            if options.get('verbose'):
                traceback.print_exc()

    def _test_tr830_parser(self, verbose=False):
        """Test the TR830 parser directly"""
        self.stdout.write("\n📋 Testing TR830 Parser...")
        
        try:
            from shipments.tr830_parser import TR830Parser, TR830Entry
            
            parser = TR830Parser()
            
            # Create sample TR830 data
            sample_text = """
            TR830 TRANSIT DOCUMENT
            DATE: 24/07/2025
            REFERENCE: TR830-2025-001
            
            VESSEL: MT. SAKINA EXPRESS
            DESCRIPTION: AUTOMOTIVE GAS OIL IN TRANSIT TO DR CONGO
            AVALUE: 500,000
            CONSIGNOR: KUWAIT PETROLEUM CORPORATION
            CONSIGNEE: SAKINA GAS COMPANY
            
            VESSEL: MT. SAKINA SPIRIT  
            DESCRIPTION: PREMIUM MOTOR SPIRIT IN TRANSIT TO SOUTH SUDAN
            AVALUE: 750,000
            CONSIGNOR: TOTAL PETROLEUM
            CONSIGNEE: SAKINA GAS COMPANY
            """
            
            self.stdout.write("🔍 Testing text-based extraction...")
            entries = parser._extract_from_text(sample_text)
            
            if entries:
                self.stdout.write(self.style.SUCCESS(f"✅ Extracted {len(entries)} entries"))
                
                if verbose:
                    for i, entry in enumerate(entries, 1):
                        self.stdout.write(f"  Entry {i}:")
                        self.stdout.write(f"    Vessel: {entry.marks}")
                        self.stdout.write(f"    Product: {entry.product_type}")
                        self.stdout.write(f"    Quantity: {entry.avalue:,.0f}L")
                        self.stdout.write(f"    Destination: {entry.destination}")
                        self.stdout.write(f"    Supplier: {entry.supplier}")
                        self.stdout.write("")
                
                # Test validation
                success, errors = parser.validate_parsing_result(entries)
                if success:
                    self.stdout.write(self.style.SUCCESS("✅ Validation: PASSED"))
                else:
                    self.stdout.write(self.style.WARNING("⚠️ Validation: PARTIAL"))
                    for error in errors:
                        self.stdout.write(f"  - {error}")
                
                # Test summary
                summary = parser.get_parsing_summary(entries)
                self.stdout.write(f"📊 Summary: {summary}")
                
            else:
                self.stdout.write(self.style.ERROR("❌ No entries extracted"))
                
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Parser test failed: {e}"))
            if verbose:
                import traceback
                traceback.print_exc()

    def _test_telegram_bot_tr830(self, verbose=False):
        """Test Telegram bot TR830 processing"""
        self.stdout.write("\n🤖 Testing Telegram Bot TR830 Processing...")
        
        try:
            # Check if we have a test user
            test_user = User.objects.filter(username='testuser').first()
            if not test_user:
                self.stdout.write(self.style.WARNING("⚠️ Creating test user..."))
                test_user = User.objects.create_user(
                    username='testuser',
                    email='test@example.com',
                    password='testpass123'
                )
                # Set telegram chat ID
                test_user.userprofile.telegram_chat_id = '12345'
                test_user.userprofile.save()
            
            # Initialize bot
            bot = TelegramBot()
            
            # Test user context
            user_context = bot.get_user_context('12345')
            if user_context.get('user_id'):
                self.stdout.write(self.style.SUCCESS(f"✅ User context found: {user_context['username']}"))
            else:
                self.stdout.write(self.style.ERROR("❌ No user context found"))
                return
            
            # Create sample TR830 content
            sample_tr830_content = self._create_sample_tr830_content()
            
            # Test TR830 document detection
            is_tr830 = bot._is_tr830_document("TR830_Sample.pdf", sample_tr830_content)
            if is_tr830:
                self.stdout.write(self.style.SUCCESS("✅ TR830 document detection working"))
            else:
                self.stdout.write(self.style.WARNING("⚠️ TR830 document detection may need adjustment"))
            
            # Test TR830 processing simulation
            self.stdout.write("🔄 Testing TR830 processing simulation...")
            
            # Create temporary file with sample content
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp_file:
                tmp_file.write(sample_tr830_content)
                tmp_file.flush()
                temp_path = tmp_file.name
            
            try:
                # This would normally process the PDF, but we'll simulate
                self.stdout.write("📄 Sample TR830 file created for testing")
                
                # Count shipments before
                shipments_before = Shipment.objects.count()
                
                # Simulate processing (without actual PDF parsing)
                self._simulate_tr830_processing(user_context)
                
                # Count shipments after
                shipments_after = Shipment.objects.count()
                
                if shipments_after > shipments_before:
                    self.stdout.write(self.style.SUCCESS(f"✅ Shipments created: {shipments_after - shipments_before}"))
                else:
                    self.stdout.write(self.style.WARNING("⚠️ No shipments created in simulation"))
                
            finally:
                try:
                    os.unlink(temp_path)
                except:
                    pass
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Bot test failed: {e}"))
            if verbose:
                import traceback
                traceback.print_exc()

    def _test_file_processing(self, file_path, verbose=False):
        """Test processing an actual TR830 file"""
        self.stdout.write(f"\n📄 Testing file: {file_path}")
        
        if not os.path.exists(file_path):
            self.stdout.write(self.style.ERROR(f"❌ File not found: {file_path}"))
            return
        
        try:
            # Test parser directly
            parser = TR830Parser()
            import_date, entries = parser.parse_pdf(file_path)
            
            self.stdout.write(self.style.SUCCESS(f"✅ Parser extracted {len(entries)} entries"))
            self.stdout.write(f"📅 Import date: {import_date}")
            
            if verbose and entries:
                for i, entry in enumerate(entries, 1):
                    self.stdout.write(f"  Entry {i}: {entry}")
            
            # Test through bot
            bot = TelegramBot()
            
            with open(file_path, 'rb') as f:
                file_content = f.read()
            
            # Check detection
            is_tr830 = bot._is_tr830_document(os.path.basename(file_path), file_content)
            self.stdout.write(f"🔍 TR830 detection: {'✅' if is_tr830 else '❌'}")
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ File processing failed: {e}"))
            if verbose:
                import traceback
                traceback.print_exc()

    def _create_sample_tr830_pdf(self):
        """Create a sample TR830 PDF for testing"""
        self.stdout.write("\n📄 Creating sample TR830 PDF...")
        
        try:
            from reportlab.pdfgen import canvas
            from reportlab.lib.pagesizes import letter
            
            # Create PDF content
            filename = "sample_tr830.pdf"
            c = canvas.Canvas(filename, pagesize=letter)
            
            # Add TR830 content
            c.drawString(100, 750, "TR830 TRANSIT DOCUMENT")
            c.drawString(100, 720, "DATE: 24/07/2025")
            c.drawString(100, 690, "REFERENCE: TR830-2025-SAMPLE")
            c.drawString(100, 660, "")
            c.drawString(100, 630, "VESSEL: MT. SAKINA TEST VESSEL")
            c.drawString(100, 600, "DESCRIPTION: AUTOMOTIVE GAS OIL IN TRANSIT TO DR CONGO")
            c.drawString(100, 570, "AVALUE: 500,000")
            c.drawString(100, 540, "CONSIGNOR: KUWAIT PETROLEUM CORPORATION")
            c.drawString(100, 510, "CONSIGNEE: SAKINA GAS COMPANY")
            c.drawString(100, 480, "")
            c.drawString(100, 450, "VESSEL: MT. SAKINA SPIRIT")
            c.drawString(100, 420, "DESCRIPTION: PREMIUM MOTOR SPIRIT IN TRANSIT TO SOUTH SUDAN")
            c.drawString(100, 390, "AVALUE: 750,000")
            c.drawString(100, 360, "CONSIGNOR: TOTAL PETROLEUM")
            c.drawString(100, 330, "CONSIGNEE: SAKINA GAS COMPANY")
            
            c.save()
            
            self.stdout.write(self.style.SUCCESS(f"✅ Sample TR830 PDF created: {filename}"))
            self.stdout.write(f"💡 Use this file to test: python manage.py test_tr830_telegram --test-file {filename}")
            
        except ImportError:
            self.stdout.write(self.style.ERROR("❌ reportlab not installed. Install with: pip install reportlab"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Error creating PDF: {e}"))

    def _create_sample_tr830_content(self):
        """Create sample TR830 PDF content as bytes"""
        try:
            from reportlab.pdfgen import canvas
            from reportlab.lib.pagesizes import letter
            import io
            
            buffer = io.BytesIO()
            c = canvas.Canvas(buffer, pagesize=letter)
            
            # Add TR830 content
            c.drawString(100, 750, "TR830 TRANSIT DOCUMENT")
            c.drawString(100, 720, "DATE: 24/07/2025")
            c.drawString(100, 690, "REFERENCE: TR830-2025-TEST")
            c.drawString(100, 630, "VESSEL: MT. SAKINA TEST")
            c.drawString(100, 600, "DESCRIPTION: GASOIL IN TRANSIT TO DR CONGO")
            c.drawString(100, 570, "AVALUE: 300,000")
            
            c.save()
            
            buffer.seek(0)
            return buffer.read()
            
        except ImportError:
            # Fallback to simple text content
            return b"TR830 TRANSIT DOCUMENT\nVESSEL: MT. SAKINA TEST\nGASOIL\n300,000"

    def _simulate_tr830_processing(self, user_context):
        """Simulate TR830 processing and shipment creation"""
        try:
            # Get or create test products
            ago_product, _ = Product.objects.get_or_create(
                name='AGO',
                defaults={'description': 'Automotive Gas Oil'}
            )
            
            pms_product, _ = Product.objects.get_or_create(
                name='PMS',
                defaults={'description': 'Premium Motor Spirit'}
            )
            
            # Create test shipments
            test_shipments = [
                {
                    'vessel_id_tag': 'MT. SAKINA TEST VESSEL',
                    'supplier': 'Kuwait Petroleum Corporation',
                    'product': ago_product,
                    'quantity_loaded': Decimal('500000'),
                    'quantity_remaining': Decimal('500000'),
                    'destination': 'DR Congo',
                    'loading_date': datetime.now(),
                    'notes': 'Test TR830 shipment - AGO',
                },
                {
                    'vessel_id_tag': 'MT. SAKINA SPIRIT',
                    'supplier': 'Total Petroleum',
                    'product': pms_product,
                    'quantity_loaded': Decimal('750000'),
                    'quantity_remaining': Decimal('750000'),
                    'destination': 'South Sudan',
                    'loading_date': datetime.now(),
                    'notes': 'Test TR830 shipment - PMS',
                }
            ]
            
            created_count = 0
            for shipment_data in test_shipments:
                # Check if similar shipment already exists
                existing = Shipment.objects.filter(
                    vessel_id_tag=shipment_data['vessel_id_tag'],
                    notes__contains='Test TR830'
                ).first()
                
                if not existing:
                    Shipment.objects.create(**shipment_data)
                    created_count += 1
            
            self.stdout.write(f"🚢 Created {created_count} test shipments")
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Simulation failed: {e}"))

    def _show_usage(self):
        """Show usage examples"""
        self.stdout.write(self.style.WARNING("Please specify what to test:"))
        self.stdout.write("\n📋 Usage Examples:")
        self.stdout.write("  python manage.py test_tr830_telegram --test-parser")
        self.stdout.write("  python manage.py test_tr830_telegram --test-bot")
        self.stdout.write("  python manage.py test_tr830_telegram --test-file /path/to/tr830.pdf")
        self.stdout.write("  python manage.py test_tr830_telegram --create-sample-pdf")
        self.stdout.write("  python manage.py test_tr830_telegram --test-parser --test-bot --verbose")
        
        self.stdout.write("\n📂 Available Features:")
        self.stdout.write("  --test-parser      : Test TR830 parser with sample data")
        self.stdout.write("  --test-bot         : Test Telegram bot TR830 integration")
        self.stdout.write("  --test-file        : Test with actual TR830 PDF file")
        self.stdout.write("  --create-sample-pdf: Create sample TR830 PDF for testing")
        self.stdout.write("  --verbose          : Show detailed output and debug info")
        
        # Check system readiness
        self.stdout.write("\n🔍 System Check:")
        
        try:
            from shipments.tr830_parser import TR830Parser
            self.stdout.write(self.style.SUCCESS("✅ TR830Parser available"))
        except ImportError:
            self.stdout.write(self.style.ERROR("❌ TR830Parser not found"))
        
        try:
            from shipments.telegram_bot import TelegramBot
            self.stdout.write(self.style.SUCCESS("✅ TelegramBot available"))
        except ImportError:
            self.stdout.write(self.style.ERROR("❌ TelegramBot not found"))
        
        try:
            import pdfplumber
            self.stdout.write(self.style.SUCCESS("✅ pdfplumber available"))
        except ImportError:
            self.stdout.write(self.style.ERROR("❌ pdfplumber not installed"))
        
        try:
            from django.conf import settings
            if hasattr(settings, 'TELEGRAM_CONFIG'):
                self.stdout.write(self.style.SUCCESS("✅ Telegram config available"))
            else:
                self.stdout.write(self.style.ERROR("❌ Telegram config missing"))
        except:
            self.stdout.write(self.style.ERROR("❌ Settings error"))