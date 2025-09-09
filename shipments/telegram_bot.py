# shipments/telegram_bot.py
# Complete Telegram Bot implementation with Customer Trip Lookup Feature
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
                return "❌ Please register your Telegram account to access document processing."
            
            # Download file
            file_content = self.download_file(file_id)
            if not file_content:
                return "❌ Could not download the file. Please try again."
            
            print(f"🔥 DEBUG: Downloaded file size: {len(file_content)} bytes")
            
            # Check file size (limit to 10MB)
            if len(file_content) > 10 * 1024 * 1024:
                return "❌ File too large. Please upload a file smaller than 10MB."
            
            # Detect document type based on filename or content
            filename_lower = filename.lower()
            
            if filename_lower.endswith('.pdf') and 'tr830' in filename_lower:
                return self._initiate_tr830_processing(chat_id, file_content, filename, user_context)
            elif filename_lower.endswith('.pdf'):
                return self._process_loading_authority_pdf(file_content, filename, user_context)
            else:
                return "❌ Unsupported file type. Please upload a PDF document."
                
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
            
            # NEW: Check if user is in customer trips flow
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
            return "❌ Error processing your message. Please try again or use /help for available commands."

    def _handle_start_command(self, chat_id, username, user_context):
        """Handle /start command"""
        if user_context.get('user_id'):
            return f"""👋 Welcome back, {username or 'there'}!

🤖 <b>Sakina Gas Telegram Bot</b>

I can help you with:
📋 Upload loading authorities (PDF/images)
📄 Process TR830 documents (PDF) - <i>Interactive mode</i>
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
        customer_trips_state = self._get_customer_trips_state(chat_id)
        
        if tr830_state:
            self._clear_tr830_state(chat_id)
            return "✅ TR830 processing cancelled. You can start over by uploading a new TR830 document."
        elif customer_trips_state:
            self._clear_customer_trips_state(chat_id)
            return "✅ Customer trips lookup cancelled."
        else:
            return "ℹ️ No active process to cancel. Use /help to see available commands."

    def _handle_general_query(self, message_text, user_context, chat_id):
        """Handle general queries when not in TR830 or customer trips flow"""
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
            # NEW: Try to find customer by name for trip lookup
            return self._handle_potential_customer_lookup(message_text, user_context, chat_id)

    def _handle_potential_customer_lookup_or_truck_exclusion(self, message_text, user_context, chat_id):
        """Handle potential customer name input for trip lookup OR truck exclusion without cache"""
        try:
            # First check if it looks like a truck identifier
            if self._looks_like_truck_identifier(message_text):
                print(f"🔥 DEBUG: Input '{message_text}' looks like truck identifier")
                
                # Since cache might not be working, let's try a different approach
                # We'll assume the user was looking at some customer's trips and try to find
                # a customer that has this truck in their recent trips
                result = self._find_customer_with_truck_and_exclude(message_text, user_context, chat_id)
                if result:
                    return result
                else:
                    return f"🔍 Truck '{message_text}' not found in recent customer trips. Please first search for a customer name, then exclude trucks from those results."
            
            # If not a truck identifier, try customer lookup
            return self._handle_potential_customer_lookup(message_text, user_context, chat_id)
            
        except Exception as e:
            logger.error(f"Error in potential customer lookup or truck exclusion: {e}")
            return "❌ Error processing your request. Please try again."

    def _find_customer_with_truck_and_exclude(self, truck_identifier, user_context, chat_id):
        """Find customer that has this truck and exclude it (cache-free approach)"""
        try:
            from .models import Customer, Trip
            
            truck_identifier = truck_identifier.upper()
            print(f"🔥 DEBUG: Looking for customers with truck: {truck_identifier}")
            
            # Find customers that have trips with this truck in the last 30 days
            recent_trips_with_truck = Trip.objects.filter(
                vehicle__plate_number__icontains=truck_identifier.replace(" ", "")
            ).select_related('customer', 'vehicle').order_by('-loading_date')[:50]
            
            print(f"🔥 DEBUG: Found {len(recent_trips_with_truck)} recent trips with similar truck")
            
            # Group by customer and find the most recent customer
            customer_last_trip = {}
            for trip in recent_trips_with_truck:
                if trip.customer.id not in customer_last_trip or trip.loading_date > customer_last_trip[trip.customer.id]['date']:
                    customer_last_trip[trip.customer.id] = {
                        'customer': trip.customer,
                        'date': trip.loading_date,
                        'truck': trip.vehicle.plate_number
                    }
            
            if customer_last_trip:
                # Get the most recent customer
                most_recent = max(customer_last_trip.values(), key=lambda x: x['date'])
                customer = most_recent['customer']
                
                print(f"🔥 DEBUG: Most recent customer with this truck: {customer.name}")
                
                # Show their trips with this truck excluded
                excluded_trucks = {truck_identifier}
                return self._show_customer_trips(customer, user_context, excluded_trucks, chat_id)
            
            return None
            
        except Exception as e:
            logger.error(f"Error finding customer with truck: {e}")
            return None

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
❓ Use <code>/help</code> for all commands"""
                
        except Exception as e:
            logger.error(f"Error in potential customer lookup: {e}")
            return "❌ Error searching for customer. Please try again."
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
❓ Use <code>/help</code> for all commands"""
                
        except Exception as e:
            logger.error(f"Error in potential customer lookup: {e}")
            return "❌ Error searching for customer. Please try again."

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
                        return f"❌ Please enter a number between 1 and {len(matches)}."
                
                # Try to match by exact name
                for match in matches:
                    if match['name'].lower() == message_text.lower():
                        selected_customer = Customer.objects.get(id=match['id'])
                        self._clear_customer_trips_state(chat_id)
                        return self._show_customer_trips(selected_customer, self.get_user_context(chat_id), set(), chat_id)
                
                return "❌ Please select a customer by typing the number or exact name from the list above."
            
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
                        return f"❌ Truck '{message_text}' not found in current trip results. Please check the truck identifier and try again."
                else:
                    # Not a truck identifier - try new customer search
                    self._clear_customer_trips_state(chat_id)
                    return self._handle_potential_customer_lookup(message_text, self.get_user_context(chat_id), chat_id)
            
            else:
                return "❌ Unknown state. Please use /cancel and try again."
                
        except Exception as e:
            logger.error(f"Error handling customer trips input: {e}")
            print(f"🔥 DEBUG: Error in customer trips input: {e}")
            self._clear_customer_trips_state(chat_id)
            return "❌ Error processing your request. Please try again."

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
            return "❌ Error retrieving customer trips. Please try again."

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
            return "❌ Error retrieving trips information."

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
            return "❌ Error retrieving shipments information."

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
                
            # Also check if any cache keys exist for this user
            try:
                from django.core.cache import cache as django_cache
                # Try to get some insight into cache state
                print(f"🔥 DEBUG: Cache backend type: {type(cache)}")
            except Exception as cache_debug_error:
                print(f"🔥 DEBUG: Cache debug error: {cache_debug_error}")
                
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
                
            # Also try with a test key to see if cache is working at all
            test_key = f"test_{chat_id}"
            cache.set(test_key, "test_value", timeout=60)
            test_result = cache.get(test_key)
            print(f"🔥 DEBUG: Cache test - stored and retrieved: {test_result == 'test_value'}")
                
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
            return "❌ Error processing loading authority PDF."

    def _initiate_tr830_processing(self, chat_id, file_content, filename, user_context):
        """Initiate TR830 document processing"""
        try:
            if not self.tr830_parser:
                return "❌ TR830 parser not available. Please contact administrator."
            
            # Parse the TR830 document first
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp_file:
                tmp_file.write(file_content)
                tmp_file.flush()
                temp_path = tmp_file.name
            
            try:
                # Parse the TR830 document using existing parser method
                import_date, entries = self.tr830_parser.parse_pdf(temp_path)
                
                if not entries:
                    return "❌ No shipment data found in the TR830 document. Please check the file format."
                
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
            return f"❌ Error processing TR830 document: {str(e)}"

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
            return f"❌ Error creating shipment: {str(e)}\n\nPlease try the process again or contact support."

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