# shipments/telegram_bot.py
# Complete fix for TR830 Telegram Bot processing
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
        from .tr830_parser import TR830Parser, TR830ParseError
        self.tr830_parser = TR830Parser()
        self.TR830ParseError = TR830ParseError

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
        except Exception as e:
            logger.error(f"Error sending message: {e}")

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
            
            print(f"🔥 DEBUG: File info response: {file_info_response.json()}")
            
            if file_info_response.status_code != 200:
                return None
            
            file_info = file_info_response.json()
            if not file_info.get('ok'):
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

Here's what I can help with:
• Upload TR830 documents for processing
• Check stock levels
• View trip information
• Get business summaries

Use /help for a complete list of commands."""

    def _handle_stock_query(self, user_context):
        """Handle stock queries"""
        try:
            from .models import Shipment
            
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
        """Handle trips queries"""
        try:
            from .models import Trip
            
            recent_trips = Trip.objects.filter(user_id=user_context['user_id']).order_by('-loading_date')[:5]
            
            if not recent_trips:
                return "🚛 *No recent trips found*"
            
            trips_summary = "🚛 *Recent Trips:*\n\n"
            for trip in recent_trips:
                trips_summary += f"📋 *{trip.kpc_order_number or 'N/A'}*\n"
                trips_summary += f"   📅 Date: {trip.loading_date}\n"
                trips_summary += f"   ⛽ Product: {trip.product.name}\n"
                trips_summary += f"   📍 Status: {trip.get_status_display()}\n\n"
            
            return trips_summary
            
        except Exception as e:
            logger.error(f"Error getting trips info: {e}")
            return "❌ Error retrieving trips information."

    def _handle_shipments_query(self, user_context):
        """Handle shipments queries"""
        try:
            from .models import Shipment
            
            recent_shipments = Shipment.objects.filter(user_id=user_context['user_id']).order_by('-import_date')[:5]
            
            if not recent_shipments:
                return "📦 *No recent shipments found*"
            
            shipments_summary = "📦 *Recent Shipments:*\n\n"
            for shipment in recent_shipments:
                shipments_summary += f"🚢 *{shipment.vessel_id_tag}*\n"
                shipments_summary += f"   📅 Import: {shipment.import_date}\n"
                shipments_summary += f"   ⛽ Product: {shipment.product.name}\n"
                shipments_summary += f"   📊 Quantity: {shipment.quantity_litres:,.0f}L\n\n"
            
            return shipments_summary
            
        except Exception as e:
            logger.error(f"Error getting shipments info: {e}")
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
                # Parse the TR830 document using the parser
                import_date, entries = self.tr830_parser.parse_pdf(temp_path)
                
                if not entries:
                    return "❌ No shipment data found in the TR830 document. Please check the file format."
                
                if len(entries) > 1:
                    return f"⚠️ Warning: Found {len(entries)} entries in TR830. Expected only 1. Please check the document."
                
                entry = entries[0]  # Should be only one entry now
                
                # Store TR830 data for interactive processing
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
                
                # Store the TR830 data using reliable method
                self._set_tr830_state(chat_id, tr830_data)
                
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
                    
        except self.TR830ParseError as e:
            return f"❌ Error parsing TR830 document: {str(e)}\n\nPlease check if this is a valid TR830 document."
        except Exception as e:
            print(f"🔥 DEBUG: Error in _initiate_tr830_processing: {e}")
            logger.error(f"Error initiating TR830 processing: {e}")
            return "❌ Error processing TR830 document. Please try again or contact support."

    def _get_tr830_state(self, chat_id):
        """Get TR830 processing state using hybrid approach (Database + File backup)"""
        print(f"🔥 DEBUG: Getting TR830 state for chat_id: {chat_id}")
        
        try:
            # Try DATABASE first
            from .models import TR830ProcessingState
            
            try:
                state_obj = TR830ProcessingState.objects.get(chat_id=chat_id)
                state_data = {
                    'filename': state_obj.filename,
                    'import_date': state_obj.import_date.isoformat(),
                    'vessel': state_obj.vessel,
                    'product_type': state_obj.product_type,
                    'destination': state_obj.destination,
                    'quantity': str(state_obj.quantity),
                    'description': state_obj.description,
                    'step': state_obj.step,
                    'user_id': state_obj.user_id,
                    'supplier': state_obj.supplier or ''
                }
                print(f"🔥 DEBUG: Found state in DATABASE: step='{state_data.get('step')}'")
                return state_data
                
            except TR830ProcessingState.DoesNotExist:
                print(f"🔥 DEBUG: No state found in database")
                
                # Fallback to CACHE
                cache_key = f"tr830_processing_{chat_id}"
                cached_state = cache.get(cache_key)
                if cached_state:
                    print(f"🔥 DEBUG: Found state in CACHE: step='{cached_state.get('step')}'")
                    return cached_state
                
                print(f"🔥 DEBUG: No state found in cache either")
                return None
                
        except Exception as e:
            print(f"🔥 DEBUG: Error accessing state: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _set_tr830_state(self, chat_id, state):
        """Set TR830 processing state using hybrid approach (Database + Cache backup)"""
        print(f"🔥 DEBUG: Setting TR830 state for chat_id: {chat_id}, step: {state.get('step')}")
        
        try:
            from .models import TR830ProcessingState
            from django.contrib.auth.models import User
            
            # First, delete any existing state
            TR830ProcessingState.objects.filter(chat_id=chat_id).delete()
            
            # Create new state in DATABASE
            user = User.objects.get(id=state['user_id'])
            
            # Handle datetime parsing carefully
            import_date_str = state['import_date']
            if isinstance(import_date_str, str):
                import_date = datetime.fromisoformat(import_date_str.replace('Z', '+00:00'))
            else:
                import_date = import_date_str
            
            # Make sure import_date is timezone-aware
            if import_date.tzinfo is None:
                import_date = timezone.make_aware(import_date)
            
            TR830ProcessingState.objects.create(
                chat_id=chat_id,
                filename=state['filename'],
                import_date=import_date,
                vessel=state['vessel'],
                product_type=state['product_type'],
                destination=state['destination'],
                quantity=Decimal(state['quantity']),
                description=state['description'],
                step=state['step'],
                user=user,
                supplier=state.get('supplier', '')
            )
            
            print(f"🔥 DEBUG: State created in DATABASE")
            
            # Also backup to CACHE
            cache_key = f"tr830_processing_{chat_id}"
            cache.set(cache_key, state, 1800)  # 30 minutes
            print(f"🔥 DEBUG: State also stored in cache")
            
        except Exception as e:
            print(f"🔥 DEBUG: Error setting state: {e}")
            import traceback
            traceback.print_exc()

    def _clear_tr830_state(self, chat_id):
        """Clear TR830 processing state from all storage methods"""
        print(f"🔥 DEBUG: Clearing TR830 state for chat_id: {chat_id}")
        
        try:
            # Clear from DATABASE
            from .models import TR830ProcessingState
            deleted_count, _ = TR830ProcessingState.objects.filter(chat_id=chat_id).delete()
            print(f"🔥 DEBUG: Deleted {deleted_count} database records")
            
            # Clear from CACHE
            cache_key = f"tr830_processing_{chat_id}"
            cache.delete(cache_key)
            print(f"🔥 DEBUG: Cache cleared")
            
        except Exception as e:
            print(f"🔥 DEBUG: Error clearing state: {e}")

    def _handle_tr830_input(self, chat_id, message_text, tr830_state):
        """Handle user input during TR830 processing flow"""
        try:
            current_step = tr830_state.get('step')
            
            if current_step == 'supplier':
                # User entered supplier name
                supplier_name = message_text.strip()
                
                if len(supplier_name) < 2:
                    return "❌ Supplier name too short. Please enter a valid supplier name (at least 2 characters)."
                
                # Update state with supplier and move to price step
                tr830_state['supplier'] = supplier_name
                tr830_state['step'] = 'price'
                self._set_tr830_state(chat_id, tr830_state)
                
                return f"✅ *Supplier Set: {supplier_name}*\n\n📝 *Step 2 of 2: Enter Price per Litre*\n\nPlease enter the price per litre (USD):\nExample: `0.68` or `0.750`\n\n💡 Type `/cancel` to cancel this process."
                
            elif current_step == 'price':
                # User entered price per litre
                try:
                    # Clean the input and convert to Decimal
                    price_text = message_text.strip().replace('$', '').replace(',', '')
                    price_per_litre = Decimal(price_text)
                    
                    if price_per_litre <= 0:
                        return "❌ Price must be greater than 0. Please enter a valid price (e.g., 0.68):"
                    
                    if price_per_litre > 10:
                        return "❌ Price seems too high (over $10/L). Please double-check and enter the correct price:"
                    
                    # Create the shipment
                    return self._create_tr830_shipment(chat_id, tr830_state, price_per_litre)
                    
                except (InvalidOperation, ValueError):
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
            import_date_str = tr830_state['import_date']
            if isinstance(import_date_str, str):
                import_date = datetime.fromisoformat(import_date_str.replace('Z', '+00:00'))
            else:
                import_date = import_date_str
                
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
                
                # Create shipment - FIXED: Use quantity_litres instead of quantity_loaded
                shipment = Shipment.objects.create(
                    user=user,
                    vessel_id_tag=vessel,
                    supplier_name=supplier,
                    product=product,
                    quantity_litres=quantity,  # FIXED: Use correct field name
                    destination=destination,
                    price_per_litre=price_per_litre,  # FIXED: Use correct field name
                    import_date=import_date.date(),
                    notes=f"Auto-created from TR830: {filename}",
                )
                
                print(f"🔥 DEBUG: Created shipment: {shipment}")
                
                # Clear processing state
                self._clear_tr830_state(chat_id)
                
                # Generate success response
                total_cost = quantity * price_per_litre
                response_msg = f"✅ *TR830 Shipment Created Successfully!*\n\n"
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
                
                return response_msg
                
        except Exception as e:
            logger.error(f"Error creating TR830 shipment: {e}")
            print(f"🔥 DEBUG: Error creating shipment: {e}")
            import traceback
            traceback.print_exc()
            self._clear_tr830_state(chat_id)
            return f"❌ Error creating shipment: {str(e)}\n\nPlease try the process again or contact support."


# Webhook view function for Django URLs
def telegram_webhook(request):
    """Django view to handle Telegram webhook"""
    if request.method == 'POST':
        try:
            import json
            webhook_data = json.loads(request.body.decode('utf-8'))
            
            bot = TelegramBot()
            result = bot.webhook_handler(webhook_data)
            
            from django.http import JsonResponse
            return JsonResponse(result)
            
        except Exception as e:
            logger.error(f"Error in telegram webhook view: {e}")
            from django.http import JsonResponse
            return JsonResponse({'status': 'error', 'message': str(e)})
    
    from django.http import HttpResponseNotAllowed
    return HttpResponseNotAllowed(['POST'])