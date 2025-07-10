# shipments/management/commands/test_whatsapp.py

from django.core.management.base import BaseCommand
from django.conf import settings
import requests

class Command(BaseCommand):
    help = 'Test WhatsApp integration'

    def handle(self, *args, **options):
        self.stdout.write("📱 Testing WhatsApp Integration")
        self.stdout.write("=" * 40)
        
        # Test 1: Configuration
        self.stdout.write("\n🔧 Configuration Check:")
        
        verify_token = settings.WHATSAPP_CONFIG.get('VERIFY_TOKEN', '')
        access_token = settings.WHATSAPP_CONFIG.get('ACCESS_TOKEN', '')
        phone_id = settings.WHATSAPP_CONFIG.get('PHONE_NUMBER_ID', '')
        gemini_key = getattr(settings, 'GEMINI_API_KEY', '')
        
        self.stdout.write(f"  ✅ Verify Token: {'SET' if verify_token else '❌ MISSING'}")
        self.stdout.write(f"  ✅ Access Token: {'SET' if access_token else '❌ MISSING'}")
        self.stdout.write(f"  ✅ Phone Number ID: {'SET' if phone_id else '❌ MISSING'}")
        self.stdout.write(f"  ✅ Gemini API Key: {'SET' if gemini_key else '❌ MISSING'}")
        
        # Test 2: Webhook URL
        self.stdout.write("\n🔗 Webhook URL Test:")
        webhook_url = "https://Sakinagas-Basheer42.pythonanywhere.com/whatsapp/webhook/"
        test_url = f"{webhook_url}?hub.verify_token={verify_token}&hub.challenge=test123"
        
        try:
            response = requests.get(test_url, timeout=10)
            self.stdout.write(f"  URL: {test_url}")
            self.stdout.write(f"  Status: {response.status_code}")
            self.stdout.write(f"  Response: {response.text}")
            
            if response.status_code == 200 and response.text == "test123":
                self.stdout.write(f"  ✅ Webhook working correctly")
            else:
                self.stdout.write(f"  ❌ Webhook verification failed")
                
        except Exception as e:
            self.stdout.write(f"  ❌ Webhook test failed: {e}")
        
        self.stdout.write("\n🎉 Test completed!")
