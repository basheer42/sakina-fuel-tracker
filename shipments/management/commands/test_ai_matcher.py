# shipments/management/commands/test_ai_matcher.py
"""
Management command to test the AI order matching system
"""
from django.core.management.base import BaseCommand
from django.conf import settings
from shipments.utils.ai_order_matcher import get_trip_with_smart_matching, IntelligentOrderMatcher
from shipments.models import Trip
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Test the AI order matching system'

    def add_arguments(self, parser):
        parser.add_argument(
            '--check-setup',
            action='store_true',
            help='Check system setup and configuration',
        )
        parser.add_argument(
            '--test',
            type=str,
            help='Test a specific order number',
        )
        parser.add_argument(
            '--run-examples',
            action='store_true',
            help='Run built-in test examples',
        )

    def handle(self, *args, **options):
        self.stdout.write("🤖 AI Order Matching System Test")
        self.stdout.write("=" * 50)
        
        if options['check_setup']:
            self.check_setup()
        elif options['test']:
            self.test_single_order(options['test'])
        elif options['run_examples']:
            self.run_test_examples()
        else:
            self.show_usage()
    
    def show_usage(self):
        """Show usage information."""
        self.stdout.write("📋 Available Commands:")
        self.stdout.write("")
        self.stdout.write("  --check-setup     Check system configuration")
        self.stdout.write("  --test ORDER      Test specific order number")
        self.stdout.write("  --run-examples    Run diagnostic examples")
        self.stdout.write("")
        self.stdout.write("📌 Examples:")
        self.stdout.write("  python manage.py test_ai_matcher --check-setup")
        self.stdout.write("  python manage.py test_ai_matcher --test S00220")
        self.stdout.write("  python manage.py test_ai_matcher --run-examples")
    
    def check_setup(self):
        """Check system setup and configuration."""
        self.stdout.write("🔍 Checking AI Matcher Setup")
        self.stdout.write("=" * 40)
        
        # Check Groq API key
        groq_key = getattr(settings, 'GROQ_API_KEY', None)
        if groq_key:
            masked_key = f"{groq_key[:8]}...{groq_key[-4:]}"
            self.stdout.write(f"✅ Groq API Key: {masked_key}")
        else:
            self.stdout.write(f"❌ Groq API Key: Not found in settings")
            return
        
        # Check settings
        enabled = getattr(settings, 'AI_MATCHER_ENABLED', False)
        local_threshold = getattr(settings, 'AI_MATCHER_LOCAL_THRESHOLD', 0.7)
        ai_threshold = getattr(settings, 'AI_MATCHER_AI_THRESHOLD', 0.8)
        
        self.stdout.write(f"✅ AI Matcher Enabled: {enabled}")
        self.stdout.write(f"✅ Local Threshold: {local_threshold}")
        self.stdout.write(f"✅ AI Threshold: {ai_threshold}")
        
        # Check active orders
        try:
            matcher = IntelligentOrderMatcher()
            active_orders = matcher.get_active_order_numbers()
            self.stdout.write(f"✅ Active Orders Found: {len(active_orders)}")
            
            if active_orders:
                self.stdout.write("📋 Sample active orders:")
                for order in active_orders[:5]:
                    self.stdout.write(f"   - {order}")
                if len(active_orders) > 5:
                    self.stdout.write(f"   ... and {len(active_orders) - 5} more")
            else:
                self.stdout.write("⚠️  No active orders found - create some trips first")
                
        except Exception as e:
            self.stdout.write(f"❌ Error checking active orders: {e}")
        
        self.stdout.write("\n🎯 Setup looks good! Ready to test.")
    
    def test_single_order(self, test_order):
        """Test matching for a single order number."""
        self.stdout.write(f"🧪 Testing Order Number: '{test_order}'")
        self.stdout.write("=" * 50)
        
        result = get_trip_with_smart_matching(test_order)
        
        if result:
            trip, metadata = result
            self.stdout.write(f"✅ MATCH FOUND!")
            self.stdout.write(f"   Original: {metadata['original_order']}")
            self.stdout.write(f"   Corrected: {metadata['corrected_order']}")
            self.stdout.write(f"   Trip ID: {trip.id}")
            self.stdout.write(f"   Customer: {trip.customer.name}")
            self.stdout.write(f"   Product: {trip.product.name}")
            self.stdout.write(f"   Status: {trip.get_status_display()}")
            self.stdout.write(f"   Method: {metadata['correction_method']}")
            self.stdout.write(f"   Confidence: {metadata['confidence']:.2f}")
            self.stdout.write(f"   Time: {metadata['processing_time']:.3f}s")
            
            if metadata['correction_method'] != 'exact_match':
                self.stdout.write(f"\n🤖 AI CORRECTION DETECTED!")
                self.stdout.write(f"   The system corrected '{test_order}' to '{metadata['corrected_order']}'")
                
        else:
            self.stdout.write(f"❌ NO MATCH FOUND")
            self.stdout.write(f"   Could not find any trip matching '{test_order}'")
            self.stdout.write(f"   This could be because:")
            self.stdout.write(f"   - Order doesn't exist in the database")
            self.stdout.write(f"   - Order is too different from existing orders")
            self.stdout.write(f"   - Trip status excludes it from matching")
        
        self.stdout.write(f"\n✅ Test completed!")
    
    def run_test_examples(self):
        """Run built-in test examples."""
        self.stdout.write("🧪 Running Built-in Test Examples")
        self.stdout.write("=" * 50)
        
        # Get a real order from database for testing
        try:
            sample_trip = Trip.objects.filter(
                kpc_order_number__isnull=False
            ).exclude(
                kpc_order_number__exact=''
            ).first()
            
            if not sample_trip:
                self.stdout.write("❌ No trips with KPC order numbers found.")
                self.stdout.write("   Create some trips first to test the system.")
                return
            
            original_order = sample_trip.kpc_order_number
            self.stdout.write(f"📋 Using real order: {original_order}")
            
        except Exception as e:
            self.stdout.write(f"❌ Error getting sample trip: {e}")
            return
        
        # Test various corruption patterns
        test_cases = [
            (original_order, "Exact match (should work)"),
            (self.create_transposition_error(original_order), "Digit transposition"),
            (self.create_o_zero_confusion(original_order), "O/0 confusion"),
            (self.create_extra_digit_error(original_order), "Extra digit"),
            (self.create_missing_prefix_error(original_order), "Missing S prefix"),
        ]
        
        self.stdout.write(f"\n🎯 Testing {len(test_cases)} corruption patterns:")
        self.stdout.write("-" * 50)
        
        for corrupted, description in test_cases:
            if corrupted == original_order:
                continue  # Skip exact match test
                
            self.stdout.write(f"\n📝 Test: {description}")
            self.stdout.write(f"   Original: {original_order}")
            self.stdout.write(f"   Corrupted: {corrupted}")
            
            result = get_trip_with_smart_matching(corrupted)
            
            if result:
                trip, metadata = result
                if trip.kpc_order_number == original_order:
                    self.stdout.write(f"✅ Correctly matched:")
                    self.stdout.write(f"   '{corrupted}' → '{trip.kpc_order_number}'")
                    self.stdout.write(f"   Method: {metadata['correction_method']}")
                    self.stdout.write(f"   Confidence: {metadata['confidence']:.2f}")
                    self.stdout.write(f"   Time: {metadata['processing_time']:.3f}s")
                    
                    if metadata['correction_method'] == 'groq_ai_correction':
                        self.stdout.write(f"   🤖 Groq AI successfully corrected the error!")
                    elif metadata['correction_method'] == 'local_fuzzy_match':
                        self.stdout.write(f"   🔍 Local fuzzy matching worked!")
                        
                else:
                    self.stdout.write(f"⚠️  Found different order: {trip.kpc_order_number}")
            else:
                self.stdout.write(f"❌ Could not correct '{corrupted}' to '{original_order}'")
            
        self.stdout.write(f"\n✅ Example test completed!")
    
    def create_transposition_error(self, order):
        """Create digit transposition error (e.g., S02020 → S00220)"""
        if len(order) >= 4:
            chars = list(order)
            # Swap two adjacent digits
            if len(chars) >= 4:
                chars[2], chars[3] = chars[3], chars[2]
            return ''.join(chars)
        return order
    
    def create_o_zero_confusion(self, order):
        """Create O/0 confusion error (e.g., S02020 → SO2020)"""
        return order.replace('0', 'O', 1) if '0' in order else order
    
    def create_extra_digit_error(self, order):
        """Create extra digit error (e.g., S02020 → S020200)"""
        return order + '0' if order else order
    
    def create_missing_prefix_error(self, order):
        """Create missing S prefix error (e.g., S02020 → 02020)"""
        return order[1:] if order.startswith('S') else order