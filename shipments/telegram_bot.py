# Enhanced Telegram Bot Integration with TR830 Support
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
from .tr830_parser import TR830Parser, TR830ParseError, TR830Entry
import logging
import io
from PIL import Image
import tempfile
import os
from datetime import datetime
from typing import List, Tuple

logger = logging.getLogger(__name__)

class TelegramBot:
    def __init__(self):
        self.bot_token = settings.TELEGRAM_CONFIG['BOT_TOKEN']
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        
        # Configure Gemini AI
        genai.configure(api_key=settings.GEMINI_API_KEY)
        self.model = genai.GenerativeModel('gemini-1.5-flash')
        self.vision_model = genai.GenerativeModel('gemini-1.5-flash')
        
        # Initialize TR830 parser
        self.tr830_parser = TR830Parser()
    
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
                return f"""❌ *Registration Required*
                
Please register your Telegram account in the system to upload documents.

Contact your administrator to link your Telegram ID: `{chat_id}`"""
            
            print(f"🔥 DEBUG: User context found: {user_context['username']}")
            
            # Download the file
            file_content = self.download_file(file_id)
            if not file_content:
                return "❌ Failed to download the document. Please try again."
            
            print(f"🔥 DEBUG: File downloaded successfully, size: {len(file_content)} bytes")
            
            # Determine document type and process accordingly
            filename_lower = filename.lower()
            
            # Check if it's a TR830 document
            if self._is_tr830_document(filename, file_content):
                return self._process_tr830_document(chat_id, file_content, user_context, filename)
            # Check if it's a loading authority
            elif filename_lower.endswith('.pdf'):
                return self._process_pdf_loading_authority(chat_id, file_content, user_context, filename)
            elif any(filename_lower.endswith(ext) for ext in ['.jpg', '.jpeg', '.png']):
                return self._process_image_loading_authority(chat_id, file_content, user_context, filename)
            else:
                return "❌ Please send a PDF document or image (JPG/PNG) of the loading authority or TR830 document."
                
        except Exception as e:
            print(f"🔥 DEBUG: Error in process_document_upload: {e}")
            logger.error(f"Error processing document upload: {e}")
            import traceback
            traceback.print_exc()
            return "❌ Error processing document. Please try again or contact support."
    
    def _is_tr830_document(self, filename, file_content):
        """Determine if the uploaded document is a TR830"""
        try:
            # Check filename patterns
            filename_lower = filename.lower()
            tr830_indicators = ['tr830', 'tr-830', 'transit', 'shipment']
            
            if any(indicator in filename_lower for indicator in tr830_indicators):
                return True
            
            # For PDF files, try to extract some text to check content
            if filename_lower.endswith('.pdf'):
                try:
                    import pdfplumber
                    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp_file:
                        tmp_file.write(file_content)
                        tmp_file.flush()
                        
                        with pdfplumber.open(tmp_file.name) as pdf:
                            if pdf.pages:
                                first_page_text = pdf.pages[0].extract_text() or ""
                                tr830_content_indicators = [
                                    'tr830', 'tr-830', 'transit document', 
                                    'vessel', 'gasoil', 'motor gasoline',
                                    'consignor', 'avalue', 'marks'
                                ]
                                
                                text_lower = first_page_text.lower()
                                if any(indicator in text_lower for indicator in tr830_content_indicators):
                                    return True
                        
                        os.unlink(tmp_file.name)
                        
                except Exception as e:
                    print(f"🔥 DEBUG: Error checking TR830 content: {e}")
                    pass
            
            return False
            
        except Exception as e:
            print(f"🔥 DEBUG: Error in _is_tr830_document: {e}")
            return False
    
    def _process_tr830_document(self, chat_id, file_content, user_context, filename):
        """Process TR830 document and create shipments"""
        try:
            print(f"🔥 DEBUG: Processing TR830 document: {filename}")
            
            # Save file to temporary location
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp_file:
                tmp_file.write(file_content)
                tmp_file.flush()
                temp_path = tmp_file.name
            
            try:
                # Parse the TR830 document
                import_date, entries = self.tr830_parser.parse_pdf(temp_path)
                
                if not entries:
                    return "❌ No shipment data found in the TR830 document. Please check the file format."
                
                # Validate the parsing result
                success, errors = self.tr830_parser.validate_parsing_result(entries)
                
                # Create shipments from the parsed entries
                created_shipments = []
                with transaction.atomic():
                    for entry in entries:
                        try:
                            # Get or create product
                            product_name = entry.product_type or 'Unknown'
                            product, _ = Product.objects.get_or_create(
                                name=product_name,
                                defaults={'description': f'{product_name} fuel'}
                            )
                            
                            # Ensure we have required data
                            if not entry.avalue or entry.avalue <= 0:
                                continue
                            
                            # Create shipment
                            shipment = Shipment.objects.create(
                                vessel_id_tag=entry.marks or f"TR830-{import_date.strftime('%Y%m%d')}",
                                supplier=entry.supplier or "Unknown Supplier",
                                product=product,
                                quantity_loaded=entry.avalue,
                                quantity_remaining=entry.avalue,
                                destination=entry.destination or "Unknown",
                                cost_per_liter=0.00,  # To be updated manually
                                total_cost=0.00,  # To be updated manually
                                loading_date=import_date,
                                notes=f"Auto-created from TR830: {filename}",
                            )
                            created_shipments.append(shipment)
                            
                        except Exception as entry_error:
                            print(f"🔥 DEBUG: Error creating shipment for entry {entry}: {entry_error}")
                            continue
                
                # Generate response message
                if created_shipments:
                    response_msg = f"✅ *TR830 Processing Complete*\n\n"
                    response_msg += f"📄 *Document:* {filename}\n"
                    response_msg += f"📅 *Import Date:* {import_date.strftime('%d/%m/%Y')}\n"
                    response_msg += f"📦 *Shipments Created:* {len(created_shipments)}\n\n"
                    
                    for i, shipment in enumerate(created_shipments, 1):
                        response_msg += f"*Shipment {i}:*\n"
                        response_msg += f"🚢 Vessel: {shipment.vessel_id_tag}\n"
                        response_msg += f"⛽ Product: {shipment.product.name}\n"
                        response_msg += f"📊 Quantity: {shipment.quantity_loaded:,.0f}L\n"
                        response_msg += f"🌍 Destination: {shipment.destination}\n\n"
                    
                    # Add validation warnings if any
                    if not success and errors:
                        response_msg += "⚠️ *Validation Warnings:*\n"
                        for error in errors[:3]:  # Limit to 3 errors for readability
                            response_msg += f"• {error}\n"
                        response_msg += "\n"
                    
                    response_msg += "💡 *Next Steps:*\n"
                    response_msg += "• Update cost per liter in the system\n"
                    response_msg += "• Verify supplier information\n"
                    response_msg += "• Check destination details\n"
                    
                    # Get summary for additional info
                    summary = self.tr830_parser.get_parsing_summary(entries)
                    if summary.get('total_quantity'):
                        response_msg += f"\n📊 *Total Quantity:* {summary['total_quantity']:,.0f}L"
                    
                    return response_msg
                else:
                    return f"❌ No valid shipments could be created from the TR830 document.\n\nIssues found:\n" + "\n".join(f"• {error}" for error in errors)
                
            finally:
                # Clean up temporary file
                try:
                    os.unlink(temp_path)
                except:
                    pass
                    
        except TR830ParseError as e:
            return f"❌ Error parsing TR830 document: {str(e)}\n\nPlease check if this is a valid TR830 document."
        except Exception as e:
            print(f"🔥 DEBUG: Error in _process_tr830_document: {e}")
            logger.error(f"Error processing TR830 document: {e}")
            import traceback
            traceback.print_exc()
            return "❌ Error processing TR830 document. Please try again or contact support."
    
    def _process_pdf_loading_authority(self, chat_id, file_content, user_context, filename):
        """Process PDF loading authority documents"""
        try:
            print(f"🔥 DEBUG: Processing PDF loading authority: {filename}")
            
            # Save PDF to temporary file
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp_file:
                tmp_file.write(file_content)
                tmp_file.flush()
                temp_path = tmp_file.name
            
            try:
                # Parse the loading authority
                order_data = parse_loading_authority_pdf(temp_path)
                
                if not order_data:
                    return "❌ Could not extract loading authority data from PDF. Please check the document format."
                
                # Create trip from parsed data
                user = User.objects.get(id=user_context['user_id'])
                trip = create_trip_from_parsed_data(order_data, user)
                
                if trip:
                    return f"""✅ *Loading Authority Processed Successfully*
                    
📄 *Document:* {filename}
🚛 *KPC Order:* {trip.kpc_order_number}
🚗 *Vehicle:* {trip.vehicle.license_plate if trip.vehicle else 'N/A'}
👤 *Customer:* {trip.customer.name if trip.customer else 'N/A'}
⛽ *Product:* {trip.product.name if trip.product else 'N/A'}
📊 *Total Quantity:* {trip.total_quantity:,.0f}L

💡 The trip has been created and is ready for processing."""
                else:
                    return "❌ Could not create trip from the loading authority data."
                    
            finally:
                try:
                    os.unlink(temp_path)
                except:
                    pass
                    
        except Exception as e:
            print(f"🔥 DEBUG: Error in _process_pdf_loading_authority: {e}")
            logger.error(f"Error processing PDF loading authority: {e}")
            return "❌ Error processing loading authority. Please try again or contact support."
    
    def _process_image_loading_authority(self, chat_id, file_content, user_context, filename):
        """Process image loading authority using AI vision"""
        try:
            print(f"🔥 DEBUG: Processing image loading authority: {filename}")
            
            # Convert to PIL Image for AI processing
            image = Image.open(io.BytesIO(file_content))
            
            # Use Gemini Vision to extract loading authority data
            prompt = """Extract loading authority information from this image. Look for:
            1. KPC Order Number
            2. Vehicle license plate
            3. Customer name
            4. Product type (PMS/AGO)
            5. Quantities by compartment
            6. Total quantity
            7. Any other relevant details
            
            Format the response as structured data that can be processed."""
            
            response = self.vision_model.generate_content([prompt, image])
            extracted_text = response.text
            
            return f"""🔍 *Image Analysis Complete*

📄 *File:* {filename}
🤖 *AI Extraction:*

{extracted_text}

💡 *Next Steps:*
• Review the extracted information
• Create loading manually if needed
• Upload PDF version for automatic processing"""
            
        except Exception as e:
            print(f"🔥 DEBUG: Error in _process_image_loading_authority: {e}")
            logger.error(f"Error processing image loading authority: {e}")
            return "❌ Error processing image. Please try uploading a PDF version or contact support."
    
    def process_message(self, chat_id, message_text, username=None):
        """Process text messages and commands"""
        try:
            message_lower = message_text.lower().strip()
            user_context = self.get_user_context(chat_id)
            
            # Handle commands
            if message_lower.startswith('/start'):
                return self._handle_start_command(chat_id, username, user_context)
            elif message_lower.startswith('/help'):
                return self._handle_help_command()
            elif message_lower in ['stock', 'inventory', 'stocks']:
                return self._handle_stock_query(user_context)
            elif message_lower in ['trips', 'loadings', 'loading']:
                return self._handle_trips_query(user_context)
            elif message_lower in ['shipments', 'vessels']:
                return self._handle_shipments_query(user_context)
            elif message_lower in ['summary', 'dashboard', 'overview']:
                return self._handle_summary_query(user_context)
            else:
                return self._handle_general_query(message_text, user_context)
                
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
📄 Process TR830 documents (PDF)
📊 Check stock levels
🚛 View trip status
📈 Business summaries

Just send a document or use /help for commands."""
        else:
            return f"""👋 Hello {username or 'there'}!

🤖 *Sakina Gas Telegram Bot*

Your Telegram ID: `{chat_id}`

To get started, please contact your administrator to link this Telegram account to your system user account.

Once linked, you can:
📋 Upload loading authorities automatically
📄 Process TR830 shipment documents
📊 Check stock levels
🚛 Manage trips
📈 View business summaries"""
    
    def _handle_help_command(self):
        """Handle /help command"""
        return """🤖 *Sakina Gas Telegram Bot - Help*

*Available Commands:*
📄 Send PDF → Auto-detect document type
📋 Loading Authority → Auto-create trips
📄 TR830 Document → Auto-create shipments
📊 `stock` → Current fuel inventory
🚛 `trips` → Recent truck loadings
📦 `shipments` → Latest arrivals
📈 `summary` → Business dashboard
❓ `/help` → Show this menu
🏠 `/start` → Main menu

*Document Types Supported:*
• **Loading Authority PDF**: Creates trips automatically
• **TR830 Shipment PDF**: Creates shipments automatically
• **Images**: AI analysis and guidance

*Quick Actions:*
• Send PDF documents for instant processing
• Send images for AI analysis
• Ask about stock levels by product
• Check trip status by order number

*File Upload:*
• PDF: Automatic parsing and data creation
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
            recent_trips = Trip.objects.order_by('-created_at')[:5]
            
            if not recent_trips:
                return "🚛 *No recent trips found*"
            
            trips_summary = "🚛 *Recent Trips:*\n\n"
            for trip in recent_trips:
                trips_summary += f"📋 *Order:* {trip.kpc_order_number}\n"
                trips_summary += f"🚗 Vehicle: {trip.vehicle.license_plate if trip.vehicle else 'N/A'}\n"
                trips_summary += f"📊 Quantity: {trip.total_quantity:,.0f}L\n"
                trips_summary += f"📈 Status: {trip.get_status_display()}\n\n"
            
            return trips_summary
            
        except Exception as e:
            logger.error(f"Error getting trips info: {e}")
            return "❌ Error retrieving trip information."
    
    def _handle_shipments_query(self, user_context):
        """Handle shipments queries"""
        if not user_context.get('user_id'):
            return "❌ Please register your Telegram account to access shipment information."
        
        try:
            recent_shipments = Shipment.objects.order_by('-created_at')[:5]
            
            if not recent_shipments:
                return "📦 *No recent shipments found*"
            
            shipments_summary = "📦 *Recent Shipments:*\n\n"
            for shipment in recent_shipments:
                shipments_summary += f"🚢 *Vessel:* {shipment.vessel_id_tag}\n"
                shipments_summary += f"⛽ Product: {shipment.product.name}\n"
                shipments_summary += f"📊 Loaded: {shipment.quantity_loaded:,.0f}L\n"
                shipments_summary += f"📦 Remaining: {shipment.quantity_remaining:,.0f}L\n"
                shipments_summary += f"🌍 Destination: {shipment.destination}\n\n"
            
            return shipments_summary
            
        except Exception as e:
            logger.error(f"Error getting shipments info: {e}")
            return "❌ Error retrieving shipment information."
    
    def _handle_summary_query(self, user_context):
        """Handle summary/dashboard queries"""
        if not user_context.get('user_id'):
            return "❌ Please register your Telegram account to access summary information."
        
        try:
            # Get basic statistics
            total_active_stock = Shipment.objects.filter(quantity_remaining__gt=0).count()
            total_stock_volume = sum(s.quantity_remaining for s in Shipment.objects.filter(quantity_remaining__gt=0))
            recent_trips_count = Trip.objects.filter(created_at__date=datetime.now().date()).count()
            
            summary = f"""📈 *Business Summary*

📦 *Stock Overview:*
• Active Shipments: {total_active_stock}
• Total Volume: {total_stock_volume:,.0f}L

🚛 *Today's Activity:*
• New Trips: {recent_trips_count}

💡 Use specific commands for detailed information:
• `stock` - Detailed inventory
• `trips` - Recent loadings  
• `shipments` - Latest arrivals"""
            
            return summary
            
        except Exception as e:
            logger.error(f"Error getting summary: {e}")
            return "❌ Error retrieving summary information."
    
    def _handle_general_query(self, message_text, user_context):
        """Handle general queries using AI"""
        try:
            system_prompt = f"""You are a helpful assistant for Sakina Gas Company's fuel tracking system via Telegram.
            
            User: {user_context.get('username', 'Guest')}
            Telegram ID: {user_context.get('chat_id', 'Unknown')}
            
            Available features:
            - Upload loading authorities (PDF or image)
            - Upload TR830 shipment documents (PDF)
            - Check stock levels (command: stock)
            - View trip status (command: trips)
            - View shipments (command: shipments)
            - Business summaries (command: summary)
            
            Keep responses concise and helpful. Use Telegram markdown formatting.
            Guide users to use specific commands or upload documents.
            """
            
            response = self.model.generate_content(f"{system_prompt}\n\nUser message: {message_text}")
            return response.text
            
        except Exception as e:
            logger.error(f"AI processing error: {e}")
            return "🤖 I'm here to help with your fuel tracking needs! Use /help for available commands or upload a document for automatic processing."
    
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
                    'is_admin': user.is_staff,
                    'chat_id': chat_id
                }
            
            return {'username': 'Guest', 'permissions': [], 'user_id': None, 'chat_id': chat_id}
            
        except Exception as e:
            logger.error(f"Error getting user context: {e}")
            return {'username': 'Guest', 'permissions': [], 'user_id': None, 'chat_id': chat_id}


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