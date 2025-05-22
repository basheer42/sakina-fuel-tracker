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

from shipments.models import Trip # Only Trip needed for this command directly

class Command(BaseCommand):
    help = 'Fetches and processes KPC status update emails to update Trip records.'

    def decode_email_header(self, header):
        if header is None: return ""
        decoded_parts = decode_header(header)
        header_parts = []
        for part, charset in decoded_parts:
            if isinstance(part, bytes):
                try: header_parts.append(part.decode(charset or 'utf-8', 'ignore'))
                except LookupError: header_parts.append(part.decode('utf-8', 'ignore'))
            else: header_parts.append(part)
        return "".join(header_parts)

    def get_email_body(self, msg):
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
                        try:
                            charset = part.get_content_charset() or 'utf-8'
                            body_html = part.get_payload(decode=True).decode(charset, 'ignore')
                            if not body: body = body_html 
                        except Exception as e: self.stdout.write(self.style.ERROR(f"Error decoding HTML part: {e}"))
        else: 
            try:
                charset = msg.get_content_charset() or 'utf-8'
                body = msg.get_payload(decode=True).decode(charset, 'ignore')
            except Exception as e: self.stdout.write(self.style.ERROR(f"Error decoding non-multipart body: {e}"))
        return body.strip()

    def parse_kpc_order_number(self, text_content):
        match = re.search(r"loading\s+order\s+(S\d+)", text_content, re.IGNORECASE)
        if match: return match.group(1).upper().strip()
        return None

    # CORRECTED parse_status_and_comment function
    def parse_status_and_comment(self, text_content):
        status_map = {
            r"has\s+been\s+REJECTED": 'KPC_REJECTED',
            r"has\s+been\s+APPROVED": 'KPC_APPROVED',
            r"has\s+been\s+gate\s+passsed": 'GATEPASSED', # Typo from KPC
            r"has\s+been\s+gate\s+passed": 'GATEPASSED',
            r"has\s+exited\s+KPC\s+depot": 'DELIVERED',
            r"details\s+update\s+has\s+been\s+APPROVED": 'KPC_APPROVED', # Or a more specific status if needed
        }
        
        comment = ""
        parsed_status = None

        for pattern, trip_status_value in status_map.items():
            status_match = re.search(pattern, text_content, re.IGNORECASE)
            if status_match:
                parsed_status = trip_status_value
                comment_patterns = [
                    r"Approval/Rejection Comment:\s*(.*)",
                    r"Gate Pass Comment:\s*(.*)",
                    r"Exit Gate Comment:\s*(.*)"
                ]
                for comment_pattern_re in comment_patterns: # renamed pattern to comment_pattern_re
                    comment_match = re.search(comment_pattern_re, text_content, re.IGNORECASE | re.DOTALL)
                    if comment_match and comment_match.group(1).strip():
                        comment = comment_match.group(1).strip().split('\n')[0].strip()
                        break 
                break 
        
        return parsed_status, comment

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Starting to process KPC status update emails..."))
        try:
            mail = imaplib.IMAP4_SSL(settings.EMAIL_PROCESSING_HOST, settings.EMAIL_PROCESSING_PORT)
            mail.login(settings.EMAIL_PROCESSING_USER, settings.EMAIL_PROCESSING_PASSWORD)
            mail.select(settings.EMAIL_PROCESSING_MAILBOX)
            self.stdout.write(self.style.SUCCESS(f"Connected to mailbox: {settings.EMAIL_PROCESSING_MAILBOX}"))

            search_criteria = f'(UNSEEN FROM "{settings.EMAIL_PROCESSING_SENDER_FILTER}")'
            status_search, message_ids = mail.search(None, search_criteria) # Renamed status to status_search

            if status_search != 'OK':
                self.stdout.write(self.style.ERROR(f"Error searching emails: {message_ids[0].decode()}"))
                return

            email_ids = message_ids[0].split()
            if not email_ids:
                self.stdout.write(self.style.WARNING("No new unread emails from specified sender."))
                mail.logout()
                return

            self.stdout.write(self.style.SUCCESS(f"Found {len(email_ids)} new email(s) to process."))

            for email_id in email_ids:
                self.stdout.write(f"\nProcessing email ID: {email_id.decode()}...")
                status_fetch, msg_data = mail.fetch(email_id, "(RFC822)") # Renamed status to status_fetch
                if status_fetch != 'OK':
                    self.stdout.write(self.style.ERROR(f"Error fetching email ID {email_id.decode()}"))
                    continue

                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        msg = email.message_from_bytes(response_part[1])
                        email_subject = self.decode_email_header(msg["subject"])
                        self.stdout.write(f"  Subject: {email_subject}")

                        body = self.get_email_body(msg)
                        if not body: self.stdout.write(self.style.WARNING("  Could not extract text body.")); continue
                        
                        kpc_order_no = self.parse_kpc_order_number(body)
                        if not kpc_order_no: self.stdout.write(self.style.WARNING(f"  Could not parse KPC Order Number from email body.")); continue
                        self.stdout.write(f"  Parsed KPC Order No: {kpc_order_no}")

                        parsed_status, parsed_comment = self.parse_status_and_comment(body)
                        if not parsed_status: self.stdout.write(self.style.WARNING(f"  Could not determine status update from email body for order {kpc_order_no}.")); continue
                        self.stdout.write(f"  Parsed Status: {parsed_status}, Comment: '{parsed_comment[:50]}...'")

                        try:
                            trip_to_update = Trip.objects.get(kpc_order_number__iexact=kpc_order_no) 
                            
                            if trip_to_update.status == parsed_status:
                                self.stdout.write(self.style.NOTICE(f"  Trip {trip_to_update.id} for order {kpc_order_no} is already in status '{parsed_status}'. No update needed."))
                            else:
                                original_trip_status_for_log = trip_to_update.status
                                trip_to_update.status = parsed_status
                                if parsed_comment:
                                    trip_to_update.kpc_comments = (trip_to_update.kpc_comments or "") + f"\nUpdate on {timezone.now().strftime('%Y-%m-%d %H:%M')}: {parsed_comment}"
                                
                                trip_to_update.save() 
                                self.stdout.write(self.style.SUCCESS(f"  Successfully updated Trip {trip_to_update.id} (Order: {kpc_order_no}) status from '{original_trip_status_for_log}' to '{parsed_status}'."))
                        
                        except Trip.DoesNotExist:
                            self.stdout.write(self.style.ERROR(f"  Trip with KPC Order No '{kpc_order_no}' not found in database."))
                        except ValidationError as e:
                            self.stdout.write(self.style.ERROR(f"  Validation Error updating Trip {kpc_order_no}: {e}"))
                        except Exception as e:
                            self.stdout.write(self.style.ERROR(f"  Unexpected error updating Trip {kpc_order_no}: {e}"))
                        
                        # mail.store(email_id, '+FLAGS', '\\Seen') 

            mail.logout()
            self.stdout.write(self.style.SUCCESS("Finished processing emails."))

        except imaplib.IMAP4.error as e: self.stdout.write(self.style.ERROR(f"IMAP Error: {e}"))
        except Exception as e: self.stdout.write(self.style.ERROR(f"An unexpected error occurred: {e}"))