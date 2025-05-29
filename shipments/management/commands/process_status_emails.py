# shipments/management/commands/process_status_emails.py
import imaplib
import email
from email.header import decode_header
from email.utils import parsedate_to_datetime 
import re
from decimal import Decimal 

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.utils import timezone
from django.db import transaction, IntegrityError # Added IntegrityError
from django.core.exceptions import ValidationError

from shipments.models import Trip 

class Command(BaseCommand):
    help = 'Fetches and processes KPC status update emails to update Trip records.'

    def decode_email_header(self, header): # Unchanged
        if header is None: return ""
        decoded_parts = decode_header(header)
        header_parts = []
        for part, charset in decoded_parts:
            if isinstance(part, bytes):
                try: header_parts.append(part.decode(charset or 'utf-8', 'ignore'))
                except LookupError: header_parts.append(part.decode('utf-8', 'ignore'))
            else: header_parts.append(part)
        return "".join(header_parts)

    def get_email_body(self, msg): # Unchanged
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition"))
                if "attachment" not in content_disposition: 
                    if content_type == "text/plain":
                        try:
                            charset = part.get_content_charset() or 'utf-8'
                            body = part.get_payload(decode=True).decode(charset, 'ignore')
                            break 
                        except Exception as e: self.stdout.write(self.style.ERROR(f"Error decoding plain text part: {e}"))
                    elif content_type == "text/html":
                        if not body: 
                            try:
                                charset = part.get_content_charset() or 'utf-8'
                                body_html = part.get_payload(decode=True).decode(charset, 'ignore')
                                body = body_html 
                            except Exception as e: self.stdout.write(self.style.ERROR(f"Error decoding HTML part: {e}"))
        else: 
            try:
                charset = msg.get_content_charset() or 'utf-8'
                body = msg.get_payload(decode=True).decode(charset, 'ignore')
            except Exception as e: self.stdout.write(self.style.ERROR(f"Error decoding non-multipart body: {e}"))
        return body.strip()

    def parse_kpc_order_number(self, text_content): # Unchanged
        match = re.search(r"loading\s+order\s+(S\d+)", text_content, re.IGNORECASE)
        if match: return match.group(1).upper().strip()
        return None

    def parse_status_and_comment(self, text_content): # Unchanged
        status_map = {
            r"has\s+been\s+REJECTED": 'KPC_REJECTED', r"has\s+been\s+APPROVED": 'KPC_APPROVED',
            r"has\s+been\s+gate\s+passsed": 'GATEPASSED', r"has\s+been\s+gate\s+passed": 'GATEPASSED',
            r"has\s+exited\s+KPC\s+depot": 'DELIVERED', r"details\s+update\s+has\s+been\s+APPROVED": 'KPC_APPROVED',}
        comment = ""; parsed_status = None
        for pattern, trip_status_value in status_map.items():
            status_match = re.search(pattern, text_content, re.IGNORECASE)
            if status_match:
                parsed_status = trip_status_value
                comment_patterns = [r"Approval/Rejection Comment:\s*(.*)", r"Gate Pass Comment:\s*(.*)", r"Exit Gate Comment:\s*(.*)"]
                for comment_pattern_re in comment_patterns:
                    comment_match = re.search(comment_pattern_re, text_content, re.IGNORECASE | re.DOTALL)
                    if comment_match and comment_match.group(1).strip():
                        comment = comment_match.group(1).strip().split('\n')[0].strip(); break 
                break 
        return parsed_status, comment

    # @transaction.atomic # Removed from here, transaction handled per email
    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Starting to process KPC status update emails..."))
        try:
            mail = imaplib.IMAP4_SSL(settings.EMAIL_PROCESSING_HOST, settings.EMAIL_PROCESSING_PORT)
            mail.login(settings.EMAIL_PROCESSING_USER, settings.EMAIL_PROCESSING_PASSWORD)
            mail.select(settings.EMAIL_PROCESSING_MAILBOX)
            self.stdout.write(self.style.SUCCESS(f"Connected to mailbox: {settings.EMAIL_PROCESSING_MAILBOX}"))

            status_update_sender = getattr(settings, 'EMAIL_STATUS_UPDATE_SENDER_FILTER', None)
            if not status_update_sender:
                self.stdout.write(self.style.ERROR("Setting EMAIL_STATUS_UPDATE_SENDER_FILTER is not defined.")); mail.logout(); return

            search_criteria = f'(UNSEEN FROM "{status_update_sender}" NOT SUBJECT "BoL")'
            self.stdout.write(self.style.NOTICE(f"Searching for status emails with criteria: {search_criteria}"))
            status_search, message_ids_bytes = mail.search(None, search_criteria)

            if status_search != 'OK':
                self.stdout.write(self.style.ERROR(f"Error searching status emails: {message_ids_bytes[0].decode()}")); mail.logout(); return

            email_ids_list = message_ids_bytes[0].split()
            if not email_ids_list or email_ids_list == [b'']:
                self.stdout.write(self.style.WARNING(f"No new unread status emails matching criteria from sender '{status_update_sender}'.")); mail.logout(); return

            self.stdout.write(self.style.SUCCESS(f"Found {len(email_ids_list)} new status email(s) matching criteria to process."))

            for email_id_bytes in email_ids_list:
                email_id_str = email_id_bytes.decode()
                self.stdout.write(f"\nProcessing status email ID: {email_id_str}...")
                try:
                    with transaction.atomic(): # Transaction per email
                        status_fetch, msg_data = mail.fetch(email_id_bytes, "(RFC822)")
                        if status_fetch != 'OK': self.stdout.write(self.style.ERROR(f"  Error fetching status email ID {email_id_str}")); continue
                        msg = None
                        for response_part in msg_data:
                            if isinstance(response_part, tuple): msg = email.message_from_bytes(response_part[1]); break
                        if not msg: self.stdout.write(self.style.ERROR(f"  Could not extract message object for email ID {email_id_str}")); continue
                        email_subject = self.decode_email_header(msg["subject"]); self.stdout.write(f"  Subject: '{email_subject}'")
                        body = self.get_email_body(msg)
                        if not body: self.stdout.write(self.style.WARNING("  Could not extract text body. Skipping.")); continue
                        kpc_order_no = self.parse_kpc_order_number(body)
                        if not kpc_order_no: self.stdout.write(self.style.WARNING(f"  Could not parse KPC Order Number from email body. Subject: '{email_subject}'. Skipping.")); continue
                        self.stdout.write(f"  Parsed KPC Order No: {kpc_order_no}")
                        parsed_status, parsed_comment = self.parse_status_and_comment(body)
                        if not parsed_status: self.stdout.write(self.style.WARNING(f"  Could not determine status update for order {kpc_order_no}. Skipping.")); continue
                        self.stdout.write(f"  Parsed Status: {parsed_status}, Comment: '{parsed_comment[:50].replace('\n', ' ')}...'")
                        
                        trip_to_update = Trip.objects.select_for_update().get(kpc_order_number__iexact=kpc_order_no)
                        if trip_to_update.status == parsed_status:
                            self.stdout.write(self.style.NOTICE(f"  Trip {trip_to_update.id} (Order: {kpc_order_no}) already in status '{parsed_status}'. No update."))
                        else:
                            original_trip_status_for_log = trip_to_update.status
                            trip_to_update.status = parsed_status
                            if parsed_comment:
                                new_comment_entry = f"Status update on {timezone.now().strftime('%Y-%m-%d %H:%M')}: {parsed_comment}"
                                trip_to_update.kpc_comments = f"{new_comment_entry}\n{trip_to_update.kpc_comments or ''}".strip()
                            trip_to_update.save(stdout=self.stdout) # Pass stdout
                            self.stdout.write(self.style.SUCCESS(f"  Successfully updated Trip {trip_to_update.id} (Order: {kpc_order_no}) from '{original_trip_status_for_log}' to '{parsed_status}'."))
                        # Mark as seen after successful processing within the transaction
                        # mail.store(email_id_bytes, '+FLAGS', '\\Seen')
                except Trip.DoesNotExist: self.stdout.write(self.style.ERROR(f"  Trip with KPC Order No '{kpc_order_no}' not found. Email Subject: '{email_subject}'")) # mail.store(...) if desired
                except ValidationError as e: self.stdout.write(self.style.ERROR(f"  VALIDATION ERROR updating Trip {kpc_order_no}: {e}")) # Transaction will rollback
                except IntegrityError as e: self.stdout.write(self.style.ERROR(f"  DATABASE INTEGRITY ERROR for Trip {kpc_order_no}: {e}")) # Transaction will rollback
                except Exception as e: self.stdout.write(self.style.ERROR(f"  UNEXPECTED ERROR updating Trip {kpc_order_no}: {e}")) # Transaction will rollback

            mail.logout()
            self.stdout.write(self.style.SUCCESS("Finished processing KPC status update emails."))
        except imaplib.IMAP4.error as e_imap: self.stdout.write(self.style.ERROR(f"IMAP Error: {e_imap}"))
        except Exception as e_global: self.stdout.write(self.style.ERROR(f"An unexpected global error: {e_global}"))