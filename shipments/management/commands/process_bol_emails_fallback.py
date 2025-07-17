# shipments/management/commands/process_bol_emails_fallback.py
import imaplib
import email
import re
import tempfile
import os
import datetime
import difflib
from decimal import Decimal, InvalidOperation
from email.header import decode_header
from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import transaction
from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from shipments.models import Trip, LoadingCompartment, Vehicle
from shipments.utils.ai_order_matcher import get_trip_with_smart_matching
from datetime import datetime, timedelta
import logging
import pdfplumber

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Fallback processor for SEEN KPC BoL PDF emails from the previous day'

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

    def extract_pdf_attachment(self, msg):
        """Extract PDF attachment from email message."""
        for part in msg.walk():
            if part.get_content_type() == "application/pdf":
                filename = part.get_filename()
                if filename and filename.lower().endswith('.pdf'):
                    pdf_content = part.get_payload(decode=True)
                    if pdf_content:
                        self.stdout.write(f"  Found PDF attachment: {filename} ({len(pdf_content)} bytes)")
                        logger.info(f"BoL Email Fallback: Found PDF attachment: {filename}")
                        return pdf_content, filename
        
        self.stdout.write(self.style.WARNING("  No PDF attachment found in email"))
        logger.warning("BoL Email Fallback: No PDF attachment found in an email.")
        return None, None

    def parse_bol_pdf_data(self, pdf_content, original_pdf_filename="unknown.pdf"):
        """Parse BoL PDF and extract compartment data with L20 quantities."""
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
                    self.stdout.write(f"    Found BoL/Shipment No in PDF: {extracted_data['kpc_shipment_no']}")
                    logger.info(f"BoL PDF Parsing: Found BoL/Shipment No in PDF: {extracted_data['kpc_shipment_no']}")
                else:
                    self.stdout.write(self.style.WARNING("    KPC Shipment No (BoL No) not found in PDF text."))
                    logger.warning(f"BoL PDF Parsing: KPC Shipment No not found in PDF text for '{original_pdf_filename}'.")

                # Enhanced Vehicle Registration parsing
                vehicle_patterns = [
                    r"Vehicle\s*(?:No\.?|Number)?\s*[:\-]?\s*([A-Z0-9]+(?:[/\\][A-Z0-9]+)?)",
                    r"Truck\s*(?:No\.?|Number)?\s*[:\-]?\s*([A-Z0-9]+(?:[/\\][A-Z0-9]+)?)",
                    r"Registration\s*(?:No\.?|Number)?\s*[:\-]?\s*([A-Z0-9]+(?:[/\\][A-Z0-9]+)?)",
                    r"\b([A-Z]{2,3}\s*\d{3,4}\s*[A-Z]?)\b",  # Common plate formats
                    r"\b([A-Z0-9]{6,}[/\\][A-Z0-9]{3,})\b"   # Truck/Trailer combinations
                ]
                
                for pattern in vehicle_patterns:
                    vehicle_match = re.search(pattern, full_text, re.IGNORECASE)
                    if vehicle_match:
                        vehicle_reg = vehicle_match.group(1).strip().upper()
                        # Clean up spacing and separators
                        vehicle_reg = re.sub(r'\s+', '', vehicle_reg)  # Remove spaces
                        vehicle_reg = re.sub(r'[/\\]', '/', vehicle_reg)  # Normalize separators
                        extracted_data['vehicle_reg'] = vehicle_reg
                        self.stdout.write(f"    Found Vehicle Registration: {vehicle_reg}")
                        logger.info(f"BoL PDF Parsing: Found Vehicle Registration: {vehicle_reg}")
                        break

                # Parse TABLE DATA for ACTUAL COMPARTMENTS with L20 quantities
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

                            # Enhanced regex pattern to capture all three quantity columns
                            row_pattern = r"(\d+)\s+.*?(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s+(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s+(\d{1,3}(?:,\d{3})*(?:\.\d+)?)"

                            for row in table[1:]:  # Skip header
                                if not row:
                                    continue
                                    
                                row_text = " ".join([str(cell or "").strip() for cell in row])
                                
                                # Skip Total rows
                                if re.search(r'\btotal\b', row_text, re.IGNORECASE):
                                    continue
                                    
                                self.stdout.write(f"      Processing row: {row_text[:100]}...")
                                
                                match = re.search(row_pattern, row_text)
                                if match and len(match.groups()) >= 4:
                                    try:
                                        compartment_no = int(match.group(1))
                                        
                                        # Only process valid compartment numbers
                                        if not (1 <= compartment_no <= 5):
                                            continue
                                        
                                        # Extract quantities
                                        order_qty_str = match.group(2)
                                        actual_qty_str = match.group(3)  
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
                                        
                                        # Extract LON from row if document-level LON missing
                                        if not lon_from_first_valid_row:
                                            lon_match = re.search(r'\b(S\d{5,7})\b', row_text)
                                            if lon_match:
                                                lon_from_first_valid_row = lon_match.group(1).upper()
                                        
                                    except (ValueError, InvalidOperation, IndexError) as e:
                                        self.stdout.write(f"      ✗ Error parsing quantities in row: {row_text[:100]}... Error: {e}")
                                        logger.warning(f"BoL PDF Parsing: Could not parse quantities for row '{row_text[:100]}...': {e}")
                                        continue
                                
                            if actual_compartments:
                                extracted_data['actual_compartments'] = actual_compartments
                                self.stdout.write(f"    Successfully parsed {len(actual_compartments)} compartment(s) from table data.")
                                logger.info(f"BoL PDF Parsing: Parsed {len(actual_compartments)} compartments for '{original_pdf_filename}'.")
                            else:
                                self.stdout.write(f"    No valid compartment rows found in table.")
                                logger.warning(f"BoL PDF Parsing: No compartment data lines matched for '{original_pdf_filename}'.")

                    if lon_from_first_valid_row and not extracted_data.get('kpc_loading_order_no'):
                        extracted_data['kpc_loading_order_no'] = lon_from_first_valid_row
                        self.stdout.write(f"    Used LON '{lon_from_first_valid_row}' from table row.")
                        logger.info(f"BoL PDF Parsing: Used LON '{lon_from_first_valid_row}' from table row for '{original_pdf_filename}'.")
                
                else:
                    self.stdout.write(self.style.ERROR("    BoL Table header NOT FOUND."))
                    logger.error(f"BoL PDF Parsing: Table header NOT found for '{original_pdf_filename}'.")

                if not extracted_data.get('kpc_loading_order_no'):
                    self.stdout.write(f"    CRITICAL: KPC Loading Order Number could NOT be determined for BoL '{original_pdf_filename}'")
                    logger.critical(f"BoL PDF Parsing: KPC LON MISSING for '{original_pdf_filename}'.")
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
        
        return extracted_data

    def find_trip_by_truck_and_order(self, vehicle_reg_from_bol, kpc_lon_from_bol):
        """Find trip using truck-based matching with order validation."""
        trip_to_update = None
        matching_method = "none"
        confidence_score = 0.0
        
        try:
            if vehicle_reg_from_bol:
                self.stdout.write(f"  🚛 Attempting truck-based matching for vehicle: {vehicle_reg_from_bol}")
                logger.info(f"BoL Email Fallback: Truck-based matching for vehicle {vehicle_reg_from_bol}")
                
                # Find vehicle by registration with flexible matching
                vehicle_matches = Vehicle.objects.filter(
                    models.Q(plate_number__iexact=vehicle_reg_from_bol) |
                    models.Q(trailer_number__iexact=vehicle_reg_from_bol) |
                    models.Q(plate_number__icontains=vehicle_reg_from_bol.replace('/', '')) |
                    models.Q(trailer_number__icontains=vehicle_reg_from_bol.replace('/', '')) |
                    models.Q(plate_number__icontains=vehicle_reg_from_bol.split('/')[0]) |
                    models.Q(trailer_number__icontains=vehicle_reg_from_bol.split('/')[0])
                )
                
                if vehicle_matches.exists():
                    vehicle = vehicle_matches.first()
                    self.stdout.write(f"  ✅ Found vehicle: {vehicle.plate_number}")
                    logger.info(f"BoL Email Fallback: Found vehicle {vehicle.plate_number} for reg {vehicle_reg_from_bol}")
                    
                    # Find active trips for this vehicle
                    active_trips = Trip.objects.filter(
                        vehicle=vehicle,
                        status__in=['PENDING', 'KPC_APPROVED', 'LOADING', 'LOADED', 'GATEPASSED', 'TRANSIT']
                    ).order_by('-loading_date', '-created_at')
                    
                    if active_trips.exists():
                        candidate_trip = active_trips.first()
                        
                        # Validate order number match
                        if kpc_lon_from_bol and kpc_lon_from_bol != 'UNKNOWN_LON':
                            if candidate_trip.kpc_order_number == kpc_lon_from_bol:
                                # Perfect match: both truck and order number
                                trip_to_update = candidate_trip
                                matching_method = "truck_and_order_exact"
                                confidence_score = 1.0
                                self.stdout.write(f"  ✅ Perfect match: Vehicle {vehicle.plate_number} + Order {kpc_lon_from_bol}")
                                logger.info(f"BoL Email Fallback: Perfect match Vehicle {vehicle.plate_number} + Order {kpc_lon_from_bol}")
                            else:
                                # Check order similarity (typo tolerance)
                                similarity_ratio = difflib.SequenceMatcher(None, candidate_trip.kpc_order_number, kpc_lon_from_bol).ratio()
                                
                                if similarity_ratio > 0.8:  # 80% similarity threshold
                                    trip_to_update = candidate_trip
                                    matching_method = "truck_with_order_similarity"
                                    confidence_score = similarity_ratio
                                    self.stdout.write(f"  ⚠️ Truck match with similar order: Expected={candidate_trip.kpc_order_number}, BoL={kpc_lon_from_bol} (similarity: {similarity_ratio:.2f})")
                                    logger.warning(f"BoL Email Fallback: Truck match with order similarity {similarity_ratio:.2f}")
                                else:
                                    # Significant order mismatch - use truck but warn
                                    trip_to_update = candidate_trip
                                    matching_method = "truck_only_order_mismatch"
                                    confidence_score = 0.5
                                    self.stdout.write(self.style.WARNING(f"  ⚠️ TRUCK MATCH BUT ORDER MISMATCH: Expected={candidate_trip.kpc_order_number}, BoL={kpc_lon_from_bol}"))
                                    logger.error(f"BoL Email Fallback: Truck match but order mismatch - Expected={candidate_trip.kpc_order_number}, BoL={kpc_lon_from_bol}")
                        else:
                            # No order number in BoL, use truck only
                            trip_to_update = candidate_trip
                            matching_method = "truck_only_no_order"
                            confidence_score = 0.7
                            self.stdout.write(f"  ℹ️ Truck-only match (no order in BoL): Vehicle {vehicle.plate_number}")
                            logger.info(f"BoL Email Fallback: Truck-only match for {vehicle.plate_number}")
                    else:
                        self.stdout.write(f"  ❌ No active trips found for vehicle {vehicle.plate_number}")
                        logger.warning(f"BoL Email Fallback: No active trips for vehicle {vehicle.plate_number}")
                else:
                    self.stdout.write(f"  ❌ Vehicle not found: {vehicle_reg_from_bol}")
                    logger.warning(f"BoL Email Fallback: Vehicle not found: {vehicle_reg_from_bol}")
            
            # Fallback to order-based matching if truck matching failed
            if not trip_to_update and kpc_lon_from_bol != 'UNKNOWN_LON':
                self.stdout.write(f"  🔄 Falling back to order-based matching for: {kpc_lon_from_bol}")
                logger.info(f"BoL Email Fallback: Fallback to order-based matching for {kpc_lon_from_bol}")
                
                smart_result = get_trip_with_smart_matching(kpc_lon_from_bol)
                if smart_result:
                    if isinstance(smart_result, tuple) and len(smart_result) == 2:
                        trip_to_update, matching_metadata = smart_result
                        matching_method = f"fallback_order_{matching_metadata.get('correction_method', 'unknown')}"
                        confidence_score = matching_metadata.get('confidence', 0.3)
                    elif hasattr(smart_result, 'id'):
                        trip_to_update = smart_result
                        matching_method = "fallback_order_exact"
                        confidence_score = 0.8
                    
                    if trip_to_update:
                        self.stdout.write(f"  🎯 Fallback match found: Trip {trip_to_update.id} ({trip_to_update.kpc_order_number})")
                        logger.info(f"BoL Email Fallback: Fallback match found Trip {trip_to_update.id}")
            
            return trip_to_update, matching_method, confidence_score
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  Error in truck-based matching: {e}"))
            logger.error(f"BoL Email Fallback: Error in truck-based matching: {e}")
            return None, "error", 0.0

    def ensure_trip_has_compartments(self, trip):
        """Ensure trip has the required compartments, create them if missing."""
        existing_compartments = LoadingCompartment.objects.filter(trip=trip)
        
        if not existing_compartments.exists():
            self.stdout.write(f"    Trip {trip.id} has no compartments. Creating default compartments...")
            logger.warning(f"BoL Email Fallback: Trip {trip.id} missing compartments, creating defaults.")
            
            for comp_num in range(1, 4): # Create 3 default compartments
                LoadingCompartment.objects.create(
                    trip=trip,
                    compartment_number=comp_num,
                    quantity_requested_litres=Decimal('0.00'),
                    quantity_actual_l20=None
                )
                self.stdout.write(f"      Created compartment {comp_num} for trip {trip.id}")
                logger.info(f"BoL Email Fallback: Created compartment {comp_num} for trip {trip.id}")
            
            return True
        else:
            self.stdout.write(f"    Trip {trip.id} has {existing_compartments.count()} existing compartments")
            return False

    def write(self, msg):
        """Allow this command to be used as stdout_writer for trip methods"""
        self.stdout.write(msg)

    def decode_email_header(self, header):
        """Decode email header properly"""
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
                header_parts.append(str(part))
        return "".join(header_parts)

    def handle(self, *args, **options):
        days_back = options['days_back']
        dry_run = options['dry_run']
        
        self.stdout.write(f"🔄 Starting FALLBACK KPC BoL email processing (last {days_back} day(s))...")
        if dry_run:
            self.stdout.write(self.style.WARNING("🔍 DRY RUN MODE - No actual processing will occur"))
        
        logger.info(f"BoL Email Fallback: Cycle Started (days_back={days_back}, dry_run={dry_run}).")
        
        try:
            # Connect to email server
            mail = imaplib.IMAP4_SSL(settings.EMAIL_PROCESSING_HOST, settings.EMAIL_PROCESSING_PORT)
            mail.login(settings.EMAIL_PROCESSING_USER, settings.EMAIL_PROCESSING_PASSWORD)
            mail.select(settings.EMAIL_PROCESSING_MAILBOX)
            self.stdout.write(f"📧 Connected to mailbox: {settings.EMAIL_PROCESSING_MAILBOX}")
            logger.info(f"BoL Email Fallback: Connected to mailbox {settings.EMAIL_PROCESSING_MAILBOX}.")

            # Calculate date for searching (yesterday by default)
            search_date = (datetime.now() - timedelta(days=days_back)).strftime('%d-%b-%Y')

            # Search for BoL emails - but search for SEEN emails
            bol_sender_filter = getattr(settings, 'EMAIL_BOL_SENDER_FILTER', 'bolconfirmation@kpc.co.ke')
            if not bol_sender_filter:
                self.stdout.write(self.style.ERROR("❌ Setting EMAIL_BOL_SENDER_FILTER is not defined."))
                mail.logout()
                return

            search_criteria = f'(SEEN FROM "{bol_sender_filter}" SUBJECT "BoL" SINCE "{search_date}")'
            self.stdout.write(f"📧 Searching for SEEN BoL emails from: {bol_sender_filter}")
            self.stdout.write(f"📅 Date range: {search_date} onwards")
            self.stdout.write(f"🔍 Search criteria: {search_criteria}")
            logger.info(f"BoL Email Fallback: Searching with criteria: {search_criteria}")

            status_search, message_ids_bytes = mail.search(None, search_criteria)

            if status_search != 'OK':
                err_msg = message_ids_bytes[0].decode()
                self.stdout.write(self.style.ERROR(f"❌ Error searching BoL emails: {err_msg}"))
                mail.logout()
                logger.error(f"BoL Email Fallback: IMAP search error: {err_msg}")
                return

            email_ids_list = message_ids_bytes[0].split()
            if not email_ids_list or email_ids_list == [b'']:
                self.stdout.write(self.style.SUCCESS("✅ No SEEN BoL emails found for the specified date range."))
                logger.info("BoL Email Fallback: No SEEN BoL emails found.")
                mail.logout()
                return

            self.stdout.write(f"📬 Found {len(email_ids_list)} SEEN BoL email(s) to review.")
            logger.info(f"BoL Email Fallback: Found {len(email_ids_list)} SEEN BoL email(s).")

            processed_successfully_ids = []

            # Process each email
            for email_id_bytes in email_ids_list:
                email_id_str = email_id_bytes.decode()
                self.stdout.write(f"\n📋 Reviewing BoL email ID: {email_id_str}...")
                logger.info(f"BoL Email Fallback: ----- Start processing email ID {email_id_str} -----")
                kpc_lon_from_bol = "UNKNOWN_LON"
                
                if dry_run:
                    self.stdout.write(self.style.WARNING(f"  🔍 DRY RUN: Would process BoL email"))
                    processed_successfully_ids.append(email_id_bytes)
                    continue
                
                try:
                    with transaction.atomic():
                        # Fetch email
                        status_fetch, msg_data = mail.fetch(email_id_bytes, "(RFC822)")
                        if status_fetch != 'OK':
                            self.stdout.write(self.style.ERROR(f"  Error fetching BoL email ID {email_id_str}"))
                            logger.error(f"BoL Email Fallback: Error fetching email ID {email_id_str}.")
                            continue
                            
                        # Parse email message
                        msg = None
                        for response_part in msg_data:
                            if isinstance(response_part, tuple):
                                msg = email.message_from_bytes(response_part[1])
                                break
                                
                        if not msg:
                            self.stdout.write(self.style.ERROR(f"  Could not extract message object for email ID {email_id_str}"))
                            logger.error(f"BoL Email Fallback: Could not extract message for email ID {email_id_str}.")
                            continue

                        # Get email subject and date
                        subject_decoded = self.decode_email_header(msg["Subject"])
                        email_date = self.decode_email_header(msg["date"])
                        self.stdout.write(f"  📧 Subject: '{subject_decoded}'")
                        self.stdout.write(f"  📅 Date: {email_date}")
                        logger.info(f"BoL Email Fallback: Email Subject '{subject_decoded}' for ID {email_id_str}.")

                        # Extract PDF attachment
                        pdf_content, pdf_filename = self.extract_pdf_attachment(msg)
                        if not pdf_content:
                            continue

                        # Parse PDF data
                        parsed_bol_data = self.parse_bol_pdf_data(pdf_content, pdf_filename)
                        if not parsed_bol_data:
                            continue

                        # Get LON and Vehicle Registration from parsed data
                        kpc_lon_from_bol = parsed_bol_data.get('kpc_loading_order_no', 'UNKNOWN_LON')
                        vehicle_reg_from_bol = parsed_bol_data.get('vehicle_reg', None)

                        self.stdout.write(f"  📊 Extracted from BoL: LON='{kpc_lon_from_bol}', Vehicle='{vehicle_reg_from_bol}'")
                        logger.info(f"BoL Email Fallback: Extracted LON={kpc_lon_from_bol}, Vehicle={vehicle_reg_from_bol}")

                        # ✅ ENHANCED: Truck-based matching with order validation
                        trip_to_update, matching_method, confidence_score = self.find_trip_by_truck_and_order(
                            vehicle_reg_from_bol, kpc_lon_from_bol
                        )

                        if not trip_to_update:
                            self.stdout.write(self.style.ERROR(f"  Trip not found using truck-based or order-based matching. LON='{kpc_lon_from_bol}', Vehicle='{vehicle_reg_from_bol}'"))
                            logger.error(f"BoL Email Fallback: Trip not found for LON={kpc_lon_from_bol}, Vehicle={vehicle_reg_from_bol}")
                            continue

                        # Log matching details
                        self.stdout.write(f"  🎯 Match found: Trip {trip_to_update.id} ({trip_to_update.kpc_order_number}) via {matching_method} (confidence: {confidence_score:.2f})")
                        logger.info(f"BoL Email Fallback: Trip {trip_to_update.id} matched via {matching_method} with confidence {confidence_score:.2f}")

                        # ✅ ENHANCED: BoL Number Extraction with Email Subject Fallback
                        bol_shipment_no = parsed_bol_data.get('kpc_shipment_no')
                        
                        if not bol_shipment_no:
                            subject_match = re.search(r'Shipment\s*No\s*-\s*(\d+)', subject_decoded, re.IGNORECASE)
                            if subject_match:
                                bol_shipment_no = subject_match.group(1)
                                self.stdout.write(f"  📧 Extracted BoL number from email subject: {bol_shipment_no}")
                                logger.info(f"BoL Email Fallback: Extracted BoL number from subject: {bol_shipment_no}")
                            else:
                                self.stdout.write(self.style.WARNING("  BoL/Shipment number not found in PDF or email subject"))
                                logger.warning(f"BoL Email Fallback: No BoL number found in PDF or subject for Trip {trip_to_update.id}")
                        
                        # Store BoL number to save after other trip updates
                        bol_shipment_no_to_save = None
                        if bol_shipment_no and not trip_to_update.bol_number:
                             bol_shipment_no_to_save = bol_shipment_no
                        elif bol_shipment_no and trip_to_update.bol_number:
                            self.stdout.write(f"  ℹ️ Trip {trip_to_update.id} already has BoL number: {trip_to_update.bol_number}")
                            logger.info(f"BoL Email Fallback: Trip {trip_to_update.id} already has BoL number: {trip_to_update.bol_number}")

                        # Ensure trip has compartments before updating
                        self.ensure_trip_has_compartments(trip_to_update)

                        # Process actual compartments
                        actual_compartments = parsed_bol_data.get('actual_compartments', [])
                        if not actual_compartments:
                            self.stdout.write(self.style.WARNING("  No actual compartment data found in BoL PDF"))
                            logger.warning(f"BoL Email Fallback: No actual compartment data for Trip {trip_to_update.id}.")
                            processed_successfully_ids.append(email_id_bytes)
                            continue

                        # Update compartments with actual L20 quantities
                        updated_compartments = 0
                        for comp_data in actual_compartments:
                            comp_no = comp_data['compartment_no']
                            requested_qty = comp_data.get('quantity_requested_litres', Decimal('0.00'))
                            actual_l20_qty = comp_data['actual_quantity_l20']
                            
                            try:
                                compartment = LoadingCompartment.objects.get(
                                    trip=trip_to_update,
                                    compartment_number=comp_no
                                )
                                
                                # Update with both requested and actual L20 quantities from BoL
                                if requested_qty > 0:
                                    compartment.quantity_requested_litres = requested_qty
                                compartment.quantity_actual_l20 = actual_l20_qty
                                compartment.save()
                                updated_compartments += 1
                                
                                self.stdout.write(f"    ✅ FALLBACK: Updated Compartment {comp_no}: Requested={requested_qty}L, Actual L20={actual_l20_qty}L")
                                logger.info(f"BoL Email Fallback: Updated compartment {comp_no} to Req={requested_qty}L, Actual L20={actual_l20_qty}L for Trip {trip_to_update.id}")
                                
                            except LoadingCompartment.DoesNotExist:
                                self.stdout.write(self.style.WARNING(f"    Compartment {comp_no} not found for Trip {trip_to_update.id}"))
                                logger.warning(f"BoL Email Fallback: Compartment {comp_no} not found for Trip {trip_to_update.id}")
                            except Exception as comp_error:
                                self.stdout.write(self.style.ERROR(f"    Error updating compartment {comp_no}: {comp_error}"))
                                logger.error(f"BoL Email Fallback: Error updating compartment {comp_no}: {comp_error}")

                        if updated_compartments > 0:
                            # ✅ PROPER DEPLETION FLOW IMPLEMENTATION
                            original_status = trip_to_update.status
                            
                            if original_status != 'LOADED':
                                # Status will change, triggering the normal depletion logic in Trip.save()
                                trip_to_update.status = 'LOADED'
                                trip_to_update.save()
                                self.stdout.write(f"  ✅ FALLBACK: Updated Trip {trip_to_update.id} status to LOADED")
                                logger.info(f"BoL Email Fallback: Updated Trip {trip_to_update.id} status to LOADED")
                            else:
                                # Trip was already LOADED, recalculate depletion with new L20 data
                                self.stdout.write(f"  ✅ FALLBACK: Trip {trip_to_update.id} already LOADED. Recalculating depletion with L20 data...")
                                logger.info(f"BoL Email Fallback: Recalculating depletion for already-LOADED Trip {trip_to_update.id}")
                                
                                try:
                                    # Force reversal of existing depletion
                                    reversal_ok, reversal_msg = trip_to_update.reverse_stock_depletion(stdout_writer=self)
                                    if reversal_ok:
                                        self.stdout.write(f"    ✅ Reversed existing depletion: {reversal_msg}")
                                        logger.info(f"BoL Email Fallback: Depletion reversal for Trip {trip_to_update.id}: {reversal_msg}")
                                        
                                        # Create new depletion based on L20 actuals
                                        depletion_ok, depletion_msg = trip_to_update.perform_stock_depletion(
                                            stdout_writer=self,
                                            use_actual_l20=True,
                                            raise_error=True
                                        )
                                        if depletion_ok:
                                            self.stdout.write(f"    ✅ Created new L20-based depletion: {depletion_msg}")
                                            logger.info(f"BoL Email Fallback: New L20 depletion for Trip {trip_to_update.id}: {depletion_msg}")
                                        else:
                                            self.stdout.write(f"    ❌ Failed to create new depletion: {depletion_msg}")
                                            logger.error(f"BoL Email Fallback: Failed to create new depletion for Trip {trip_to_update.id}: {depletion_msg}")
                                    else:
                                        self.stdout.write(f"    ❌ Failed to reverse existing depletion: {reversal_msg}")
                                        logger.error(f"BoL Email Fallback: Failed to reverse depletion for Trip {trip_to_update.id}: {reversal_msg}")
                                except Exception as depletion_error:
                                    self.stdout.write(self.style.ERROR(f"    ❌ Error during depletion recalculation: {depletion_error}"))
                                    logger.error(f"BoL Email Fallback: Depletion recalculation error for Trip {trip_to_update.id}: {depletion_error}")

                            # Save BoL number if needed
                            if bol_shipment_no_to_save:
                                try:
                                    trip_to_update.bol_number = bol_shipment_no_to_save
                                    trip_to_update.save()
                                    self.stdout.write(f"    ✅ Saved BoL number: {bol_shipment_no_to_save}")
                                    logger.info(f"BoL Email Fallback: Saved BoL number {bol_shipment_no_to_save} for Trip {trip_to_update.id}")
                                except Exception as bol_error:
                                    self.stdout.write(self.style.ERROR(f"    ❌ Error saving BoL number: {bol_error}"))
                                    logger.error(f"BoL Email Fallback: Error saving BoL number for Trip {trip_to_update.id}: {bol_error}")

                            self.stdout.write(f"  ✅ FALLBACK: Successfully updated {updated_compartments} compartments for Trip {trip_to_update.id}")
                            logger.info(f"BoL Email Fallback: Successfully updated {updated_compartments} compartments for Trip {trip_to_update.id}")

                        processed_successfully_ids.append(email_id_bytes)
                        self.stdout.write(f"  ✅ BoL fallback processing completed for Trip {trip_to_update.id}")
                        logger.info(f"BoL Email Fallback: Completed processing for Trip {trip_to_update.id}")

                except ValidationError as e:
                    self.stdout.write(self.style.ERROR(f"  VALIDATION ERROR for BoL {kpc_lon_from_bol}: {e}"))
                    logger.error(f"BoL Email Fallback: ValidationError for LON {kpc_lon_from_bol} (Email ID {email_id_str}): {e}", exc_info=True)
                except IntegrityError as e:
                    self.stdout.write(self.style.ERROR(f"  DATABASE INTEGRITY ERROR for BoL {kpc_lon_from_bol}: {e}"))
                    logger.error(f"BoL Email Fallback: IntegrityError for LON {kpc_lon_from_bol} (Email ID {email_id_str}): {e}", exc_info=True)
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"  UNEXPECTED ERROR for BoL {kpc_lon_from_bol}: {e}"))
                    logger.error(f"BoL Email Fallback: Unexpected error for LON {kpc_lon_from_bol} (Email ID {email_id_str}): {e}", exc_info=True)
                finally:
                    logger.info(f"BoL Email Fallback: ----- Finished processing email ID {email_id_str} -----")

            # Summary
            self.stdout.write(f"\n📊 FALLBACK PROCESSING SUMMARY:")
            self.stdout.write(f"  📬 Total emails reviewed: {len(email_ids_list)}")
            self.stdout.write(f"  ✅ Successfully processed: {len(processed_successfully_ids)}")
            self.stdout.write(f"  ❌ Failed to process: {len(email_ids_list) - len(processed_successfully_ids)}")

            mail.logout()
            self.stdout.write(self.style.SUCCESS("✅ Finished FALLBACK processing of KPC BoL emails."))
            logger.info("BoL Email Fallback: Cycle Ended.")
            
        except imaplib.IMAP4.error as e_imap:
            self.stdout.write(self.style.ERROR(f"❌ IMAP Error: {e_imap}"))
            logger.critical(f"BoL Email Fallback: IMAP Error: {e_imap}", exc_info=True)
        except Exception as e_global:
            self.stdout.write(self.style.ERROR(f"❌ An unexpected global error: {e_global}"))
            logger.critical(f"BoL Email Fallback: Global unexpected error: {e_global}", exc_info=True)