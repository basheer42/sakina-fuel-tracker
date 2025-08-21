# shipments/telegram_bot.py
# Complete Telegram Bot implementation - FIXED VERSION
import os
import json
import logging
import tempfile
import requests
from pathlib import Path
from decimal import Decimal, InvalidOperation
from datetime import datetime
from django.core.cache import cache
from django.db import transaction
from django.contrib.auth.models import User
from django.utils import timezone

logger = logging.getLogger(__name__)

class TelegramBot:
    def __init__(self):
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        if not self.bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN not found in environment variables")
        
        # Import here to avoid circular imports
        try:
            from .tr830_parser import TR830Parser, TR830ParseError
            self.tr830_parser = TR830Parser()
            self.TR830ParseError = TR830ParseError
        except ImportError as e:
            logger.error(f"Failed to import TR830Parser: {e}")
            self.tr830_parser = None
            self.TR830ParseError = Exception

    def webhook_handler(self, webhook_data):
        """Main webhook handler for Telegram updates"""
        try:
            print(f"🔥 DEBUG: Telegram webhook called")
            print(f"🔥 DEBUG: Webhook data: {webhook_data}")
            
            if 'message' not in webhook_data:
                return {'status': 'ignored', 'reason': 'No message in webhook'}
            
            message = webhook_data['message']
            chat_id = str(message['chat']['id'])
            username = message['from'].get('username') or message['from'].get('first_name')
            
            print(f"🔥 DEBUG: Processing message from chat_id: {chat_id}, username: {username}")
            
            # Handle document uploads
            if 'document' in message:
                file_id = message['document']['file_id']
                filename = message['document']['file_name']
                print(f"🔥 DEBUG: Document upload: {filename}")
                response = self.process_document_upload(chat_id, file_id, filename)
                
            # Handle text messages
            elif 'text' in message:
                text = message['text']
                print(f"🔥 DEBUG: Text message: {text}")
                response = self.process_message(chat_id, text, username)
                
            else:
                response = "❌ Unsupported message type. Please send text or documents."
            
            # Send response back to user
            if response:
                self.send_message(chat_id, response)
            
            return {'status': 'success'}
            
        except Exception as e:
            logger.error(f"Error in webhook handler: {e}")
            print(f"🔥 DEBUG: Error in webhook_handler: {e}")
            return {'status': 'error', 'message': str(e)}

    def send_message(self, chat_id, text):
        """Send message to Telegram user"""
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            data = {
                'chat_id': chat_id,
                'text': text,
                'parse_mode': 'Markdown'
            }
            response = requests.post(url, json=data)
            if response.status_code != 200:
                logger.error(f"Failed to send message: {response.text}")
                print(f"🔥 DEBUG: Send message failed: {response.text}")
            else:
                print(f"🔥 DEBUG: Message sent successfully to {chat_id}")
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            print(f"🔥 DEBUG: Error in send_message: {e}")

    def get_user_context(self, chat_id):
        """Get user context from Telegram chat ID"""
        try:
            from .models import UserProfile
            
            # Try to find user by telegram_chat_id
            try:
                profile = UserProfile.objects.get(telegram_chat_id=str(chat_id))
                user = profile.user
                return {
                    'user_id': user.id,
                    'username': user.username,
                    'is_staff': user.is_staff,
                    'email': user.email
                }
            except UserProfile.DoesNotExist:
                return {'user_id': None}
                
        except Exception as e:
            logger.error(f"Error getting user context: {e}")
            return {'user_id': None}

    def download_file(self, file_id):
        """Download file from Telegram"""
        try:
            # Get file info
            file_info_url = f"https://api.telegram.org/bot{self.bot_token}/getFile"
            file_info_response = requests.get(file_info_url, params={'file_id': file_id})
            
            print(f"🔥 DEBUG: File info response: {file_info_response.status_code}")
            
            if file_info_response.status_code != 200:
                print(f"🔥 DEBUG: Failed to get file info: {file_info_response.text}")
                return None
            
            file_info = file_info_response.json()
            if not file_info.get('ok'):
                print(f"🔥 DEBUG: File info not ok: {file_info}")
                return None
            
            file_path = file_info['result']['file_path']
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
            
            # Check if it's a TR830 document
            if self._is_tr830_document(filename, file_content):
                return self._initiate_tr830_processing(chat_id, file_content, user_context, filename)
            else:
                return "❌ This doesn't appear to be a TR830 document. Please upload a valid TR830 PDF."
                
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
            else:
                print(f"🔥 DEBUG: Falling back to general query handler")
                return self._handle_general_query(message_text, user_context)
                
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            print(f"🔥 DEBUG: Error in process_message: {e}")
            return "❌ Error processing your message. Please try again or use /help for available commands."

    def _handle_start_command(self, chat_id, username, user_context):
        """Handle /start command"""
        if user_context.get('user_id'):
            return f"""👋 Welcome back, {username or 'there'}!

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

    def _handle_cancel_command(self, chat_id):
        """Handle /cancel command"""
        tr830_state = self._get_tr830_state(chat_id)
        if tr830_state:
            self._clear_tr830_state(chat_id)
            return "✅ TR830 processing cancelled. You can start over by uploading a new TR830 document."
        else:
            return "ℹ️ No active process to cancel. Use /help to see available commands."

    def _handle_general_query(self, message_text, user_context):
        """Handle general queries when not in TR830 flow"""
        if not user_context.get('user_id'):
            return "❌ Please register your Telegram account to access system information."
        
        message_lower = message_text.lower()
        
        if any(word in message_lower for word in ['stock', 'inventory', 'fuel']):
            return self._handle_stock_query(user_context)
        elif any(word in message_lower for word in ['trip', 'loading', 'delivery']):
            return self._handle_trips_query(user_context)
        elif any(word in message_lower for word in ['shipment', 'vessel', 'arrival']):
            return self._handle_shipments_query(user_context)
        else:
            return """ℹ️ I didn't understand your request.

Try one of these:
📊 Type `stock` for inventory levels
🚛 Type `trips` for recent loadings
📦 Type `shipments` for vessel arrivals
📄 Upload a TR830 PDF for processing
❓ Use `/help` for all commands"""

    def _handle_stock_query(self, user_context):
        """Handle stock/inventory queries"""
        try:
            from .models import Shipment, Product
            
            products = Product.objects.all()
            stock_info = "📊 *Current Stock Levels*\n\n"
            
            for product in products:
                total_quantity = sum(
                    shipment.quantity_remaining 
                    for shipment in Shipment.objects.filter(
                        product=product,
                        quantity_remaining__gt=0
                    )
                )
                stock_info += f"⛽ *{product.name}*: {total_quantity:,.0f}L\n"
            
            return stock_info or "📊 No stock information available."
            
        except Exception as e:
            logger.error(f"Error handling stock query: {e}")
            return "❌ Error retrieving stock information."

    def _handle_trips_query(self, user_context):
        """Handle trips queries"""
        try:
            from .models import Trip
            
            recent_trips = Trip.objects.filter(
                user_id=user_context['user_id']
            ).order_by('-loading_date')[:5]
            
            if not recent_trips:
                return "🚛 No recent trips found."
            
            trips_info = "🚛 *Recent Trips*\n\n"
            for trip in recent_trips:
                trips_info += f"📋 *Order:* {trip.kpc_order_number or 'N/A'}\n"
                trips_info += f"⛽ *Product:* {trip.product.name}\n"
                trips_info += f"📊 *Quantity:* {trip.quantity_loaded:,.0f}L\n"
                trips_info += f"📅 *Date:* {trip.loading_date.strftime('%d/%m/%Y')}\n"
                trips_info += f"🚛 *Vehicle:* {trip.vehicle.plate_number}\n"
                trips_info += f"📍 *Status:* {trip.get_status_display()}\n\n"
            
            return trips_info
            
        except Exception as e:
            logger.error(f"Error handling trips query: {e}")
            return "❌ Error retrieving trips information."

    def _handle_shipments_query(self, user_context):
        """Handle shipments queries"""
        try:
            from .models import Shipment
            
            recent_shipments = Shipment.objects.order_by('-import_date')[:5]
            
            if not recent_shipments:
                return "📦 No recent shipments found."
            
            shipments_info = "📦 *Recent Shipments*\n\n"
            for shipment in recent_shipments:
                shipments_info += f"🚢 *Vessel:* {shipment.vessel_id_tag}\n"
                shipments_info += f"⛽ *Product:* {shipment.product.name}\n"
                shipments_info += f"📊 *Remaining:* {shipment.quantity_remaining:,.0f}L\n"
                shipments_info += f"📅 *Arrival:* {shipment.import_date.strftime('%d/%m/%Y')}\n\n"
            
            return shipments_info
            
        except Exception as e:
            logger.error(f"Error handling shipments query: {e}")
            return "❌ Error retrieving shipments information."

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
                    # Try to import pdfplumber, fallback gracefully if not available
                    try:
                        import pdfplumber
                    except ImportError:
                        print("🔥 DEBUG: pdfplumber not available, using filename check only")
                        return False
                    
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
            
            if not self.tr830_parser:
                return "❌ TR830 parser not available. Please contact administrator."
            
            # Parse the TR830 document first
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp_file:
                tmp_file.write(file_content)
                tmp_file.flush()
                temp_path = tmp_file.name
            
            try:
                # Parse the TR830 document using the parser
                import_date, entries = self.tr830_parser.parse_pdf(temp_path)
                
                if not entries:
                    return "❌ No shipment data found in the TR830 document. Please check the file format."
                
                print(f"🔥 DEBUG: Parsed {len(entries)} entries from TR830")
                
                # Store parsed data in cache for interactive processing
                tr830_data = {
                    'step': 'awaiting_supplier',
                    'filename': filename,
                    'import_date': import_date.isoformat(),
                    'entries': [
                        {
                            'vessel': entry.vessel,
                            'product_type': entry.product_type,
                            'quantity': str(entry.quantity),
                            'destination_name': entry.destination_name
                        } for entry in entries
                    ],
                    'user_id': user_context['user_id']
                }
                
                self._save_tr830_state(chat_id, tr830_data)
                
                # Create initial response with parsed data
                response = "✅ *TR830 Document Parsed Successfully!*\n\n"
                response += f"📄 *File:* {filename}\n"
                response += f"📅 *Import Date:* {import_date.strftime('%d/%m/%Y')}\n\n"
                
                response += "*🚢 Parsed Shipment Data:*\n"
                for i, entry in enumerate(entries, 1):
                    response += f"{i}. *Vessel:* {entry.vessel}\n"
                    response += f"   *Product:* {entry.product_type}\n"
                    response += f"   *Quantity:* {entry.quantity:,.0f}L\n"
                    response += f"   *Destination:* {entry.destination_name}\n\n"
                
                response += "🏭 *Step 1: Please provide the supplier name*\n"
                response += "Type the supplier company name (e.g., 'Kuwait Petroleum Corporation'):"
                
                return response
                
            finally:
                os.unlink(temp_path)
                
        except Exception as e:
            print(f"🔥 DEBUG: Error in _initiate_tr830_processing: {e}")
            logger.error(f"Error initiating TR830 processing: {e}")
            import traceback
            traceback.print_exc()
            return f"❌ Error processing TR830 document: {str(e)}"

    def _handle_tr830_input(self, chat_id, message_text, tr830_state):
        """Handle input during TR830 interactive processing"""
        try:
            current_step = tr830_state.get('step')
            print(f"🔥 DEBUG: Handling TR830 input for step: {current_step}")
            
            if current_step == 'awaiting_supplier':
                # User provided supplier name
                tr830_state['supplier'] = message_text.strip()
                tr830_state['step'] = 'awaiting_price'
                
                self._save_tr830_state(chat_id, tr830_state)
                
                return f"""✅ *Supplier Set:* {message_text}

💰 *Step 2: Please provide the price per litre*
Type the price in USD (e.g., '0.65' for $0.65 per litre):"""
            
            elif current_step == 'awaiting_price':
                # User provided price
                try:
                    price_per_litre = Decimal(message_text.strip())
                    if price_per_litre <= 0:
                        return "❌ Price must be greater than zero. Please enter a valid price:"
                    
                    tr830_state['price_per_litre'] = str(price_per_litre)
                    
                    # Now create the shipment
                    return self._create_tr830_shipment(chat_id, tr830_state)
                    
                except (ValueError, InvalidOperation):
                    return "❌ Invalid price format. Please enter a number (e.g., '0.65'):"
            
            else:
                return "❌ Unknown processing step. Please start over with /cancel and upload a new TR830."
                
        except Exception as e:
            print(f"🔥 DEBUG: Error in _handle_tr830_input: {e}")
            logger.error(f"Error handling TR830 input: {e}")
            self._clear_tr830_state(chat_id)
            return "❌ Error processing your input. Please start over."

    def _create_tr830_shipment(self, chat_id, tr830_state):
        """Create shipment from TR830 data"""
        try:
            print(f"🔥 DEBUG: Creating TR830 shipment")
            
            from .models import Shipment, Product, Destination
            
            supplier = tr830_state['supplier']
            price_per_litre = Decimal(tr830_state['price_per_litre'])
            import_date = datetime.fromisoformat(tr830_state['import_date'])
            entries = tr830_state['entries']
            
            created_shipments = []
            
            with transaction.atomic():
                for entry_data in entries:
                    vessel = entry_data['vessel']
                    product_type = entry_data['product_type']
                    quantity = Decimal(entry_data['quantity'])
                    destination_name = entry_data['destination_name']
                    
                    # Get or create product
                    product, _ = Product.objects.get_or_create(name=product_type)
                    
                    # Get or create destination
                    destination, _ = Destination.objects.get_or_create(name=destination_name)
                    
                    # Calculate total cost
                    total_cost = quantity * price_per_litre
                    
                    # Create shipment
                    shipment = Shipment.objects.create(
                        vessel_id_tag=vessel,
                        supplier=supplier,
                        product=product,
                        destination=destination,
                        quantity_loaded=quantity,
                        quantity_remaining=quantity,
                        price_per_litre=price_per_litre,
                        total_cost=total_cost,
                        import_date=import_date,
                        notes=f"Created via Telegram Bot from {tr830_state['filename']}"
                    )
                    
                    created_shipments.append(shipment)
                    
                    print(f"🔥 DEBUG: Created shipment {shipment.id}: {vessel} - {product_type}")
            
            # Clear the TR830 state
            self._clear_tr830_state(chat_id)
            
            # Create success response
            if len(created_shipments) == 1:
                shipment = created_shipments[0]
                response_msg = f"🎉 *Shipment Created Successfully!*\n\n"
                response_msg += f"🆔 *Shipment ID:* {shipment.id}\n"
                response_msg += f"🚢 *Vessel:* {vessel}\n"
                response_msg += f"🏭 *Supplier:* {supplier}\n"
                response_msg += f"⛽ *Product:* {product_type}\n"
                response_msg += f"🌍 *Destination:* {destination_name}\n"
                response_msg += f"📊 *Quantity:* {quantity:,.0f}L\n"
                response_msg += f"💰 *Price:* ${price_per_litre}/L\n"
                response_msg += f"💸 *Total Cost:* ${total_cost:,.2f}\n"
                response_msg += f"📅 *Import Date:* {import_date.strftime('%d/%m/%Y')}\n\n"
                response_msg += f"🎉 The shipment has been added to your inventory!"
            else:
                response_msg = f"🎉 *{len(created_shipments)} Shipments Created Successfully!*\n\n"
                total_value = sum(s.total_cost for s in created_shipments)
                total_quantity = sum(s.quantity_loaded for s in created_shipments)
                response_msg += f"🏭 *Supplier:* {supplier}\n"
                response_msg += f"📊 *Total Quantity:* {total_quantity:,.0f}L\n"
                response_msg += f"💸 *Total Value:* ${total_value:,.2f}\n"
                response_msg += f"📅 *Import Date:* {import_date.strftime('%d/%m/%Y')}\n\n"
                response_msg += f"🎉 All shipments have been added to your inventory!"
                
            return response_msg
                
        except Exception as e:
            logger.error(f"Error creating TR830 shipment: {e}")
            print(f"🔥 DEBUG: Error creating shipment: {e}")
            import traceback
            traceback.print_exc()
            self._clear_tr830_state(chat_id)
            return f"❌ Error creating shipment: {str(e)}\n\nPlease try the process again or contact support."

    def _get_tr830_state(self, chat_id):
        """Get TR830 processing state for user"""
        try:
            cache_key = f"tr830_state_{chat_id}"
            state = cache.get(cache_key)
            print(f"🔥 DEBUG: Retrieved TR830 state for {chat_id}: {state is not None}")
            return state
        except Exception as e:
            print(f"🔥 DEBUG: Error getting TR830 state: {e}")
            logger.error(f"Error getting TR830 state: {e}")
            return None

    def _save_tr830_state(self, chat_id, state_data):
        """Save TR830 processing state for user"""
        try:
            cache_key = f"tr830_state_{chat_id}"
            cache.set(cache_key, state_data, timeout=3600)  # 1 hour timeout
            print(f"🔥 DEBUG: Saved TR830 state for {chat_id}: step={state_data.get('step')}")
        except Exception as e:
            print(f"🔥 DEBUG: Error saving TR830 state: {e}")
            logger.error(f"Error saving TR830 state: {e}")

    def _clear_tr830_state(self, chat_id):
        """Clear TR830 processing state for user"""
        try:
            cache_key = f"tr830_state_{chat_id}"
            cache.delete(cache_key)
            print(f"🔥 DEBUG: Cleared TR830 state for {chat_id}")
        except Exception as e:
            print(f"🔥 DEBUG: Error clearing TR830 state: {e}")
            logger.error(f"Error clearing TR830 state: {e}")