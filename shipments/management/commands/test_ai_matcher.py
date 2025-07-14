# shipments/management/commands/test_ai_matcher.py
from django.core.management.base import BaseCommand
from django.conf import settings
from shipments.ai_order_matcher import get_trip_with_smart_matching, IntelligentOrderMatcher
from shipments.models import Trip
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Test the AI order matcher with Groq'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--test-order',
            type=str,
            help='Test a specific order number'
        )
        parser.add_argument(
            '--check-setup',
            action='store_true',
            help='Check if everything is set up correctly'
        )
        parser.add_argument(
            '--run-example',
            action='store_true',
            help='Run example with S02020 vs S00220'
        )
    
    def handle(self, *args, **options):
        if options['check_setup']:
            self.check_setup()
        
        if options['test_order']:
            self.test_single_order(options['test_order'])
        
        if options['run_example']:
            self.run_example_test()
        
        if not any([options['check_setup'], options['test_order'], options['run_example']]):
            self.stdout.write("Use --help to see available options")
    
    def check_setup(self):
        """Check if AI matcher is set up correctly."""
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
                self.stdout.write(f"   '{test_order}' was corrected to '{trip.kpc_order_number}'")
                
        else:
            self.stdout.write(f"❌ NO MATCH FOUND for '{test_order}'")
            
            # Show available orders for reference
            try:
                matcher = IntelligentOrderMatcher()
                active_orders = matcher.get_active_order_numbers()
                if active_orders:
                    self.stdout.write(f"\n📋 Available active orders:")
                    for order in active_orders[:10]:
                        self.stdout.write(f"   - {order}")
                    if len(active_orders) > 10:
                        self.stdout.write(f"   ... and {len(active_orders) - 10} more")
                else:
                    self.stdout.write(f"\n⚠️  No active orders found in database")
            except Exception as e:
                self.stdout.write(f"\n❌ Error getting active orders: {e}")
    
    def run_example_test(self):
        """Run the classic S02020 vs S00220 example."""
        self.stdout.write("🎯 Running Example Test: S02020 vs S00220")
        self.stdout.write("=" * 50)
        
        # First, check if we have an order like S02020
        correct_orders = Trip.objects.filter(
            kpc_order_number__iregex=r'^S\d{5}$'
        ).values_list('kpc_order_number', flat=True)[:5]
        
        if not correct_orders:
            self.stdout.write("❌ No orders found matching S##### pattern")
            self.stdout.write("💡 Create a trip with order number like 'S02020' first")
            return
        
        # Take the first order and create a "corrupted" version
        original_order = correct_orders[0]
        
        # Create corruption by transposing digits
        if len(original_order) >= 6:
            # Transpose last two digits: S02020 -> S02002
            corrupted = original_order[:-2] + original_order[-1] + original_order[-2]
        else:
            corrupted = original_order.replace('0', 'O', 1)  # O vs 0 confusion
        
        self.stdout.write(f"📝 Test Case:")
        self.stdout.write(f"   Correct Order: {original_order}")
        self.stdout.write(f"   Corrupted Input: {corrupted}")
        self.stdout.write(f"   Expected: Should find {original_order}")
        
        self.stdout.write(f"\n🔍 Testing corrupted order '{corrupted}'...")
        
        result = get_trip_with_smart_matching(corrupted)
        
        if result:
            trip, metadata = result
            if trip.kpc_order_number == original_order:
                self.stdout.write(f"🎉 SUCCESS! Correctly matched:")
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