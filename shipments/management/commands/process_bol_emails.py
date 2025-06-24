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

    def parse_bol_pdf_data(self, pdf_content_bytes, original_pdf_filename="attachment.pdf"):
        self.stdout.write(f"  Attempting to parse BoL PDF: {original_pdf_filename}")
        logger.info(f"BoL PDF Parsing: Starting for '{original_pdf_filename}'")
        extracted_data = {
            'actual_compartments': [], 
            'kpc_loading_order_no': None, 
            'kpc_shipment_no': None, 
            'delivery_date': None, 
            'delivery_time': None, 
            'vehicle_no': None
        }
        tmp_pdf_path = None
        full_text = ""

        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
                tmp_pdf.write(pdf_content_bytes)
                tmp_pdf_path = tmp_pdf.name
            
            with pdfplumber.open(tmp_pdf_path) as pdf:
                if not pdf.pages:
                    self.stdout.write(self.style.ERROR(f"    PDF '{original_pdf_filename}' has no pages."))
                    logger.error(f"BoL PDF Parsing: PDF '{original_pdf_filename}' has no pages.")
                    return None
                for page_num, page in enumerate(pdf.pages):
                    self.stdout.write(f"    Extracting text from page {page_num + 1}...")
                    page_text = page.extract_text_simple(x_tolerance=1.5, y_tolerance=1.5) 
                    if not page_text: 
                        page_text = page.extract_text()
                    if page_text:
                        full_text += page_text + "\n"
            
            if not full_text.strip():
                self.stdout.write(self.style.ERROR(f"    No text extracted from BoL PDF: {original_pdf_filename}"))
                logger.error(f"BoL PDF Parsing: No text extracted from BoL PDF: {original_pdf_filename}")
                return None

            cleaned_full_text_for_lon = re.sub(r'\s+', ' ', full_text.strip()) 
            lon_patterns = [
                r"LON\s*(?:NO\.?|NUMBER)?\s*[:\-]?\s*(S\d{5,7}\b)", 
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

            vehicle_no_match = re.search(r"Vehicle\s*(?:No\.?|Reg)?\s*[:\-]?\s*([A-Z0-9\s\/]+?)(?:\s+Trailer|$)", full_text, re.IGNORECASE)
            if vehicle_no_match: extracted_data['vehicle_no'] = vehicle_no_match.group(1).strip().replace(" ", "")
            else: self.stdout.write(self.style.WARNING("    Vehicle No not found in BoL text."))

            table_header_pattern = r"Product\s+Comp\.\s+Order Qty\.\(L\)\s+Actual Qty\.\(L\)\s+Actual Qty\.\(L20\)\s+Temp\.\s*°C\s+Density(?:\s*@20°C KGV)?\s+LON NO\."
            header_match = re.search(table_header_pattern, full_text, re.IGNORECASE | re.MULTILINE)

            if header_match:
                self.stdout.write(self.style.SUCCESS("    BoL Table header FOUND!"))
                logger.info(f"BoL PDF Parsing: Table header found for '{original_pdf_filename}'.")
                text_after_header = full_text[header_match.end():]
                
                self.stdout.write("--- DEBUG: TEXT AFTER HEADER (first 15 lines for compartment parsing) ---")
                logger.info(f"--- DEBUG_BOL_TABLE_CONTENT for {original_pdf_filename} (Max 15 lines) ---")
                debug_lines_count = 0
                for line_to_debug in text_after_header.splitlines():
                    stripped_line = line_to_debug.strip()
                    if stripped_line: 
                        if debug_lines_count < 15:
                            self.stdout.write(f"DEBUG LINE: '{stripped_line}'")
                            logger.info(f"'{stripped_line}'")
                            debug_lines_count += 1
                        else:
                            break
                self.stdout.write("--- END DEBUG ---")
                logger.info(f"--- END DEBUG_BOL_TABLE_CONTENT for {original_pdf_filename} ---")

                row_pattern_str = r"^(MOTOR SPIRIT PREMIUM|AUTOMOTIVE GASOIL|PMS|AGO)\s+(\d+)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d\.]+)\s+([\d\.]+)\s*(S\d+)?\s*$"
                row_pattern = re.compile(row_pattern_str, re.IGNORECASE | re.MULTILINE)
                
                lon_from_first_valid_row = None
                parsed_compartments_count = 0

                for line_num, line in enumerate(text_after_header.splitlines()):
                    line_content_for_match = line.strip() 
                    if not line_content_for_match: continue 
                    
                    match = row_pattern.search(line_content_for_match)
                    if match:
                        try:
                            product_name_bol_raw = match.group(1).strip().upper()
                            if "MOTOR SPIRIT" in product_name_bol_raw or "PMS" in product_name_bol_raw:
                                normalized_product_name = "PMS"
                            elif "GASOIL" in product_name_bol_raw or "AGO" in product_name_bol_raw:
                                normalized_product_name = "AGO"
                            else:
                                normalized_product_name = product_name_bol_raw 
                            
                            comp_num_str = match.group(2)
                            order_qty_str = match.group(3).replace(',', '')
                            actual_qty_str = match.group(4).replace(',', '')
                            actual_l20_str = match.group(5).replace(',', '') 
                            temp_str = match.group(6).replace(',', '.') 
                            density_kg_m3_str = match.group(7).replace(',', '.')
                            lon_no_from_row = match.group(8).strip().upper() if match.group(8) else None

                            comp_num = int(comp_num_str)
                            order_qty = Decimal(order_qty_str) if order_qty_str else Decimal('0')
                            actual_qty = Decimal(actual_qty_str) if actual_qty_str else Decimal('0')
                            actual_l20 = Decimal(actual_l20_str) if actual_l20_str else Decimal('0')
                            temp = Decimal(temp_str) if temp_str else None
                            
                            density_kg_l = None
                            if density_kg_m3_str:
                                density_kg_m3 = Decimal(density_kg_m3_str)
                                density_kg_l = (density_kg_m3 / Decimal('1000')).quantize(Decimal('0.0001'))
                            
                            if actual_l20 < 0: raise ValueError("Actual L20 quantity cannot be negative.")
                            if temp is not None and not (Decimal('-50') < temp < Decimal('100')): 
                                raise ValueError(f"Temperature {temp} out of reasonable range.")
                            if density_kg_l is not None and not (Decimal('0.6000') < density_kg_l < Decimal('0.9000')): 
                                raise ValueError(f"Density {density_kg_l} kg/L out of reasonable range (0.6000-0.9000 kg/L). Original kg/m³: {density_kg_m3_str}")

                            if lon_no_from_row and not lon_from_first_valid_row:
                                lon_from_first_valid_row = lon_no_from_row
                            
                            extracted_data['actual_compartments'].append({
                                'compartment_number': comp_num, 
                                'order_qty': order_qty,
                                'actual_qty': actual_qty,
                                'quantity_l20': actual_l20, 
                                'temperature': temp, 
                                'density': density_kg_l,
                                'row_lon': lon_no_from_row,
                                'product_name_bol': normalized_product_name
                            })
                            parsed_compartments_count +=1
                            self.stdout.write(f"    Parsed Comp {comp_num}: Product='{normalized_product_name}' (from '{product_name_bol_raw}'), L20={actual_l20_str}L, Temp={temp_str}°C, Density_PDF={density_kg_m3_str}kg/m³ ({density_kg_l}kg/L), Row_LON={lon_no_from_row or 'N/A'}")
                            logger.info(f"BoL PDF Parsing: Parsed Comp {comp_num} for '{original_pdf_filename}': {normalized_product_name}, L20={actual_l20_str}L, Density={density_kg_l} kg/L")
                            
                        except (ValueError, InvalidOperation) as e_val: 
                            self.stdout.write(self.style.WARNING(f"    Could not parse numbers or invalid value in table row: '{line_content_for_match}'. Error: {e_val}"))
                            logger.warning(f"BoL PDF Parsing: Value/InvalidOp error parsing row for '{original_pdf_filename}': '{line_content_for_match}' - {e_val}")
                        except Exception as e_row: 
                            self.stdout.write(self.style.ERROR(f"    Error parsing table row '{line_content_for_match}': {e_row}"))
                            logger.error(f"BoL PDF Parsing: Generic error parsing row for '{original_pdf_filename}': '{line_content_for_match}' - {e_row}", exc_info=True)
                    
                if parsed_compartments_count == 0 :
                     self.stdout.write(self.style.WARNING(f"    No compartment data lines matched row pattern: '{row_pattern_str}' after table header. Check PDF structure or regex."))
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

            bol_sender_filter = getattr(settings, 'EMAIL_BOL_SENDER_FILTER', None)
            if not bol_sender_filter:
                self.stdout.write(self.style.ERROR("Setting EMAIL_BOL_SENDER_FILTER is not defined.")); mail.logout(); return

            search_criteria = f'(UNSEEN FROM "{bol_sender_filter}" SUBJECT "BoL")' 
            self.stdout.write(self.style.NOTICE(f"Searching for BoL emails with criteria: {search_criteria}"))
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
                        trip_to_update = Trip.objects.select_for_update().get(kpc_order_number__iexact=kpc_lon_from_bol)
                        self.stdout.write(self.style.SUCCESS(f"  Found Trip ID {trip_to_update.id} for LON {kpc_lon_from_bol}."))
                        logger.info(f"BoL Email Processing: Matched Trip ID {trip_to_update.id} for LON {kpc_lon_from_bol}.")

                        if ShipmentDepletion.objects.filter(trip=trip_to_update).exists():
                            self.stdout.write(f"  Trip {trip_to_update.id} has existing depletions. Reversing them...")
                            logger.info(f"BoL Email Processing: Reversing existing depletions for Trip ID {trip_to_update.id} before BoL processing.")
                            reversal_ok, reversal_msg = trip_to_update.reverse_stock_depletion(stdout_writer=self) 
                            if not reversal_ok:
                                self.stdout.write(self.style.ERROR(f"  CRITICAL: Failed to reverse prior stock for Trip {trip_to_update.id}: {reversal_msg}. Aborting BoL processing for this trip."))
                                logger.critical(f"BoL Email Processing: Failed to reverse stock for Trip ID {trip_to_update.id}. BoL NOT PROCESSED. Message: {reversal_msg}")
                                raise CommandError(f"BoL processing failed for Trip {trip_to_update.id} due to reversal failure.")
                            self.stdout.write(self.style.SUCCESS(f"  Successfully reversed prior stock for Trip {trip_to_update.id}. {reversal_msg}"))
                            logger.info(f"BoL Email Processing: Successfully reversed stock for Trip ID {trip_to_update.id}. {reversal_msg}")
                        
                        trip_to_update.bol_number = parsed_bol_data.get('kpc_shipment_no', trip_to_update.bol_number)
                        if parsed_bol_data.get('delivery_date'): trip_to_update.loading_date = parsed_bol_data['delivery_date']
                        if parsed_bol_data.get('delivery_time'): trip_to_update.loading_time = parsed_bol_data['delivery_time']
                        
                        original_status = trip_to_update.status
                        trip_to_update.status = 'LOADED' 
                        
                        if parsed_bol_data.get('actual_compartments'):
                            logger.info(f"BoL Email Processing: Updating/Creating {len(parsed_bol_data['actual_compartments'])} compartments from BoL for Trip ID {trip_to_update.id}.")
                            
                            bol_comp_numbers_found = {c['compartment_number'] for c in parsed_bol_data['actual_compartments']}
                            
                            # Delete LA compartments that are NOT in the BoL (if any)
                            for comp_in_db in trip_to_update.requested_compartments.all():
                                if comp_in_db.compartment_number not in bol_comp_numbers_found:
                                    logger.info(f"BoL Email Processing: Deleting LA Comp {comp_in_db.compartment_number} for Trip {trip_to_update.id} as it's not in BoL data.")
                                    comp_in_db.delete()

                            for bol_comp_data in parsed_bol_data['actual_compartments']:
                                comp_num = bol_comp_data['compartment_number']
                                try:
                                    # Get existing LA compartment to preserve its requested_litres
                                    # If it doesn't exist, quantity_requested_litres will be based on BoL L20
                                    # This assumes compartments are first created from LA PDF.
                                    existing_lc = None
                                    try:
                                        existing_lc = LoadingCompartment.objects.get(trip=trip_to_update, compartment_number=comp_num)
                                        final_requested_qty = existing_lc.quantity_requested_litres
                                    except LoadingCompartment.DoesNotExist:
                                        # If LA never created this comp, then BoL actual IS the requested for this new comp
                                        final_requested_qty = bol_comp_data.get('quantity_l20') 
                                        logger.info(f"BoL Email Processing: Comp {comp_num} for Trip {trip_to_update.id} not found from LA, creating new. Requested set to BoL L20.")

                                    lc_obj, created = LoadingCompartment.objects.update_or_create(
                                        trip=trip_to_update,
                                        compartment_number=comp_num,
                                        defaults={
                                            'quantity_requested_litres': final_requested_qty,
                                            'quantity_actual_l20': bol_comp_data.get('quantity_l20'),
                                            'temperature': bol_comp_data.get('temperature'),
                                            'density': bol_comp_data.get('density') 
                                        }
                                    )
                                    action_str = "Created" if created else "Updated"
                                    self.stdout.write(f"    {action_str} Comp {comp_num} for Trip {trip_to_update.id} with L20: {lc_obj.quantity_actual_l20}, Req: {lc_obj.quantity_requested_litres}")
                                    logger.info(f"BoL Email Processing: {action_str} Comp {comp_num} for Trip ID {trip_to_update.id}, L20: {lc_obj.quantity_actual_l20}, Req: {lc_obj.quantity_requested_litres}.")
                                except Exception as e_comp: 
                                    self.stdout.write(self.style.ERROR(f"    Error creating/updating comp {comp_num} for Trip {trip_to_update.id}: {e_comp}"))
                                    logger.error(f"BoL Email Processing: Error creating/updating comp {comp_num} for Trip ID {trip_to_update.id}: {e_comp}", exc_info=True)
                        else: # No compartments in BoL data
                            logger.warning(f"BoL Email Processing: No compartment data in parsed_bol_data for Trip ID {trip_to_update.id}. If LA compartments existed, they are kept as is with only actuals potentially being zeroed if no explicit actuals logic.")
                            # If BoL has no compartments, should we delete existing LA compartments?
                            # For now, it keeps them. If BoL implies zero loading, then actuals should be set to 0.
                            # This case might need more specific handling based on business rules.

                        # Save the trip. This will trigger the stock depletion logic in Trip.save()
                        # It will use total_actual_l20_from_compartments due to 'LOADED' status.
                        trip_to_update.save(stdout_writer=self) 
                        
                        self.stdout.write(self.style.SUCCESS(f"  Successfully processed BoL and updated Trip {trip_to_update.id}. Status changed from '{original_status}' to '{trip_to_update.status}'."))
                        logger.info(f"BoL Email Processing: Successfully processed BoL for Trip ID {trip_to_update.id}. Status {original_status} -> {trip_to_update.status}.")
                        processed_successfully_ids.append(email_id_bytes)

                except Trip.DoesNotExist: 
                    self.stdout.write(self.style.ERROR(f"  Trip with KPC LON '{kpc_lon_from_bol}' from BoL PDF not found. Email ID: {email_id_str}"))
                    logger.error(f"BoL Email Processing: Trip for LON '{kpc_lon_from_bol}' (Email ID {email_id_str}) not found.")
                except ValidationError as e_val: 
                    self.stdout.write(self.style.ERROR(f"  VALIDATION ERROR for Trip LON '{kpc_lon_from_bol}': {e_val}. Email ID: {email_id_str}. Rolled back."))
                    logger.error(f"BoL Email Processing: ValidationError for LON '{kpc_lon_from_bol}' (Email ID {email_id_str}): {e_val}", exc_info=True)
                except CommandError as e_cmd: 
                    self.stdout.write(self.style.ERROR(f"  COMMAND ERROR for Trip LON '{kpc_lon_from_bol}': {e_cmd}. Email ID: {email_id_str}. Rolled back."))
                    logger.error(f"BoL Email Processing: CommandError for LON '{kpc_lon_from_bol}' (Email ID {email_id_str}): {e_cmd}", exc_info=True)
                except IntegrityError as e_int: 
                    self.stdout.write(self.style.ERROR(f"  DATABASE INTEGRITY ERROR for Trip LON '{kpc_lon_from_bol}': {e_int}. Email ID: {email_id_str}. Rolled back."))
                    logger.error(f"BoL Email Processing: IntegrityError for LON '{kpc_lon_from_bol}' (Email ID {email_id_str}): {e_int}", exc_info=True)
                except Exception as e_main: 
                    self.stdout.write(self.style.ERROR(f"  UNEXPECTED ERROR for Trip LON '{kpc_lon_from_bol}': {e_main}. Email ID: {email_id_str}. Rolled back."))
                    logger.error(f"BoL Email Processing: Unexpected error for LON '{kpc_lon_from_bol}' (Email ID {email_id_str}): {e_main}", exc_info=True)
                finally:
                    logger.info(f"BoL Email Processing: ----- Finished processing email ID {email_id_str} -----")

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
            self.stdout.write(self.style.ERROR(f"IMAP Connection/Operation Error: {e_imap}"))
            logger.critical(f"BoL Email Processing: IMAP Error: {e_imap}", exc_info=True)
        except Exception as e_global: 
            self.stdout.write(self.style.ERROR(f"An unexpected global error in BoL processing: {e_global}"))
            logger.critical(f"BoL Email Processing: Global unexpected error: {e_global}", exc_info=True)