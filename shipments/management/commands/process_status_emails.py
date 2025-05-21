# shipments/management/commands/process_status_emails.py
import imaplib
import email
from email.header import decode_header
import re
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from django.core.exceptions import ValidationError

from shipments.models import Trip, Product, Customer, Vehicle, Destination, LoadingCompartment # Assuming models are in 'shipments' app

class Command(BaseCommand):
    help = 'Fetches and processes KPC status update emails to update Trip records.'

    def decode_email_header(self, header):
        if header is None:
            return ""
        decoded_parts = decode_header(header)
        header_parts = []
        for part, charset in decoded_parts:
            if isinstance(part, bytes):
                try:
                    header_parts.append(part.decode(charset or 'utf-8', 'ignore'))
                except LookupError: # Unknown encoding
                    header_parts.append(part.decode('utf-8', 'ignore')) # Fallback
            else:
                header_parts.append(part)
        return "".join(header_parts)

    def get_email_body(self, msg):
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition"))
                if "attachment" not in content_disposition: # not an attachment
                    if content_type == "text/plain":
                        try:
                            charset = part.get_content_charset() or 'utf-8'
                            body = part.get_payload(decode=True).decode(charset, 'ignore')
                            break # Take the first plain text part
                        except Exception as e:
                            self.stdout.write(self.style.ERROR(f"Error decoding plain text part: {e}"))
                    elif content_type == "text/html": # Fallback to HTML if plain text fails or not found
                        try:
                            charset = part.get_content_charset() or 'utf-8'
                            # For now, just decode HTML, actual parsing of HTML can be complex
                            body_html = part.get_payload(decode=True).decode(charset, 'ignore')
                            if not body: # Only use HTML if plain text body is still empty
                                body = body_html # In a real scenario, you'd strip HTML tags
                                # For now, we'll hope the regexes work on raw HTML text too
                        except Exception as e:
                            self.stdout.write(self.style.ERROR(f"Error decoding HTML part: {e}"))
        else: # Not multipart
            try:
                charset = msg.get_content_charset() or 'utf-8'
                body = msg.get_payload(decode=True).decode(charset, 'ignore')
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error decoding non-multipart body: {e}"))
        return body.strip()

    def parse_kpc_order_number(self, text_content):
        # Looks for "Loading order S02083E", "Loading order S02097R", "loading order S02092"
        # Extracts "S" followed by digits
        match = re.search(r"loading\s+order\s+(S\d+)", text_content, re.IGNORECASE)
        if match:
            return match.group(1).upper() # e.g., S02083 (strips trailing letters implicitly)
        return None

    def parse_status_and_comment(self, text_content):
        status_map = {
            'REJECTED': 'KPC_REJECTED',
            'APPROVED': 'KPC_APPROVED', # This will trigger perform_stock_depletion via Trip.save()
            'GATE PASSSSED': 'GATEPASSED', # Handle typo
            'GATE PASSED': 'GATEPASSED',
            'EXITED KPC DEPOT': 'DELIVERED' # Changed from TRANSIT to DELIVERED as per your request
        }
        comment = ""
        parsed_status = None

        for keyword, trip_status in status_map.items():
            # Regex to find "has been KEYWORD" or "has KEYWORD"
            status_match = re.search(rf"has\s+(?:been\s+)?({re.escape(keyword)})", text_content, re.IGNORECASE)
            if status_match:
                parsed_status = trip_status
                # Try to get comment after status line
                comment_match_approval = re.search(r"Approval/Rejection Comment:\s*(.*)", text_content, re.IGNORECASE | re.DOTALL)
                comment_match_gatepass = re.search(r"Gate Pass Comment:\s*(.*)", text_content, re.IGNORECASE | re.DOTALL)
                comment_match_exit = re.search(r"Exit Gate Comment:\s*(.*)", text_content, re.IGNORECASE | re.DOTALL)

                if comment_match_approval:
                    comment = comment_match_approval.group(1).strip().split('\n')[0].strip() # Take first line of comment
                elif comment_match_gatepass:
                    comment = comment_match_gatepass.group(1).strip().split('\n')[0].strip()
                elif comment_match_exit:
                    comment = comment_match_exit.group(1).strip().split('\n')[0].strip()
                break
        
        return parsed_status, comment

    @transaction.atomic # Process all emails in a single transaction, or one transaction per email
    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Starting to process KPC status update emails..."))

        try:
            mail = imaplib.IMAP4_SSL(settings.EMAIL_PROCESSING_HOST, settings.EMAIL_PROCESSING_PORT)
            mail.login(settings.EMAIL_PROCESSING_USER, settings.EMAIL_PROCESSING_PASSWORD)
            mail.select(settings.EMAIL_PROCESSING_MAILBOX) # e.g., 'INBOX'
            self.stdout.write(self.style.SUCCESS(f"Connected to mailbox: {settings.EMAIL_PROCESSING_MAILBOX}"))

            # Search for unread emails from the specific sender
            # Note: Date filtering can be added: '(UNSEEN SINCE "01-Jan-2024" FROM "{settings.EMAIL_PROCESSING_SENDER_FILTER}")'
            search_criteria = f'(UNSEEN FROM "{settings.EMAIL_PROCESSING_SENDER_FILTER}")'
            status, message_ids = mail.search(None, search_criteria)

            if status != 'OK':
                self.stdout.write(self.style.ERROR(f"Error searching for emails: {message_ids[0].decode()}"))
                return

            email_ids = message_ids[0].split()
            if not email_ids:
                self.stdout.write(self.style.WARNING("No new unread emails found from specified sender."))
                return

            self.stdout.write(self.style.SUCCESS(f"Found {len(email_ids)} new email(s) to process."))

            for email_id in email_ids:
                self.stdout.write(f"\nProcessing email ID: {email_id.decode()}...")
                status, msg_data = mail.fetch(email_id, "(RFC822)")
                if status != 'OK':
                    self.stdout.write(self.style.ERROR(f"Error fetching email ID {email_id.decode()}"))
                    continue

                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        msg = email.message_from_bytes(response_part[1])
                        email_subject = self.decode_email_header(msg["subject"])
                        email_from = self.decode_email_header(msg["from"])
                        email_date = self.decode_email_header(msg["date"]) # Parsed later if needed

                        self.stdout.write(f"  From: {email_from}")
                        self.stdout.write(f"  Subject: {email_subject}")

                        body = self.get_email_body(msg)
                        if not body:
                            self.stdout.write(self.style.WARNING("  Could not extract text body from email."))
                            continue
                        
                        # self.stdout.write(f"  Body Preview: {body[:300]}...") # Debug: print body preview

                        kpc_order_no = self.parse_kpc_order_number(body)
                        if not kpc_order_no:
                            self.stdout.write(self.style.WARNING(f"  Could not parse KPC Order Number from email body."))
                            continue
                        
                        self.stdout.write(f"  Parsed KPC Order No: {kpc_order_no}")

                        parsed_status, parsed_comment = self.parse_status_and_comment(body)
                        if not parsed_status:
                            self.stdout.write(self.style.WARNING(f"  Could not determine status update from email body for order {kpc_order_no}."))
                            continue

                        self.stdout.write(f"  Parsed Status: {parsed_status}, Comment: '{parsed_comment[:50]}...'")

                        try:
                            trip_to_update = Trip.objects.get(bol_number__iexact=kpc_order_no) # Case-insensitive match
                            
                            # Check if this status update is new or redundant
                            if trip_to_update.status == parsed_status:
                                self.stdout.write(self.style.NOTICE(f"  Trip {trip_to_update.id} for order {kpc_order_no} is already in status '{parsed_status}'. No update needed."))
                            else:
                                original_trip_status_for_log = trip_to_update.status
                                trip_to_update.status = parsed_status
                                if parsed_comment:
                                    trip_to_update.kpc_comments = (trip_to_update.kpc_comments or "") + f"\nUpdate on {timezone.now().strftime('%Y-%m-%d %H:%M')}: {parsed_comment}"
                                
                                trip_to_update.save() # This will trigger the perform_stock_depletion if status becomes KPC_APPROVED
                                self.stdout.write(self.style.SUCCESS(f"  Successfully updated Trip {trip_to_update.id} (Order: {kpc_order_no}) status from '{original_trip_status_for_log}' to '{parsed_status}'."))
                        
                        except Trip.DoesNotExist:
                            self.stdout.write(self.style.ERROR(f"  Trip with KPC Order No '{kpc_order_no}' not found in database."))
                        except ValidationError as e: # Catch validation errors from Trip.save() or perform_stock_depletion()
                            self.stdout.write(self.style.ERROR(f"  Validation Error updating Trip {kpc_order_no}: {e}"))
                            # The trip status might have been reverted by the model's save method if depletion failed
                        except Exception as e:
                            self.stdout.write(self.style.ERROR(f"  Unexpected error updating Trip {kpc_order_no}: {e}"))
                        
                        # Mark email as read (seen) - for production, might move to processed folder
                        # mail.store(email_id, '+FLAGS', '\\Seen')

            mail.logout()
            self.stdout.write(self.style.SUCCESS("Finished processing emails."))

        except imaplib.IMAP4.error as e:
            self.stdout.write(self.style.ERROR(f"IMAP Error: {e}"))
            self.stdout.write(self.style.ERROR("Ensure EMAIL_PROCESSING settings are correct in settings.py and the mail server is accessible."))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"An unexpected error occurred: {e}"))