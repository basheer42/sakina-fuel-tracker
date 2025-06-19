import json
import requests
import google.generativeai as genai
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.http import HttpResponse, JsonResponse
from django.contrib.auth.models import User
from .models import Shipment, Trip, Product, Customer
import logging
import io
from PIL import Image

logger = logging.getLogger(__name__)

class WhatsAppGeminiAssistant:
    def __init__(self):
        self.access_token = settings.WHATSAPP_CONFIG['ACCESS_TOKEN']
        self.phone_number_id = settings.WHATSAPP_CONFIG['PHONE_NUMBER_ID']
        
        # Configure Gemini
        genai.configure(api_key=settings.GEMINI_API_KEY)
        self.model = genai.GenerativeModel('gemini-1.5-flash')  # Free model
        self.vision_model = genai.GenerativeModel('gemini-1.5-flash')  # Can handle images too
        
    def send_message(self, phone_number, message):
        """Send a message back to WhatsApp"""
        url = f"https://graph.facebook.com/v17.0/{self.phone_number_id}/messages"
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json',
        }
        data = {
            "messaging_product": "whatsapp",
            "to": phone_number,
            "text": {"body": message}
        }
        
        response = requests.post(url, headers=headers, json=data)
        return response.json()
    
    def send_image_message(self, phone_number, image_url, caption=""):
        """Send an image message to WhatsApp"""
        url = f"https://graph.facebook.com/v17.0/{self.phone_number_id}/messages"
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json',
        }
        data = {
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": "image",
            "image": {
                "link": image_url,
                "caption": caption
            }
        }
        
        response = requests.post(url, headers=headers, json=data)
        return response.json()
    
    def process_user_message(self, phone_number, message_text, user_profile=None):
        """Process incoming message with Gemini AI and return response"""
        try:
            # Get user context from your system
            user_context = self.get_user_context(phone_number)
            
            # Prepare AI prompt with system context
            system_prompt = f"""
            You are an AI assistant for Sakina Gas Fuel Tracker system. You help manage fuel inventory, shipments, and truck loadings.
            
            User Context:
            - Phone: {phone_number}
            - User: {user_context.get('username', 'Unknown')}
            - Permissions: {user_context.get('permissions', [])}
            
            Available Commands:
            1. "stock status" or "stock" - Get current fuel stock levels
            2. "recent shipments" or "shipments" - Get latest shipment arrivals  
            3. "latest loadings" or "loadings" - Get recent truck loadings
            4. "add shipment [details]" - Add new shipment (if permitted)
            5. "trip status [order_number]" - Check specific trip status
            6. "help" - Show available commands
            7. "summary" or "dashboard" - Get overall business summary
            
            Current System Data:
            - Total Products: {self.get_products_count()}
            - Active Shipments: {self.get_active_shipments_count()}
            - Pending Trips: {self.get_pending_trips_count()}
            
            Guidelines:
            - Be helpful and concise
            - Use emojis to make responses friendly
            - If user needs data, suggest using EXECUTE commands
            - Always verify user permissions before data operations
            - Format numbers with commas (e.g., 45,230L)
            
            User Message: {message_text}
            
            Respond helpfully. If you need to fetch system data, include EXECUTE commands in your response.
            """
            
            # Send to Gemini
            response = self.model.generate_content(system_prompt)
            ai_response = response.text
            
            # Check if AI needs to perform system actions
            if "EXECUTE:" in ai_response:
                ai_response = self.execute_system_command(ai_response, user_context)
            
            return ai_response
            
        except Exception as e:
            logger.error(f"Error processing Gemini AI message: {e}")
            return "Sorry, I encountered an error processing your request. Please try again or type 'help' for available commands."
    
    def process_image_message(self, phone_number, image_url):
        """Process image messages with Gemini Vision"""
        try:
            # Download image
            response = requests.get(image_url)
            image = Image.open(io.BytesIO(response.content))
            
            # Prepare prompt for document analysis
            prompt = """
            Analyze this image which might be a fuel tracking document (loading authority, receipt, BOL, etc.).
            
            Extract and structure any relevant information:
            
            🔍 DOCUMENT TYPE: [Identify what type of document this is]
            
            📋 EXTRACTED DATA:
            - Order Numbers (KPC, BOL, etc.): 
            - Date & Time:
            - Vehicle/Truck Info:
            - Product Type (AGO, PMS, etc.):
            - Quantity (Litres):
            - Customer/Company:
            - Destination:
            - Driver Details:
            - Other Important Info:
            
            💡 SUGGESTED ACTION: [What should be done with this information - create shipment, update trip, etc.]
            
            ⚠️ CONFIDENCE: [How confident are you in the extracted data - High/Medium/Low]
            
            If the image is not a document, just describe what you see and ask for clarification.
            """
            
            # Use Gemini Vision to analyze image
            response = self.vision_model.generate_content([prompt, image])
            
            return f"📄 **Document Analysis:**\n\n{response.text}"
            
        except Exception as e:
            logger.error(f"Error processing image with Gemini: {e}")
            return "Sorry, I couldn't analyze this image. Please make sure it's a clear photo of a document and try again."
    
    def get_user_context(self, phone_number):
        """Get user information based on phone number"""
        try:
            # You'll need to link phone numbers to users in your system
            user = User.objects.filter(userprofile__phone_number=phone_number).first()
            if user:
                return {
                    'username': user.username,
                    'user_id': user.id,
                    'permissions': list(user.get_all_permissions()),
                    'is_admin': user.is_staff
                }
            return {'username': 'Guest', 'permissions': [], 'user_id': None}
        except Exception as e:
            logger.error(f"Error getting user context: {e}")
            return {'username': 'Guest', 'permissions': [], 'user_id': None}
    
    def execute_system_command(self, ai_response, user_context):
        """Execute system commands requested by AI"""
        try:
            if not user_context.get('user_id'):
                return ai_response.replace("EXECUTE:", "") + "\n\n⚠️ Please register your phone number to access system data."
            
            if "EXECUTE:stock_status" in ai_response or "EXECUTE:stock" in ai_response:
                stock_data = self.get_stock_summary(user_context['user_id'])
                return f"📊 **Current Stock Levels:**\n\n{stock_data}"
            elif "EXECUTE:recent_shipments" in ai_response or "EXECUTE:shipments" in ai_response:
                shipments = self.get_recent_shipments(user_context['user_id'])
                return f"🚢 **Recent Shipments:**\n\n{shipments}"
            elif "EXECUTE:latest_loadings" in ai_response or "EXECUTE:loadings" in ai_response:
                loadings = self.get_latest_loadings(user_context['user_id'])
                return f"🚛 **Latest Loadings:**\n\n{loadings}"
            elif "EXECUTE:summary" in ai_response or "EXECUTE:dashboard" in ai_response:
                summary = self.get_business_summary(user_context['user_id'])
                return f"📈 **Business Summary:**\n\n{summary}"
            else:
                return ai_response.replace("EXECUTE:", "")
        except Exception as e:
            return f"❌ Error executing command: {str(e)}"
    
    def get_stock_summary(self, user_id):
        """Get current stock summary"""
        try:
            from .views import get_user_accessible_shipments, get_user_accessible_trips, calculate_product_stock_summary
            
            user = User.objects.get(id=user_id)
            shipments_qs = get_user_accessible_shipments(user)
            trips_qs = get_user_accessible_trips(user)
            
            stock_summary = calculate_product_stock_summary(shipments_qs, trips_qs, user)
            
            if not stock_summary:
                return "📦 No stock data available"
            
            summary_text = ""
            total_available = 0
            
            for item in stock_summary:
                available = float(item.get('net_available', 0))
                total_available += available
                status_emoji = "🟢" if available > 10000 else "🟡" if available > 5000 else "🔴"
                
                summary_text += f"{status_emoji} **{item['product_name']}** @ {item['destination_name']}\n"
                summary_text += f"   Available: {available:,.0f}L\n"
                summary_text += f"   Physical: {float(item.get('physical_stock', 0)):,.0f}L\n"
                summary_text += f"   Committed: {float(item.get('booked_stock', 0)):,.0f}L\n\n"
            
            summary_text += f"🏆 **Total Available: {total_available:,.0f}L**"
            return summary_text
            
        except Exception as e:
            logger.error(f"Error getting stock summary: {e}")
            return "❌ Error retrieving stock data"
    
    def get_recent_shipments(self, user_id):
        """Get recent shipments"""
        try:
            user = User.objects.get(id=user_id)
            from .views import get_user_accessible_shipments
            
            recent_shipments = get_user_accessible_shipments(user).order_by('-created_at')[:5]
            
            if not recent_shipments:
                return "📦 No recent shipments found"
            
            shipments_text = ""
            for i, shipment in enumerate(recent_shipments, 1):
                shipments_text += f"{i}. **{shipment.vessel_id_tag}**\n"
                shipments_text += f"   📊 {shipment.quantity_litres:,.0f}L {shipment.product.name}\n"
                shipments_text += f"   🏭 From: {shipment.supplier_name}\n"
                shipments_text += f"   📅 {shipment.import_date.strftime('%b %d, %Y')}\n\n"
            
            return shipments_text
            
        except Exception as e:
            logger.error(f"Error getting recent shipments: {e}")
            return "❌ Error retrieving shipment data"
    
    def get_latest_loadings(self, user_id):
        """Get latest truck loadings"""
        try:
            user = User.objects.get(id=user_id)
            from .views import get_latest_loadings
            
            loadings = get_latest_loadings(user, limit=5)
            
            if not loadings:
                return "🚛 No recent loadings found"
            
            loadings_text = ""
            for i, loading in enumerate(loadings, 1):
                status_emoji = "✅" if "Delivered" in loading['status'] else "🚛"
                loadings_text += f"{i}. {status_emoji} **{loading['truck_number']}**\n"
                loadings_text += f"   🛢️ {loading['product']} ({loading['total_loaded']:,.0f}L)\n"
                loadings_text += f"   🏢 {loading['customer']} → {loading['destination']}\n"
                loadings_text += f"   📅 {loading['loading_date'].strftime('%b %d, %Y')}\n\n"
            
            return loadings_text
            
        except Exception as e:
            logger.error(f"Error getting latest loadings: {e}")
            return "❌ Error retrieving loading data"
    
    def get_business_summary(self, user_id):
        """Get overall business summary"""
        try:
            user = User.objects.get(id=user_id)
            from .views import get_user_accessible_shipments, get_user_accessible_trips
            from datetime import datetime, timedelta
            
            # Get data for last 7 days
            week_ago = datetime.now().date() - timedelta(days=7)
            
            recent_shipments = get_user_accessible_shipments(user).filter(created_at__date__gte=week_ago)
            recent_trips = get_user_accessible_trips(user).filter(loading_date__gte=week_ago)
            
            delivered_trips = recent_trips.filter(status='DELIVERED')
            
            summary = f"""
📊 **Weekly Summary** (Last 7 days)

🚢 **Shipments Received:** {recent_shipments.count()}
   Total: {sum(s.quantity_litres for s in recent_shipments):,.0f}L

🚛 **Loadings Completed:** {delivered_trips.count()}
   Total: {sum(t.total_loaded for t in delivered_trips):,.0f}L

⏳ **Pending Trips:** {recent_trips.filter(status='PENDING').count()}

📈 **Active Products:** {self.get_products_count()}
🏭 **Active Shipments:** {self.get_active_shipments_count()}
            """
            
            return summary.strip()
            
        except Exception as e:
            logger.error(f"Error getting business summary: {e}")
            return "❌ Error retrieving business summary"
    
    def get_products_count(self):
        return Product.objects.count()
    
    def get_active_shipments_count(self):
        return Shipment.objects.filter(quantity_remaining__gt=0).count()
    
    def get_pending_trips_count(self):
        return Trip.objects.filter(status='PENDING').count()

# WhatsApp Webhook Views
@csrf_exempt
@require_http_methods(["GET", "POST"])
def whatsapp_webhook(request):
    """Handle WhatsApp webhook for incoming messages"""
    
    if request.method == "GET":
        # Webhook verification
        verify_token = request.GET.get('hub.verify_token')
        if verify_token == settings.WHATSAPP_CONFIG['VERIFY_TOKEN']:
            return HttpResponse(request.GET.get('hub.challenge'))
        return HttpResponse('Verification failed', status=403)
    
    elif request.method == "POST":
        # Handle incoming messages
        try:
            data = json.loads(request.body.decode('utf-8'))
            
            # Extract message data
            for entry in data.get('entry', []):
                for change in entry.get('changes', []):
                    value = change.get('value', {})
                    
                    if 'messages' in value:
                        for message in value['messages']:
                            phone_number = message['from']
                            message_type = message.get('type', 'text')
                            
                            # Initialize AI assistant
                            ai_assistant = WhatsAppGeminiAssistant()
                            
                            if message_type == 'text':
                                # Text message
                                message_text = message.get('text', {}).get('body', '')
                                response = ai_assistant.process_user_message(phone_number, message_text)
                                ai_assistant.send_message(phone_number, response)
                                
                            elif message_type == 'image':
                                # Image message
                                image_id = message.get('image', {}).get('id')
                                if image_id:
                                    # Download image from WhatsApp
                                    image_url = ai_assistant.get_media_url(image_id)
                                    response = ai_assistant.process_image_message(phone_number, image_url)
                                    ai_assistant.send_message(phone_number, response)
            
            return JsonResponse({'status': 'success'})
            
        except Exception as e:
            logger.error(f"Error processing WhatsApp webhook: {e}")
            return JsonResponse({'error': str(e)}, status=500)
    
    def get_media_url(self, media_id):
        """Get media URL from WhatsApp"""
        url = f"https://graph.facebook.com/v17.0/{media_id}"
        headers = {'Authorization': f'Bearer {self.access_token}'}
        response = requests.get(url, headers=headers)
        return response.json().get('url')