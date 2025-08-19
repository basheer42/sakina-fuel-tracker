# shipments/management/commands/test_tr830_parser.py

from django.core.management.base import BaseCommand
import os
import tempfile
from decimal import Decimal

class Command(BaseCommand):
    help = 'Test TR830 parser with sample data or files'

    def add_arguments(self, parser):
        parser.add_argument(
            '--file',
            type=str,
            help='Path to TR830 PDF file to test'
        )
        parser.add_argument(
            '--sample',
            action='store_true',
            help='Run with sample data'
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed output'
        )

    def handle(self, *args, **options):
        self.stdout.write("🧪 Testing TR830 Parser...")
        self.stdout.write("=" * 50)
        
        try:
            # Try to import the TR830 parser
            try:
                from shipments.tr830_parser import TR830Parser, TR830ParseError
                self.stdout.write(self.style.SUCCESS("✅ TR830Parser imported successfully"))
            except ImportError as e:
                self.stdout.write(self.style.ERROR(f"❌ Could not import TR830Parser: {e}"))
                self.stdout.write("Make sure you have created the tr830_parser.py file")
                return
        
            if options['file']:
                self._test_file(options['file'], options.get('verbose', False))
            elif options['sample']:
                self._test_sample_data(options.get('verbose', False))
            else:
                self._show_usage()
                
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Unexpected error: {e}"))

    def _test_file(self, pdf_path, verbose=False):
        """Test with actual PDF file"""
        if not os.path.exists(pdf_path):
            self.stdout.write(self.style.ERROR(f"❌ File not found: {pdf_path}"))
            return
        
        try:
            from shipments.tr830_parser import TR830Parser
            
            parser = TR830Parser()
            self.stdout.write(f"📄 Testing file: {pdf_path}")
            
            import_date, entries = parser.parse_pdf(pdf_path)
            
            self.stdout.write(self.style.SUCCESS(f"✅ Successfully parsed {len(entries)} entries"))
            self.stdout.write(f"📅 Import date: {import_date.date()}")
            
            if verbose and entries:
                self.stdout.write("\n📋 Parsed Entries:")
                for i, entry in enumerate(entries, 1):
                    self.stdout.write(f"  Entry {i}:")
                    self.stdout.write(f"    Vessel: {entry.marks or 'N/A'}")
                    self.stdout.write(f"    Product: {entry.product_type or 'N/A'}")
                    self.stdout.write(f"    Quantity: {entry.avalue or 'N/A'}")
                    self.stdout.write(f"    Destination: {entry.destination or 'N/A'}")
                    self.stdout.write(f"    Supplier: {entry.supplier or 'N/A'}")
                    self.stdout.write("")
            
            # Show summary
            summary = parser.get_parsing_summary(entries)
            self.stdout.write(f"\n📊 Parsing Summary:")
            for key, value in summary.items():
                self.stdout.write(f"  {key}: {value}")
            
            # Validate
            success, errors = parser.validate_parsing_result(entries)
            if success:
                self.stdout.write(self.style.SUCCESS("\n✅ Validation: PASSED"))
            else:
                self.stdout.write(self.style.ERROR("\n❌ Validation: FAILED"))
                for error in errors:
                    self.stdout.write(f"  - {error}")
                    
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Error processing file: {e}"))
            import traceback
            if verbose:
                self.stdout.write(f"Full traceback:\n{traceback.format_exc()}")

    def _test_sample_data(self, verbose=False):
        """Test with sample text data"""
        try:
            from shipments.tr830_parser import TR830Parser
            
            # Sample TR830 text data
            sample_text = """
            TR830 DOCUMENT
            DATE: 24/07/2025
            
            MT. SAKINA TEST VESSEL
            DESCRIPTION: GASOIL IN TRANSIT TO DR CONGO
            AVALUE: 200,000
            CONSIGNOR: KUWAIT PETROLEUM CORPORATION
            REF: TR830-2025-001
            
            Product: AGO
            Destination: DRC
            """
            
            self.stdout.write("🧪 Testing with sample data...")
            
            parser = TR830Parser()
            
            # Test text extraction methods
            self.stdout.write("\n1️⃣ Testing date extraction...")
            extracted_date = parser._extract_date_with_fallbacks(sample_text)
            self.stdout.write(f"   ✅ Extracted date: {extracted_date.date()}")
            
            self.stdout.write("\n2️⃣ Testing product identification...")
            product_type = parser._extract_product_type_from_text(sample_text)
            self.stdout.write(f"   ✅ Product type: {product_type or 'Not found'}")
            
            self.stdout.write("\n3️⃣ Testing quantity parsing...")
            quantity = parser._extract_quantity_from_text(sample_text)
            self.stdout.write(f"   ✅ Quantity: {quantity or 'Not found'}")
            
            self.stdout.write("\n4️⃣ Testing destination extraction...")
            destination = parser._extract_destination_from_text(sample_text)
            self.stdout.write(f"   ✅ Destination: {destination or 'Not found'}")
            
            self.stdout.write("\n5️⃣ Testing text-based entry extraction...")
            entries = parser._extract_from_text(sample_text)
            
            if entries:
                self.stdout.write(self.style.SUCCESS(f"   ✅ Created {len(entries)} entries from sample text"))
                
                if verbose:
                    for i, entry in enumerate(entries, 1):
                        self.stdout.write(f"     Entry {i}: {entry}")
                
                # Test validation
                success, errors = parser.validate_parsing_result(entries)
                if success:
                    self.stdout.write(self.style.SUCCESS("   ✅ Validation: PASSED"))
                else:
                    self.stdout.write(self.style.WARNING("   ⚠️ Validation: PARTIAL"))
                    for error in errors:
                        self.stdout.write(f"     - {error}")
            else:
                self.stdout.write(self.style.WARNING("   ⚠️ No entries extracted from sample text"))
            
            # Test specific parsing functions
            self.stdout.write("\n6️⃣ Testing utility functions...")
            
            # Test quantity parsing
            test_quantities = ["200,000", "1,500,000", "50000", "2.5E5"]
            for qty_str in test_quantities:
                parsed_qty = parser._parse_quantity(qty_str)
                status = "✅" if parsed_qty and parsed_qty > 0 else "❌"
                self.stdout.write(f"   {status} '{qty_str}' → {parsed_qty}")
            
            # Test product mapping
            test_products = ["GASOIL", "MOTOR GASOLINE", "AGO", "PMS"]
            for prod_str in test_products:
                mapped_prod = parser._identify_product_type(prod_str)
                status = "✅" if mapped_prod else "❌"
                self.stdout.write(f"   {status} '{prod_str}' → {mapped_prod}")
            
            self.stdout.write(self.style.SUCCESS("\n🎉 Sample test completed successfully!"))
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Sample test failed: {e}"))
            import traceback
            if verbose:
                self.stdout.write(f"Full traceback:\n{traceback.format_exc()}")

    def _show_usage(self):
        """Show usage examples"""
        self.stdout.write(self.style.WARNING("Please specify what to test:"))
        self.stdout.write("\n📋 Usage Examples:")
        self.stdout.write("  python manage.py test_tr830_parser --sample")
        self.stdout.write("  python manage.py test_tr830_parser --sample --verbose")
        self.stdout.write("  python manage.py test_tr830_parser --file /path/to/tr830.pdf")
        self.stdout.write("  python manage.py test_tr830_parser --file /path/to/tr830.pdf --verbose")
        
        self.stdout.write("\n📂 Available Features:")
        self.stdout.write("  --sample    : Test with built-in sample data")
        self.stdout.write("  --file      : Test with actual TR830 PDF file")
        self.stdout.write("  --verbose   : Show detailed output and debug info")
        
        # Check if TR830 parser exists
        try:
            from shipments.tr830_parser import TR830Parser
            self.stdout.write(self.style.SUCCESS("\n✅ TR830Parser is available and ready to test"))
        except ImportError:
            self.stdout.write(self.style.ERROR("\n❌ TR830Parser not found. Make sure tr830_parser.py exists"))