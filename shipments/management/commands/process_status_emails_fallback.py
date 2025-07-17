# shipments/management/commands/process_status_emails_fallback.py
import imaplib
import email
import re
from email.header import decode_header
from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from shipments.models import Trip
from shipments.utils.ai_order_matcher import get_trip_with_smart_matching
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Fallback processor for SEEN KPC status emails from the previous day'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days-back',
            type=int,
            default=1,
            help='Number of days back to search for emails (default: 1)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be processed without actually processing'
        )

    def handle(self, *args, **options):
        days_back = options['days_back']
        dry_run = options['dry_run']
        
        if dry_run:
            self.stdout.write("🔍 DRY RUN MODE - No actual processing will occur")
        
        self.stdout.write(f"🔄 Starting FALLBACK KPC status email processing (last {days_back} day(s))...")
        logger.info(f"Status Email Fallback: Cycle Started (days_back={days_back}, dry_run={dry_run}).")

        try:
            mail = imaplib.IMAP4_SSL(settings.EMAIL_PROCESSING_HOST, settings.EMAIL_PROCESSING_PORT)
            mail.login(settings.EMAIL_PROCESSING_USER, settings.EMAIL_PROCESSING_PASSWORD)
            mail.select(settings.EMAIL_PROCESSING_MAILBOX)

            # Calculate date for searching (yesterday by default)
            search_date = (datetime.now() - timedelta(days=days_back)).strftime('%d-%b-%Y')

            # Production KPC status email filter - but search for SEEN emails
            status_sender_filter = getattr(settings, 'EMAIL_STATUS_UPDATE_SENDER_FILTER', 'truckloading@kpc.co.ke')
            search_criteria = f'(SEEN FROM "{status_sender_filter}" SUBJECT "Loading Order" SINCE "{search_date}")'

            self.stdout.write(self.style.NOTICE(f"📧 Searching SEEN status emails from: {status_sender_filter}"))
            self.stdout.write(f"📅 Date range: {search_date} onwards")
            logger.info(f"Status Email Fallback: Searching with criteria: {search_criteria}")

            status, messages = mail.search(None, search_criteria)

            if status != 'OK':
                self.stdout.write(self.style.ERROR("Failed to search for status emails"))
                logger.error("Status Email Fallback: Failed to search emails.")
                mail.logout()
                return

            email_ids_list = messages[0].split()
            if not email_ids_list:
                self.stdout.write("✅ No SEEN status emails found for the specified date range.")
                logger.info("Status Email Fallback: No SEEN status emails found.")
                mail.logout()
                return

            self.stdout.write(self.style.SUCCESS(f"📬 Found {len(email_ids_list)} SEEN status email(s) to review."))
            logger.info(f"Status Email Fallback: Found {len(email_ids_list)} SEEN status email(s).")

            processed_successfully_ids = []

            for email_id_bytes in email_ids_list:
                email_id_str = email_id_bytes.decode()
                self.stdout.write(f"\n📋 Reviewing status email ID: {email_id_str}...")
                logger.info(f"Status Email Fallback: ----- Start processing email ID {email_id_str} -----")
                kpc_order_no = "UNKNOWN_ORDER"
                
                if dry_run:
                    self.stdout.write(self.style.WARNING(f"  🔍 DRY RUN: Would process status email"))
                    processed_successfully_ids.append(email_id_bytes)
                    continue
                    
                try:
                    with transaction.atomic():
                        status_fetch, msg_data = mail.fetch(email_id_bytes, "(RFC822)")
                        if status_fetch != 'OK':
                            self.stdout.write(self.style.ERROR(f"  Error fetching status email ID {email_id_str}"))
                            logger.error(f"Status Email Fallback: Error fetching email ID {email_id_str}.")
                            continue
                        msg = None
                        for response_part in msg_data:
                            if isinstance(response_part, tuple): 
                                msg = email.message_from_bytes(response_part[1])
                                break
                        if not msg:
                            self.stdout.write(self.style.ERROR(f"  Could not extract message object for email ID {email_id_str}"))
                            logger.error(f"Status Email Fallback: Could not extract message for email ID {email_id_str}.")
                            continue

                        email_subject = self.decode_email_header(msg["subject"])
                        email_date = self.decode_email_header(msg["date"])
                        self.stdout.write(f"  📧 Subject: '{email_subject}'")
                        self.stdout.write(f"  📅 Date: {email_date}")
                        logger.info(f"Status Email Fallback: Email Subject '{email_subject}' for ID {email_id_str}.")

                        body = self.get_email_body(msg)
                        if not body:
                            self.stdout.write(self.style.WARNING("  Could not extract text body. Skipping."))
                            logger.warning(f"Status Email Fallback: No text body for email ID {email_id_str}.")
                            continue

                        kpc_order_no = self.parse_kpc_order_number(body)
                        if not kpc_order_no:
                            self.stdout.write(self.style.WARNING(f"  Could not parse KPC Order Number from email body. Subject: '{email_subject}'. Skipping."))
                            logger.warning(f"Status Email Fallback: No KPC Order No from email ID {email_id_str}, Subject: {email_subject}.")
                            continue

                        self.stdout.write(f"  🔢 KPC Order No: {kpc_order_no}")

                        parsed_status = self.parse_status_from_body(body)
                        if not parsed_status:
                            self.stdout.write(self.style.WARNING(f"  Could not parse status from email body for order {kpc_order_no}. Skipping."))
                            logger.warning(f"Status Email Fallback: No status parsed from email ID {email_id_str}.")
                            continue

                        self.stdout.write(f"  📊 Parsed Status: {parsed_status}")

                        parsed_comment = self.parse_comment_from_body(body)
                        if parsed_comment:
                            self.stdout.write(f"  💬 Comment: {parsed_comment}")

                        # Use AI matching to find the trip
                        smart_result = get_trip_with_smart_matching(kpc_order_no)
                        if not smart_result:
                            self.stdout.write(self.style.ERROR(f"  Trip with KPC Order Number {kpc_order_no} not found even with AI matching. Subject: '{email_subject}'"))
                            logger.error(f"Status Email Fallback: Trip for KPC Order Number {kpc_order_no} not found even with AI matching. Email ID {email_id_str}.")
                            continue

                        trip_to_update, matching_metadata = smart_result

                        # Log the matching details for audit trail
                        if matching_metadata['correction_method'] != 'exact_match':
                            self.stdout.write(self.style.WARNING(f"  🤖 AI CORRECTION: '{matching_metadata['original_order']}' → '{matching_metadata['corrected_order']}'"))
                            self.stdout.write(f"     Method: {matching_metadata['correction_method']}, Confidence: {matching_metadata['confidence']:.2f}")
                            logger.info(f"Status Email Fallback: AI corrected '{matching_metadata['original_order']}' to '{matching_metadata['corrected_order']}' via {matching_metadata['correction_method']} (confidence: {matching_metadata['confidence']:.2f})")

                        # Get fresh instance with select_for_update
                        trip_to_update = Trip.objects.select_for_update().get(pk=trip_to_update.pk)

                        if trip_to_update.status == parsed_status:
                            self.stdout.write(self.style.NOTICE(f"  ✅ Trip {trip_to_update.id} (Order: {kpc_order_no}) already in status '{parsed_status}'. No update."))
                            logger.info(f"Status Email Fallback: Trip {trip_to_update.id} already in status {parsed_status}.")
                        else:
                            original_trip_status_for_log = trip_to_update.status
                            trip_to_update.status = parsed_status
                            if parsed_comment:
                                new_comment_entry = f"FALLBACK Status update on {timezone.now().strftime('%Y-%m-%d %H:%M')}: {parsed_comment}"
                                trip_to_update.kpc_comments = f"{new_comment_entry}\n{trip_to_update.kpc_comments or ''}".strip()

                            trip_to_update.save(stdout_writer=self) # Pass command instance

                            self.stdout.write(self.style.SUCCESS(f"  ✅ FALLBACK: Updated Trip {trip_to_update.id} (Order: {kpc_order_no}) from '{original_trip_status_for_log}' to '{parsed_status}'."))
                            logger.info(f"Status Email Fallback: Trip {trip_to_update.id} updated from {original_trip_status_for_log} to {parsed_status}.")

                        processed_successfully_ids.append(email_id_bytes)

                except ValidationError as e:
                    self.stdout.write(self.style.ERROR(f"  VALIDATION ERROR updating Trip {kpc_order_no}: {e}"))
                    logger.error(f"Status Email Fallback: ValidationError for Trip {kpc_order_no} (Email ID {email_id_str}): {e}", exc_info=True)
                except IntegrityError as e:
                    self.stdout.write(self.style.ERROR(f"  DATABASE INTEGRITY ERROR for Trip {kpc_order_no}: {e}"))
                    logger.error(f"Status Email Fallback: IntegrityError for Trip {kpc_order_no} (Email ID {email_id_str}): {e}", exc_info=True)
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"  UNEXPECTED ERROR updating Trip {kpc_order_no}: {e}"))
                    logger.error(f"Status Email Fallback: Unexpected error for Trip {kpc_order_no} (Email ID {email_id_str}): {e}", exc_info=True)
                finally:
                    logger.info(f"Status Email Fallback: ----- Finished processing email ID {email_id_str} -----")

            # Summary - NO EMAIL MARKING since these are fallback
            self.stdout.write(f"\n📊 FALLBACK PROCESSING SUMMARY:")
            self.stdout.write(f"  📬 Total emails reviewed: {len(email_ids_list)}")
            self.stdout.write(f"  ✅ Successfully processed: {len(processed_successfully_ids)}")
            self.stdout.write(f"  ❌ Failed to process: {len(email_ids_list) - len(processed_successfully_ids)}")

            if not dry_run:
                self.stdout.write(f"📧 Processed {len(processed_successfully_ids)} status email(s) in fallback mode.")
                logger.info(f"Status Email Fallback: Processed {len(processed_successfully_ids)} emails successfully.")
            else:
                logger.info(f"Status Email Fallback: DRY RUN completed - {len(processed_successfully_ids)} emails would be processed.")

            mail.logout()
            self.stdout.write(self.style.SUCCESS("✅ Finished FALLBACK processing of KPC status emails."))
            logger.info("Status Email Fallback: Cycle Ended.")
            
        except imaplib.IMAP4.error as e_imap:
            self.stdout.write(self.style.ERROR(f"IMAP Error: {e_imap}"))
            logger.critical(f"Status Email Fallback: IMAP Error: {e_imap}", exc_info=True)
        except Exception as e_global:
            self.stdout.write(self.style.ERROR(f"An unexpected global error: {e_global}"))
            logger.critical(f"Status Email Fallback: Global unexpected error: {e_global}", exc_info=True)

    # EXACT SAME UTILITY METHODS FROM ORIGINAL
    def decode_email_header(self, header):
        if header is None: 
            return ""
        decoded_parts = decode_header(header)
        header_parts = []
        for part, charset in decoded_parts:
            if isinstance(part, bytes):
                try: 
                    header_parts.append(part.decode(charset or 'utf-8', 'ignore'))
                except LookupError: 
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
                if "attachment" not in content_disposition:
                    if content_type == "text/plain":
                        try:
                            charset = part.get_content_charset() or 'utf-8'
                            body = part.get_payload(decode=True).decode(charset, 'ignore')
                            break
                        except Exception as e: 
                            self.stdout.write(self.style.ERROR(f"Error decoding plain text part: {e}"))
                    elif content_type == "text/html":
                        if not body:
                            try:
                                charset = part.get_content_charset() or 'utf-8'
                                body_html = part.get_payload(decode=True).decode(charset, 'ignore')
                                body = body_html
                            except Exception as e: 
                                self.stdout.write(self.style.ERROR(f"Error decoding HTML part: {e}"))
        else:
            try:
                charset = msg.get_content_charset() or 'utf-8'
                body = msg.get_payload(decode=True).decode(charset, 'ignore')
            except Exception as e: 
                self.stdout.write(self.style.ERROR(f"Error decoding email body: {e}"))
        return body

    def parse_kpc_order_number(self, body):
        # Using same patterns as original
        patterns = [
            r'Order\s*No[:\s]+([A-Z0-9\-/]+)',
            r'Order\s*Number[:\s]+([A-Z0-9\-/]+)',
            r'KPC\s*Order[:\s]+([A-Z0-9\-/]+)',
            r'Loading\s*Order[:\s]+([A-Z0-9\-/]+)',
            r'Order[:\s]+([A-Z0-9\-/]+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, body, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def parse_status_from_body(self, body):
        # Using same patterns as original  
        if re.search(r'loading\s+started|commenced|begun', body, re.IGNORECASE):
            return 'LOADING'
        elif re.search(r'loading\s+completed|finished|done', body, re.IGNORECASE):
            return 'LOADED'
        elif re.search(r'departed|left|dispatched', body, re.IGNORECASE):
            return 'DEPARTED'
        elif re.search(r'arrived|reached|destination', body, re.IGNORECASE):
            return 'ARRIVED'
        elif re.search(r'confirmed|approved|ready', body, re.IGNORECASE):
            return 'CONFIRMED'
        return None

    def parse_comment_from_body(self, body):
        # Using same patterns as original
        comment_patterns = [
            r'Comment[:\s]+(.+?)(?:\n|$)',
            r'Note[:\s]+(.+?)(?:\n|$)',
            r'Remarks[:\s]+(.+?)(?:\n|$)',
        ]
        
        for pattern in comment_patterns:
            match = re.search(pattern, body, re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1).strip()
        return None

    def write(self, msg):
        """Allow this command to be used as stdout_writer for trip methods"""
        self.stdout.write(msg)