# shipments/management/commands/process_status_emails.py
import imaplib
import email
from email.header import decode_header
from email.utils import parsedate_to_datetime # For parsing email date
import re
from decimal import Decimal # Not directly used here, but was in original

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from django.core.exceptions import ValidationError

from shipments.models import Trip # Only Trip needed for this command directly

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
                except LookupError: # Handle unknown encoding
                    header_parts.append(part.decode('utf-8', 'ignore'))
            else:
                header_parts.append(part)
        return "".join(header_parts)

    def get_email_body(self, msg):
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition"))
                if "attachment" not in content_disposition: # Ignore attachments
                    if content_type == "text/plain":
                        try:
                            charset = part.get_content_charset() or 'utf-8'
                            body = part.get_payload(decode=True).decode(charset, 'ignore')
                            break # Prefer plain text
                        except Exception as e:
                            self.stdout.write(self.style.ERROR(f"Error decoding plain text part: {e}"))
                    elif content_type == "text/html":
                        # Fallback to HTML if plain text not found or fails
                        if not body: # Only if plain text body is still empty
                            try:
                                charset = part.get_content_charset() or 'utf-8'
                                body_html = part.get_payload(decode=True).decode(charset, 'ignore')
                                body = body_html # Use HTML as fallback
                            except Exception as e:
                                self.stdout.write(self.style.ERROR(f"Error decoding HTML part: {e}"))
        else: # Not a multipart message, just a single part
            try:
                charset = msg.get_content_charset() or 'utf-8'
                body = msg.get_payload(decode=True).decode(charset, 'ignore')
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error decoding non-multipart body: {e}"))
        return body.strip()

    def parse_kpc_order_number(self, text_content):
        # Look for "loading order S<numbers>"
        match = re.search(r"loading\s+order\s+(S\d+)", text_content, re.IGNORECASE)
        if match:
            return match.group(1).upper().strip()
        return None

    def parse_status_and_comment(self, text_content):
        status_map = {
            # Order matters: more specific patterns first if overlap is possible
            r"has\s+been\s+REJECTED": 'KPC_REJECTED',
            r"has\s+been\s+APPROVED": 'KPC_APPROVED',
            # KPC typo "gate passsed" and correct "gate passed"
            r"has\s+been\s+gate\s+passsed": 'GATEPASSED',
            r"has\s+been\s+gate\s+passed": 'GATEPASSED',
            r"has\s+exited\s+KPC\s+depot": 'DELIVERED', # This sets to DELIVERED based on original logic.
                                                       # Consider if IN_TRANSIT is more appropriate if final customer delivery is a separate step.
            r"details\s+update\s+has\s+been\s+APPROVED": 'KPC_APPROVED', # Could be a more specific status if needed
        }

        comment = ""
        parsed_status = None

        for pattern, trip_status_value in status_map.items():
            status_match = re.search(pattern, text_content, re.IGNORECASE)
            if status_match:
                parsed_status = trip_status_value
                # Try to extract comment based on typical KPC email structure
                comment_patterns = [
                    r"Approval/Rejection Comment:\s*(.*)", # For REJECTED/APPROVED
                    r"Gate Pass Comment:\s*(.*)",          # For GATEPASSED
                    r"Exit Gate Comment:\s*(.*)"           # For EXITED
                ]
                for comment_pattern_re in comment_patterns:
                    # Using re.DOTALL to allow .* to match newlines, then take first line
                    comment_match = re.search(comment_pattern_re, text_content, re.IGNORECASE | re.DOTALL)
                    if comment_match and comment_match.group(1).strip():
                        # Take only the first line of the comment
                        comment = comment_match.group(1).strip().split('\n')[0].strip()
                        break # Found a comment for this status
                break # Found a status
        
        return parsed_status, comment

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Starting to process KPC status update emails..."))
        try:
            mail = imaplib.IMAP4_SSL(settings.EMAIL_PROCESSING_HOST, settings.EMAIL_PROCESSING_PORT)
            mail.login(settings.EMAIL_PROCESSING_USER, settings.EMAIL_PROCESSING_PASSWORD)
            mail.select(settings.EMAIL_PROCESSING_MAILBOX)
            self.stdout.write(self.style.SUCCESS(f"Connected to mailbox: {settings.EMAIL_PROCESSING_MAILBOX}"))

            status_update_sender = getattr(settings, 'EMAIL_STATUS_UPDATE_SENDER_FILTER', None)
            if not status_update_sender:
                self.stdout.write(self.style.ERROR("Setting EMAIL_STATUS_UPDATE_SENDER_FILTER is not defined."))
                mail.logout()
                return

            search_criteria = f'(UNSEEN FROM "{status_update_sender}")'
            status_search, message_ids_bytes = mail.search(None, search_criteria)

            if status_search != 'OK':
                self.stdout.write(self.style.ERROR(f"Error searching status emails: {message_ids_bytes[0].decode()}"))
                mail.logout()
                return

            email_ids_list = message_ids_bytes[0].split()
            if not email_ids_list or email_ids_list == [b'']:
                self.stdout.write(self.style.WARNING(f"No new unread status emails from sender '{status_update_sender}'."))
                mail.logout()
                return

            self.stdout.write(self.style.SUCCESS(f"Found {len(email_ids_list)} new status email(s) from '{status_update_sender}' to process."))

            for email_id_bytes in email_ids_list:
                email_id_str = email_id_bytes.decode()
                self.stdout.write(f"\nProcessing status email ID: {email_id_str}...")
                status_fetch, msg_data = mail.fetch(email_id_bytes, "(RFC822)")
                if status_fetch != 'OK':
                    self.stdout.write(self.style.ERROR(f"  Error fetching status email ID {email_id_str}"))
                    continue

                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        msg = email.message_from_bytes(response_part[1])
                        email_subject = self.decode_email_header(msg["subject"])
                        email_from = self.decode_email_header(msg["from"])
                        email_date_str = msg.get('date')
                        email_datetime = parsedate_to_datetime(email_date_str) if email_date_str else timezone.now()

                        self.stdout.write(f"  From: {email_from}, Subject: '{email_subject}', Date: {email_datetime.strftime('%Y-%m-%d %H:%M')}")

                        body = self.get_email_body(msg)
                        if not body:
                            self.stdout.write(self.style.WARNING("  Could not extract text body. Skipping."))
                            # mail.store(email_id_bytes, '+FLAGS', '\\Seen') # Mark as seen if problematic
                            continue
                        
                        kpc_order_no = self.parse_kpc_order_number(body)
                        if not kpc_order_no:
                            self.stdout.write(self.style.WARNING(f"  Could not parse KPC Order Number from email body. Subject: '{email_subject}'. Skipping."))
                            # mail.store(email_id_bytes, '+FLAGS', '\\Seen')
                            continue
                        self.stdout.write(f"  Parsed KPC Order No: {kpc_order_no}")

                        parsed_status, parsed_comment = self.parse_status_and_comment(body)
                        if not parsed_status:
                            self.stdout.write(self.style.WARNING(f"  Could not determine status update from email body for order {kpc_order_no}. Skipping."))
                            # mail.store(email_id_bytes, '+FLAGS', '\\Seen')
                            continue
                        
                        self.stdout.write(f"  Parsed Status: {parsed_status}, Comment: '{parsed_comment[:50].replace('\n', ' ')}...'")

                        try:
                            trip_to_update = Trip.objects.select_for_update().get(kpc_order_number__iexact=kpc_order_no)
                            
                            if trip_to_update.status == parsed_status:
                                self.stdout.write(self.style.NOTICE(f"  Trip {trip_to_update.id} for order {kpc_order_no} is already in status '{parsed_status}'. No update needed."))
                            else:
                                original_trip_status_for_log = trip_to_update.status
                                trip_to_update.status = parsed_status
                                if parsed_comment:
                                    new_comment_entry = f"Status update on {timezone.now().strftime('%Y-%m-%d %H:%M')}: {parsed_comment}"
                                    trip_to_update.kpc_comments = f"{new_comment_entry}\n{trip_to_update.kpc_comments or ''}".strip()
                                
                                trip_to_update.save() # This will trigger perform_stock_depletion if status becomes KPC_APPROVED
                                self.stdout.write(self.style.SUCCESS(f"  Successfully updated Trip {trip_to_update.id} (Order: {kpc_order_no}) status from '{original_trip_status_for_log}' to '{parsed_status}'."))
                        
                        except Trip.DoesNotExist:
                            self.stdout.write(self.style.ERROR(f"  Trip with KPC Order No '{kpc_order_no}' not found in database. Email Subject: '{email_subject}'"))
                        except ValidationError as e:
                            self.stdout.write(self.style.ERROR(f"  Validation Error updating Trip {kpc_order_no}: {e}"))
                        except Exception as e:
                            self.stdout.write(self.style.ERROR(f"  Unexpected error updating Trip {kpc_order_no}: {e}"))
                        
                        # Mark email as seen after processing attempt (successful or known error like Trip.DoesNotExist)
                        # mail.store(email_id_bytes, '+FLAGS', '\\Seen')

            mail.logout()
            self.stdout.write(self.style.SUCCESS("Finished processing KPC status update emails."))

        except imaplib.IMAP4.error as e_imap:
            self.stdout.write(self.style.ERROR(f"IMAP Error: {e_imap}"))
        except Exception as e_global:
            self.stdout.write(self.style.ERROR(f"An unexpected global error occurred: {e_global}"))