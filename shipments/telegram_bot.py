# shipments/telegram_bot.py
# Complete Telegram Bot implementation with Customer Trip Lookup Feature AND BOL Processing
import os
import json
import logging
import tempfile
import requests
import re
import difflib
from pathlib import Path
from decimal import Decimal, InvalidOperation
from datetime import datetime
from django.core.cache import cache
from django.db import transaction
from django.contrib.auth.models import User
from django.utils import timezone
from django.conf import settings

logger = logging.getLogger(__name__)

class TelegramBot:
    def __init__(self):
        # FIXED: Token loading to work with Django settings
        self.bot_token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '') or os.getenv('TELEGRAM_BOT_TOKEN')
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
                response = "⚠ Unsupported message type. Please send text or documents."
            
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
                'parse_mode': 'HTML'  # FIXED: Use HTML instead of Markdown to avoid parsing errors
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
            
            # Download the actual file
            download_url = f"https://api.telegram.org/file/bot{self.bot_token}/{file_path}"
            download_response = requests.get(download_url)
            
            print(f"🔥 DEBUG: File download response: {download_response.status_code}")
            
            if download_response.status_code == 200:
                return download_response.content
            else:
                print(f"🔥 DEBUG: Failed to download file: {download_response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Error downloading file: {e}")
            print(f"🔥 DEBUG: Error in download_file: {e}")
            return None

    def process_document_upload(self, chat_id, file_id, filename):
        """Process uploaded documents"""
        try:
            user_context = self.get_user_context(chat_id)
            
            if not user_context.get('user_id'):
                return "⚠ Please register your Telegram account to access document processing."
            
            # Download file
            file_content = self.download_file(file_id)
            if not file_content:
                return "⚠ Could not download the file. Please try again."
            
            print(f"🔥 DEBUG: Downloaded file size: {len(file_content)} bytes")
            
            # Check file size (limit to 10MB)
            if len(file_content) > 10 * 1024 * 1024:
                return "⚠ File too large. Please upload a file smaller than 10MB."
            
            # Detect document type based on filename or content
            filename_lower = filename.lower()
            
            if filename_lower.endswith('.pdf') and 'tr830' in filename_lower:
                return self._initiate_tr830_processing(chat_id, file_content, filename, user_context)
            elif filename_lower.endswith('.pdf') and any(keyword in filename_lower for keyword in ['bol', 'bill', 'loading', 'shipment']):
                # NEW: BOL document processing
                return self._initiate_bol_processing(chat_id, file_content, filename, user_context)
            elif filename_lower.endswith('.pdf'):
                return self._process_loading_authority_pdf(file_content, filename, user_context)
            else:
                return "⚠ Unsupported file type. Please upload a PDF document."
                
        except Exception as e:
            print(f"🔥 DEBUG: Error in process_document_upload: {e}")
            logger.error(f"Error processing document upload: {e}")
            return "⚠ Error processing your document. Please try again."

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
            
            # NEW: Check if user is in BOL processing flow
            bol_state = self._get_bol_state(chat_id)
            print(f"🔥 DEBUG: BOL state found: {bol_state is not None}")
            
            if bol_state:
                print(f"🔥 DEBUG: Handling BOL input for step: {bol_state.get('step')}")
                return self._handle_bol_input(chat_id, message_text, bol_state)
            
            # Check if user is in customer trips flow
            customer_trips_state = self._get_customer_trips_state(chat_id)
            if customer_trips_state:
                print(f"🔥 DEBUG: Handling customer trips input")
                return self._handle_customer_trips_input(chat_id, message_text, customer_trips_state)
            
            # Handle commands
            if message_lower.startswith('/start'):
                return self._handle_start_command(chat_id, username, user_context)
            elif message_lower.startswith('/help'):
                return self._handle_help_command()
            elif message_lower.startswith('/cancel'):
                return self._handle_cancel_command(chat_id)
            else:
                print(f"🔥 DEBUG: Falling back to general query handler")
                return self._handle_general_query(message_text, user_context, chat_id)
                
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            print(f"🔥 DEBUG: Error in process_message: {e}")
            return "⚠ Error processing your message. Please try again or use /help for available commands."

    def _handle_start_command(self, chat_id, username, user_context):
        """Handle /start command"""
        if user_context.get('user_id'):
            return f"""👋 Welcome back, {username or 'there'}!

🤖 <b>Sakina Gas Telegram Bot</b>

I can help you with:
📋 Upload loading authorities (PDF/images)
📄 Process TR830 documents (PDF) - <i>Interactive mode</i>
📜 Process BOL documents (PDF) - <i>NEW: Interactive trip updates</i>
📊 Check stock levels
🚛 View trip status
🧾 Customer trip lookup - <i>Type customer name</i>
📈 Business summaries

Just send a document or use /help for commands."""
        else:
            return f"""👋 Hello {username or 'there'}!

🤖 <b>Sakina Gas Telegram Bot</b>

Your Telegram ID: <code>{chat_id}</code>

To get started, please contact your administrator to link this Telegram account to your system user account.

Once linked, you can:
📋 Upload loading authorities automatically
📄 Process TR830 shipment documents interactively
📜 Process BOL documents for trip updates
📊 Check stock levels
🚛 Manage trips
🧾 Customer trip lookup
📈 View business summaries"""

    def _handle_help_command(self):
        """Handle /help command"""
        return """🤖 <b>Sakina Gas Telegram Bot - Help</b>

<b>Available Commands:</b>
📄 Send PDF → Auto-detect document type
📋 Loading Authority → Auto-create trips
📄 TR830 Document → <i>Interactive processing</i>
📜 BOL Document → <i>Update trip compartments</i>
📊 <code>stock</code> → Current fuel inventory
🚛 <code>trips</code> → Recent truck loadings
📦 <code>shipments</code> → Latest arrivals
🧾 <b>Customer trip lookup</b> → Type customer name to see last 10 trips
📈 <code>summary</code> → Business dashboard
❓ <code>/help</code> → Show this menu
🏠 <code>/start</code> → Main menu
❌ <code>/cancel</code> → Cancel current process

<b>TR830 Interactive Processing:</b>
1. Upload TR830 PDF
2. Bot parses vessel, product, quantity, destination
3. You provide supplier name
4. You provide price per litre
5. Shipment created automatically

<b>BOL Interactive Processing:</b>
1. Upload BOL PDF
2. Bot extracts loading order number, vehicle, compartments
3. Finds matching trip automatically
4. Updates compartment quantities with L20 actuals
5. Sets trip status to LOADED and processes stock depletion

<b>Customer Trip Lookup:</b>
1. Type any customer name to see their last 10 trips
2. When results are shown, type truck number to exclude it
3. Fresh filtered list will be displayed

<b>Quick Actions:</b>
• Send PDF documents for instant processing
• Ask about stock levels by product
• Check trip status by order number

Just send a document or type a command!"""

    def _handle_cancel_command(self, chat_id):
        """Handle /cancel command"""
        tr830_state = self._get_tr830_state(chat_id)
        bol_state = self._get_bol_state(chat_id)
        customer_trips_state = self._get_customer_trips_state(chat_id)
        
        if tr830_state:
            self._clear_tr830_state(chat_id)
            return "✅ TR830 processing cancelled. You can start over by uploading a new TR830 document."
        elif bol_state:
            self._clear_bol_state(chat_id)
            return "✅ BOL processing cancelled. You can start over by uploading a new BOL document."
        elif customer_trips_state:
            self._clear_customer_trips_state(chat_id)
            return "✅ Customer trips lookup cancelled."
        else:
            return "ℹ️ No active process to cancel. Use /help to see available commands."

    def _handle_general_query(self, message_text, user_context, chat_id):
        """Handle general queries when not in TR830, BOL, or customer trips flow"""
        if not user_context.get('user_id'):
            return "⚠ Please register your Telegram account to access system information."
        
        message_lower = message_text.lower()
        
        if any(word in message_lower for word in ['stock', 'inventory', 'fuel']):
            return self._handle_stock_query(user_context)
        elif any(word in message_lower for word in ['trip', 'loading', 'delivery']):
            return self._handle_trips_query(user_context)
        elif any(word in message_lower for word in ['shipment', 'vessel', 'arrival']):
            return self._handle_shipments_query(user_context)
        else:
            # Try to find customer by name for trip lookup
            return self._handle_potential_customer_lookup(message_text, user_context, chat_id)

    def _handle_potential_customer_lookup(self, message_text, user_context, chat_id):
        """Handle potential customer name input for trip lookup"""
        try:
            from .models import Customer
            
            # Clean the input - could be a customer name
            customer_name = message_text.strip()
            
            # Try to find customers that match the input (case-insensitive partial match)
            matching_customers = Customer.objects.filter(
                name__icontains=customer_name
            ).order_by('name')[:5]  # Limit to 5 matches
            
            if matching_customers:
                if len(matching_customers) == 1:
                    # Exact match or single result - show trips immediately
                    customer = matching_customers[0]
                    return self._show_customer_trips(customer, user_context, set(), chat_id)
                else:
                    # Multiple matches - let user choose
                    response = f"🔍 Found multiple customers matching '<b>{customer_name}</b>':\n\n"
                    for i, customer in enumerate(matching_customers, 1):
                        response += f"{i}. {customer.name}\n"
                    response += f"\nPlease type the exact customer name or number (1-{len(matching_customers)}) to see their trips."
                    
                    # Save state for customer selection
                    state = {
                        'step': 'customer_selection',
                        'query': customer_name,
                        'matches': [{'id': c.id, 'name': c.name} for c in matching_customers],
                        'excluded_trucks': []
                    }
                    self._save_customer_trips_state(chat_id, state)
                    return response
            else:
                # No customer match - show help message
                return """ℹ️ I didn't understand your request.

Try one of these:
📊 Type <code>stock</code> for inventory levels
🚛 Type <code>trips</code> for recent loadings
📦 Type <code>shipments</code> for vessel arrivals
🧾 Type a <b>customer name</b> to see their last 10 trips
📄 Upload a TR830 PDF for processing
📜 Upload a BOL PDF for trip updates
❓ Use <code>/help</code> for all commands"""
                
        except Exception as e:
            logger.error(f"Error in potential customer lookup: {e}")
            return "⚠ Error searching for customer. Please try again."

    def _handle_customer_trips_input(self, chat_id, message_text, customer_trips_state):
        """Handle input during customer trips lookup flow"""
        try:
            from .models import Customer, Vehicle
            
            current_step = customer_trips_state.get('step', '')
            message_text = message_text.strip()
            
            if current_step == 'customer_selection':
                # User is selecting from multiple customer matches
                matches = customer_trips_state.get('matches', [])
                
                # Try to match by number
                if message_text.isdigit():
                    selection = int(message_text) - 1
                    if 0 <= selection < len(matches):
                        selected_customer = Customer.objects.get(id=matches[selection]['id'])
                        self._clear_customer_trips_state(chat_id)
                        return self._show_customer_trips(selected_customer, self.get_user_context(chat_id), set(), chat_id)
                    else:
                        return f"⚠ Please enter a number between 1 and {len(matches)}."
                
                # Try to match by exact name
                for match in matches:
                    if match['name'].lower() == message_text.lower():
                        selected_customer = Customer.objects.get(id=match['id'])
                        self._clear_customer_trips_state(chat_id)
                        return self._show_customer_trips(selected_customer, self.get_user_context(chat_id), set(), chat_id)
                
                return "⚠ Please select a customer by typing the number or exact name from the list above."
            
            elif current_step == 'trips_displayed':
                # User might be trying to exclude a truck by typing truck number
                customer_id = customer_trips_state.get('customer_id')
                excluded_trucks = set(customer_trips_state.get('excluded_trucks', []))
                
                # Check if the input looks like a truck plate number or ID
                if self._looks_like_truck_identifier(message_text):
                    # Try to find the truck in the current trip results
                    customer = Customer.objects.get(id=customer_id)
                    truck_found = self._find_truck_in_customer_trips(customer, message_text, excluded_trucks)
                    
                    if truck_found:
                        excluded_trucks.add(message_text.upper())
                        # Update state with new exclusion
                        customer_trips_state['excluded_trucks'] = list(excluded_trucks)
                        self._save_customer_trips_state(chat_id, customer_trips_state)
                        
                        # Show updated results
                        return self._show_customer_trips(customer, self.get_user_context(chat_id), excluded_trucks, chat_id)
                    else:
                        return f"⚠ Truck '{message_text}' not found in current trip results. Please check the truck identifier and try again."
                else:
                    # Not a truck identifier - try new customer search
                    self._clear_customer_trips_state(chat_id)
                    return self._handle_potential_customer_lookup(message_text, self.get_user_context(chat_id), chat_id)
            
            else:
                return "⚠ Unknown state. Please use /cancel and try again."
                
        except Exception as e:
            logger.error(f"Error handling customer trips input: {e}")
            print(f"🔥 DEBUG: Error in customer trips input: {e}")
            self._clear_customer_trips_state(chat_id)
            return "⚠ Error processing your request. Please try again."

    def _looks_like_truck_identifier(self, text):
        """Check if text looks like a truck plate number or identifier"""
        # Common patterns for truck plates: ABC123, KAA123A, etc.
        import re
        text = text.strip().upper()
        
        # Pattern for typical plate numbers (3+ chars, mix of letters/numbers)
        # Must have at least one letter and one number
        if len(text) < 3 or len(text) > 15:
            return False
            
        has_letter = any(c.isalpha() for c in text)
        has_number = any(c.isdigit() for c in text)
        
        # Must contain both letters and numbers, and only alphanumeric characters
        return has_letter and has_number and text.isalnum()

    def _find_truck_in_customer_trips(self, customer, truck_identifier, excluded_trucks):
        """Check if truck identifier exists in current customer trips"""
        try:
            from .models import Trip
            
            truck_identifier = truck_identifier.upper()
            
            # Get customer's recent trips (same query as _show_customer_trips)
            trips = Trip.objects.filter(
                customer=customer
            ).select_related(
                'vehicle', 'product', 'destination'
            ).order_by('-loading_date')[:10]
            
            # Filter out excluded trucks and check for match
            for trip in trips:
                truck_plate = trip.vehicle.plate_number.upper()
                if truck_plate not in excluded_trucks:
                    # Check for exact match or partial match
                    if (truck_identifier == truck_plate or 
                        truck_identifier in truck_plate or
                        truck_plate in truck_identifier):
                        return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error checking truck in customer trips: {e}")
            return False

    def _show_customer_trips(self, customer, user_context, excluded_trucks, chat_id):
        """Display last 10 trips for a customer with truck exclusion"""
        try:
            from .models import Trip
            
            # Get customer's recent trips
            trips = Trip.objects.filter(
                customer=customer
            ).select_related(
                'vehicle', 'product', 'destination', 'user'
            ).order_by('-loading_date')[:10]
            
            if not trips:
                return f"📋 No trips found for customer: <b>{customer.name}</b>"
            
            # Filter out excluded trucks
            filtered_trips = []
            excluded_count = 0
            
            for trip in trips:
                truck_plate = trip.vehicle.plate_number.upper()
                # Check if this truck should be excluded (exact match or partial match)
                should_exclude = False
                for excluded_truck in excluded_trucks:
                    excluded_truck_upper = excluded_truck.upper()
                    if (excluded_truck_upper == truck_plate or 
                        excluded_truck_upper in truck_plate or
                        truck_plate in excluded_truck_upper):
                        should_exclude = True
                        break
                
                if should_exclude:
                    excluded_count += 1
                else:
                    filtered_trips.append(trip)
            
            if not filtered_trips:
                return f"""📋 <b>Customer:</b> {customer.name}

🚫 All trips are excluded by truck filters.
Use /cancel to reset or type a customer name to search again."""
            
            # Build response
            response = f"📋 <b>Customer:</b> {customer.name}\n"
            response += f"🚛 <b>Last {len(filtered_trips)} trips</b>"
            
            if excluded_count > 0:
                response += f" (💫 {excluded_count} trips excluded)"
            
            if excluded_trucks:
                excluded_list = ", ".join(sorted(excluded_trucks))
                response += f"\n🚫 <b>Excluded trucks:</b> {excluded_list}"
                
            response += "\n\n"
            
            for i, trip in enumerate(filtered_trips, 1):
                status_emoji = self._get_status_emoji(trip.status)
                
                response += f"<b>{i}.</b> 📋 {trip.kpc_order_number or f'Trip #{trip.id}'}\n"
                response += f"   📅 {trip.loading_date.strftime('%d/%m/%Y')}\n"
                response += f"   🚛 {trip.vehicle.plate_number}"
                if trip.vehicle.trailer_number:
                    response += f" + {trip.vehicle.trailer_number}"
                response += f"\n   ⛽ {trip.product.name} → {trip.destination.name}\n"
                response += f"   📊 {getattr(trip, 'total_loaded', trip.total_requested_from_compartments):,.0f}L\n"
                response += f"   {status_emoji} {trip.get_status_display()}\n"
                response += f"   👤 {trip.user.username}\n\n"
            
            response += "💡 <b>Tip:</b> Type a truck number to exclude it from results."
            
            # Save state for potential truck exclusions
            state = {
                'step': 'trips_displayed',
                'customer_id': customer.id,
                'excluded_trucks': list(excluded_trucks)
            }
            self._save_customer_trips_state(chat_id, state)
            
            return response
            
        except Exception as e:
            logger.error(f"Error showing customer trips: {e}")
            return "⚠ Error retrieving customer trips. Please try again."

    def _get_status_emoji(self, status):
        """Get emoji for trip status"""
        status_emojis = {
            'PENDING': '⏳',
            'KPC_APPROVED': '✅',
            'KPC_REJECTED': '❌',
            'LOADING': '🔄',
            'LOADED': '📦',
            'GATEPASSED': '🚪',
            'TRANSIT': '🚛',
            'DELIVERED': '✅',
            'CANCELLED': '❌'
        }
        return status_emojis.get(status, '📋')

    def _handle_stock_query(self, user_context):
        """Handle stock/inventory queries"""
        try:
            from .models import Shipment, Product
            
            products = Product.objects.all()
            stock_info = "📊 <b>Current Stock Levels</b>\n\n"
            
            for product in products:
                total_quantity = sum(
                    shipment.quantity_remaining 
                    for shipment in Shipment.objects.filter(
                        product=product,
                        quantity_remaining__gt=0
                    )
                )
                stock_info += f"⛽ <b>{product.name}</b>: {total_quantity:,.0f}L\n"
            
            return stock_info or "📊 No stock information available."
            
        except Exception as e:
            logger.error(f"Error handling stock query: {e}")
            return "⚠ Error retrieving stock information."

    def _handle_trips_query(self, user_context):
        """Handle trips queries"""
        try:
            from .models import Trip
            
            recent_trips = Trip.objects.filter(
                user_id=user_context['user_id']
            ).order_by('-loading_date')[:5]
            
            if not recent_trips:
                return "🚛 No recent trips found."
            
            trips_info = "🚛 <b>Recent Trips</b>\n\n"
            for trip in recent_trips:
                trips_info += f"📋 <b>Order:</b> {trip.kpc_order_number or 'N/A'}\n"
                trips_info += f"⛽ <b>Product:</b> {trip.product.name}\n"
                trips_info += f"📊 <b>Quantity:</b> {getattr(trip, 'total_loaded', 0):,.0f}L\n"
                trips_info += f"📅 <b>Date:</b> {trip.loading_date.strftime('%d/%m/%Y')}\n"
                trips_info += f"🚛 <b>Vehicle:</b> {trip.vehicle.plate_number}\n"
                trips_info += f"📍 <b>Status:</b> {trip.status.title()}\n\n"
            
            return trips_info
            
        except Exception as e:
            logger.error(f"Error handling trips query: {e}")
            return "⚠ Error retrieving trips information."

    def _handle_shipments_query(self, user_context):
        """Handle shipments queries"""
        try:
            from .models import Shipment
            
            recent_shipments = Shipment.objects.filter(
                user_id=user_context['user_id']
            ).order_by('-import_date')[:5]
            
            if not recent_shipments:
                return "📦 No recent shipments found."
            
            shipments_info = "📦 <b>Recent Shipments</b>\n\n"
            for shipment in recent_shipments:
                shipments_info += f"🚢 <b>Vessel:</b> {shipment.vessel_id_tag}\n"
                shipments_info += f"⛽ <b>Product:</b> {shipment.product.name}\n"
                shipments_info += f"📊 <b>Quantity:</b> {shipment.quantity_litres:,.0f}L\n"
                shipments_info += f"📅 <b>Date:</b> {shipment.import_date.strftime('%d/%m/%Y')}\n"
                shipments_info += f"🏭 <b>Supplier:</b> {shipment.supplier_name}\n"
                shipments_info += f"📍 <b>Destination:</b> {shipment.destination.name if shipment.destination else 'N/A'}\n\n"
            
            return shipments_info
            
        except Exception as e:
            logger.error(f"Error handling shipments query: {e}")
            return "⚠ Error retrieving shipments information."

    # ========================
    # CUSTOMER TRIPS STATE MANAGEMENT
    # ========================
    
    def _get_customer_trips_state(self, chat_id):
        """Get customer trips processing state for user"""
        try:
            cache_key = f"customer_trips_state_{chat_id}"
            print(f"🔥 DEBUG: Looking for cache key: {cache_key}")
            state = cache.get(cache_key)
            print(f"🔥 DEBUG: Retrieved customer trips state for {chat_id}: {state is not None}")
            if state:
                print(f"🔥 DEBUG: Customer trips state details: step={state.get('step')}, customer_id={state.get('customer_id')}")
                print(f"🔥 DEBUG: Full state: {state}")
            else:
                print(f"🔥 DEBUG: No customer trips state found in cache")
                
            return state
        except Exception as e:
            print(f"🔥 DEBUG: Error getting customer trips state: {e}")
            logger.error(f"Error getting customer trips state: {e}")
            return None

    def _save_customer_trips_state(self, chat_id, state_data, timeout=1800):
        """Save customer trips processing state for user"""
        try:
            cache_key = f"customer_trips_state_{chat_id}"
            print(f"🔥 DEBUG: Saving state with cache key: {cache_key}")
            print(f"🔥 DEBUG: State data being saved: {state_data}")
            
            # Use a longer timeout and ensure the data is serializable
            result = cache.set(cache_key, state_data, timeout=timeout)
            print(f"🔥 DEBUG: Cache.set returned: {result}")
            print(f"🔥 DEBUG: Saved customer trips state for {chat_id}: step={state_data.get('step')} (timeout={timeout}s)")
            
            # Immediately verify it was saved
            verify_state = cache.get(cache_key)
            if verify_state:
                print(f"🔥 DEBUG: Customer trips state save verified successfully")
                print(f"🔥 DEBUG: Verified data: {verify_state}")
            else:
                print(f"🔥 DEBUG: WARNING: Customer trips state save verification failed!")
                
        except Exception as e:
            print(f"🔥 DEBUG: Error saving customer trips state: {e}")
            logger.error(f"Error saving customer trips state: {e}")
            import traceback
            traceback.print_exc()

    def _clear_customer_trips_state(self, chat_id):
        """Clear customer trips processing state for user"""
        try:
            cache_key = f"customer_trips_state_{chat_id}"
            cache.delete(cache_key)
            print(f"🔥 DEBUG: Cleared customer trips state for {chat_id}")
        except Exception as e:
            print(f"🔥 DEBUG: Error clearing customer trips state: {e}")
            logger.error(f"Error clearing customer trips state: {e}")

    # ========================
    # BOL PROCESSING (NEW FUNCTIONALITY)
    # ========================

    def _initiate_bol_processing(self, chat_id, file_content, filename, user_context):
        """Initiate BOL document processing"""
        try:
            print(f"🔥 DEBUG: Starting BOL processing for file: {filename}")
            
            # Parse the BOL document first
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp_file:
                tmp_file.write(file_content)
                tmp_file.flush()
                temp_path = tmp_file.name
            
            try:
                # Parse the BOL document using the same logic as the email processor
                parsed_bol_data = self._parse_bol_pdf_data(file_content, filename)
                
                if not parsed_bol_data:
                    return "⚠ Could not extract data from BOL document. Please check the file format."
                
                print(f"🔥 DEBUG: Parsed BOL data: {parsed_bol_data}")
                
                kpc_loading_order_no = parsed_bol_data.get('kpc_loading_order_no')
                vehicle_reg = parsed_bol_data.get('vehicle_reg')
                actual_compartments = parsed_bol_data.get('actual_compartments', [])
                bol_shipment_no = parsed_bol_data.get('kpc_shipment_no')
                
                if not kpc_loading_order_no:
                    return "⚠ Could not find KPC Loading Order Number in BOL document. Please ensure this is a valid BOL PDF."
                
                # Try to find matching trip
                trip_to_update, matching_method, confidence_score = self._find_trip_by_truck_and_order(
                    vehicle_reg, kpc_loading_order_no
                )
                
                if not trip_to_update:
                    # Store parsed data for manual trip selection
                    bol_state = {
                        'step': 'awaiting_trip_selection',
                        'filename': filename,
                        'kpc_loading_order_no': kpc_loading_order_no,
                        'vehicle_reg': vehicle_reg,
                        'actual_compartments': actual_compartments,
                        'bol_shipment_no': bol_shipment_no,
                        'user_id': user_context['user_id']
                    }
                    self._save_bol_state(chat_id, bol_state, timeout=3600)
                    
                    return f"""📜 <b>BOL Document Parsed!</b>

📄 <b>File:</b> {filename}
📋 <b>Loading Order:</b> {kpc_loading_order_no}
🚛 <b>Vehicle:</b> {vehicle_reg or 'Not found'}
📜 <b>BOL Number:</b> {bol_shipment_no or 'Not found'}
📦 <b>Compartments:</b> {len(actual_compartments)} found

⚠ <b>No matching trip found automatically.</b>

Please type the Trip ID or KPC Order Number that this BOL should update, or use /cancel to abort."""
                
                # Auto-process the BOL
                return self._process_bol_update(chat_id, trip_to_update, parsed_bol_data, matching_method, confidence_score)
                
            finally:
                os.unlink(temp_path)
                
        except Exception as e:
            print(f"🔥 DEBUG: Error in BOL processing: {e}")
            logger.error(f"Error initiating BOL processing: {e}")
            import traceback
            traceback.print_exc()
            return f"⚠ Error processing BOL document: {str(e)}"

    def _parse_bol_pdf_data(self, pdf_content, original_pdf_filename="unknown.pdf"):
        """Parse BOL PDF and extract compartment data with L20 quantities - using same logic as email processor"""
        try:
            # Import pdfplumber - this should be available since it's used in the email processor
            import pdfplumber
        except ImportError:
            logger.error("pdfplumber not available for BOL parsing")
            return None
            
        extracted_data = {}
        tmp_pdf_path = None
        
        try:
            # Create temporary PDF file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(pdf_content)
                tmp_pdf_path = tmp.name

            # Parse PDF with pdfplumber
            with pdfplumber.open(tmp_pdf_path) as pdf:
                if not pdf.pages:
                    print(f"🔥 DEBUG: No pages in PDF '{original_pdf_filename}'")
                    return None

                full_text = ''
                all_tables = []
                
                # Extract text and tables from all pages
                for page in pdf.pages:
                    page_text = page.extract_text() or ""
                    full_text += page_text + "\n"
                    page_tables = page.extract_tables()
                    if page_tables:
                        all_tables.extend(page_tables)

                print(f"🔥 DEBUG: Extracted {len(full_text)} characters of text from BOL PDF")

                # Parse LOADING ORDER NUMBER (LON) - same patterns as email processor
                cleaned_full_text_for_lon = re.sub(r'\s+', ' ', full_text)
                lon_patterns = [
                    r"(?:KPC\s+)?Loading\s*(?:Order\s*)?(?:No\.?|NUMBER)?\s*[:\-]?\s*(S\d{5,7}\b)", 
                    r"Loading\s*Order\s*(?:No\.?|Number)?\s*[:\-]?\s*(S\d{5,7}\b)",
                    r"Order\s*No\s*[:\-]?\s*(S\d{5,7}\b)",
                    r"\b(S\d{5,7})\b" 
                ]
                
                for pattern in lon_patterns:
                    match = re.search(pattern, cleaned_full_text_for_lon, re.IGNORECASE)
                    if match:
                        # Handle different match group scenarios
                        if len(match.groups()) > 0 and match.group(1):
                            lon_candidate = match.group(1)
                            # If pattern captured digits only, prepend S
                            if lon_candidate.isdigit():
                                lon_candidate = 'S' + lon_candidate
                        else:
                            lon_candidate = match.group(0)
                            
                        # Validate LON format
                        if lon_candidate and (lon_candidate.upper().startswith('S') or lon_candidate.isdigit()):
                            if lon_candidate.isdigit():
                                lon_candidate = 'S' + lon_candidate
                            final_lon = lon_candidate.upper()
                            # Ensure it's S followed by at least 5 digits
                            if re.match(r'^S\d{5,7}[A-Z]?$', final_lon):
                
                # Parse BOL/Shipment number
                shipment_no_match = re.search(r"Shipment\s*(?:No\.?|Number)?\s*[:\-]?\s*(\d+)", full_text, re.IGNORECASE)
                if shipment_no_match:
                    extracted_data['kpc_shipment_no'] = shipment_no_match.group(1).strip()
                    print(f"🔥 DEBUG: Found BOL/Shipment No: {extracted_data['kpc_shipment_no']}")

                # Enhanced Vehicle Registration parsing - same patterns as email processor
                vehicle_patterns = [
                    r"Vehicle\s*(?:No\.?|Number)?\s*[:\-]?\s*([A-Z0-9]+(?:[/\\][A-Z0-9]+)?)",
                    r"Truck\s*(?:No\.?|Number)?\s*[:\-]?\s*([A-Z0-9]+(?:[/\\][A-Z0-9]+)?)",
                    r"Registration\s*(?:No\.?|Number)?\s*[:\-]?\s*([A-Z0-9]+(?:[/\\][A-Z0-9]+)?)",
                    r"\b([A-Z]{2,3}\s*\d{3,4}\s*[A-Z]?)\b",  # Common plate formats
                    r"\b([A-Z0-9]{6,}[/\\][A-Z0-9]{3,})\b"   # Truck/Trailer combinations
                ]
                
                for pattern in vehicle_patterns:
                    vehicle_match = re.search(pattern, full_text, re.IGNORECASE)
                    if vehicle_match:
                        vehicle_reg = vehicle_match.group(1).strip().upper()
                        # Clean up spacing and separators
                        vehicle_reg = re.sub(r'\s+', '', vehicle_reg)  # Remove spaces
                        vehicle_reg = re.sub(r'[/\\]', '/', vehicle_reg)  # Normalize separators
                        extracted_data['vehicle_reg'] = vehicle_reg
                        print(f"🔥 DEBUG: Found Vehicle Registration: {vehicle_reg}")
                        break

                # Parse TABLE DATA for ACTUAL COMPARTMENTS with L20 quantities
                if all_tables:
                    print(f"🔥 DEBUG: Found {len(all_tables)} table(s) in BOL PDF")
                    
                    header_found = False
                    actual_compartments = []
                    lon_from_first_valid_row = None
                    
                    for table_idx, table in enumerate(all_tables):
                        if not table or len(table) < 2:
                            continue
                            
                        header_row = table[0] if table[0] else []
                        header_text = " ".join([str(cell or "").strip() for cell in header_row]).upper()

                        if any(keyword in header_text for keyword in ['LOAD', 'ORDER', 'COMPARTMENT', 'ACTUAL', 'QUANTITY']):
                            header_found = True
                            print(f"🔥 DEBUG: BOL Table {table_idx + 1} header found")
                            
                            # Enhanced regex pattern to capture all three quantity columns
                            row_pattern = r"(\d+)\s+.*?(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s+(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s+(\d{1,3}(?:,\d{3})*(?:\.\d+)?)"

                            for row in table[1:]:  # Skip header
                                if not row:
                                    continue
                                    
                                row_text = " ".join([str(cell or "").strip() for cell in row])
                                
                                # Skip Total rows
                                if re.search(r'\btotal\b', row_text, re.IGNORECASE):
                                    continue
                                    
                                print(f"🔥 DEBUG: Processing BOL row: {row_text[:100]}...")
                                
                                match = re.search(row_pattern, row_text)
                                if match and len(match.groups()) >= 4:
                                    try:
                                        compartment_no = int(match.group(1))
                                        
                                        # Only process valid compartment numbers
                                        if not (1 <= compartment_no <= 5):
                                            continue
                                        
                                        # Extract quantities
                                        order_qty_str = match.group(2)
                                        actual_qty_str = match.group(3)  
                                        actual_l20_qty_str = match.group(4)
                                        
                                        # Clean and parse quantities
                                        requested_litres = Decimal(order_qty_str.replace(',', ''))
                                        actual_l20_litres = Decimal(actual_l20_qty_str.replace(',', ''))
                                        
                                        actual_compartments.append({
                                            'compartment_no': compartment_no,
                                            'quantity_requested_litres': requested_litres,
                                            'actual_quantity_l20': actual_l20_litres
                                        })
                                        
                                        print(f"🔥 DEBUG: BOL Compartment {compartment_no}: Requested={requested_litres}L, Actual L20={actual_l20_litres}L")
                                        
                                        # Extract LON from row if document-level LON missing
                                        if not lon_from_first_valid_row:
                                            lon_match = re.search(r'\b(S\d{5,7})\b', row_text)
                                            if lon_match:
                                                lon_from_first_valid_row = lon_match.group(1).upper()
                                        
                                    except (ValueError, InvalidOperation, IndexError) as e:
                                        print(f"🔥 DEBUG: Error parsing BOL quantities in row: {row_text[:100]}... Error: {e}")
                                        continue
                                
                            if actual_compartments:
                                extracted_data['actual_compartments'] = actual_compartments
                                print(f"🔥 DEBUG: Successfully parsed {len(actual_compartments)} compartment(s) from BOL")
                            else:
                                print(f"🔥 DEBUG: No valid compartment rows found in BOL table")

                    if lon_from_first_valid_row and not extracted_data.get('kpc_loading_order_no'):
                        extracted_data['kpc_loading_order_no'] = lon_from_first_valid_row
                        print(f"🔥 DEBUG: Used LON '{lon_from_first_valid_row}' from BOL table row")
                
                if not extracted_data.get('kpc_loading_order_no'):
                    print(f"🔥 DEBUG: CRITICAL: KPC Loading Order Number could NOT be determined for BOL '{original_pdf_filename}'")
                    return None

        except Exception as e_parse:
            print(f"🔥 DEBUG: General error during BOL PDF parsing for '{original_pdf_filename}': {e_parse}")
            logger.error(f"BOL PDF Parsing: General error for '{original_pdf_filename}': {e_parse}", exc_info=True)
            return None
        finally:
            if tmp_pdf_path and os.path.exists(tmp_pdf_path):
                try:
                    os.remove(tmp_pdf_path)
                except Exception as e_remove:
                    logger.warning(f"Could not remove temp PDF {tmp_pdf_path}: {e_remove}")
        
        return extracted_data

    def _find_trip_by_truck_and_order(self, vehicle_reg_from_bol, kpc_lon_from_bol):
        """Find trip using truck-based matching with order validation - same logic as email processor"""
        try:
            from .models import Vehicle, Trip
            from .utils.ai_order_matcher import get_trip_with_smart_matching
            
            trip_to_update = None
            matching_method = "none"
            confidence_score = 0.0
            
            print(f"🔥 DEBUG: Looking for trip with Vehicle={vehicle_reg_from_bol}, LON={kpc_lon_from_bol}")
            
            # Primary matching: Vehicle-based with order validation
            if vehicle_reg_from_bol:
                # Find vehicle by partial plate match (same logic as email processor)
                vehicles = Vehicle.objects.all()
                matching_vehicle = None
                
                for vehicle in vehicles:
                    plate_clean = re.sub(r'[^A-Z0-9]', '', vehicle.plate_number.upper())
                    bol_reg_clean = re.sub(r'[^A-Z0-9]', '', vehicle_reg_from_bol.upper())
                    
                    # Check for exact match or partial match
                    if (plate_clean == bol_reg_clean or 
                        plate_clean in bol_reg_clean or 
                        bol_reg_clean in plate_clean):
                        matching_vehicle = vehicle
                        print(f"🔥 DEBUG: Found matching vehicle: {vehicle.plate_number}")
                        break
                
                if matching_vehicle:
                    # Look for active trips for this vehicle
                    candidate_trips = Trip.objects.filter(
                        vehicle=matching_vehicle,
                        status__in=['PENDING', 'KPC_APPROVED', 'LOADING']
                    ).order_by('-loading_date')
                    
                    if candidate_trips:
                        candidate_trip = candidate_trips[0]  # Most recent
                        
                        # Validate against order number if available
                        if kpc_lon_from_bol and kpc_lon_from_bol != 'UNKNOWN_LON':
                            if candidate_trip.kpc_order_number:
                                # Calculate similarity between expected and actual order numbers
                                similarity_ratio = difflib.SequenceMatcher(
                                    None, 
                                    candidate_trip.kpc_order_number.upper(), 
                                    kpc_lon_from_bol.upper()
                                ).ratio()
                                
                                if similarity_ratio >= 0.8:  # 80% similarity threshold
                                    trip_to_update = candidate_trip
                                    matching_method = "truck_and_order_match"
                                    confidence_score = min(0.9, similarity_ratio + 0.1)
                                    print(f"🔥 DEBUG: High confidence match - Vehicle + Order similarity {similarity_ratio:.2f}")
                                elif similarity_ratio >= 0.5:  # Moderate similarity
                                    trip_to_update = candidate_trip
                                    matching_method = "truck_order_partial"
                                    confidence_score = 0.6
                                    print(f"🔥 DEBUG: Medium confidence match - Order similarity {similarity_ratio:.2f}")
                                else:
                                    # Significant order mismatch - use truck but warn
                                    trip_to_update = candidate_trip
                                    matching_method = "truck_only_order_mismatch"
                                    confidence_score = 0.5
                                    print(f"🔥 DEBUG: WARNING: Truck match but order mismatch - Expected={candidate_trip.kpc_order_number}, BOL={kpc_lon_from_bol}")
                        else:
                            # No order number in BOL, use truck only
                            trip_to_update = candidate_trip
                            matching_method = "truck_only_no_order"
                            confidence_score = 0.7
                            print(f"🔥 DEBUG: Truck-only match (no order in BOL)")
                    else:
                        print(f"🔥 DEBUG: No active trips found for vehicle {matching_vehicle.plate_number}")
                else:
                    print(f"🔥 DEBUG: Vehicle not found: {vehicle_reg_from_bol}")
            
            # Fallback to order-based matching if truck matching failed
            if not trip_to_update and kpc_lon_from_bol != 'UNKNOWN_LON':
                print(f"🔥 DEBUG: Falling back to order-based matching for: {kpc_lon_from_bol}")
                
                smart_result = get_trip_with_smart_matching(kpc_lon_from_bol)
                if smart_result:
                    if isinstance(smart_result, tuple) and len(smart_result) == 2:
                        trip_to_update, matching_metadata = smart_result
                        matching_method = f"fallback_order_{matching_metadata.get('correction_method', 'unknown')}"
                        confidence_score = matching_metadata.get('confidence', 0.3)
                    elif hasattr(smart_result, 'id'):
                        trip_to_update = smart_result
                        matching_method = "fallback_order_exact"
                        confidence_score = 0.8
                    
                    if trip_to_update:
                        print(f"🔥 DEBUG: Fallback match found: Trip {trip_to_update.id} ({trip_to_update.kpc_order_number})")
            
            return trip_to_update, matching_method, confidence_score
            
        except Exception as e:
            print(f"🔥 DEBUG: Error in truck-based matching: {e}")
            logger.error(f"BOL Processing: Error in truck-based matching: {e}")
            return None, "error", 0.0

    def _handle_bol_input(self, chat_id, message_text, bol_state):
        """Handle input during BOL interactive processing"""
        try:
            current_step = bol_state.get('step')
            print(f"🔥 DEBUG: Handling BOL input for step: {current_step}")
            
            if current_step == 'awaiting_trip_selection':
                # User provided trip ID or order number
                from .models import Trip
                
                message_text = message_text.strip()
                trip_to_update = None
                
                # Try to find trip by ID
                if message_text.isdigit():
                    try:
                        trip_to_update = Trip.objects.get(id=int(message_text))
                    except Trip.DoesNotExist:
                        pass
                
                # Try to find trip by KPC order number
                if not trip_to_update:
                    try:
                        trip_to_update = Trip.objects.get(kpc_order_number__iexact=message_text)
                    except Trip.DoesNotExist:
                        pass
                
                if not trip_to_update:
                    return f"⚠ Trip not found with ID or order number '{message_text}'. Please try again or use /cancel."
                
                # Process BOL update with manually selected trip
                parsed_bol_data = {
                    'kpc_loading_order_no': bol_state.get('kpc_loading_order_no'),
                    'vehicle_reg': bol_state.get('vehicle_reg'),
                    'actual_compartments': bol_state.get('actual_compartments', []),
                    'kpc_shipment_no': bol_state.get('bol_shipment_no')
                }
                
                return self._process_bol_update(chat_id, trip_to_update, parsed_bol_data, "manual_selection", 1.0)
            
            else:
                return "⚠ Unknown BOL processing step. Please start over with /cancel."
                
        except Exception as e:
            print(f"🔥 DEBUG: Error in BOL input handling: {e}")
            logger.error(f"Error handling BOL input: {e}")
            self._clear_bol_state(chat_id)
            return "⚠ Error processing your BOL input. Please start over."

    def _process_bol_update(self, chat_id, trip_to_update, parsed_bol_data, matching_method, confidence_score):
        """Process BOL update for a trip - same logic as email processor"""
        try:
            from .models import LoadingCompartment
            
            print(f"🔥 DEBUG: Processing BOL update for trip {trip_to_update.id}")
            
            kpc_loading_order_no = parsed_bol_data.get('kpc_loading_order_no')
            vehicle_reg = parsed_bol_data.get('vehicle_reg')
            actual_compartments = parsed_bol_data.get('actual_compartments', [])
            bol_shipment_no = parsed_bol_data.get('kpc_shipment_no')
            
            with transaction.atomic():
                # Ensure trip has compartments before updating
                self._ensure_trip_has_compartments(trip_to_update)
                
                if not actual_compartments:
                    response = f"""📜 <b>BOL Processing Complete</b>

🎯 <b>Trip Found:</b> {trip_to_update.id} ({trip_to_update.kpc_order_number})
🔍 <b>Matching:</b> {matching_method} (confidence: {confidence_score:.2f})
⚠ <b>Warning:</b> No compartment data found in BOL PDF

Trip information updated but compartment quantities remain unchanged."""
                    
                    self._clear_bol_state(chat_id)
                    return response

                # Update compartments with actual L20 quantities
                updated_compartments = 0
                for comp_data in actual_compartments:
                    comp_no = comp_data['compartment_no']
                    requested_qty = comp_data.get('quantity_requested_litres', Decimal('0.00'))
                    actual_l20_qty = comp_data['actual_quantity_l20']
                    
                    try:
                        compartment = LoadingCompartment.objects.get(
                            trip=trip_to_update,
                            compartment_number=comp_no
                        )
                        
                        # Update with both requested and actual L20 quantities from BOL
                        if requested_qty > 0:
                            compartment.quantity_requested_litres = requested_qty
                        compartment.quantity_actual_l20 = actual_l20_qty
                        compartment.save()
                        updated_compartments += 1
                        
                        print(f"🔥 DEBUG: Updated Compartment {comp_no}: Requested={requested_qty}L, Actual L20={actual_l20_qty}L")
                        
                    except LoadingCompartment.DoesNotExist:
                        print(f"🔥 DEBUG: Compartment {comp_no} not found for Trip {trip_to_update.id}")
                    except Exception as comp_error:
                        print(f"🔥 DEBUG: Error updating compartment {comp_no}: {comp_error}")
                        logger.error(f"BOL Processing: Error updating compartment {comp_no}: {comp_error}")

                if updated_compartments > 0:
                    # PROPER DEPLETION FLOW IMPLEMENTATION - same as email processor
                    original_status = trip_to_update.status
                    
                    if original_status != 'LOADED':
                        # Status will change, triggering the normal depletion logic in Trip.save()
                        trip_to_update.status = 'LOADED'
                        
                        # Update BOL number if available and not already set
                        if bol_shipment_no and not trip_to_update.bol_number:
                            trip_to_update.bol_number = bol_shipment_no
                        
                        trip_to_update.save()
                        print(f"🔥 DEBUG: Updated Trip {trip_to_update.id} status to LOADED")
                        
                        # Build success response
                        response = f"""✅ <b>BOL Processing Complete!</b>

🎯 <b>Trip Updated:</b> {trip_to_update.id} ({trip_to_update.kpc_order_number})
🔍 <b>Matching Method:</b> {matching_method} (confidence: {confidence_score:.2f})
🚛 <b>Vehicle:</b> {vehicle_reg or 'N/A'}
📜 <b>BOL Number:</b> {bol_shipment_no or 'N/A'}
📦 <b>Compartments Updated:</b> {updated_compartments}
📊 <b>Status Changed:</b> {original_status} → LOADED

Stock depletion has been automatically processed based on L20 actual quantities."""
                        
                    else:
                        # Trip was already LOADED, recalculate depletion with new L20 data
                        print(f"🔥 DEBUG: Trip {trip_to_update.id} already LOADED. Recalculating depletion with L20 data...")
                        
                        try:
                            # Force reversal of existing depletion
                            reversal_ok, reversal_msg = trip_to_update.reverse_stock_depletion(stdout_writer=None)
                            if reversal_ok:
                                print(f"🔥 DEBUG: Reversed existing depletion: {reversal_msg}")
                                
                                # Create new depletion based on L20 actuals
                                depletion_ok, depletion_msg = trip_to_update.perform_stock_depletion(
                                    stdout_writer=None,
                                    use_actual_l20=True,
                                    raise_error=True
                                )
                                if depletion_ok:
                                    print(f"🔥 DEBUG: Created new L20-based depletion: {depletion_msg}")
                                    
                                    response = f"""✅ <b>BOL Processing Complete!</b>

🎯 <b>Trip Updated:</b> {trip_to_update.id} ({trip_to_update.kpc_order_number})
🔍 <b>Matching Method:</b> {matching_method} (confidence: {confidence_score:.2f})
🚛 <b>Vehicle:</b> {vehicle_reg or 'N/A'}
📜 <b>BOL Number:</b> {bol_shipment_no or 'N/A'}
📦 <b>Compartments Updated:</b> {updated_compartments}
🔄 <b>Stock Depletion:</b> Recalculated with L20 actuals

Existing depletion reversed and new depletion created based on actual loaded quantities."""
                                else:
                                    response = f"""⚠ <b>BOL Processing Partial Success</b>

🎯 <b>Trip Updated:</b> {trip_to_update.id} ({trip_to_update.kpc_order_number})
📦 <b>Compartments Updated:</b> {updated_compartments}
❌ <b>Depletion Error:</b> {depletion_msg}

Compartment data updated but stock depletion calculation failed."""
                            else:
                                response = f"""⚠ <b>BOL Processing Partial Success</b>

🎯 <b>Trip Updated:</b> {trip_to_update.id} ({trip_to_update.kpc_order_number})
📦 <b>Compartments Updated:</b> {updated_compartments}
❌ <b>Reversal Error:</b> {reversal_msg}

Compartment data updated but existing depletion could not be reversed."""
                                
                        except Exception as depletion_error:
                            print(f"🔥 DEBUG: Depletion processing error: {depletion_error}")
                            response = f"""⚠ <b>BOL Processing Partial Success</b>

🎯 <b>Trip Updated:</b> {trip_to_update.id} ({trip_to_update.kpc_order_number})
📦 <b>Compartments Updated:</b> {updated_compartments}
❌ <b>Depletion Error:</b> {str(depletion_error)}

Compartment data updated but stock depletion processing failed."""
                else:
                    response = f"""⚠ <b>BOL Processing Complete</b>

🎯 <b>Trip Found:</b> {trip_to_update.id} ({trip_to_update.kpc_order_number})
🔍 <b>Matching:</b> {matching_method} (confidence: {confidence_score:.2f})
❌ <b>No Compartments Updated</b>

No valid compartment data could be processed from the BOL."""

            # Clear the BOL processing state
            self._clear_bol_state(chat_id)
            return response
                
        except Exception as e:
            logger.error(f"Error processing BOL update: {e}")
            print(f"🔥 DEBUG: Error in BOL update processing: {e}")
            import traceback
            traceback.print_exc()
            self._clear_bol_state(chat_id)
            return f"⚠ Error processing BOL update: {str(e)}\n\nPlease try the process again or contact support."

    def _ensure_trip_has_compartments(self, trip):
        """Ensure trip has the required compartments, create them if missing - same logic as email processor"""
        try:
            from .models import LoadingCompartment
            
            existing_compartments = LoadingCompartment.objects.filter(trip=trip)
            
            if not existing_compartments.exists():
                print(f"🔥 DEBUG: Trip {trip.id} has no compartments. Creating default compartments...")
                
                for comp_num in range(1, 4):  # Create 3 default compartments
                    LoadingCompartment.objects.create(
                        trip=trip,
                        compartment_number=comp_num,
                        quantity_requested_litres=Decimal('0.00'),
                        quantity_actual_l20=None
                    )
                    print(f"🔥 DEBUG: Created compartment {comp_num} for trip {trip.id}")
                
                return True
            else:
                print(f"🔥 DEBUG: Trip {trip.id} has {existing_compartments.count()} existing compartments")
                return False
        except Exception as e:
            print(f"🔥 DEBUG: Error ensuring trip has compartments: {e}")
            logger.error(f"Error ensuring trip has compartments: {e}")
            return False

    # ========================
    # BOL STATE MANAGEMENT
    # ========================
    
    def _get_bol_state(self, chat_id):
        """Get BOL processing state for user"""
        try:
            cache_key = f"bol_state_{chat_id}"
            state = cache.get(cache_key)
            print(f"🔥 DEBUG: Retrieved BOL state for {chat_id}: {state is not None}")
            if state:
                print(f"🔥 DEBUG: BOL state details: step={state.get('step')}, keys={list(state.keys())}")
            return state
        except Exception as e:
            print(f"🔥 DEBUG: Error getting BOL state: {e}")
            logger.error(f"Error getting BOL state: {e}")
            return None

    def _save_bol_state(self, chat_id, state_data, timeout=3600):
        """Save BOL processing state for user"""
        try:
            cache_key = f"bol_state_{chat_id}"
            cache.set(cache_key, state_data, timeout=timeout)
            print(f"🔥 DEBUG: Saved BOL state for {chat_id}: step={state_data.get('step')} (timeout={timeout}s)")
            
            # Verify it was saved
            verify_state = cache.get(cache_key)
            if verify_state:
                print(f"🔥 DEBUG: BOL state save verified successfully")
            else:
                print(f"🔥 DEBUG: WARNING: BOL state save verification failed!")
                
        except Exception as e:
            print(f"🔥 DEBUG: Error saving BOL state: {e}")
            logger.error(f"Error saving BOL state: {e}")

    def _clear_bol_state(self, chat_id):
        """Clear BOL processing state for user"""
        try:
            cache_key = f"bol_state_{chat_id}"
            cache.delete(cache_key)
            print(f"🔥 DEBUG: Cleared BOL state for {chat_id}")
        except Exception as e:
            print(f"🔥 DEBUG: Error clearing BOL state: {e}")
            logger.error(f"Error clearing BOL state: {e}")

    # ========================
    # TR830 PROCESSING (EXISTING)
    # ========================

    def _process_loading_authority_pdf(self, file_content, filename, user_context):
        """Process loading authority PDF (existing functionality)"""
        try:
            # This would implement loading authority processing
            # For now, return a placeholder
            return f"📋 Loading Authority processing for {filename} is not yet implemented."
        except Exception as e:
            logger.error(f"Error processing loading authority: {e}")
            return "⚠ Error processing loading authority PDF."

    def _initiate_tr830_processing(self, chat_id, file_content, filename, user_context):
        """Initiate TR830 document processing"""
        try:
            if not self.tr830_parser:
                return "⚠ TR830 parser not available. Please contact administrator."
            
            # Parse the TR830 document first
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp_file:
                tmp_file.write(file_content)
                tmp_file.flush()
                temp_path = tmp_file.name
            
            try:
                # Parse the TR830 document using existing parser method
                import_date, entries = self.tr830_parser.parse_pdf(temp_path)
                
                if not entries:
                    return "⚠ No shipment data found in the TR830 document. Please check the file format."
                
                print(f"🔥 DEBUG: Parsed {len(entries)} entries from TR830")
                
                # Store parsed data in cache for interactive processing - FIXED ATTRIBUTES
                tr830_data = {
                    'step': 'awaiting_supplier',
                    'filename': filename,
                    'import_date': import_date.isoformat(),
                    'entries': [
                        {
                            'vessel': entry.marks,  # FIXED: Use marks attribute
                            'product_type': entry.product_type,
                            'quantity': str(entry.avalue),  # FIXED: Use avalue attribute
                            'destination_name': entry.destination  # FIXED: Use destination attribute
                        } for entry in entries
                    ],
                    'user_id': user_context['user_id']
                }
                
                # FIXED: Use longer timeout and better cache key
                self._save_tr830_state(chat_id, tr830_data, timeout=7200)  # 2 hours
                
                # Create initial response with parsed data - FIXED ATTRIBUTES AND HTML
                response = "✅ <b>TR830 Document Parsed Successfully!</b>\n\n"
                response += f"📄 <b>File:</b> {filename}\n"
                response += f"📅 <b>Import Date:</b> {import_date.strftime('%d/%m/%Y')}\n\n"
                
                response += "<b>🚢 Parsed Shipment Data:</b>\n"
                for i, entry in enumerate(entries, 1):
                    response += f"{i}. <b>Vessel:</b> {entry.marks}\n"  # FIXED: Use marks
                    response += f"   <b>Product:</b> {entry.product_type}\n"
                    response += f"   <b>Quantity:</b> {entry.avalue:,.0f}L\n"  # FIXED: Use avalue
                    response += f"   <b>Destination:</b> {entry.destination}\n\n"  # FIXED: Use destination
                
                response += "🏭 <b>Step 1: Please provide the supplier name</b>\n"
                response += "Type the supplier company name (e.g., 'Kuwait Petroleum Corporation'):"
                
                return response
                
            finally:
                os.unlink(temp_path)
                
        except Exception as e:
            print(f"🔥 DEBUG: Error in _initiate_tr830_processing: {e}")
            logger.error(f"Error initiating TR830 processing: {e}")
            import traceback
            traceback.print_exc()
            return f"⚠ Error processing TR830 document: {str(e)}"

    def _handle_tr830_input(self, chat_id, message_text, tr830_state):
        """Handle input during TR830 interactive processing"""
        try:
            current_step = tr830_state.get('step')
            print(f"🔥 DEBUG: Handling TR830 input for step: {current_step}")
            print(f"🔥 DEBUG: Current state: {tr830_state}")
            
            if current_step == 'awaiting_supplier':
                # User provided supplier name
                tr830_state['supplier'] = message_text.strip()
                tr830_state['step'] = 'awaiting_price'
                
                # FIXED: Use longer timeout to prevent state loss
                self._save_tr830_state(chat_id, tr830_state, timeout=7200)
                
                return f"""✅ <b>Supplier Set:</b> {message_text}

💰 <b>Step 2: Please provide the price per litre</b>
Type the price in USD (e.g., '0.65' for $0.65 per litre):"""
            
            elif current_step == 'awaiting_price':
                # User provided price
                try:
                    price_per_litre = Decimal(message_text.strip())
                    if price_per_litre <= 0:
                        return "⚠ Price must be greater than zero. Please enter a valid price:"
                    
                    tr830_state['price_per_litre'] = str(price_per_litre)
                    
                    # Now create the shipment
                    return self._create_tr830_shipment(chat_id, tr830_state)
                    
                except (ValueError, InvalidOperation):
                    return "⚠ Invalid price format. Please enter a number (e.g., '0.65'):"
            
            else:
                return "⚠ Unknown processing step. Please start over with /cancel and upload a new TR830."
                
        except Exception as e:
            print(f"🔥 DEBUG: Error in _handle_tr830_input: {e}")
            logger.error(f"Error handling TR830 input: {e}")
            self._clear_tr830_state(chat_id)
            return "⚠ Error processing your input. Please start over."

    def _create_tr830_shipment(self, chat_id, tr830_state):
        """Create shipment from TR830 data"""
        try:
            print(f"🔥 DEBUG: Creating TR830 shipment")
            print(f"🔥 DEBUG: State data: {tr830_state}")
            
            from .models import Shipment, Product, Destination
            
            supplier = tr830_state['supplier']
            price_per_litre = Decimal(tr830_state['price_per_litre'])
            import_date = datetime.fromisoformat(tr830_state['import_date'])
            entries = tr830_state['entries']
            user = User.objects.get(id=tr830_state['user_id'])
            
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
                    
                    # FIXED: Create shipment WITHOUT total_cost (it's calculated automatically)
                    shipment = Shipment.objects.create(
                        user=user,
                        vessel_id_tag=vessel,
                        supplier_name=supplier,  # FIXED: Use correct field name
                        product=product,
                        destination=destination,
                        quantity_litres=quantity,  # FIXED: Use correct field name
                        quantity_remaining=quantity,
                        price_per_litre=price_per_litre,
                        import_date=import_date,
                        notes=f"Created via Telegram Bot from {tr830_state['filename']}"
                        # REMOVED: total_cost - it's calculated automatically as a property
                    )
                    
                    created_shipments.append(shipment)
                    
                    print(f"🔥 DEBUG: Created shipment: {shipment.id}")
            
            # Clear the processing state
            self._clear_tr830_state(chat_id)
            
            # Build success response
            response_msg = f"✅ <b>Success! Created {len(created_shipments)} shipment(s)</b>\n\n"
            
            for shipment in created_shipments:
                response_msg += f"📦 <b>Shipment ID:</b> {shipment.id}\n"
                response_msg += f"🚢 <b>Vessel:</b> {shipment.vessel_id_tag}\n"
                response_msg += f"⛽ <b>Product:</b> {shipment.product.name}\n"
                response_msg += f"📊 <b>Quantity:</b> {shipment.quantity_litres:,.0f}L\n"
                response_msg += f"💰 <b>Total Value:</b> ${shipment.total_cost:,.2f}\n"
                response_msg += f"📍 <b>Destination:</b> {shipment.destination.name}\n\n"
            
            response_msg += "🎉 All shipments have been successfully added to your inventory!"
            
            return response_msg
                
        except Exception as e:
            logger.error(f"Error creating TR830 shipment: {e}")
            print(f"🔥 DEBUG: Error creating shipment: {e}")
            import traceback
            traceback.print_exc()
            self._clear_tr830_state(chat_id)
            return f"⚠ Error creating shipment: {str(e)}\n\nPlease try the process again or contact support."

    def _get_tr830_state(self, chat_id):
        """Get TR830 processing state for user"""
        try:
            cache_key = f"tr830_state_{chat_id}"
            state = cache.get(cache_key)
            print(f"🔥 DEBUG: Retrieved TR830 state for {chat_id}: {state is not None}")
            if state:
                print(f"🔥 DEBUG: State details: step={state.get('step')}, keys={list(state.keys())}")
            return state
        except Exception as e:
            print(f"🔥 DEBUG: Error getting TR830 state: {e}")
            logger.error(f"Error getting TR830 state: {e}")
            return None

    def _save_tr830_state(self, chat_id, state_data, timeout=3600):
        """Save TR830 processing state for user"""
        try:
            cache_key = f"tr830_state_{chat_id}"
            cache.set(cache_key, state_data, timeout=timeout)
            print(f"🔥 DEBUG: Saved TR830 state for {chat_id}: step={state_data.get('step')} (timeout={timeout}s)")
            
            # Verify it was saved
            verify_state = cache.get(cache_key)
            if verify_state:
                print(f"🔥 DEBUG: State save verified successfully")
            else:
                print(f"🔥 DEBUG: WARNING: State save verification failed!")
                
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
                                extracted_data['kpc_loading_order_no'] = final_lon
                                print(f"🔥 DEBUG: Found LON: {final_lon} using pattern: {pattern}")
                                break
                
                # If no LON found in document text, try to extract from table data (fallback)
                if not extracted_data.get('kpc_loading_order_no') and all_tables:
                    print(f"🔥 DEBUG: No LON in document text, searching in table data...")
                    for table in all_tables:
                        for row in table:
                            if not row:
                                continue
                            row_text = " ".join([str(cell or "").strip() for cell in row])
                            lon_match = re.search(r'\b(S\d{5,7}[A-Z]?)\b', row_text)
                            if lon_match:
                                extracted_data['kpc_loading_order_no'] = lon_match.group(1).upper()
                                print(f"🔥 DEBUG: Found LON in table: {extracted_data['kpc_loading_order_no']}")
                                break
                        if extracted_data.get('kpc_loading_order_no'):
                            break
                
                # Parse BOL/Shipment number
                shipment_no_match = re.search(r"Shipment\s*(?:No\.?|Number)?\s*[:\-]?\s*(\d+)", full_text, re.IGNORECASE)
                if shipment_no_match:
                    extracted_data['kpc_shipment_no'] = shipment_no_match.group(1).strip()
                    print(f"🔥 DEBUG: Found BOL/Shipment No: {extracted_data['kpc_shipment_no']}")

                # Enhanced Vehicle Registration parsing - same patterns as email processor
                vehicle_patterns = [
                    r"Vehicle\s*(?:No\.?|Number)?\s*[:\-]?\s*([A-Z0-9]+(?:[/\\][A-Z0-9]+)?)",
                    r"Truck\s*(?:No\.?|Number)?\s*[:\-]?\s*([A-Z0-9]+(?:[/\\][A-Z0-9]+)?)",
                    r"Registration\s*(?:No\.?|Number)?\s*[:\-]?\s*([A-Z0-9]+(?:[/\\][A-Z0-9]+)?)",
                    r"\b([A-Z]{2,3}\s*\d{3,4}\s*[A-Z]?)\b",  # Common plate formats
                    r"\b([A-Z0-9]{6,}[/\\][A-Z0-9]{3,})\b"   # Truck/Trailer combinations
                ]
                
                for pattern in vehicle_patterns:
                    vehicle_match = re.search(pattern, full_text, re.IGNORECASE)
                    if vehicle_match:
                        vehicle_reg = vehicle_match.group(1).strip().upper()
                        # Clean up spacing and separators
                        vehicle_reg = re.sub(r'\s+', '', vehicle_reg)  # Remove spaces
                        vehicle_reg = re.sub(r'[/\\]', '/', vehicle_reg)  # Normalize separators
                        extracted_data['vehicle_reg'] = vehicle_reg
                        print(f"🔥 DEBUG: Found Vehicle Registration: {vehicle_reg}")
                        break

                # Parse TABLE DATA for ACTUAL COMPARTMENTS with L20 quantities
                if all_tables:
                    print(f"🔥 DEBUG: Found {len(all_tables)} table(s) in BOL PDF")
                    
                    header_found = False
                    actual_compartments = []
                    lon_from_first_valid_row = None
                    
                    for table_idx, table in enumerate(all_tables):
                        if not table or len(table) < 2:
                            continue
                            
                        header_row = table[0] if table[0] else []
                        header_text = " ".join([str(cell or "").strip() for cell in header_row]).upper()

                        if any(keyword in header_text for keyword in ['LOAD', 'ORDER', 'COMPARTMENT', 'ACTUAL', 'QUANTITY']):
                            header_found = True
                            print(f"🔥 DEBUG: BOL Table {table_idx + 1} header found")
                            
                            # Enhanced regex pattern to capture all three quantity columns
                            row_pattern = r"(\d+)\s+.*?(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s+(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s+(\d{1,3}(?:,\d{3})*(?:\.\d+)?)"

                            for row in table[1:]:  # Skip header
                                if not row:
                                    continue
                                    
                                row_text = " ".join([str(cell or "").strip() for cell in row])
                                
                                # Skip Total rows
                                if re.search(r'\btotal\b', row_text, re.IGNORECASE):
                                    continue
                                    
                                print(f"🔥 DEBUG: Processing BOL row: {row_text[:100]}...")
                                
                                match = re.search(row_pattern, row_text)
                                if match and len(match.groups()) >= 4:
                                    try:
                                        compartment_no = int(match.group(1))
                                        
                                        # Only process valid compartment numbers
                                        if not (1 <= compartment_no <= 5):
                                            continue
                                        
                                        # Extract quantities
                                        order_qty_str = match.group(2)
                                        actual_qty_str = match.group(3)  
                                        actual_l20_qty_str = match.group(4)
                                        
                                        # Clean and parse quantities
                                        requested_litres = Decimal(order_qty_str.replace(',', ''))
                                        actual_l20_litres = Decimal(actual_l20_qty_str.replace(',', ''))
                                        
                                        actual_compartments.append({
                                            'compartment_no': compartment_no,
                                            'quantity_requested_litres': requested_litres,
                                            'actual_quantity_l20': actual_l20_litres
                                        })
                                        
                                        print(f"🔥 DEBUG: BOL Compartment {compartment_no}: Requested={requested_litres}L, Actual L20={actual_l20_litres}L")
                                        
                                        # Extract LON from row if document-level LON missing
                                        if not lon_from_first_valid_row:
                                            lon_match = re.search(r'\b(S\d{5,7})\b', row_text)
                                            if lon_match:
                                                lon_from_first_valid_row = lon_match.group(1).upper()
                                        
                                    except (ValueError, InvalidOperation, IndexError) as e:
                                        print(f"🔥 DEBUG: Error parsing BOL quantities in row: {row_text[:100]}... Error: {e}")
                                        continue
                                
                            if actual_compartments:
                                extracted_data['actual_compartments'] = actual_compartments
                                print(f"🔥 DEBUG: Successfully parsed {len(actual_compartments)} compartment(s) from BOL")
                            else:
                                print(f"🔥 DEBUG: No valid compartment rows found in BOL table")

                    if lon_from_first_valid_row and not extracted_data.get('kpc_loading_order_no'):
                        extracted_data['kpc_loading_order_no'] = lon_from_first_valid_row
                        print(f"🔥 DEBUG: Used LON '{lon_from_first_valid_row}' from BOL table row")
                
                if not extracted_data.get('kpc_loading_order_no'):
                    print(f"🔥 DEBUG: CRITICAL: KPC Loading Order Number could NOT be determined for BOL '{original_pdf_filename}'")
                    return None

        except Exception as e_parse:
            print(f"🔥 DEBUG: General error during BOL PDF parsing for '{original_pdf_filename}': {e_parse}")
            logger.error(f"BOL PDF Parsing: General error for '{original_pdf_filename}': {e_parse}", exc_info=True)
            return None
        finally:
            if tmp_pdf_path and os.path.exists(tmp_pdf_path):
                try:
                    os.remove(tmp_pdf_path)
                except Exception as e_remove:
                    logger.warning(f"Could not remove temp PDF {tmp_pdf_path}: {e_remove}")
        
        return extracted_data

    def _find_trip_by_truck_and_order(self, vehicle_reg_from_bol, kpc_lon_from_bol):
        """Find trip using truck-based matching with order validation - same logic as email processor"""
        try:
            from .models import Vehicle, Trip
            from .utils.ai_order_matcher import get_trip_with_smart_matching
            
            trip_to_update = None
            matching_method = "none"
            confidence_score = 0.0
            
            print(f"🔥 DEBUG: Looking for trip with Vehicle={vehicle_reg_from_bol}, LON={kpc_lon_from_bol}")
            
            # Primary matching: Vehicle-based with order validation
            if vehicle_reg_from_bol:
                # Find vehicle by partial plate match (same logic as email processor)
                vehicles = Vehicle.objects.all()
                matching_vehicle = None
                
                for vehicle in vehicles:
                    plate_clean = re.sub(r'[^A-Z0-9]', '', vehicle.plate_number.upper())
                    bol_reg_clean = re.sub(r'[^A-Z0-9]', '', vehicle_reg_from_bol.upper())
                    
                    # Check for exact match or partial match
                    if (plate_clean == bol_reg_clean or 
                        plate_clean in bol_reg_clean or 
                        bol_reg_clean in plate_clean):
                        matching_vehicle = vehicle
                        print(f"🔥 DEBUG: Found matching vehicle: {vehicle.plate_number}")
                        break
                
                if matching_vehicle:
                    # Look for active trips for this vehicle
                    candidate_trips = Trip.objects.filter(
                        vehicle=matching_vehicle,
                        status__in=['PENDING', 'KPC_APPROVED', 'LOADING']
                    ).order_by('-loading_date')
                    
                    if candidate_trips:
                        candidate_trip = candidate_trips[0]  # Most recent
                        
                        # Validate against order number if available
                        if kpc_lon_from_bol and kpc_lon_from_bol != 'UNKNOWN_LON':
                            if candidate_trip.kpc_order_number:
                                # Calculate similarity between expected and actual order numbers
                                similarity_ratio = difflib.SequenceMatcher(
                                    None, 
                                    candidate_trip.kpc_order_number.upper(), 
                                    kpc_lon_from_bol.upper()
                                ).ratio()
                                
                                if similarity_ratio >= 0.8:  # 80% similarity threshold
                                    trip_to_update = candidate_trip
                                    matching_method = "truck_and_order_match"
                                    confidence_score = min(0.9, similarity_ratio + 0.1)
                                    print(f"🔥 DEBUG: High confidence match - Vehicle + Order similarity {similarity_ratio:.2f}")
                                elif similarity_ratio >= 0.5:  # Moderate similarity
                                    trip_to_update = candidate_trip
                                    matching_method = "truck_order_partial"
                                    confidence_score = 0.6
                                    print(f"🔥 DEBUG: Medium confidence match - Order similarity {similarity_ratio:.2f}")
                                else:
                                    # Significant order mismatch - use truck but warn
                                    trip_to_update = candidate_trip
                                    matching_method = "truck_only_order_mismatch"
                                    confidence_score = 0.5
                                    print(f"🔥 DEBUG: WARNING: Truck match but order mismatch - Expected={candidate_trip.kpc_order_number}, BOL={kpc_lon_from_bol}")
                        else:
                            # No order number in BOL, use truck only
                            trip_to_update = candidate_trip
                            matching_method = "truck_only_no_order"
                            confidence_score = 0.7
                            print(f"🔥 DEBUG: Truck-only match (no order in BOL)")
                    else:
                        print(f"🔥 DEBUG: No active trips found for vehicle {matching_vehicle.plate_number}")
                else:
                    print(f"🔥 DEBUG: Vehicle not found: {vehicle_reg_from_bol}")
            
            # Fallback to order-based matching if truck matching failed
            if not trip_to_update and kpc_lon_from_bol != 'UNKNOWN_LON':
                print(f"🔥 DEBUG: Falling back to order-based matching for: {kpc_lon_from_bol}")
                
                smart_result = get_trip_with_smart_matching(kpc_lon_from_bol)
                if smart_result:
                    if isinstance(smart_result, tuple) and len(smart_result) == 2:
                        trip_to_update, matching_metadata = smart_result
                        matching_method = f"fallback_order_{matching_metadata.get('correction_method', 'unknown')}"
                        confidence_score = matching_metadata.get('confidence', 0.3)
                    elif hasattr(smart_result, 'id'):
                        trip_to_update = smart_result
                        matching_method = "fallback_order_exact"
                        confidence_score = 0.8
                    
                    if trip_to_update:
                        print(f"🔥 DEBUG: Fallback match found: Trip {trip_to_update.id} ({trip_to_update.kpc_order_number})")
            
            return trip_to_update, matching_method, confidence_score
            
        except Exception as e:
            print(f"🔥 DEBUG: Error in truck-based matching: {e}")
            logger.error(f"BOL Processing: Error in truck-based matching: {e}")
            return None, "error", 0.0

    def _handle_bol_input(self, chat_id, message_text, bol_state):
        """Handle input during BOL interactive processing"""
        try:
            current_step = bol_state.get('step')
            print(f"🔥 DEBUG: Handling BOL input for step: {current_step}")
            
            if current_step == 'awaiting_trip_selection':
                # User provided trip ID or order number
                from .models import Trip
                
                message_text = message_text.strip()
                trip_to_update = None
                
                # Try to find trip by ID
                if message_text.isdigit():
                    try:
                        trip_to_update = Trip.objects.get(id=int(message_text))
                    except Trip.DoesNotExist:
                        pass
                
                # Try to find trip by KPC order number
                if not trip_to_update:
                    try:
                        trip_to_update = Trip.objects.get(kpc_order_number__iexact=message_text)
                    except Trip.DoesNotExist:
                        pass
                
                if not trip_to_update:
                    return f"⚠ Trip not found with ID or order number '{message_text}'. Please try again or use /cancel."
                
                # Process BOL update with manually selected trip
                parsed_bol_data = {
                    'kpc_loading_order_no': bol_state.get('kpc_loading_order_no'),
                    'vehicle_reg': bol_state.get('vehicle_reg'),
                    'actual_compartments': bol_state.get('actual_compartments', []),
                    'kpc_shipment_no': bol_state.get('bol_shipment_no')
                }
                
                return self._process_bol_update(chat_id, trip_to_update, parsed_bol_data, "manual_selection", 1.0)
            
            else:
                return "⚠ Unknown BOL processing step. Please start over with /cancel."
                
        except Exception as e:
            print(f"🔥 DEBUG: Error in BOL input handling: {e}")
            logger.error(f"Error handling BOL input: {e}")
            self._clear_bol_state(chat_id)
            return "⚠ Error processing your BOL input. Please start over."

    def _process_bol_update(self, chat_id, trip_to_update, parsed_bol_data, matching_method, confidence_score):
        """Process BOL update for a trip - same logic as email processor"""
        try:
            from .models import LoadingCompartment
            
            print(f"🔥 DEBUG: Processing BOL update for trip {trip_to_update.id}")
            
            kpc_loading_order_no = parsed_bol_data.get('kpc_loading_order_no')
            vehicle_reg = parsed_bol_data.get('vehicle_reg')
            actual_compartments = parsed_bol_data.get('actual_compartments', [])
            bol_shipment_no = parsed_bol_data.get('kpc_shipment_no')
            
            with transaction.atomic():
                # Ensure trip has compartments before updating
                self._ensure_trip_has_compartments(trip_to_update)
                
                if not actual_compartments:
                    response = f"""📜 <b>BOL Processing Complete</b>

🎯 <b>Trip Found:</b> {trip_to_update.id} ({trip_to_update.kpc_order_number})
🔍 <b>Matching:</b> {matching_method} (confidence: {confidence_score:.2f})
⚠ <b>Warning:</b> No compartment data found in BOL PDF

Trip information updated but compartment quantities remain unchanged."""
                    
                    self._clear_bol_state(chat_id)
                    return response

                # Update compartments with actual L20 quantities
                updated_compartments = 0
                for comp_data in actual_compartments:
                    comp_no = comp_data['compartment_no']
                    requested_qty = comp_data.get('quantity_requested_litres', Decimal('0.00'))
                    actual_l20_qty = comp_data['actual_quantity_l20']
                    
                    try:
                        compartment = LoadingCompartment.objects.get(
                            trip=trip_to_update,
                            compartment_number=comp_no
                        )
                        
                        # Update with both requested and actual L20 quantities from BOL
                        if requested_qty > 0:
                            compartment.quantity_requested_litres = requested_qty
                        compartment.quantity_actual_l20 = actual_l20_qty
                        compartment.save()
                        updated_compartments += 1
                        
                        print(f"🔥 DEBUG: Updated Compartment {comp_no}: Requested={requested_qty}L, Actual L20={actual_l20_qty}L")
                        
                    except LoadingCompartment.DoesNotExist:
                        print(f"🔥 DEBUG: Compartment {comp_no} not found for Trip {trip_to_update.id}")
                    except Exception as comp_error:
                        print(f"🔥 DEBUG: Error updating compartment {comp_no}: {comp_error}")
                        logger.error(f"BOL Processing: Error updating compartment {comp_no}: {comp_error}")

                if updated_compartments > 0:
                    # PROPER DEPLETION FLOW IMPLEMENTATION - same as email processor
                    original_status = trip_to_update.status
                    
                    if original_status != 'LOADED':
                        # Status will change, triggering the normal depletion logic in Trip.save()
                        trip_to_update.status = 'LOADED'
                        
                        # Update BOL number if available and not already set
                        if bol_shipment_no and not trip_to_update.bol_number:
                            trip_to_update.bol_number = bol_shipment_no
                        
                        trip_to_update.save()
                        print(f"🔥 DEBUG: Updated Trip {trip_to_update.id} status to LOADED")
                        
                        # Build success response
                        response = f"""✅ <b>BOL Processing Complete!</b>

🎯 <b>Trip Updated:</b> {trip_to_update.id} ({trip_to_update.kpc_order_number})
🔍 <b>Matching Method:</b> {matching_method} (confidence: {confidence_score:.2f})
🚛 <b>Vehicle:</b> {vehicle_reg or 'N/A'}
📜 <b>BOL Number:</b> {bol_shipment_no or 'N/A'}
📦 <b>Compartments Updated:</b> {updated_compartments}
📊 <b>Status Changed:</b> {original_status} → LOADED

Stock depletion has been automatically processed based on L20 actual quantities."""
                        
                    else:
                        # Trip was already LOADED, recalculate depletion with new L20 data
                        print(f"🔥 DEBUG: Trip {trip_to_update.id} already LOADED. Recalculating depletion with L20 data...")
                        
                        try:
                            # Force reversal of existing depletion
                            reversal_ok, reversal_msg = trip_to_update.reverse_stock_depletion(stdout_writer=None)
                            if reversal_ok:
                                print(f"🔥 DEBUG: Reversed existing depletion: {reversal_msg}")
                                
                                # Create new depletion based on L20 actuals
                                depletion_ok, depletion_msg = trip_to_update.perform_stock_depletion(
                                    stdout_writer=None,
                                    use_actual_l20=True,
                                    raise_error=True
                                )
                                if depletion_ok:
                                    print(f"🔥 DEBUG: Created new L20-based depletion: {depletion_msg}")
                                    
                                    response = f"""✅ <b>BOL Processing Complete!</b>

🎯 <b>Trip Updated:</b> {trip_to_update.id} ({trip_to_update.kpc_order_number})
🔍 <b>Matching Method:</b> {matching_method} (confidence: {confidence_score:.2f})
🚛 <b>Vehicle:</b> {vehicle_reg or 'N/A'}
📜 <b>BOL Number:</b> {bol_shipment_no or 'N/A'}
📦 <b>Compartments Updated:</b> {updated_compartments}
🔄 <b>Stock Depletion:</b> Recalculated with L20 actuals

Existing depletion reversed and new depletion created based on actual loaded quantities."""
                                else:
                                    response = f"""⚠ <b>BOL Processing Partial Success</b>

🎯 <b>Trip Updated:</b> {trip_to_update.id} ({trip_to_update.kpc_order_number})
📦 <b>Compartments Updated:</b> {updated_compartments}
❌ <b>Depletion Error:</b> {depletion_msg}

Compartment data updated but stock depletion calculation failed."""
                            else:
                                response = f"""⚠ <b>BOL Processing Partial Success</b>

🎯 <b>Trip Updated:</b> {trip_to_update.id} ({trip_to_update.kpc_order_number})
📦 <b>Compartments Updated:</b> {updated_compartments}
❌ <b>Reversal Error:</b> {reversal_msg}

Compartment data updated but existing depletion could not be reversed."""
                                
                        except Exception as depletion_error:
                            print(f"🔥 DEBUG: Depletion processing error: {depletion_error}")
                            response = f"""⚠ <b>BOL Processing Partial Success</b>

🎯 <b>Trip Updated:</b> {trip_to_update.id} ({trip_to_update.kpc_order_number})
📦 <b>Compartments Updated:</b> {updated_compartments}
❌ <b>Depletion Error:</b> {str(depletion_error)}

Compartment data updated but stock depletion processing failed."""
                else:
                    response = f"""⚠ <b>BOL Processing Complete</b>

🎯 <b>Trip Found:</b> {trip_to_update.id} ({trip_to_update.kpc_order_number})
🔍 <b>Matching:</b> {matching_method} (confidence: {confidence_score:.2f})
❌ <b>No Compartments Updated</b>

No valid compartment data could be processed from the BOL."""

            # Clear the BOL processing state
            self._clear_bol_state(chat_id)
            return response
                
        except Exception as e:
            logger.error(f"Error processing BOL update: {e}")
            print(f"🔥 DEBUG: Error in BOL update processing: {e}")
            import traceback
            traceback.print_exc()
            self._clear_bol_state(chat_id)
            return f"⚠ Error processing BOL update: {str(e)}\n\nPlease try the process again or contact support."

    def _ensure_trip_has_compartments(self, trip):
        """Ensure trip has the required compartments, create them if missing - same logic as email processor"""
        try:
            from .models import LoadingCompartment
            
            existing_compartments = LoadingCompartment.objects.filter(trip=trip)
            
            if not existing_compartments.exists():
                print(f"🔥 DEBUG: Trip {trip.id} has no compartments. Creating default compartments...")
                
                for comp_num in range(1, 4):  # Create 3 default compartments
                    LoadingCompartment.objects.create(
                        trip=trip,
                        compartment_number=comp_num,
                        quantity_requested_litres=Decimal('0.00'),
                        quantity_actual_l20=None
                    )
                    print(f"🔥 DEBUG: Created compartment {comp_num} for trip {trip.id}")
                
                return True
            else:
                print(f"🔥 DEBUG: Trip {trip.id} has {existing_compartments.count()} existing compartments")
                return False
        except Exception as e:
            print(f"🔥 DEBUG: Error ensuring trip has compartments: {e}")
            logger.error(f"Error ensuring trip has compartments: {e}")
            return False

    # ========================
    # BOL STATE MANAGEMENT
    # ========================
    
    def _get_bol_state(self, chat_id):
        """Get BOL processing state for user"""
        try:
            cache_key = f"bol_state_{chat_id}"
            state = cache.get(cache_key)
            print(f"🔥 DEBUG: Retrieved BOL state for {chat_id}: {state is not None}")
            if state:
                print(f"🔥 DEBUG: BOL state details: step={state.get('step')}, keys={list(state.keys())}")
            return state
        except Exception as e:
            print(f"🔥 DEBUG: Error getting BOL state: {e}")
            logger.error(f"Error getting BOL state: {e}")
            return None

    def _save_bol_state(self, chat_id, state_data, timeout=3600):
        """Save BOL processing state for user"""
        try:
            cache_key = f"bol_state_{chat_id}"
            cache.set(cache_key, state_data, timeout=timeout)
            print(f"🔥 DEBUG: Saved BOL state for {chat_id}: step={state_data.get('step')} (timeout={timeout}s)")
            
            # Verify it was saved
            verify_state = cache.get(cache_key)
            if verify_state:
                print(f"🔥 DEBUG: BOL state save verified successfully")
            else:
                print(f"🔥 DEBUG: WARNING: BOL state save verification failed!")
                
        except Exception as e:
            print(f"🔥 DEBUG: Error saving BOL state: {e}")
            logger.error(f"Error saving BOL state: {e}")

    def _clear_bol_state(self, chat_id):
        """Clear BOL processing state for user"""
        try:
            cache_key = f"bol_state_{chat_id}"
            cache.delete(cache_key)
            print(f"🔥 DEBUG: Cleared BOL state for {chat_id}")
        except Exception as e:
            print(f"🔥 DEBUG: Error clearing BOL state: {e}")
            logger.error(f"Error clearing BOL state: {e}")

    # ========================
    # TR830 PROCESSING (EXISTING)
    # ========================

    def _process_loading_authority_pdf(self, file_content, filename, user_context):
        """Process loading authority PDF (existing functionality)"""
        try:
            # This would implement loading authority processing
            # For now, return a placeholder
            return f"📋 Loading Authority processing for {filename} is not yet implemented."
        except Exception as e:
            logger.error(f"Error processing loading authority: {e}")
            return "⚠ Error processing loading authority PDF."

    def _initiate_tr830_processing(self, chat_id, file_content, filename, user_context):
        """Initiate TR830 document processing"""
        try:
            if not self.tr830_parser:
                return "⚠ TR830 parser not available. Please contact administrator."
            
            # Parse the TR830 document first
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp_file:
                tmp_file.write(file_content)
                tmp_file.flush()
                temp_path = tmp_file.name
            
            try:
                # Parse the TR830 document using existing parser method
                import_date, entries = self.tr830_parser.parse_pdf(temp_path)
                
                if not entries:
                    return "⚠ No shipment data found in the TR830 document. Please check the file format."
                
                print(f"🔥 DEBUG: Parsed {len(entries)} entries from TR830")
                
                # Store parsed data in cache for interactive processing - FIXED ATTRIBUTES
                tr830_data = {
                    'step': 'awaiting_supplier',
                    'filename': filename,
                    'import_date': import_date.isoformat(),
                    'entries': [
                        {
                            'vessel': entry.marks,  # FIXED: Use marks attribute
                            'product_type': entry.product_type,
                            'quantity': str(entry.avalue),  # FIXED: Use avalue attribute
                            'destination_name': entry.destination  # FIXED: Use destination attribute
                        } for entry in entries
                    ],
                    'user_id': user_context['user_id']
                }
                
                # FIXED: Use longer timeout and better cache key
                self._save_tr830_state(chat_id, tr830_data, timeout=7200)  # 2 hours
                
                # Create initial response with parsed data - FIXED ATTRIBUTES AND HTML
                response = "✅ <b>TR830 Document Parsed Successfully!</b>\n\n"
                response += f"📄 <b>File:</b> {filename}\n"
                response += f"📅 <b>Import Date:</b> {import_date.strftime('%d/%m/%Y')}\n\n"
                
                response += "<b>🚢 Parsed Shipment Data:</b>\n"
                for i, entry in enumerate(entries, 1):
                    response += f"{i}. <b>Vessel:</b> {entry.marks}\n"  # FIXED: Use marks
                    response += f"   <b>Product:</b> {entry.product_type}\n"
                    response += f"   <b>Quantity:</b> {entry.avalue:,.0f}L\n"  # FIXED: Use avalue
                    response += f"   <b>Destination:</b> {entry.destination}\n\n"  # FIXED: Use destination
                
                response += "🏭 <b>Step 1: Please provide the supplier name</b>\n"
                response += "Type the supplier company name (e.g., 'Kuwait Petroleum Corporation'):"
                
                return response
                
            finally:
                os.unlink(temp_path)
                
        except Exception as e:
            print(f"🔥 DEBUG: Error in _initiate_tr830_processing: {e}")
            logger.error(f"Error initiating TR830 processing: {e}")
            import traceback
            traceback.print_exc()
            return f"⚠ Error processing TR830 document: {str(e)}"

    def _handle_tr830_input(self, chat_id, message_text, tr830_state):
        """Handle input during TR830 interactive processing"""
        try:
            current_step = tr830_state.get('step')
            print(f"🔥 DEBUG: Handling TR830 input for step: {current_step}")
            print(f"🔥 DEBUG: Current state: {tr830_state}")
            
            if current_step == 'awaiting_supplier':
                # User provided supplier name
                tr830_state['supplier'] = message_text.strip()
                tr830_state['step'] = 'awaiting_price'
                
                # FIXED: Use longer timeout to prevent state loss
                self._save_tr830_state(chat_id, tr830_state, timeout=7200)
                
                return f"""✅ <b>Supplier Set:</b> {message_text}

💰 <b>Step 2: Please provide the price per litre</b>
Type the price in USD (e.g., '0.65' for $0.65 per litre):"""
            
            elif current_step == 'awaiting_price':
                # User provided price
                try:
                    price_per_litre = Decimal(message_text.strip())
                    if price_per_litre <= 0:
                        return "⚠ Price must be greater than zero. Please enter a valid price:"
                    
                    tr830_state['price_per_litre'] = str(price_per_litre)
                    
                    # Now create the shipment
                    return self._create_tr830_shipment(chat_id, tr830_state)
                    
                except (ValueError, InvalidOperation):
                    return "⚠ Invalid price format. Please enter a number (e.g., '0.65'):"
            
            else:
                return "⚠ Unknown processing step. Please start over with /cancel and upload a new TR830."
                
        except Exception as e:
            print(f"🔥 DEBUG: Error in _handle_tr830_input: {e}")
            logger.error(f"Error handling TR830 input: {e}")
            self._clear_tr830_state(chat_id)
            return "⚠ Error processing your input. Please start over."

    def _create_tr830_shipment(self, chat_id, tr830_state):
        """Create shipment from TR830 data"""
        try:
            print(f"🔥 DEBUG: Creating TR830 shipment")
            print(f"🔥 DEBUG: State data: {tr830_state}")
            
            from .models import Shipment, Product, Destination
            
            supplier = tr830_state['supplier']
            price_per_litre = Decimal(tr830_state['price_per_litre'])
            import_date = datetime.fromisoformat(tr830_state['import_date'])
            entries = tr830_state['entries']
            user = User.objects.get(id=tr830_state['user_id'])
            
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
                    
                    # FIXED: Create shipment WITHOUT total_cost (it's calculated automatically)
                    shipment = Shipment.objects.create(
                        user=user,
                        vessel_id_tag=vessel,
                        supplier_name=supplier,  # FIXED: Use correct field name
                        product=product,
                        destination=destination,
                        quantity_litres=quantity,  # FIXED: Use correct field name
                        quantity_remaining=quantity,
                        price_per_litre=price_per_litre,
                        import_date=import_date,
                        notes=f"Created via Telegram Bot from {tr830_state['filename']}"
                        # REMOVED: total_cost - it's calculated automatically as a property
                    )
                    
                    created_shipments.append(shipment)
                    
                    print(f"🔥 DEBUG: Created shipment: {shipment.id}")
            
            # Clear the processing state
            self._clear_tr830_state(chat_id)
            
            # Build success response
            response_msg = f"✅ <b>Success! Created {len(created_shipments)} shipment(s)</b>\n\n"
            
            for shipment in created_shipments:
                response_msg += f"📦 <b>Shipment ID:</b> {shipment.id}\n"
                response_msg += f"🚢 <b>Vessel:</b> {shipment.vessel_id_tag}\n"
                response_msg += f"⛽ <b>Product:</b> {shipment.product.name}\n"
                response_msg += f"📊 <b>Quantity:</b> {shipment.quantity_litres:,.0f}L\n"
                response_msg += f"💰 <b>Total Value:</b> ${shipment.total_cost:,.2f}\n"
                response_msg += f"📍 <b>Destination:</b> {shipment.destination.name}\n\n"
            
            response_msg += "🎉 All shipments have been successfully added to your inventory!"
            
            return response_msg
                
        except Exception as e:
            logger.error(f"Error creating TR830 shipment: {e}")
            print(f"🔥 DEBUG: Error creating shipment: {e}")
            import traceback
            traceback.print_exc()
            self._clear_tr830_state(chat_id)
            return f"⚠ Error creating shipment: {str(e)}\n\nPlease try the process again or contact support."

    def _get_tr830_state(self, chat_id):
        """Get TR830 processing state for user"""
        try:
            cache_key = f"tr830_state_{chat_id}"
            state = cache.get(cache_key)
            print(f"🔥 DEBUG: Retrieved TR830 state for {chat_id}: {state is not None}")
            if state:
                print(f"🔥 DEBUG: State details: step={state.get('step')}, keys={list(state.keys())}")
            return state
        except Exception as e:
            print(f"🔥 DEBUG: Error getting TR830 state: {e}")
            logger.error(f"Error getting TR830 state: {e}")
            return None

    def _save_tr830_state(self, chat_id, state_data, timeout=3600):
        """Save TR830 processing state for user"""
        try:
            cache_key = f"tr830_state_{chat_id}"
            cache.set(cache_key, state_data, timeout=timeout)
            print(f"🔥 DEBUG: Saved TR830 state for {chat_id}: step={state_data.get('step')} (timeout={timeout}s)")
            
            # Verify it was saved
            verify_state = cache.get(cache_key)
            if verify_state:
                print(f"🔥 DEBUG: State save verified successfully")
            else:
                print(f"🔥 DEBUG: WARNING: State save verification failed!")
                
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