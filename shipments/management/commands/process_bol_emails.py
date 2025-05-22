# shipments/management/commands/process_bol_emails.py

import imaplib
import email
from email.header import decode_header
from email.utils import parsedate_to_datetime
import os
import tempfile
import re
from decimal import Decimal, InvalidOperation
import datetime # For strptime

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile

from shipments.models import Trip, LoadingCompartment, Shipment, ShipmentDepletion, Product, Customer, Vehicle, Destination
import pdfplumber

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
                    return attachment_filename, pdf_attachment_bytes
        self.stdout.write(self.style.WARNING("  No PDF attachment found in the email."))
        return None, None

    def parse_bol_pdf_data(self, pdf_content_bytes, original_pdf_filename="attachment.pdf"):
        self.stdout.write(f"  Attempting to parse BoL PDF: {original_pdf_filename}")
        extracted_data = {
            'actual_compartments': [],
            'kpc_loading_order_no': None, 
            'kpc_shipment_no': None,     
            'delivery_date': None,
            'delivery_time': None,
            'vehicle_no': None,
        }
        
        tmp_pdf_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
                tmp_pdf.write(pdf_content_bytes)
                tmp_pdf_path = tmp_pdf.name
            
            full_text = ""
            with pdfplumber.open(tmp_pdf_path) as pdf:
                if not pdf.pages:
                    self.stdout.write(self.style.ERROR(f"    PDF '{original_pdf_filename}' has no pages."))
                    return None
                for page_num, page in enumerate(pdf.pages):
                    self.stdout.write(f"    Extracting text from page {page_num + 1}...")
                    page_text = page.extract_text_simple(x_tolerance=1, y_tolerance=1) 
                    if page_text:
                        full_text += page_text + "\n"
            
            if not full_text.strip():
                self.stdout.write(self.style.ERROR(f"    No text could be extracted from BoL PDF: {original_pdf_filename}"))
                return None

            # --- Extract Header Data ---
            shipment_no_match = re.search(r"Shipment No\s*:\s*(\d+)", full_text)
            if shipment_no_match: extracted_data['kpc_shipment_no'] = shipment_no_match.group(1).strip()
            else: self.stdout.write(self.style.WARNING("    KPC Shipment No (BoL No) not found in header."))

            delivery_date_match = re.search(r"Delivery Date\s*:\s*(\d{2}\.\d{2}\.\d{4})", full_text)
            if delivery_date_match:
                try: extracted_data['delivery_date'] = datetime.datetime.strptime(delivery_date_match.group(1), '%d.%m.%Y').date()
                except ValueError: self.stdout.write(self.style.WARNING(f"    Could not parse Delivery Date: {delivery_date_match.group(1)}"))
            
            delivery_time_match = re.search(r"Delivery Time\s*:\s*(\d{2}:\d{2}:\d{2})", full_text)
            if delivery_time_match:
                try: extracted_data['delivery_time'] = datetime.datetime.strptime(delivery_time_match.group(1), '%H:%M:%S').time()
                except ValueError: self.stdout.write(self.style.WARNING(f"    Could not parse Delivery Time: {delivery_time_match.group(1)}"))

            vehicle_no_match = re.search(r"Vehicle No\s*:\s*([A-Z0-9\/]+)", full_text)
            if vehicle_no_match: extracted_data['vehicle_no'] = vehicle_no_match.group(1).strip()

            # --- Extract Table Data (Compartments) ---
            # REVISED PATTERN
            table_header_pattern = r"Product\s+Comp\.\s+Order Qty\.\(L\)\s+Actual Qty\.\(L\)\s+Actual Qty\.\(L20\)\s+Temp\.\s*°C\s+Density(?:\s*@20°C KGV)?\s+LON NO\."
            
            slice_start = 0
            known_header_part_match = re.search(r"Actual Qty\.\(L20\)", full_text, re.IGNORECASE)
            if known_header_part_match:
                slice_start = max(0, known_header_part_match.start() - 150) 
            slice_end = slice_start + 400 
            self.stdout.write(self.style.NOTICE(f"\n--- Text Slice for Header Search (approximate) ---\n{full_text[slice_start:slice_end]}\n------------------------------------------\n"))
            
            header_match = re.search(table_header_pattern, full_text, re.IGNORECASE)
            
            if header_match:
                self.stdout.write(self.style.SUCCESS("    BoL Table header FOUND!"))
                text_after_header = full_text[header_match.end():]
                row_pattern = re.compile(
                    r"^\s*(AUTOMOTIVE GASOIL|PREMIUM MOTOR SPIRIT|PMS|AGO)\s+"
                    r"(\d+)\s+"         
                    r"([\d,\.]+)\s+"    
                    r"([\d,\.]+)\s+"    
                    r"([\d,\.]+)\s+"    
                    r"([\d\.]+)\s+"     
                    r"([\d\.]+)\s+"     
                    r"(S\d+)\s*$",      
                    re.IGNORECASE | re.MULTILINE
                )
                
                current_lon = None
                for match in row_pattern.finditer(text_after_header):
                    try:
                        comp_num = int(match.group(2))
                        actual_l20_str = match.group(5).replace(',', '')
                        temp_str = match.group(6).replace(',', '.') 
                        density_str = match.group(7).replace(',', '.') 
                        lon_no_from_row = match.group(8).strip().upper()

                        if not current_lon: 
                            current_lon = lon_no_from_row
                        elif current_lon != lon_no_from_row:
                            self.stdout.write(self.style.WARNING(f"    Multiple LONs found in table ({current_lon} vs {lon_no_from_row}). Using first: {current_lon}"))

                        extracted_data['actual_compartments'].append({
                            'compartment_number': comp_num,
                            'quantity_l20': Decimal(actual_l20_str),
                            'temperature': Decimal(temp_str),
                            'density': Decimal(density_str)
                        })
                        self.stdout.write(f"    Parsed Comp {comp_num}: L20={actual_l20_str}, Temp={temp_str}, Density={density_str}, LON={lon_no_from_row}")
                    except (ValueError, InvalidOperation) as e_val:
                        self.stdout.write(self.style.WARNING(f"    Could not parse numbers in table row: '{match.group(0)}'. Error: {e_val}"))
                    except Exception as e_row:
                        self.stdout.write(self.style.ERROR(f"    Error parsing table row '{match.group(0)}': {e_row}"))
                
                if current_lon:
                    extracted_data['kpc_loading_order_no'] = current_lon
                
                if not extracted_data['actual_compartments']:
                    self.stdout.write(self.style.WARNING("    No compartment data lines matched pattern after table header."))
            else:
                self.stdout.write(self.style.ERROR("    BoL Table header NOT FOUND using pattern. Cannot parse compartment details."))
            
            if not extracted_data.get('kpc_loading_order_no'):
                lon_general_match = re.search(r"LON NO\.\s*(S\d+)", full_text, re.IGNORECASE) 
                if lon_general_match:
                    extracted_data['kpc_loading_order_no'] = lon_general_match.group(1).strip().upper()
                    self.stdout.write(self.style.WARNING(f"    Found LON '{extracted_data['kpc_loading_order_no']}' via general search (not from table rows)."))
                else:
                    self.stdout.write(self.style.ERROR("    KPC Loading Order No (LON Sxxxxx) not found anywhere in BoL. Cannot link to Trip."))
                    return None

        except Exception as e_parse:
            self.stdout.write(self.style.ERROR(f"    General error during PDF parsing for '{original_pdf_filename}': {e_parse}"))
            return None
        finally:
            if tmp_pdf_path and os.path.exists(tmp_pdf_path):
                os.remove(tmp_pdf_path)
        
        if not extracted_data.get('kpc_loading_order_no'):
            self.stdout.write(self.style.ERROR("    Essential: KPC Loading Order No is missing after parsing."))
            return None
        
        return extracted_data

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Starting to process KPC BoL PDF emails..."))
        try:
            mail = imaplib.IMAP4_SSL(settings.EMAIL_PROCESSING_HOST, settings.EMAIL_PROCESSING_PORT)
            mail.login(settings.EMAIL_PROCESSING_USER, settings.EMAIL_PROCESSING_PASSWORD)
            mail.select(settings.EMAIL_PROCESSING_MAILBOX)
            self.stdout.write(self.style.SUCCESS(f"Connected to mailbox: {settings.EMAIL_PROCESSING_MAILBOX}"))

            bol_sender_filter = getattr(settings, 'EMAIL_BOL_SENDER_FILTER', None)
            if not bol_sender_filter:
                self.stdout.write(self.style.ERROR("Setting EMAIL_BOL_SENDER_FILTER is not defined in settings.py."))
                mail.logout()
                return

            search_criteria = f'(UNSEEN FROM "{bol_sender_filter}")'
            status_search, message_ids_bytes = mail.search(None, search_criteria)

            if status_search != 'OK':
                self.stdout.write(self.style.ERROR(f"Error searching BoL emails: {message_ids_bytes[0].decode()}"))
                mail.logout()
                return

            email_ids_list = message_ids_bytes[0].split()
            if not email_ids_list or email_ids_list == [b'']:
                self.stdout.write(self.style.WARNING(f"No new unread BoL emails from sender '{bol_sender_filter}'."))
                mail.logout()
                return

            self.stdout.write(self.style.SUCCESS(f"Found {len(email_ids_list)} new BoL email(s) from '{bol_sender_filter}' to process."))

            for email_id_bytes in email_ids_list:
                email_id_str = email_id_bytes.decode()
                self.stdout.write(f"\nProcessing BoL email ID: {email_id_str}...")
                try:
                    with transaction.atomic(): 
                        status_fetch, msg_data = mail.fetch(email_id_bytes, "(RFC822)")
                        if status_fetch != 'OK':
                            self.stdout.write(self.style.ERROR(f"  Error fetching BoL email ID {email_id_str}"))
                            continue 

                        msg = None 
                        for response_part in msg_data:
                            if isinstance(response_part, tuple):
                                msg = email.message_from_bytes(response_part[1])
                                break 
                        if not msg:
                            self.stdout.write(self.style.ERROR(f"  Could not extract message object for email ID {email_id_str}"))
                            continue

                        email_subject = self.decode_email_header(msg["subject"])
                        self.stdout.write(f"  Subject: '{email_subject}'")

                        pdf_filename, pdf_content = self.get_pdf_attachment_from_email(msg)

                        if not pdf_content:
                            self.stdout.write(self.style.WARNING(f"  No PDF attachment in email {email_id_str}. Skipping."))
                            continue
                        
                        parsed_bol_data = self.parse_bol_pdf_data(pdf_content, original_pdf_filename=pdf_filename)

                        if not parsed_bol_data or not parsed_bol_data.get('kpc_loading_order_no'):
                            self.stdout.write(self.style.ERROR(f"  Failed to parse BoL PDF '{pdf_filename}' or missing LON. Skipping update."))
                            continue
                        
                        kpc_lon_from_bol = parsed_bol_data['kpc_loading_order_no']
                        
                        trip_to_update = Trip.objects.select_for_update().get(kpc_order_number__iexact=kpc_lon_from_bol)
                        self.stdout.write(self.style.SUCCESS(f"  Found Trip ID {trip_to_update.id} for LON {kpc_lon_from_bol}."))
                        
                        if ShipmentDepletion.objects.filter(trip=trip_to_update).exists():
                            self.stdout.write(f"  Trip {trip_to_update.id} has existing depletions. Reversing them...")
                            if not trip_to_update.reverse_stock_depletion():
                                self.stdout.write(self.style.ERROR(f"  CRITICAL: Failed to reverse prior stock for Trip {trip_to_update.id}. Aborting BoL."))
                                raise CommandError(f"BoL processing failed for Trip {trip_to_update.id} due to reversal failure.")
                            self.stdout.write(self.style.SUCCESS(f"  Successfully reversed prior stock for Trip {trip_to_update.id}."))
                        
                        trip_to_update.bol_number = parsed_bol_data.get('kpc_shipment_no', trip_to_update.bol_number)
                        if parsed_bol_data.get('delivery_date'): trip_to_update.loading_date = parsed_bol_data['delivery_date']
                        if parsed_bol_data.get('delivery_time'): trip_to_update.loading_time = parsed_bol_data['delivery_time']
                        trip_to_update.status = 'LOADED' 
                        
                        has_actual_l20_data = False
                        if parsed_bol_data.get('actual_compartments'):
                            for bol_comp_data in parsed_bol_data['actual_compartments']:
                                comp_num = bol_comp_data['compartment_number']
                                try:
                                    lc_to_update, created = trip_to_update.requested_compartments.get_or_create(
                                        compartment_number=comp_num,
                                        defaults={'quantity_requested_litres': bol_comp_data.get('quantity_l20', Decimal('0.00'))}
                                    )
                                    if created: self.stdout.write(self.style.WARNING(f"    Compartment {comp_num} for Trip {trip_to_update.id} created from BoL data."))
                                    lc_to_update.quantity_actual_l20 = bol_comp_data.get('quantity_l20')
                                    lc_to_update.temperature = bol_comp_data.get('temperature')
                                    lc_to_update.density = bol_comp_data.get('density')
                                    lc_to_update.save()
                                    if lc_to_update.quantity_actual_l20 is not None and lc_to_update.quantity_actual_l20 > 0:
                                        has_actual_l20_data = True
                                    self.stdout.write(f"    Updated/Created Comp {comp_num} for Trip {trip_to_update.id} with L20: {lc_to_update.quantity_actual_l20}")
                                except Exception as e_comp:
                                    self.stdout.write(self.style.ERROR(f"    Error updating/creating comp {comp_num} for Trip {trip_to_update.id}: {e_comp}"))
                        
                        if not has_actual_l20_data and not parsed_bol_data.get('actual_compartments'): 
                            self.stdout.write(self.style.WARNING(f"  No actual compartment L20 data parsed from BoL for Trip {trip_to_update.id}. Depletion will use requested quantities if Trip.save() allows, or may fail."))
                        elif not has_actual_l20_data and parsed_bol_data.get('actual_compartments'): 
                             self.stdout.write(self.style.WARNING(f"  All parsed L20 quantities were zero or None for Trip {trip_to_update.id}. Depletion will likely be zero or use requested quantities."))

                        trip_to_update.save() 
                        self.stdout.write(self.style.SUCCESS(f"  Successfully processed BoL and updated Trip {trip_to_update.id}. Final status: {trip_to_update.status}"))
                        # mail.store(email_id_bytes, '+FLAGS', '\\Seen') 

                except Trip.DoesNotExist:
                    self.stdout.write(self.style.ERROR(f"  Trip with KPC LON '{kpc_lon_from_bol}' from BoL PDF not found. Email ID: {email_id_str}"))
                except ValidationError as e_val: 
                    self.stdout.write(self.style.ERROR(f"  VALIDATION ERROR for Trip LON '{kpc_lon_from_bol}': {e_val}. Email ID: {email_id_str}. Transaction rolled back."))
                except CommandError as e_cmd: 
                     self.stdout.write(self.style.ERROR(f"  COMMAND ERROR for Trip LON '{kpc_lon_from_bol}': {e_cmd}. Email ID: {email_id_str}. Transaction rolled back."))
                except Exception as e_main: 
                    self.stdout.write(self.style.ERROR(f"  UNEXPECTED ERROR for Trip LON '{kpc_lon_from_bol}': {e_main}. Email ID: {email_id_str}. Transaction rolled back."))
            
            mail.logout()
            self.stdout.write(self.style.SUCCESS("Finished processing KPC BoL PDF emails."))

        except imaplib.IMAP4.error as e_imap:
            self.stdout.write(self.style.ERROR(f"IMAP Connection/Operation Error: {e_imap}"))
        except Exception as e_global:
            self.stdout.write(self.style.ERROR(f"An unexpected global error occurred in BoL processing: {e_global}"))