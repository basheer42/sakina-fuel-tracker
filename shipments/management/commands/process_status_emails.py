# shipments/management/commands/process_status_emails.py
import imaplib
import email
import re
from email.header import decode_header
from django.core.management.base import BaseCommand
from django.conf import settings  # <-- This line was missing!
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from shipments.models import Trip
from shipments.utils.ai_order_matcher import get_trip_with_smart_matching
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Process KPC status update emails and update trip statuses'

    def handle(self, *args, **options):
        self.stdout.write("Starting KPC status email processing...")
        logger.info("Status Email Processing: Cycle Started.")

        try:
            mail = imaplib.IMAP4_SSL(settings.EMAIL_PROCESSING_HOST, settings.EMAIL_PROCESSING_PORT)
            mail.login(settings.EMAIL_PROCESSING_USER, settings.EMAIL_PROCESSING_PASSWORD)
            mail.select(settings.EMAIL_PROCESSING_MAILBOX)

            # Production KPC status email filter
            status_sender_filter = getattr(settings, 'EMAIL_STATUS_UPDATE_SENDER_FILTER', 'truckloading@kpc.co.ke')
            search_criteria = f'(UNSEEN FROM "{status_sender_filter}" SUBJECT "Loading Order")'

            self.stdout.write(self.style.NOTICE(f"Searching for status emails from: {status_sender_filter}"))
            logger.info(f"Status Email Processing: Searching with criteria: {search_criteria}")

            status, messages = mail.search(None, search_criteria)

            if status != 'OK':
                self.stdout.write(self.style.ERROR("Failed to search for status emails"))
                logger.error("Status Email Processing: Failed to search emails.")
                mail.logout(); return

            email_ids_list = messages[0].split()
            if not email_ids_list:
                self.stdout.write("No new status emails found.")
                logger.info("Status Email Processing: No new status emails found.")
                mail.logout(); return

            self.stdout.write(self.style.SUCCESS(f"Found {len(email_ids_list)} new status email(s) matching criteria to process."))
            logger.info(f"Status Email Processing: Found {len(email_ids_list)} new status email(s).")

            processed_successfully_ids = []

            for email_id_bytes in email_ids_list:
                email_id_str = email_id_bytes.decode()
                self.stdout.write(f"\nProcessing status email ID: {email_id_str}...")
                logger.info(f"Status Email Processing: ----- Start processing email ID {email_id_str} -----")
                kpc_order_no = "UNKNOWN_ORDER"
                try:
                    with transaction.atomic():
                        status_fetch, msg_data = mail.fetch(email_id_bytes, "(RFC822)")
                        if status_fetch != 'OK':
                            self.stdout.write(self.style.ERROR(f"  Error fetching status email ID {email_id_str}"))
                            logger.error(f"Status Email Processing: Error fetching email ID {email_id_str}.")
                            continue
                        msg = None
                        for response_part in msg_data:
                            if isinstance(response_part, tuple): msg = email.message_from_bytes(response_part[1]); break
                        if not msg:
                            self.stdout.write(self.style.ERROR(f"  Could not extract message object for email ID {email_id_str}"))
                            logger.error(f"Status Email Processing: Could not extract message for email ID {email_id_str}.")
                            continue

                        email_subject = self.decode_email_header(msg["subject"])
                        self.stdout.write(f"  Subject: '{email_subject}'")
                        logger.info(f"Status Email Processing: Email Subject '{email_subject}' for ID {email_id_str}.")

                        body = self.get_email_body(msg)
                        if not body:
                            self.stdout.write(self.style.WARNING("  Could not extract text body. Skipping."))
                            logger.warning(f"Status Email Processing: No text body for email ID {email_id_str}.")
                            continue

                        kpc_order_no = self.parse_kpc_order_number(body)
                        if not kpc_order_no:
                            self.stdout.write(self.style.WARNING(f"  Could not parse KPC Order Number from email body. Subject: '{email_subject}'. Skipping."))
                            logger.warning(f"Status Email Processing: No KPC Order No from email ID {email_id_str}, Subject: {email_subject}.")
                            continue
                        self.stdout.write(f"  Parsed KPC Order No: {kpc_order_no}")

                        parsed_status, parsed_comment = self.parse_status_and_comment(body)
                        if not parsed_status:
                            self.stdout.write(self.style.WARNING(f"  Could not determine status update for order {kpc_order_no}. Skipping."))
                            logger.warning(f"Status Email Processing: Could not determine status for KPC Order No {kpc_order_no}, Email ID {email_id_str}.")
                            continue
                        # Fix the f-string issue by creating a variable for the comment preview
                        comment_preview = parsed_comment[:50].replace('\n', ' ') if parsed_comment else 'No comment'
                        self.stdout.write(f"  Parsed Status: {parsed_status}, Comment: '{comment_preview}...'")

                        # 🤖 ENHANCED: Use AI-powered fuzzy matching
                        smart_result = get_trip_with_smart_matching(kpc_order_no)
                        if not smart_result:
                            self.stdout.write(self.style.ERROR(f"  Trip with KPC Order Number '{kpc_order_no}' not found (tried AI matching). Email Subject: '{email_subject}'"))
                            logger.error(f"Status Email Processing: Trip for KPC Order Number {kpc_order_no} not found even with AI matching. Email ID {email_id_str}.")
                            continue

                        trip_to_update, matching_metadata = smart_result

                        # Log the matching details for audit trail
                        if matching_metadata['correction_method'] != 'exact_match':
                            self.stdout.write(self.style.WARNING(f"  🤖 AI CORRECTION: '{matching_metadata['original_order']}' → '{matching_metadata['corrected_order']}'"))
                            self.stdout.write(f"     Method: {matching_metadata['correction_method']}, Confidence: {matching_metadata['confidence']:.2f}")
                            logger.info(f"Status Email Processing: AI corrected '{matching_metadata['original_order']}' to '{matching_metadata['corrected_order']}' via {matching_metadata['correction_method']} (confidence: {matching_metadata['confidence']:.2f})")

                        # Get fresh instance with select_for_update
                        trip_to_update = Trip.objects.select_for_update().get(pk=trip_to_update.pk)

                        if trip_to_update.status == parsed_status:
                            self.stdout.write(self.style.NOTICE(f"  Trip {trip_to_update.id} (Order: {kpc_order_no}) already in status '{parsed_status}'. No update."))
                            logger.info(f"Status Email Processing: Trip {trip_to_update.id} already in status {parsed_status}.")
                        else:
                            original_trip_status_for_log = trip_to_update.status
                            trip_to_update.status = parsed_status
                            if parsed_comment:
                                new_comment_entry = f"Status update on {timezone.now().strftime('%Y-%m-%d %H:%M')}: {parsed_comment}"
                                trip_to_update.kpc_comments = f"{new_comment_entry}\n{trip_to_update.kpc_comments or ''}".strip()

                            trip_to_update.save(stdout_writer=self) # Pass command instance

                            self.stdout.write(self.style.SUCCESS(f"  Successfully updated Trip {trip_to_update.id} (Order: {kpc_order_no}) from '{original_trip_status_for_log}' to '{parsed_status}'."))
                            logger.info(f"Status Email Processing: Trip {trip_to_update.id} updated from {original_trip_status_for_log} to {parsed_status}.")

                        processed_successfully_ids.append(email_id_bytes)

                except ValidationError as e:
                    self.stdout.write(self.style.ERROR(f"  VALIDATION ERROR updating Trip {kpc_order_no}: {e}"))
                    logger.error(f"Status Email Processing: ValidationError for Trip {kpc_order_no} (Email ID {email_id_str}): {e}", exc_info=True)
                except IntegrityError as e:
                    self.stdout.write(self.style.ERROR(f"  DATABASE INTEGRITY ERROR for Trip {kpc_order_no}: {e}"))
                    logger.error(f"Status Email Processing: IntegrityError for Trip {kpc_order_no} (Email ID {email_id_str}): {e}", exc_info=True)
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"  UNEXPECTED ERROR updating Trip {kpc_order_no}: {e}"))
                    logger.error(f"Status Email Processing: Unexpected error for Trip {kpc_order_no} (Email ID {email_id_str}): {e}", exc_info=True)
                finally:
                    logger.info(f"Status Email Processing: ----- Finished processing email ID {email_id_str} -----")

            if processed_successfully_ids:
                for email_id_bytes_seen in processed_successfully_ids:
                    try:
                        mail.store(email_id_bytes_seen, '+FLAGS', '\\Seen')
                        logger.info(f"Status Email Processing: Marked email ID {email_id_bytes_seen.decode()} as Seen.")
                    except Exception as e_seen:
                        logger.error(f"Status Email Processing: Failed to mark email ID {email_id_bytes_seen.decode()} as Seen: {e_seen}")
                self.stdout.write(self.style.SUCCESS(f"Marked {len(processed_successfully_ids)} status email(s) as seen."))

            mail.logout()
            self.stdout.write(self.style.SUCCESS("Finished processing KPC status update emails."))
            logger.info("Status Email Processing: Cycle Ended.")
        except imaplib.IMAP4.error as e_imap:
            self.stdout.write(self.style.ERROR(f"IMAP Error: {e_imap}"))
            logger.critical(f"Status Email Processing: IMAP Error: {e_imap}", exc_info=True)
        except Exception as e_global:
            self.stdout.write(self.style.ERROR(f"An unexpected global error: {e_global}"))
            logger.critical(f"Status Email Processing: Global unexpected error: {e_global}", exc_info=True)

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

    def parse_kpc_order_number(self, text_content):
        match = re.search(r"loading\s+order\s+(S\d+)", text_content, re.IGNORECASE)
        if match: return match.group(1).upper().strip()
        return None

    def parse_status_and_comment(self, text_content):
        status_map = {
            r"has\s+been\s+REJECTED": 'KPC_REJECTED',
            r"has\s+been\s+APPROVED": 'KPC_APPROVED',
            r"has\s+been\s+gate\s+passsed": 'GATEPASSED',
            r"has\s+been\s+gate\s+passed": 'GATEPASSED',
            r"has\s+exited\s+KPC\s+depot": 'DELIVERED',
            r"details\s+update\s+has\s+been\s+APPROVED": 'KPC_APPROVED',
        }
        comment = ""
        parsed_status = None
        for pattern, trip_status_value in status_map.items():
            status_match = re.search(pattern, text_content, re.IGNORECASE)
            if status_match:
                parsed_status = trip_status_value
                comment_patterns = [
                    r"Approval/Rejection Comment:\s*(.*?)(?=\n\n|\nFrom:|\nSent:|\nTo:|\Z)",
                    r"Comment:\s*(.*?)(?=\n\n|\nFrom:|\nSent:|\nTo:|\Z)",
                    r"Reason:\s*(.*?)(?=\n\n|\nFrom:|\nSent:|\nTo:|\Z)"
                ]
                for comment_pattern in comment_patterns:
                    comment_match = re.search(comment_pattern, text_content, re.IGNORECASE | re.DOTALL)
                    if comment_match:
                        comment = comment_match.group(1).strip()
                        break
                break
        return parsed_status, comment