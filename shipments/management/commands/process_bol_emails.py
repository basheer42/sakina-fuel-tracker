# shipments/management/commands/process_bol_emails.py
import imaplib
import email
import re
import tempfile
import os
import datetime
from decimal import Decimal, InvalidOperation
from email.header import decode_header
from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from shipments.models import Trip, LoadingCompartment
from shipments.utils.ai_order_matcher import get_trip_with_smart_matching
import logging
import pdfplumber

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Process KPC BoL PDF emails and update trip statuses and compartments'

    def extract_pdf_attachment(self, msg):
        """Extract PDF attachment from email message."""
        for part in msg.walk():
            if part.get_content_type() == "application/pdf":
                filename = part.get_filename()
                if filename and filename.lower().endswith('.pdf'):
                    pdf_content = part.get_payload(decode=True)
                    if pdf_content:
                        self.stdout.write(f"  Found PDF attachment: {filename} ({len(pdf_content)} bytes)")
                        logger.info(f"BoL Email Processing: Found PDF attachment: {filename}")
                        return pdf_content, filename
        
        self.stdout.write(self.style.WARNING("  No PDF attachment found in email"))
        logger.warning("BoL Email Processing: No PDF attachment found in an email.")
        return None, None

    def parse_bol_pdf_data(self, pdf_content, original_pdf_filename="unknown.pdf"):
        """Parse BoL PDF and extract compartment data."""
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
                    self.stdout.write(self.style.ERROR(f"    No pages in PDF '{original_pdf_filename}'."))
                    logger.error(f"BoL PDF Parsing: No pages in '{original_pdf_filename}'.")
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

                self.stdout.write(f"    Extracted {len(full_text)} characters of text from '{original_pdf_filename}'.")
                logger.info(f"BoL PDF Parsing: Extracted {len(full_text)} chars from '{original_pdf_filename}'.")

                # Parse LOADING ORDER NUMBER (LON)
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
                            self.stdout.write(f"    Found LON '{found_lon_in_doc}' in document text using pattern: {pattern}")
                            logger.info(f"BoL PDF Parsing: Found LON '{found_lon_in_doc}' in document text for '{original_pdf_filename}'.")
                            extracted_data['kpc_loading_order_no'] = found_lon_in_doc
                            break
                
                # Parse other fields
                shipment_no_match = re.search(r"Shipment\s*(?:No\.?|Number)?\s*[:\-]?\s*(\d+)", full_text, re.IGNORECASE)
                if shipment_no_match:
                    extracted_data['kpc_shipment_no'] = shipment_no_match.group(1).strip()
                else:
                    self.stdout.write(self.style.WARNING("    KPC Shipment No (BoL No) not found in header."))

                vehicle_no_match = re.search(r"Vehicle\s*(?:No\.?|Number)?\s*[:\-]?\s*([A-Z0-9]+(?:[/\\][A-Z0-9]+)?)", full_text, re.IGNORECASE)
                if vehicle_no_match:
                    extracted_data['vehicle_reg'] = vehicle_no_match.group(1).strip().upper()

                # DEBUG: Show table content
                if all_tables:
                    self.stdout.write("=== DEBUG: TABLE CONTENT ===")
                    for table_idx, table in enumerate(all_tables):
                        self.stdout.write(f"Table {table_idx + 1} ({len(table)} rows):")
                        for row_idx, row in enumerate(table[:8]):  # Show first 8 rows
                            if row:
                                row_text = " | ".join([str(cell or "").strip() for cell in row])
                                self.stdout.write(f"  Row {row_idx}: {row_text}")
                    self.stdout.write("=== END DEBUG ===")

                # Parse TABLE DATA for ACTUAL COMPARTMENTS
                if all_tables:
                    self.stdout.write(f"    Found {len(all_tables)} table(s) in PDF. Searching for compartment data...")
                    logger.info(f"BoL PDF Parsing: Found {len(all_tables)} table(s) in '{original_pdf_filename}'.")

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
                            self.stdout.write(f"    BoL Table {table_idx + 1} header identified: {header_text[:100]}...")
                            logger.info(f"BoL PDF Parsing: Table {table_idx + 1} header found for '{original_pdf_filename}'.")

                            # Regex to find compartment number followed by three quantity values
                            row_pattern = r"(\d+)\s+.*?(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s+(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s+(\d{1,3}(?:,\d{3})*(?:\.\d+)?)"

                            for row in table[1:]:  # Skip header
                                if not row:
                                    continue
                                    
                                row_text = " ".join([str(cell or "").strip() for cell in row])
                                
                                if re.search(r'\btotal\b', row_text, re.IGNORECASE):
                                    continue
                                    
                                self.stdout.write(f"      Processing row: {row_text[:100]}...")
                                
                                match = re.search(row_pattern, row_text)
                                if match and len(match.groups()) == 4:
                                    try:
                                        compartment_no = int(match.group(1))
                                        
                                        if not (1 <= compartment_no <= 5):  # Flexible check for up to 5 compartments
                                            continue
                                        
                                        # CORRECTED: Extract Order Qty (group 2) and Actual L20 Qty (group 4)
                                        order_qty_str = match.group(2)
                                        actual_l20_qty_str = match.group(4)
                                        
                                        # Clean and parse quantities
                                        requested_litres = Decimal(order_qty_str.replace(',', ''))
                                        actual_l20_litres = Decimal(actual_l20_qty_str.replace(',', ''))
                                        
                                        actual_compartments.append({
                                            'compartment_no': compartment_no,
                                            'quantity_requested_litres': requested_litres,
                                            'actual_quantity_l20': actual_l20_litres
                                        })
                                        
                                        self.stdout.write(f"      ✓ Compartment {compartment_no}: Requested={requested_litres}L, Actual L20={actual_l20_litres}L")
                                        logger.info(f"BoL PDF Parsing: Compartment {compartment_no} Req={requested_litres}L, Actual L20={actual_l20_litres}L")
                                        
                                        # Extract LON from first valid row if document-level LON missing
                                        if not lon_from_first_valid_row:
                                            lon_match = re.search(r'\b(S\d{5,7})\b', row_text)
                                            if lon_match:
                                                lon_from_first_valid_row = lon_match.group(1).upper()
                                        
                                    except (ValueError, InvalidOperation, IndexError) as e:
                                        self.stdout.write(f"      ✗ Error parsing numbers in row: {row_text[:100]}... Error: {e}")
                                        logger.warning(f"BoL PDF Parsing: Could not parse numbers for row '{row_text[:100]}...': {e}")
                                        continue
                                
                            if actual_compartments:
                                extracted_data['actual_compartments'] = actual_compartments
                                self.stdout.write(f"    Successfully parsed {len(actual_compartments)} compartment(s) from table data.")
                                logger.info(f"BoL PDF Parsing: Parsed {len(actual_compartments)} compartments for '{original_pdf_filename}'.")
                            else:
                                self.stdout.write(f"    No valid compartment rows found in table. Check PDF structure or regex.")
                                logger.warning(f"BoL PDF Parsing: No compartment data lines matched for '{original_pdf_filename}'.")

                    if lon_from_first_valid_row and not extracted_data.get('kpc_loading_order_no'):
                        extracted_data['kpc_loading_order_no'] = lon_from_first_valid_row
                        self.stdout.write(f"    Used LON '{lon_from_first_valid_row}' from first table row as document-level LON was missing.")
                        logger.info(f"BoL PDF Parsing: Used LON '{lon_from_first_valid_row}' from table row for '{original_pdf_filename}'.")
                
                else:
                    self.stdout.write(self.style.ERROR("    BoL Table header NOT FOUND. Cannot parse compartment details."))
                    logger.error(f"BoL PDF Parsing: Table header NOT found for '{original_pdf_filename}'.")

                if not extracted_data.get('kpc_loading_order_no'):
                    self.stdout.write(f"    CRITICAL: KPC Loading Order Number (Sxxxxx) could NOT be determined for BoL '{original_pdf_filename}' after all parsing attempts.")
                    logger.critical(f"BoL PDF Parsing: KPC LON MISSING for '{original_pdf_filename}'. PDF text snippet:\n{full_text[:1000]}")
                    return None

        except Exception as e_parse:
            self.stdout.write(self.style.ERROR(f"    General error during PDF parsing for '{original_pdf_filename}': {e_parse}"))
            logger.error(f"BoL PDF Parsing: General error for '{original_pdf_filename}': {e_parse}", exc_info=True)
            return None
        finally:
            if tmp_pdf_path and os.path.exists(tmp_pdf_path):
                try:
                    os.remove(tmp_pdf_path)
                except Exception as e_remove:
                    logger.warning(f"Could not remove temp PDF {tmp_pdf_path}: {e_remove}")
        
        if not extracted_data.get('actual_compartments'):
            logger.warning(f"BoL PDF Parsing: No actual compartment data extracted for LON {extracted_data.get('kpc_loading_order_no')} in '{original_pdf_filename}'.")
        
        return extracted_data

    def ensure_trip_has_compartments(self, trip):
        """Ensure trip has the required compartments, create them if missing."""
        existing_compartments = LoadingCompartment.objects.filter(trip=trip)
        
        if not existing_compartments.exists():
            self.stdout.write(f"    Trip {trip.id} has no compartments. Creating default compartments...")
            logger.warning(f"BoL Email Processing: Trip {trip.id} missing compartments, creating defaults.")
            
            # Create 3 default compartments
            for comp_num in range(1, 4):
                LoadingCompartment.objects.create(
                    trip=trip,
                    compartment_number=comp_num,
                    quantity_requested_litres=Decimal('0.00'),
                    quantity_actual_l20=None
                )
                self.stdout.write(f"      Created compartment {comp_num} for trip {trip.id}")
                logger.info(f"BoL Email Processing: Created compartment {comp_num} for trip {trip.id}")
            
            return True
        else:
            self.stdout.write(f"    Trip {trip.id} has {existing_compartments.count()} existing compartments")
            return False

    def handle(self, *args, **options):
        self.stdout.write("Starting to process KPC BoL PDF emails...")
        logger.info("BoL Email Processing: Cycle Started.")
        
        try:
            # Connect to email server
            mail = imaplib.IMAP4_SSL(settings.EMAIL_PROCESSING_HOST, settings.EMAIL_PROCESSING_PORT)
            mail.login(settings.EMAIL_PROCESSING_USER, settings.EMAIL_PROCESSING_PASSWORD)
            mail.select(settings.EMAIL_PROCESSING_MAILBOX)
            self.stdout.write(f"Connected to mailbox: {settings.EMAIL_PROCESSING_MAILBOX}")
            logger.info(f"BoL Email Processing: Connected to mailbox {settings.EMAIL_PROCESSING_MAILBOX}.")

            # Search for BoL emails
            bol_sender_filter = getattr(settings, 'EMAIL_BOL_SENDER_FILTER', 'bolconfirmation@kpc.co.ke')
            if not bol_sender_filter:
                self.stdout.write(self.style.ERROR("Setting EMAIL_BOL_SENDER_FILTER is not defined."))
                mail.logout()
                return

            search_criteria = f'(UNSEEN FROM "{bol_sender_filter}" SUBJECT "BoL")' 
            self.stdout.write(f"Searching for BoL emails from: {bol_sender_filter}")
            logger.info(f"BoL Email Processing: Searching with criteria: {search_criteria}")
            status_search, message_ids_bytes = mail.search(None, search_criteria)

            if status_search != 'OK':
                err_msg = message_ids_bytes[0].decode()
                self.stdout.write(self.style.ERROR(f"Error searching BoL emails: {err_msg}"))
                mail.logout()
                logger.error(f"BoL Email Processing: IMAP search error: {err_msg}")
                return

            email_ids_list = message_ids_bytes[0].split()
            if not email_ids_list or email_ids_list == [b'']:
                self.stdout.write(f"No new unread BoL emails matching criteria from sender '{bol_sender_filter}'.")
                logger.info("BoL Email Processing: No new BoL emails found.")
                mail.logout()
                return

            self.stdout.write(f"Found {len(email_ids_list)} new BoL email(s) matching criteria to process.")
            logger.info(f"BoL Email Processing: Found {len(email_ids_list)} new BoL email(s).")

            processed_successfully_ids = []

            # Process each email
            for email_id_bytes in email_ids_list:
                email_id_str = email_id_bytes.decode()
                self.stdout.write(f"\nProcessing BoL email ID: {email_id_str}...")
                logger.info(f"BoL Email Processing: ----- Start processing email ID {email_id_str} -----")
                kpc_lon_from_bol = "UNKNOWN_LON"
                
                try:
                    with transaction.atomic():
                        # Fetch email
                        status_fetch, msg_data = mail.fetch(email_id_bytes, "(RFC822)")
                        if status_fetch != 'OK':
                            self.stdout.write(self.style.ERROR(f"  Error fetching BoL email ID {email_id_str}"))
                            logger.error(f"BoL Email Processing: Error fetching email ID {email_id_str}.")
                            continue
                            
                        # Parse email message
                        msg = None
                        for response_part in msg_data:
                            if isinstance(response_part, tuple):
                                msg = email.message_from_bytes(response_part[1])
                                break
                                
                        if not msg:
                            self.stdout.write(self.style.ERROR(f"  Could not extract message object for email ID {email_id_str}"))
                            logger.error(f"BoL Email Processing: Could not extract message for email ID {email_id_str}.")
                            continue

                        # Get email subject
                        subject = msg.get("Subject", "No Subject")
                        if isinstance(subject, str):
                            subject_decoded = subject
                        else:
                            subject_parts = decode_header(subject)
                            subject_decoded = ''.join([
                                part.decode(encoding or 'utf-8') if isinstance(part, bytes) else str(part)
                                for part, encoding in subject_parts
                            ])
                        
                        self.stdout.write(f"  Subject: '{subject_decoded}'")
                        logger.info(f"BoL Email Processing: Email Subject '{subject_decoded}' for ID {email_id_str}.")

                        # Extract PDF attachment
                        pdf_content, pdf_filename = self.extract_pdf_attachment(msg)
                        if not pdf_content:
                            continue

                        # Parse PDF data
                        parsed_bol_data = self.parse_bol_pdf_data(pdf_content, pdf_filename)
                        if not parsed_bol_data:
                            continue

                        # Get LON from parsed data
                        kpc_lon_from_bol = parsed_bol_data.get('kpc_loading_order_no', 'UNKNOWN_LON')

                        # ✅ FIXED: Proper handling of smart matching tuple return
                        try:
                            smart_result = get_trip_with_smart_matching(kpc_lon_from_bol)
                            
                            if not smart_result:
                                self.stdout.write(self.style.ERROR(f"  Trip with LON '{kpc_lon_from_bol}' not found (tried AI matching). PDF: '{pdf_filename}'"))
                                logger.error(f"BoL Email Processing: Trip for LON {kpc_lon_from_bol} not found even with AI matching. Email ID {email_id_str}.")
                                continue
                            
                            # ✅ ROBUST: Handle both tuple and direct trip object returns
                            if isinstance(smart_result, tuple) and len(smart_result) == 2:
                                # New smart matching returns (trip, metadata)
                                trip_to_update, matching_metadata = smart_result
                                
                                # Log the matching details for debugging
                                correction_method = matching_metadata.get('correction_method', 'unknown')
                                confidence = matching_metadata.get('confidence', 0.0)
                                corrected_order = matching_metadata.get('corrected_order', kpc_lon_from_bol)
                                
                                if correction_method != 'exact_match':
                                    self.stdout.write(f"  🤖 Smart match: '{kpc_lon_from_bol}' → '{corrected_order}' (method: {correction_method}, confidence: {confidence:.2f})")
                                    logger.info(f"BoL Email Processing: Smart match for LON {kpc_lon_from_bol} → {corrected_order} using {correction_method} (confidence: {confidence:.2f})")
                                    
                            elif hasattr(smart_result, 'id'):
                                # Direct trip object (fallback compatibility)
                                trip_to_update = smart_result
                                self.stdout.write(self.style.WARNING(f"  Using legacy trip object return for LON {kpc_lon_from_bol}"))
                                logger.warning(f"BoL Email Processing: Legacy trip object return for LON {kpc_lon_from_bol}")
                                
                            else:
                                # Unexpected return type
                                self.stdout.write(self.style.ERROR(f"  Unexpected return type from smart matching for LON {kpc_lon_from_bol}: {type(smart_result)}"))
                                logger.error(f"BoL Email Processing: Unexpected return type from smart matching for LON {kpc_lon_from_bol}: {type(smart_result)}")
                                continue
                            
                            # Verify we have a valid trip object
                            if not trip_to_update or not hasattr(trip_to_update, 'id'):
                                self.stdout.write(self.style.ERROR(f"  Invalid trip object returned for LON {kpc_lon_from_bol}"))
                                logger.error(f"BoL Email Processing: Invalid trip object returned for LON {kpc_lon_from_bol}")
                                continue
                            
                            self.stdout.write(f"  Found Trip ID {trip_to_update.id} for LON {kpc_lon_from_bol}.")
                            logger.info(f"BoL Email Processing: Found Trip ID {trip_to_update.id} for LON {kpc_lon_from_bol}.")
                            
                        except Exception as smart_match_error:
                            self.stdout.write(self.style.ERROR(f"  Smart matching error for BoL {kpc_lon_from_bol}: {str(smart_match_error)}"))
                            logger.error(f"BoL Email Processing: Smart matching error for LON {kpc_lon_from_bol} (Email ID {email_id_str}): {str(smart_match_error)}")
                            continue

                        # ✅ FIXED: Ensure trip has compartments before updating
                        self.ensure_trip_has_compartments(trip_to_update)

                        # Process actual compartments
                        actual_compartments = parsed_bol_data.get('actual_compartments', [])
                        if not actual_compartments:
                            self.stdout.write(self.style.WARNING("  No actual compartment data found in BoL PDF"))
                            logger.warning(f"BoL Email Processing: No actual compartment data for Trip {trip_to_update.id}.")
                            # Mark as processed even without compartment data
                            processed_successfully_ids.append(email_id_bytes)
                            continue

                        # Update compartments with actual quantities
                        updated_compartments = 0
                        for comp_data in actual_compartments:
                            comp_no = comp_data['compartment_no']
                            requested_qty = comp_data['quantity_requested_litres']
                            actual_l20_qty = comp_data['actual_quantity_l20']
                            
                            try:
                                compartment = LoadingCompartment.objects.get(
                                    trip=trip_to_update,
                                    compartment_number=comp_no
                                )
                                
                                # CORRECTED: Update with both requested and actual L20 quantities
                                compartment.quantity_requested_litres = requested_qty
                                compartment.quantity_actual_l20 = actual_l20_qty
                                compartment.save()
                                updated_compartments += 1
                                
                                self.stdout.write(f"    Updated Compartment {comp_no}: Requested={requested_qty}L, Actual L20={actual_l20_qty}L")
                                logger.info(f"BoL Email Processing: Updated compartment {comp_no} to Req={requested_qty}L, Actual L20={actual_l20_qty}L for Trip {trip_to_update.id}")
                                
                            except LoadingCompartment.DoesNotExist:
                                self.stdout.write(self.style.WARNING(f"    Compartment {comp_no} not found for Trip {trip_to_update.id}"))
                                logger.warning(f"BoL Email Processing: Compartment {comp_no} not found for Trip {trip_to_update.id}")
                            except Exception as comp_error:
                                self.stdout.write(self.style.ERROR(f"    Error updating compartment {comp_no}: {comp_error}"))
                                logger.error(f"BoL Email Processing: Error updating compartment {comp_no}: {comp_error}")

                        if updated_compartments > 0:
                            # Update trip status to LOADED if not already
                            if trip_to_update.status != 'LOADED':
                                trip_to_update.status = 'LOADED'
                                trip_to_update.save()
                                self.stdout.write(f"  Updated Trip {trip_to_update.id} status to LOADED")
                                logger.info(f"BoL Email Processing: Updated Trip {trip_to_update.id} status to LOADED")

                            self.stdout.write(f"  ✅ Successfully updated {updated_compartments} compartment(s) for Trip {trip_to_update.id}")
                            logger.info(f"BoL Email Processing: Successfully updated {updated_compartments} compartments for Trip {trip_to_update.id}")

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
                        
                self.stdout.write(f"Marked {len(processed_successfully_ids)} BoL email(s) as seen.")

            mail.logout()
            self.stdout.write("Finished processing KPC BoL PDF emails.")
            logger.info("BoL Email Processing: Cycle Ended.")
            
        except imaplib.IMAP4.error as e_imap:
            self.stdout.write(self.style.ERROR(f"IMAP Error: {e_imap}"))
            logger.critical(f"BoL Email Processing: IMAP Error: {e_imap}", exc_info=True)
        except Exception as e_global:
            self.stdout.write(self.style.ERROR(f"An unexpected global error: {e_global}"))
            logger.critical(f"BoL Email Processing: Global unexpected error: {e_global}", exc_info=True)