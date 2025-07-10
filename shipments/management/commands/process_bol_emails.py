# shipments/management/commands/process_bol_emails.py

import imaplib
import email
from email.header import decode_header
from email.utils import parsedate_to_datetime
import os
import tempfile
import re
from decimal import Decimal, InvalidOperation
import datetime
import logging 

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.utils import timezone
from django.db import transaction, IntegrityError
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile

from shipments.models import Trip, LoadingCompartment, Shipment, ShipmentDepletion, Product, Customer, Vehicle, Destination
from shipments.utils.ai_order_matcher import get_trip_with_smart_matching
import pdfplumber

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Fetches and processes KPC Bill of Lading (BoL) PDF emails to update Trip records and reconcile stock.'

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

    def get_pdf_attachment_from_email(self, msg):
        pdf_attachment_bytes = None
        attachment_filename = None
        for part in msg.walk():
            content_disposition = str(part.get("Content-Disposition"))
            content_type = part.get_content_type()
            if "attachment" in content_disposition and content_type == "application/pdf":
                attachment_filename_raw = part.get_filename()
                if attachment_filename_raw: attachment_filename = self.decode_email_header(attachment_filename_raw)
                else: attachment_filename = f"bol_{timezone.now().strftime('%Y%m%d%H%M%S')}.pdf"
                pdf_attachment_bytes = part.get_payload(decode=True)
                if pdf_attachment_bytes:
                    self.stdout.write(self.style.SUCCESS(f"  Found PDF attachment: {attachment_filename} ({len(pdf_attachment_bytes)} bytes)"))
                    logger.info(f"BoL Email Processing: Found PDF attachment: {attachment_filename}")
                    return attachment_filename, pdf_attachment_bytes
        self.stdout.write(self.style.WARNING("  No PDF attachment found in the email."))
        logger.warning("BoL Email Processing: No PDF attachment found in an email.")
        return None, None

    def parse_bol_pdf_data(self, pdf_content, original_pdf_filename="unknown.pdf"):
        extracted_data = {}; tmp_pdf_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(pdf_content); tmp_pdf_path = tmp.name

            with pdfplumber.open(tmp_pdf_path) as pdf:
                if not pdf.pages:
                    self.stdout.write(self.style.ERROR(f"    No pages in PDF '{original_pdf_filename}'."))
                    logger.error(f"BoL PDF Parsing: No pages in '{original_pdf_filename}'.")
                    return None

                full_text = ''; all_tables = []
                for page in pdf.pages:
                    page_text = page.extract_text() or ""
                    full_text += page_text + "\n"
                    page_tables = page.extract_tables()
                    if page_tables: all_tables.extend(page_tables)

                self.stdout.write(f"    Extracted {len(full_text)} characters of text from '{original_pdf_filename}'.")
                logger.info(f"BoL PDF Parsing: Extracted {len(full_text)} chars from '{original_pdf_filename}'.")

                # Parse LOADING ORDER NUMBER
                cleaned_full_text_for_lon = re.sub(r'\s+', ' ', full_text)
                lon_patterns = [
                    r"(?:KPC\s+)?Loading\s*(?:Order\s*)?(?:No\.?|NUMBER)?\s*[:\-]?\s*(S\d{5,7}\b)", 
                    r"Loading\s*Order\s*(?:No\.?|Number)?\s*[:\-]?\s*(S\d{5,7}\b)",
                    r"Order\s*No\s*[:\-]?\s*(S\d{5,7}\b)",
                    r"\b(S\d{5,7})\b" 
                ]
                found_lon_in_doc = None
                for pattern in lon_patterns:
                    match = re.search(pattern, cleaned_full_text_for_lon, re.IGNORECASE)
                    if match:
                        lon_candidate = match.group(1) if len(match.groups()) > 0 and match.group(1) else match.group(0)
                        if lon_candidate and lon_candidate.upper().startswith('S') and lon_candidate[1:].isdigit():
                            found_lon_in_doc = lon_candidate.upper()
                            self.stdout.write(self.style.SUCCESS(f"    Found LON '{found_lon_in_doc}' in document text using pattern: {pattern}"))
                            logger.info(f"BoL PDF Parsing: Found LON '{found_lon_in_doc}' in document text for '{original_pdf_filename}'.")
                            extracted_data['kpc_loading_order_no'] = found_lon_in_doc
                            break
                
                shipment_no_match = re.search(r"Shipment\s*(?:No\.?|Number)?\s*[:\-]?\s*(\d+)", full_text, re.IGNORECASE)
                if shipment_no_match: extracted_data['kpc_shipment_no'] = shipment_no_match.group(1).strip()
                else: self.stdout.write(self.style.WARNING("    KPC Shipment No (BoL No) not found in header."))

                delivery_date_match = re.search(r"Delivery\s*Date\s*[:\-]?\s*(\d{1,2}[\.\/-]\d{1,2}[\.\/-]\d{2,4})", full_text, re.IGNORECASE)
                if delivery_date_match:
                    date_str = delivery_date_match.group(1)
                    for fmt in ('%d.%m.%Y', '%d/%m/%Y', '%d-%m-%Y', '%d.%m.%y', '%d/%m/%y', '%d-%m-%y'):
                        try: 
                            extracted_data['delivery_date'] = datetime.datetime.strptime(date_str, fmt).date(); break
                        except ValueError: continue
                    if not extracted_data['delivery_date']: self.stdout.write(self.style.WARNING(f"    Could not parse Delivery Date: {date_str}"))
                
                delivery_time_match = re.search(r"Delivery\s*Time\s*[:\-]?\s*(\d{1,2}:\d{2}(?::\d{2})?)", full_text, re.IGNORECASE)
                if delivery_time_match:
                    time_str = delivery_time_match.group(1)
                    for fmt in ('%H:%M:%S', '%H:%M'):
                        try: 
                            extracted_data['delivery_time'] = datetime.datetime.strptime(time_str, fmt).time(); break
                        except ValueError: continue
                    if not extracted_data['delivery_time']: self.stdout.write(self.style.WARNING(f"    Could not parse Delivery Time: {time_str}"))

                vehicle_no_match = re.search(r"Vehicle\s*(?:No\.?|Reg)?\s*[:\-]?\s*([A-Z0-9\s\/]+?)(?=\s|$)", full_text, re.IGNORECASE)
                if vehicle_no_match: extracted_data['vehicle_reg'] = vehicle_no_match.group(1).strip().upper()

                # Parse TABLE DATA for ACTUAL COMPARTMENTS
                if all_tables:
                    self.stdout.write(f"    Found {len(all_tables)} table(s) in PDF. Searching for compartment data...")
                    logger.info(f"BoL PDF Parsing: Found {len(all_tables)} table(s) in '{original_pdf_filename}'.")

                    header_found = False; actual_compartments = []; lon_from_first_valid_row = None
                    for table_idx, table in enumerate(all_tables):
                        if not table or len(table) < 2: continue
                        header_row = table[0] if table[0] else []
                        header_text = " ".join([str(cell or "").strip() for cell in header_row]).upper()

                        if any(keyword in header_text for keyword in ['LOAD', 'ORDER', 'COMPARTMENT', 'ACTUAL', 'QUANTITY']):
                            header_found = True
                            self.stdout.write(self.style.SUCCESS(f"    BoL Table {table_idx + 1} header identified: {header_text[:100]}..."))
                            logger.info(f"BoL PDF Parsing: Table {table_idx + 1} header found for '{original_pdf_filename}'.")

                            row_pattern_str = r"(\d+)\s+(S\d+)\s+.*?(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*$"
                            for row_idx, row in enumerate(table[1:], start=1):
                                if not row: continue
                                row_text = " ".join([str(cell or "").strip() for cell in row])
                                if not row_text.strip(): continue

                                row_match = re.search(row_pattern_str, row_text)
                                if row_match:
                                    comp_no_str, lon_cell, actual_qty_str = row_match.groups()
                                    if not lon_from_first_valid_row: lon_from_first_valid_row = lon_cell.upper()

                                    try:
                                        comp_no = int(comp_no_str.strip())
                                        actual_qty_cleaned = actual_qty_str.replace(',', '')
                                        actual_qty = Decimal(actual_qty_cleaned)
                                        actual_compartments.append({'compartment_number': comp_no, 'actual_quantity': actual_qty})
                                        self.stdout.write(f"      Compartment {comp_no}: {actual_qty}L (from row: {row_text[:50]}...)")
                                        logger.info(f"BoL PDF Parsing: Compartment {comp_no} = {actual_qty}L for '{original_pdf_filename}'.")
                                    except (ValueError, InvalidOperation) as e:
                                        self.stdout.write(self.style.WARNING(f"      Could not parse compartment data from row: {row_text[:50]}... Error: {e}"))
                                        logger.warning(f"BoL PDF Parsing: Parse error for row '{row_text}' in '{original_pdf_filename}': {e}")
                                else:
                                    logger.debug(f"BoL PDF Parsing: Row '{row_text}' did not match pattern for '{original_pdf_filename}'.")

                            if actual_compartments:
                                extracted_data['actual_compartments'] = actual_compartments
                                self.stdout.write(self.style.SUCCESS(f"    Successfully parsed {len(actual_compartments)} compartment(s) from table data."))
                                logger.info(f"BoL PDF Parsing: Parsed {len(actual_compartments)} compartments for '{original_pdf_filename}'.")
                            else:
                                self.stdout.write(self.style.WARNING(f"    No valid compartment rows found in table. Check PDF structure or regex."))
                                logger.warning(f"BoL PDF Parsing: No compartment data lines matched for '{original_pdf_filename}'. Regex: {row_pattern_str}")

                    if lon_from_first_valid_row and not extracted_data['kpc_loading_order_no']:
                        extracted_data['kpc_loading_order_no'] = lon_from_first_valid_row
                        self.stdout.write(self.style.NOTICE(f"    Used LON '{lon_from_first_valid_row}' from first table row as document-level LON was missing."))
                        logger.info(f"BoL PDF Parsing: Used LON '{lon_from_first_valid_row}' from table row for '{original_pdf_filename}'.")
                
                else: 
                    self.stdout.write(self.style.ERROR("    BoL Table header NOT FOUND. Cannot parse compartment details."))
                    logger.error(f"BoL PDF Parsing: Table header NOT found for '{original_pdf_filename}'.")

                if not extracted_data.get('kpc_loading_order_no'):
                    self.stdout.write(self.style.ERROR(f"    CRITICAL: KPC Loading Order Number (Sxxxxx) could NOT be determined for BoL '{original_pdf_filename}' after all parsing attempts."))
                    logger.critical(f"BoL PDF Parsing: KPC LON MISSING for '{original_pdf_filename}'. PDF text snippet:\n{full_text[:1000]}")
                    return None

        except Exception as e_parse: 
            self.stdout.write(self.style.ERROR(f"    General error during PDF parsing for '{original_pdf_filename}': {e_parse}"))
            logger.error(f"BoL PDF Parsing: General error for '{original_pdf_filename}': {e_parse}", exc_info=True)
            return None
        finally:
            if tmp_pdf_path and os.path.exists(tmp_pdf_path): 
                try: os.remove(tmp_pdf_path)
                except Exception as e_remove: logger.warning(f"Could not remove temp PDF {tmp_pdf_path}: {e_remove}")
        
        if not extracted_data.get('actual_compartments'):
            logger.warning(f"BoL PDF Parsing: No actual compartment data extracted for LON {extracted_data.get('kpc_loading_order_no')} in '{original_pdf_filename}'.")

        return extracted_data

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Starting to process KPC BoL PDF emails..."))
        logger.info("BoL Email Processing: Cycle Started.")
        try:
            mail = imaplib.IMAP4_SSL(settings.EMAIL_PROCESSING_HOST, settings.EMAIL_PROCESSING_PORT)
            mail.login(settings.EMAIL_PROCESSING_USER, settings.EMAIL_PROCESSING_PASSWORD)
            mail.select(settings.EMAIL_PROCESSING_MAILBOX)
            self.stdout.write(self.style.SUCCESS(f"Connected to mailbox: {settings.EMAIL_PROCESSING_MAILBOX}"))
            logger.info(f"BoL Email Processing: Connected to mailbox {settings.EMAIL_PROCESSING_MAILBOX}.")

            bol_sender_filter = getattr(settings, 'EMAIL_BOL_SENDER_FILTER', 'bolconfirmation@kpc.co.ke')
            if not bol_sender_filter:
                self.stdout.write(self.style.ERROR("Setting EMAIL_BOL_SENDER_FILTER is not defined.")); mail.logout(); return

            search_criteria = f'(UNSEEN FROM "{bol_sender_filter}" SUBJECT "BoL")' 
            self.stdout.write(self.style.NOTICE(f"Searching for BoL emails from: {bol_sender_filter}"))
            logger.info(f"BoL Email Processing: Searching with criteria: {search_criteria}")
            status_search, message_ids_bytes = mail.search(None, search_criteria)

            if status_search != 'OK':
                err_msg = message_ids_bytes[0].decode()
                self.stdout.write(self.style.ERROR(f"Error searching BoL emails: {err_msg}")); mail.logout()
                logger.error(f"BoL Email Processing: IMAP search error: {err_msg}")
                return

            email_ids_list = message_ids_bytes[0].split()
            if not email_ids_list or email_ids_list == [b'']:
                self.stdout.write(self.style.WARNING(f"No new unread BoL emails matching criteria from sender '{bol_sender_filter}'."))
                logger.info("BoL Email Processing: No new BoL emails found.")
                mail.logout(); return

            self.stdout.write(self.style.SUCCESS(f"Found {len(email_ids_list)} new BoL email(s) matching criteria to process."))
            logger.info(f"BoL Email Processing: Found {len(email_ids_list)} new BoL email(s).")

            processed_successfully_ids = [] 

            for email_id_bytes in email_ids_list:
                email_id_str = email_id_bytes.decode()
                self.stdout.write(f"\nProcessing BoL email ID: {email_id_str}...")
                logger.info(f"BoL Email Processing: ----- Start processing email ID {email_id_str} -----")
                kpc_lon_from_bol = "UNKNOWN_LON" 
                try:
                    with transaction.atomic():
                        status_fetch, msg_data = mail.fetch(email_id_bytes, "(RFC822)")
                        if status_fetch != 'OK': 
                            self.stdout.write(self.style.ERROR(f"  Error fetching BoL email ID {email_id_str}"))
                            logger.error(f"BoL Email Processing: Error fetching email ID {email_id_str}.")
                            continue
                        msg = None
                        for response_part in msg_data:
                            if isinstance(response_part, tuple): msg = email.message_from_bytes(response_part[1]); break
                        if not msg: 
                            self.stdout.write(self.style.ERROR(f"  Could not extract message object for email ID {email_id_str}"))
                            logger.error(f"BoL Email Processing: Could not extract message for email ID {email_id_str}.")
                            continue
                        
                        email_subject = self.decode_email_header(msg["subject"])
                        self.stdout.write(f"  Subject: '{email_subject}'")
                        logger.info(f"BoL Email Processing: Email Subject '{email_subject}' for ID {email_id_str}.")
                        
                        pdf_filename, pdf_content = self.get_pdf_attachment_from_email(msg)
                        if not pdf_content: 
                            self.stdout.write(self.style.WARNING(f"  No PDF attachment in email {email_id_str}. Skipping."))
                            logger.warning(f"BoL Email Processing: No PDF in email ID {email_id_str}. Skipping.")
                            continue 
                        
                        parsed_bol_data = self.parse_bol_pdf_data(pdf_content, original_pdf_filename=pdf_filename)
                        if not parsed_bol_data or not parsed_bol_data.get('kpc_loading_order_no'):
                            self.stdout.write(self.style.ERROR(f"  Failed to parse BoL PDF '{pdf_filename}' or missing critical LON. Skipping update."))
                            logger.error(f"BoL Email Processing: Failed parse or missing LON for PDF '{pdf_filename}' from email ID {email_id_str}.")
                            continue 
                        
                        kpc_lon_from_bol = parsed_bol_data['kpc_loading_order_no']
                        
                        # 🤖 ENHANCED: Use AI-powered fuzzy matching
                        smart_result = get_trip_with_smart_matching(kpc_lon_from_bol)
                        if not smart_result:
                            self.stdout.write(self.style.ERROR(f"  Trip with LON '{kpc_lon_from_bol}' not found (tried AI matching). PDF: '{pdf_filename}'"))
                            logger.error(f"BoL Email Processing: Trip for LON {kpc_lon_from_bol} not found even with AI matching. Email ID {email_id_str}.")
                            continue
                        
                        trip_to_update, matching_metadata = smart_result
                        
                        # Log the matching details for audit trail
                        if matching_metadata['correction_method'] != 'exact_match':
                            self.stdout.write(self.style.WARNING(f"  🤖 AI CORRECTION: '{matching_metadata['original_order']}' → '{matching_metadata['corrected_order']}'"))
                            self.stdout.write(f"     Method: {matching_metadata['correction_method']}, Confidence: {matching_metadata['confidence']:.2f}")
                            logger.info(f"BoL Email Processing: AI corrected '{matching_metadata['original_order']}' to '{matching_metadata['corrected_order']}' via {matching_metadata['correction_method']} (confidence: {matching_metadata['confidence']:.2f})")
                        
                        # Get fresh instance with select_for_update
                        trip_to_update = Trip.objects.select_for_update().get(pk=trip_to_update.pk)
                        
                        self.stdout.write(self.style.SUCCESS(f"  Found Trip ID {trip_to_update.id} for LON {kpc_lon_from_bol}."))
                        logger.info(f"BoL Email Processing: Matched Trip ID {trip_to_update.id} for LON {kpc_lon_from_bol}.")

                        if ShipmentDepletion.objects.filter(trip=trip_to_update).exists():
                            self.stdout.write(f"  Trip {trip_to_update.id} has existing depletions. Reversing them...")
                            logger.info(f"BoL Email Processing: Reversing existing depletions for Trip ID {trip_to_update.id} before BoL processing.")
                            
                            existing_depletions = ShipmentDepletion.objects.filter(trip=trip_to_update)
                            for depletion in existing_depletions:
                                shipment = depletion.shipment_batch
                                shipment.quantity_remaining += depletion.quantity_depleted
                                shipment.save()
                                self.stdout.write(f"    Restored {depletion.quantity_depleted}L to {shipment.vessel_id_tag}")
                                logger.info(f"BoL Email Processing: Restored {depletion.quantity_depleted}L to shipment {shipment.id}.")
                            
                            existing_depletions.delete()
                            self.stdout.write(f"  Reversed {existing_depletions.count()} existing depletions for Trip {trip_to_update.id}.")
                            logger.info(f"BoL Email Processing: Deleted existing depletions for Trip {trip_to_update.id}.")

                        # Update trip status to LOADED
                        original_status = trip_to_update.status
                        trip_to_update.status = 'LOADED'
                        
                        # Update actual compartment quantities if available
                        actual_compartments = parsed_bol_data.get('actual_compartments', [])
                        if actual_compartments:
                            self.stdout.write(f"  Updating {len(actual_compartments)} compartment actual quantities...")
                            for comp_data in actual_compartments:
                                comp_no = comp_data['compartment_number']
                                actual_qty = comp_data['actual_quantity']
                                
                                try:
                                    compartment = LoadingCompartment.objects.get(
                                        trip=trip_to_update, 
                                        compartment_number=comp_no
                                    )
                                    compartment.actual_quantity = actual_qty
                                    compartment.save()
                                    self.stdout.write(f"    Compartment {comp_no}: {actual_qty}L (was {compartment.allocated_quantity}L)")
                                    logger.info(f"BoL Email Processing: Updated compartment {comp_no} actual quantity to {actual_qty}L.")
                                except LoadingCompartment.DoesNotExist:
                                    self.stdout.write(self.style.WARNING(f"    Compartment {comp_no} not found for Trip {trip_to_update.id}"))
                                    logger.warning(f"BoL Email Processing: Compartment {comp_no} not found for Trip {trip_to_update.id}.")
                        else:
                            self.stdout.write(self.style.WARNING("  No actual compartment data found in BoL PDF"))
                            logger.warning(f"BoL Email Processing: No actual compartment data for Trip {trip_to_update.id}.")
                        
                        # Save trip with updated status and compartments
                        trip_to_update.save()
                        
                        self.stdout.write(self.style.SUCCESS(f"  Successfully updated Trip {trip_to_update.id} status from '{original_status}' to 'LOADED'"))
                        logger.info(f"BoL Email Processing: Updated Trip {trip_to_update.id} status from {original_status} to LOADED.")
                        
                        # Mark email as processed
                        processed_successfully_ids.append(email_id_bytes)

                except ValidationError as e: 
                    self.stdout.write(self.style.ERROR(f"  VALIDATION ERROR for BoL {kpc_lon_from_bol}: {e}"))
                    logger.error(f"BoL Email Processing: ValidationError for LON {kpc_lon_from_bol} (Email ID {email_id_str}): {e}", exc_info=True)
                except IntegrityError as e: 
                    self.stdout.write(self.style.ERROR(f"  DATABASE INTEGRITY ERROR for BoL {kpc_lon_from_bol}: {e}"))
                    logger.error(f"BoL Email Processing: IntegrityError for LON {kpc_lon_from_bol} (Email ID {email_id_str}): {e}", exc_info=True)
                except Exception as e: 
                    self.stdout.write(self.style.ERROR(f"  UNEXPECTED ERROR for BoL {kpc_lon_from_bol}: {e}"))
                    logger.error(f"BoL Email Processing: Unexpected error for LON {kpc_lon_from_bol} (Email ID {email_id_str}): {e}", exc_info=True)
                finally:
                    logger.info(f"BoL Email Processing: ----- Finished processing email ID {email_id_str} -----")

            # Mark processed emails as seen
            if processed_successfully_ids:
                for email_id_bytes_seen in processed_successfully_ids:
                    try:
                        mail.store(email_id_bytes_seen, '+FLAGS', '\\Seen')
                        logger.info(f"BoL Email Processing: Marked email ID {email_id_bytes_seen.decode()} as Seen.")
                    except Exception as e_seen:
                        logger.error(f"BoL Email Processing: Failed to mark email ID {email_id_bytes_seen.decode()} as Seen: {e_seen}")
                self.stdout.write(self.style.SUCCESS(f"Marked {len(processed_successfully_ids)} BoL email(s) as seen."))

            mail.logout()
            self.stdout.write(self.style.SUCCESS("Finished processing KPC BoL PDF emails."))
            logger.info("BoL Email Processing: Cycle Ended.")
            
        except imaplib.IMAP4.error as e_imap: 
            self.stdout.write(self.style.ERROR(f"IMAP Error: {e_imap}"))
            logger.critical(f"BoL Email Processing: IMAP Error: {e_imap}", exc_info=True)
        except Exception as e_global: 
            self.stdout.write(self.style.ERROR(f"An unexpected global error: {e_global}"))
            logger.critical(f"BoL Email Processing: Global unexpected error: {e_global}", exc_info=True)