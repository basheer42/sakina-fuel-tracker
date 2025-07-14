# Complete Telegram Bot Integration for Loading Authority Processing
# File: shipments/telegram_bot.py

import json
import requests
import google.generativeai as genai
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.http import HttpResponse, JsonResponse
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.db import transaction
from .models import Shipment, Trip, Product, Customer, Vehicle
from .views import parse_loading_authority_pdf, create_trip_from_parsed_data
import logging
import io
from PIL import Image
import tempfile
import os

logger = logging.getLogger(__name__)

class TelegramBot:
    def __init__(self):
        self.bot_token = settings.TELEGRAM_CONFIG['BOT_TOKEN']
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        
        # Configure Gemini AI
        genai.configure(api_key=settings.GEMINI_API_KEY)
        self.model = genai.GenerativeModel('gemini-1.5-flash')
        self.vision_model = genai.GenerativeModel('gemini-1.5-flash')
    
    def send_message(self, chat_id, text, parse_mode='Markdown'):
        """Send a text message to Telegram"""
        url = f"{self.base_url}/sendMessage"
        data = {
            'chat_id': chat_id,
            'text': text,
            'parse_mode': parse_mode
        }
        response = requests.post(url, json=data)
        return response.json()
    
    def send_document(self, chat_id, document_url, caption=""):
        """Send a document to Telegram"""
        url = f"{self.base_url}/sendDocument"
        data = {
            'chat_id': chat_id,
            'document': document_url,
            'caption': caption
        }
        response = requests.post(url, json=data)
        return response.json()
    
    def download_file(self, file_id):
        """Download file from Telegram"""
        try:
            print(f"🔥 DEBUG: Starting file download for file_id: {file_id}")
            
            # Get file info
            file_info_url = f"{self.base_url}/getFile?file_id={file_id}"
            file_response = requests.get(file_info_url)
            file_data = file_response.json()
            
            print(f"🔥 DEBUG: File info response: {file_data}")
            
            if not file_data.get('ok'):
                print(f"🔥 DEBUG: File info request failed: {file_data}")
                return None
                
            file_path = file_data['result']['file_path']
            file_url = f"https://api.telegram.org/file/bot{self.bot_token}/{file_path}"
            
            print(f"🔥 DEBUG: Downloading from URL: {file_url}")
            
            # Download the actual file
            file_content = requests.get(file_url)
            
            print(f"🔥 DEBUG: Download response status: {file_content.status_code}")
            print(f"🔥 DEBUG: Downloaded content size: {len(file_content.content)} bytes")
            print(f"🔥 DEBUG: Content starts with: {file_content.content[:20]}")
            
            return file_content.content if file_content.status_code == 200 else None
            
        except Exception as e:
            print(f"🔥 DEBUG: Error in download_file: {e}")
            logger.error(f"Error downloading file: {e}")
            return None
    
    def process_document_upload(self, chat_id, file_id, filename):
        """Process PDF or image document uploads"""
        try:
            print(f"🔥 DEBUG: Processing document upload - chat_id: {chat_id}, file_id: {file_id}, filename: {filename}")
            
            user_context = self.get_user_context(chat_id)
            
            if not user_context.get('user_id'):
                return """❌ *Registration Required*
                
Please register your Telegram account in the system to upload documents.

Contact your administrator to link your Telegram ID: `{}`""".format(chat_id)
            
            print(f"🔥 DEBUG: User context found: {user_context['username']}")
            
            # Download the file
            file_content = self.download_file(file_id)
            if not file_content:
                return "❌ Failed to download the document. Please try again."
            
            print(f"🔥 DEBUG: File downloaded successfully, size: {len(file_content)} bytes")
            
            # Process based on file type
            if filename.lower().endswith('.pdf'):
                return self._process_pdf_loading_authority(chat_id, file_content, user_context, filename)
            elif any(filename.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png']):
                return self._process_image_loading_authority(chat_id, file_content, user_context, filename)
            else:
                return "❌ Please send a PDF document or image (JPG/PNG) of the loading authority."
                
        except Exception as e:
            print(f"🔥 DEBUG: Error in process_document_upload: {e}")
            logger.error(f"Error processing document upload: {e}")
            return "❌ Error processing document. Please try again or contact support."
    
    def _process_pdf_loading_authority(self, chat_id, pdf_content, user_context, filename):
        """Process PDF loading authority using existing system logic"""
        try:
            print(f"🔥 DEBUG: Processing PDF - filename: {filename}")
            print(f"🔥 DEBUG: PDF content size: {len(pdf_content)} bytes")
            print(f"🔥 DEBUG: PDF content starts with: {pdf_content[:20]}")
            print(f"🔥 DEBUG: Is PDF header present: {pdf_content.startswith(b'%PDF')}")
            
            # Create Django file object
            pdf_file = ContentFile(pdf_content, name=filename)
            
            print(f"🔥 DEBUG: Created ContentFile, about to call parse_loading_authority_pdf")
            
            # Use existing PDF parsing logic
            parsed_data = parse_loading_authority_pdf(pdf_file)
            
            print(f"🔥 DEBUG: Parsed data result: {parsed_data}")
            
            if not parsed_data:
                return """❌ *Could not extract data from the PDF*

Please ensure:
• It's a valid KPC Loading Authority document
• The PDF is not corrupted or password protected
• All required fields are clearly visible

You can also try sending a clear photo of the document."""
            
            print(f"🔥 DEBUG: PDF parsed successfully, creating trip...")
            
            # Create trip using existing logic
            with transaction.atomic():
                user = User.objects.get(id=user_context['user_id'])
                
                # Create mock request object for existing function
                class MockRequest:
                    def __init__(self, user):
                        self.user = user
                
                mock_request = MockRequest(user)
                trip_instance = create_trip_from_parsed_data(
                    parsed_data, 
                    mock_request, 
                    filename
                )
                
                print(f"🔥 DEBUG: Trip creation result: {trip_instance}")
                
                if trip_instance:
                    return f"""✅ *Loading Authority Processed Successfully!*

🆔 *Trip ID:* `{trip_instance.id}`
📋 *KPC Order:* `{trip_instance.kpc_order_number}`
🚛 *Vehicle:* {trip_instance.vehicle.plate_number if trip_instance.vehicle else 'Not specified'}
👤 *Customer:* {trip_instance.customer.name if trip_instance.customer else 'Not specified'}
⛽ *Product:* {trip_instance.product.name if trip_instance.product else 'Not specified'}
📊 *Total Quantity:* {trip_instance.total_requested_from_compartments:,.0f} litres
📅 *Status:* {trip_instance.get_status_display()}

*Compartment Breakdown:*
{self._format_compartments(trip_instance)}

The trip has been created with 'Pending' status.

🔗 [View in System](https://Sakinagas-Basheer42.pythonanywhere.com/shipments/trips/{trip_instance.id}/)"""
                else:
                    return "❌ Failed to create trip record. Please check the document format and try again."
                    
        except Exception as e:
            print(f"🔥 DEBUG: Error processing PDF loading authority: {e}")
            logger.error(f"Error processing PDF loading authority: {e}")
            import traceback
            traceback.print_exc()
            return "❌ Error processing PDF. Please ensure the document is a valid KPC Loading Authority and try again."
    
    def _process_image_loading_authority(self, chat_id, image_content, user_context, filename):
        """Process image of loading authority using Gemini Vision"""
        try:
            print(f"🔥 DEBUG: Processing image - filename: {filename}")
            
            # Convert to PIL Image
            image = Image.open(io.BytesIO(image_content))
            
            # Enhanced prompt for loading authority extraction
            prompt = """
            Analyze this loading authority document image and extract key information.

            Extract the following data if clearly visible:
            - KPC Order Number (usually starts with 'S' followed by numbers)
            - Vehicle Plate Number
            - Customer/Company Name
            - Product Type (PMS, AGO, etc.)
            - Total Quantity (in litres)
            - Compartment quantities (usually 3 compartments)
            - Loading Date
            - Destination

            Return the information in this exact format:
            📋 KPC Order: [order number or "Not visible"]
            🚛 Vehicle: [plate number or "Not visible"]
            👤 Customer: [company name or "Not visible"]
            ⛽ Product: [fuel type or "Not visible"]
            📊 Quantity: [total litres or "Not visible"]
            🗂️ Compartments: [comp1] + [comp2] + [comp3] litres or "Not visible"
            📅 Date: [loading date or "Not visible"]
            📍 Destination: [location or "Not visible"]

            Only extract what you can clearly and confidently read.
            """
            
            response = self.vision_model.generate_content([prompt, image])
            extracted_info = response.text.strip()
            
            return f"""📄 *Loading Authority Image Analysis:*

{extracted_info}

⚠️ *Important Notes:*
• Image analysis may not be 100% accurate
• Please verify all details before proceeding
• For guaranteed accuracy, upload the PDF document instead
• You can manually create the trip in the system if needed

💡 *Next Steps:*
1. Review the extracted information above
2. Create the trip manually if data looks correct
3. Or upload the PDF version for automatic processing"""
                
        except Exception as e:
            print(f"🔥 DEBUG: Error processing image: {e}")
            logger.error(f"Error processing image with Gemini: {e}")
            return "❌ Could not analyze the image. Please ensure it's a clear photo of a loading authority document, or upload the PDF version instead."
    
    def _format_compartments(self, trip_instance):
        """Format compartment information for display"""
        try:
            compartments = trip_instance.loadingcompartment_set.all().order_by('compartment_number')
            if compartments:
                comp_list = []
                for comp in compartments:
                    comp_list.append(f"  • Compartment {comp.compartment_number}: {comp.quantity_requested_litres:,.0f}L")
                return "\n".join(comp_list)
            else:
                return "  • Compartment details not available"
        except Exception:
            return "  • Compartment details not available"
    
    def process_message(self, chat_id, message_text, username=None):
        """Process text messages"""
        try:
            user_context = self.get_user_context(chat_id)
            
            # Handle commands
            if message_text.startswith('/start'):
                return self._handle_start_command(chat_id, username, user_context)
            elif message_text.startswith('/help'):
                return self._handle_help_command()
            elif message_text.lower() in ['stock', 'inventory']:
                return self._handle_stock_query(user_context)
            elif message_text.lower() in ['trips', 'loadings']:
                return self._handle_trips_query(user_context)
            elif message_text.lower() in ['summary', 'dashboard']:
                return self._handle_summary_query(user_context)
            else:
                return self._handle_general_query(chat_id, message_text, user_context)
                
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            return "❌ Error processing your message. Please try again or use /help for available commands."
    
    def _handle_start_command(self, chat_id, username, user_context):
        """Handle /start command"""
        if user_context.get('user_id'):
            return f"""👋 Welcome back, *{user_context['username']}*!

🤖 *Sakina Gas Telegram Bot*

I can help you with:
📋 Upload loading authorities (PDF/images)
📊 Check stock levels
🚛 View trip status
📈 Business summaries

Just send a loading authority document or use /help for commands."""
        else:
            return f"""👋 Hello {username or 'there'}!

🤖 *Sakina Gas Telegram Bot*

Your Telegram ID: `{chat_id}`

To get started, please contact your administrator to link this Telegram account to your system user account.

Once linked, you can:
📋 Upload loading authorities automatically
📊 Check stock levels
🚛 Manage trips
📈 View business summaries"""
    
    def _handle_help_command(self):
        """Handle /help command"""
        return """🤖 *Sakina Gas Telegram Bot - Help*

*Available Commands:*
📋 Send PDF/image → Auto-create trips
📊 `stock` → Current fuel inventory
🚛 `trips` → Recent truck loadings
📦 `shipments` → Latest arrivals
📈 `summary` → Business dashboard
❓ `/help` → Show this menu
🏠 `/start` → Main menu

*Quick Actions:*
• Send PDF documents for instant processing
• Send images of loading authorities
• Ask about stock levels by product
• Check trip status by order number

*File Upload:*
• PDF: Automatic parsing and trip creation
• Images: AI analysis and guidance

Just send a document or type a command!"""
    
    def _handle_stock_query(self, user_context):
        """Handle stock queries"""
        if not user_context.get('user_id'):
            return "❌ Please register your Telegram account to access stock information."
        
        try:
            # Get stock summary using existing models
            active_shipments = Shipment.objects.filter(quantity_remaining__gt=0)
            
            if not active_shipments:
                return "📊 *Stock Status:* No active inventory"
            
            stock_summary = "📊 *Current Stock Levels:*\n\n"
            for shipment in active_shipments[:10]:  # Limit to 10 for readability
                stock_summary += f"⛽ *{shipment.product.name}* ({shipment.destination})\n"
                stock_summary += f"   📦 Remaining: {shipment.quantity_remaining:,.0f}L\n"
                stock_summary += f"   🚢 Vessel: {shipment.vessel_id_tag}\n\n"
            
            return stock_summary
            
        except Exception as e:
            logger.error(f"Error getting stock info: {e}")
            return "❌ Error retrieving stock information."
    
    def _handle_trips_query(self, user_context):
        """Handle trip queries"""
        if not user_context.get('user_id'):
            return "❌ Please register your Telegram account to access trip information."
        
        try:
            recent_trips = Trip.objects.all().order_by('-created_at')[:5]
            
            if not recent_trips:
                return "🚛 *Recent Trips:* No trips found"
            
            trips_summary = "🚛 *Recent Trips:*\n\n"
            for trip in recent_trips:
                trips_summary += f"📋 *{trip.kpc_order_number}*\n"
                trips_summary += f"   🚛 Vehicle: {trip.vehicle.plate_number if trip.vehicle else 'N/A'}\n"
                trips_summary += f"   📊 Quantity: {trip.total_requested_from_compartments:,.0f}L\n"
                trips_summary += f"   📅 Status: {trip.get_status_display()}\n\n"
            
            return trips_summary
            
        except Exception as e:
            logger.error(f"Error getting trips info: {e}")
            return "❌ Error retrieving trip information."
    
    def _handle_summary_query(self, user_context):
        """Handle summary queries"""
        if not user_context.get('user_id'):
            return "❌ Please register your Telegram account to access summary information."
        
        try:
            total_products = Product.objects.count()
            active_shipments = Shipment.objects.filter(quantity_remaining__gt=0).count()
            pending_trips = Trip.objects.filter(status='PENDING').count()
            
            return f"""📈 *Business Summary:*

📦 *Products:* {total_products}
🚢 *Active Shipments:* {active_shipments}
🚛 *Pending Trips:* {pending_trips}

Use specific commands for detailed information:
• `stock` - Detailed inventory
• `trips` - Recent loadings
• Send documents for processing"""
            
        except Exception as e:
            logger.error(f"Error getting summary: {e}")
            return "❌ Error retrieving summary information."
    
    def _handle_general_query(self, chat_id, message_text, user_context):
        """Handle general queries with AI"""
        try:
            system_prompt = f"""
            You are an AI assistant for Sakina Gas Fuel Tracker system.
            
            User: {user_context.get('username', 'Guest')}
            Telegram ID: {chat_id}
            
            Available features:
            - Upload loading authorities (PDF or image)
            - Check stock levels (command: stock)
            - View trip status (command: trips)
            - Business summaries (command: summary)
            
            Keep responses concise and helpful. Use Telegram markdown formatting.
            Guide users to use specific commands or upload documents.
            """
            
            response = self.model.generate_content(f"{system_prompt}\n\nUser message: {message_text}")
            return response.text
            
        except Exception as e:
            logger.error(f"AI processing error: {e}")
            return "🤖 I'm here to help with your fuel tracking needs! Use /help for available commands or upload a loading authority document."
    
    def get_user_context(self, chat_id):
        """Get user information based on Telegram chat ID"""
        try:
            # Look for user with this telegram_chat_id
            user = User.objects.filter(userprofile__telegram_chat_id=str(chat_id)).first()
            
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


# Telegram Webhook View
@csrf_exempt
@require_http_methods(["POST"])
def telegram_webhook(request):
    """Handle Telegram webhook for incoming messages"""
    try:
        print(f"🔥 DEBUG: Telegram webhook called")
        
        data = json.loads(request.body.decode('utf-8'))
        print(f"🔥 DEBUG: Webhook data: {data}")
        
        # Extract message data
        message = data.get('message', {})
        if not message:
            print(f"🔥 DEBUG: No message in webhook data")
            return JsonResponse({'status': 'no_message'})
        
        chat_id = message['chat']['id']
        username = message.get('from', {}).get('username')
        
        print(f"🔥 DEBUG: Processing message from chat_id: {chat_id}, username: {username}")
        
        # Initialize bot
        bot = TelegramBot()
        
        # Handle different message types
        if 'text' in message:
            # Text message
            text = message['text']
            print(f"🔥 DEBUG: Text message: {text}")
            response = bot.process_message(chat_id, text, username)
            bot.send_message(chat_id, response)
            
        elif 'document' in message:
            # Document upload
            file_id = message['document']['file_id']
            filename = message['document']['file_name']
            print(f"🔥 DEBUG: Document upload - file_id: {file_id}, filename: {filename}")
            response = bot.process_document_upload(chat_id, file_id, filename)
            bot.send_message(chat_id, response)
            
        elif 'photo' in message:
            # Photo upload
            photo = message['photo'][-1]  # Get largest photo
            file_id = photo['file_id']
            filename = f"telegram_image_{chat_id}.jpg"
            print(f"🔥 DEBUG: Photo upload - file_id: {file_id}")
            response = bot.process_document_upload(chat_id, file_id, filename)
            bot.send_message(chat_id, response)
        
        print(f"🔥 DEBUG: Webhook processing complete")
        return JsonResponse({'status': 'success'})
        
    except Exception as e:
        print(f"🔥 DEBUG: Error processing Telegram webhook: {e}")
        logger.error(f"Error processing Telegram webhook: {e}")
        import traceback
        traceback.print_exc()
        return JsonResponse({'error': str(e)}, status=500)