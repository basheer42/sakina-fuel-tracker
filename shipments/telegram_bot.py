# shipments/telegram_bot.py - ENHANCED WITH INTERACTIVE TR830 PROCESSING
import json
import requests
import google.generativeai as genai
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.http import HttpResponse, JsonResponse
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.cache import cache
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
from decimal import Decimal, InvalidOperation
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
                return self._initiate_tr830_processing(chat_id, file_content, user_context, filename)
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
            return "❌ Error processing your document. Please try again."

    def process_message(self, chat_id, message_text, username=None):
        """Process text messages and commands"""
        try:
            print(f"🔥 DEBUG: Processing message - chat_id: {chat_id}, text: '{message_text}'")
            
            message_lower = message_text.lower().strip()
            user_context = self.get_user_context(chat_id)
            
            # CRITICAL: Check if user is in TR830 processing flow FIRST
            tr830_state = self._get_tr830_state(chat_id)
            print(f"🔥 DEBUG: TR830 state found: {tr830_state is not None}")
            
            if tr830_state:
                print(f"🔥 DEBUG: Handling TR830 input for step: {tr830_state.get('step')}")
                return self._handle_tr830_input(chat_id, message_text, tr830_state)
            
            # Handle commands
            if message_lower.startswith('/start'):
                return self._handle_start_command(chat_id, username, user_context)
            elif message_lower.startswith('/help'):
                return self._handle_help_command()
            elif message_lower.startswith('/cancel'):
                return self._handle_cancel_command(chat_id)
            elif message_lower in ['stock', 'inventory', 'stocks']:
                return self._handle_stock_query(user_context)
            elif message_lower in ['trips', 'loadings', 'loading']:
                return self._handle_trips_query(user_context)
            elif message_lower in ['shipments', 'vessels']:
                return self._handle_shipments_query(user_context)
            elif message_lower in ['summary', 'dashboard', 'overview']:
                return self._handle_summary_query(user_context)
            else:
                print(f"🔥 DEBUG: Falling back to general query handler")
                return self._handle_general_query(message_text, user_context)
                
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            print(f"🔥 DEBUG: Error in process_message: {e}")
            return "❌ Error processing your message. Please try again or use /help for available commands."

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
                                    'consignor', 'avalue', 'marks', 'seaenvoy',
                                    'commodity hs code', 'customs office'
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

    def _initiate_tr830_processing(self, chat_id, file_content, user_context, filename):
        """Initiate interactive TR830 processing"""
        try:
            print(f"🔥 DEBUG: Initiating TR830 processing: {filename}")
            
            # Parse the TR830 document first
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp_file:
                tmp_file.write(file_content)
                tmp_file.flush()
                temp_path = tmp_file.name
            
            try:
                # Parse the TR830 document using the FIXED parser
                import_date, entries = self.tr830_parser.parse_pdf(temp_path)
                
                if not entries:
                    return "❌ No shipment data found in the TR830 document. Please check the file format."
                
                if len(entries) > 1:
                    return f"⚠️ Warning: Found {len(entries)} entries in TR830. Expected only 1. Please check the document."
                
                entry = entries[0]  # Should be only one entry now
                
                # Store TR830 data in cache for interactive processing
                tr830_data = {
                    'filename': filename,
                    'import_date': import_date.isoformat(),
                    'vessel': entry.marks,
                    'product_type': entry.product_type,
                    'destination': entry.destination,
                    'quantity': str(entry.avalue),
                    'description': entry.description,
                    'step': 'supplier',  # Start with supplier input
                    'user_id': user_context['user_id']
                }
                
                # Cache the TR830 data for 30 minutes
                cache_key = f"tr830_processing_{chat_id}"
                print(f"🔥 DEBUG: Storing TR830 data with key '{cache_key}': {tr830_data}")
                cache.set(cache_key, tr830_data, 1800)  # 30 minutes
                
                # Verify the state was stored
                stored_state = cache.get(cache_key)
                print(f"🔥 DEBUG: Verification - state stored successfully: {stored_state is not None}")
                if stored_state:
                    print(f"🔥 DEBUG: Stored state step: {stored_state.get('step')}")
                
                # Send parsing summary and request supplier
                response_msg = f"✅ *TR830 Document Parsed Successfully!*\n\n"
                response_msg += f"📄 *Document:* {filename}\n"
                response_msg += f"📅 *Import Date:* {import_date.strftime('%d/%m/%Y')}\n"
                response_msg += f"🚢 *Vessel:* {entry.marks}\n"
                response_msg += f"⛽ *Product:* {entry.product_type}\n"
                response_msg += f"🌍 *Destination:* {entry.destination}\n"
                response_msg += f"📊 *Quantity:* {entry.avalue:,.0f}L\n\n"
                
                response_msg += f"📝 *Step 1 of 2: Enter Supplier Name*\n\n"
                response_msg += f"Please enter the supplier name for this shipment:\n"
                response_msg += f"Example: `Kuwait Petroleum Corporation`\n\n"
                response_msg += f"💡 Type `/cancel` to cancel this process."
                
                return response_msg
                
            finally:
                # Clean up temporary file
                try:
                    os.unlink(temp_path)
                except:
                    pass
                    
        except TR830ParseError as e:
            return f"❌ Error parsing TR830 document: {str(e)}\n\nPlease check if this is a valid TR830 document."
        except Exception as e:
            print(f"🔥 DEBUG: Error in _initiate_tr830_processing: {e}")
            logger.error(f"Error initiating TR830 processing: {e}")
            return "❌ Error processing TR830 document. Please try again or contact support."

    def _get_tr830_state(self, chat_id):
        """Get TR830 processing state using file-based storage (reliable)"""
        print(f"🔥 DEBUG: Getting TR830 state for chat_id: {chat_id}")
        
        try:
            import os
            import json
            from pathlib import Path
            
            # Use a reliable file-based storage
            state_dir = Path("/tmp/tr830_states")
            state_dir.mkdir(exist_ok=True)
            state_file = state_dir / f"{chat_id}.json"
            
            if state_file.exists():
                try:
                    with open(state_file, 'r') as f:
                        state = json.load(f)
                    print(f"🔥 DEBUG: Found state in FILE: step='{state.get('step')}'")
                    return state
                except Exception as e:
                    print(f"🔥 DEBUG: Error reading state file: {e}")
                    
            print(f"🔥 DEBUG: No state file found: {state_file}")
            return None
                
        except Exception as e:
            print(f"🔥 DEBUG: Error accessing file state: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _set_tr830_state(self, chat_id, state):
        """Set TR830 processing state using file-based storage (reliable)"""
        print(f"🔥 DEBUG: Setting TR830 state for chat_id: {chat_id}, step: {state.get('step')}")
        
        try:
            import os
            import json
            from pathlib import Path
            
            # Use a reliable file-based storage
            state_dir = Path("/tmp/tr830_states")
            state_dir.mkdir(exist_ok=True)
            state_file = state_dir / f"{chat_id}.json"
            
            with open(state_file, 'w') as f:
                json.dump(state, f, indent=2)
            
            print(f"🔥 DEBUG: State saved to FILE: {state_file}")
            
            # Verify the file was written
            if state_file.exists():
                print(f"🔥 DEBUG: State file verification: SUCCESS")
            else:
                print(f"🔥 DEBUG: State file verification: FAILED")
                    
        except Exception as e:
            print(f"🔥 DEBUG: Error setting file state: {e}")
            import traceback
            traceback.print_exc()

    def _clear_tr830_state(self, chat_id):
        """Clear TR830 processing state from file storage"""
        print(f"🔥 DEBUG: Clearing TR830 state for chat_id: {chat_id}")
        
        try:
            import os
            from pathlib import Path
            
            state_dir = Path("/tmp/tr830_states")
            state_file = state_dir / f"{chat_id}.json"
            
            if state_file.exists():
                state_file.unlink()
                print(f"🔥 DEBUG: State file deleted: {state_file}")
            else:
                print(f"🔥 DEBUG: No state file to delete")
                
        except Exception as e:
            print(f"🔥 DEBUG: Error clearing file state: {e}")

    def _handle_tr830_input(self, chat_id, message_text, tr830_state):
        """Handle user input during TR830 processing flow"""
        try:
            current_step = tr830_state.get('step')
            
            if current_step == 'supplier':
                # User entered supplier name
                supplier_name = message_text.strip()
                
                if len(supplier_name) < 2:
                    return "❌ Supplier name too short. Please enter a valid supplier name:"
                
                # Update state with supplier
                tr830_state['supplier'] = supplier_name
                tr830_state['step'] = 'price'
                self._set_tr830_state(chat_id, tr830_state)
                
                response_msg = f"✅ *Supplier Set:* {supplier_name}\n\n"
                response_msg += f"📝 *Step 2 of 2: Enter Price per Litre*\n\n"
                response_msg += f"Please enter the price per litre (USD):\n"
                response_msg += f"Example: `0.68` or `0.750`\n\n"
                response_msg += f"💡 Type `/cancel` to cancel this process."
                
                return response_msg
                
            elif current_step == 'price':
                # User entered price
                try:
                    price_text = message_text.strip().replace('$', '').replace(',', '')
                    price = Decimal(price_text)
                    
                    if price < 0:
                        return "❌ Price cannot be negative. Please enter a valid price per litre:"
                    
                    if price > 10:  # Reasonable upper limit
                        return "❌ Price seems too high. Please enter price per litre in USD (e.g., 0.68):"
                    
                    # Create the shipment
                    return self._create_tr830_shipment(chat_id, tr830_state, price)
                    
                except (ValueError, InvalidOperation):
                    return "❌ Invalid price format. Please enter a decimal number (e.g., 0.68):"
            
            return "❌ Unknown processing step. Please try uploading the TR830 document again."
            
        except Exception as e:
            logger.error(f"Error handling TR830 input: {e}")
            print(f"🔥 DEBUG: Error in _handle_tr830_input: {e}")
            import traceback
            traceback.print_exc()
            self._clear_tr830_state(chat_id)
            return f"❌ Error processing your input: {str(e)}\n\nPlease try uploading the TR830 document again."

    def _create_tr830_shipment(self, chat_id, tr830_state, price_per_litre):
        """Create shipment from TR830 data"""
        try:
            # Import the required models
            from .models import Destination, Product, Shipment
            from django.contrib.auth.models import User
            
            # Extract data from state
            import_date = datetime.fromisoformat(tr830_state['import_date'])
            vessel = tr830_state['vessel']
            product_type = tr830_state['product_type']
            destination_name = tr830_state['destination']
            quantity = Decimal(tr830_state['quantity'])
            supplier = tr830_state['supplier']
            filename = tr830_state['filename']
            description = tr830_state['description']
            user_id = tr830_state['user_id']
            
            with transaction.atomic():
                # Get user
                user = User.objects.get(id=user_id)
                
                # Get or create product
                product, created = Product.objects.get_or_create(
                    name=product_type,
                    defaults={'description': f'{product_type} fuel'}
                )
                
                if created:
                    print(f"🔥 DEBUG: Created new product: {product_type}")
                
                # Get or create destination
                destination, dest_created = Destination.objects.get_or_create(
                    name=destination_name,
                    defaults={}
                )
                
                if dest_created:
                    print(f"🔥 DEBUG: Created new destination: {destination_name}")
                
                # Create shipment
                shipment = Shipment.objects.create(
                    user=user,
                    vessel_id_tag=vessel,
                    supplier_name=supplier,
                    product=product,
                    quantity_loaded=quantity,
                    quantity_remaining=quantity,
                    destination=destination,
                    cost_per_liter=price_per_litre,
                    total_cost=quantity * price_per_litre,
                    import_date=import_date.date(),
                    notes=f"Auto-created from TR830: {filename}",
                )
                
                print(f"🔥 DEBUG: Created shipment: {shipment}")
                
                # Clear processing state
                self._clear_tr830_state(chat_id)
                
                # Generate success response
                response_msg = f"✅ *TR830 Shipment Created Successfully!*\n\n"
                response_msg += f"📦 *Shipment ID:* {shipment.id}\n"
                response_msg += f"📄 *Document:* {filename}\n"
                response_msg += f"📅 *Import Date:* {import_date.strftime('%d/%m/%Y')}\n\n"
                
                response_msg += f"🚢 *Vessel:* {vessel}\n"
                response_msg += f"⛽ *Product:* {product_type}\n"
                response_msg += f"🌍 *Destination:* {destination_name}\n"
                response_msg += f"📊 *Quantity:* {quantity:,.0f}L\n"
                response_msg += f"🏭 *Supplier:* {supplier}\n"
                response_msg += f"💰 *Price/L:* ${price_per_litre:.3f}\n"
                response_msg += f"💵 *Total Value:* ${quantity * price_per_litre:,.2f}\n\n"
                
                response_msg += f"✨ *Shipment ready for loading operations!*\n"
                response_msg += f"📱 Check the web interface for more details."
                
                return response_msg
                
        except Exception as e:
            logger.error(f"Error creating TR830 shipment: {e}")
            print(f"🔥 DEBUG: Error creating shipment: {e}")
            import traceback
            traceback.print_exc()
            self._clear_tr830_state(chat_id)
            return f"❌ Error creating shipment: {str(e)}\n\nPlease try again or contact support."

    def _handle_cancel_command(self, chat_id):
        """Handle /cancel command"""
        tr830_state = self._get_tr830_state(chat_id)
        if tr830_state:
            self._clear_tr830_state(chat_id)
            return "❌ TR830 processing cancelled. You can upload a new document anytime."
        else:
            return "ℹ️ No active processing to cancel."

    def _handle_start_command(self, chat_id, username, user_context):
        """Handle /start command"""
        if user_context.get('user_id'):
            return f"""👋 Welcome back, *{user_context['username']}*!

🤖 *Sakina Gas Telegram Bot*

I can help you with:
📋 Upload loading authorities (PDF/images)
📄 Process TR830 documents (PDF) - *Interactive mode*
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
📄 Process TR830 shipment documents interactively
📊 Check stock levels
🚛 Manage trips
📈 View business summaries"""

    def _handle_help_command(self):
        """Handle /help command"""
        return """🤖 *Sakina Gas Telegram Bot - Help*

*Available Commands:*
📄 Send PDF → Auto-detect document type
📋 Loading Authority → Auto-create trips
📄 TR830 Document → *Interactive processing*
📊 `stock` → Current fuel inventory
🚛 `trips` → Recent truck loadings
📦 `shipments` → Latest arrivals
📈 `summary` → Business dashboard
❓ `/help` → Show this menu
🏠 `/start` → Main menu
❌ `/cancel` → Cancel current process

*TR830 Interactive Processing:*
1. Upload TR830 PDF
2. Bot parses vessel, product, quantity, destination
3. You provide supplier name
4. You provide price per litre
5. Shipment created automatically

*Quick Actions:*
• Send PDF documents for instant processing
• Ask about stock levels by product
• Check trip status by order number

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

    def _handle_shipments_query(self, user_context):
        """Handle shipments queries"""
        if not user_context.get('user_id'):
            return "❌ Please register your Telegram account to access shipment information."
        
        try:
            # Get recent shipments
            recent_shipments = Shipment.objects.order_by('-loading_date')[:5]
            
            if not recent_shipments:
                return "📦 *Recent Shipments:* No shipments found"
            
            shipments_summary = "📦 *Recent Shipments:*\n\n"
            for shipment in recent_shipments:
                shipments_summary += f"🚢 *{shipment.vessel_id_tag}*\n"
                shipments_summary += f"   ⛽ {shipment.product.name} - {shipment.quantity_loaded:,.0f}L\n"
                shipments_summary += f"   🌍 {shipment.destination}\n"
                shipments_summary += f"   📅 {shipment.loading_date.strftime('%d/%m/%Y')}\n\n"
            
            return shipments_summary
            
        except Exception as e:
            logger.error(f"Error getting shipments info: {e}")
            return "❌ Error retrieving shipment information."

    def _handle_general_query(self, message_text, user_context):
        """Handle general queries using AI"""
        try:
            system_prompt = f"""You are a helpful assistant for Sakina Gas fuel tracking system.
            
            Current user: {user_context.get('username', 'Guest')}
            User permissions: {user_context.get('permissions', [])}
            
            Respond to fuel-related queries and guide users to upload documents or use commands.
            Keep responses concise and helpful.
            Use Telegram markdown formatting.
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

    # Additional methods for loading authority processing would continue here...
    # (keeping existing methods for PDF and image processing)


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
            filename = message['document'].get('file_name', 'unknown.pdf')
            print(f"🔥 DEBUG: Document upload: {filename}")
            response = bot.process_document_upload(chat_id, file_id, filename)
            bot.send_message(chat_id, response)
            
        elif 'photo' in message:
            # Photo upload
            file_id = message['photo'][-1]['file_id']  # Get highest resolution
            filename = f"image_{file_id}.jpg"
            print(f"🔥 DEBUG: Photo upload: {filename}")
            response = bot.process_document_upload(chat_id, file_id, filename)
            bot.send_message(chat_id, response)
        
        return JsonResponse({'status': 'ok'})
        
    except Exception as e:
        print(f"🔥 DEBUG: Error in telegram_webhook: {e}")
        logger.error(f"Error processing Telegram webhook: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)})