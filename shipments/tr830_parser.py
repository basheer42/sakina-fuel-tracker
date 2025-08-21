# shipments/tr830_parser.py - FIXED VERSION - ONE ENTRY PER TR830
import re
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Tuple
import pdfplumber
from django.core.exceptions import ValidationError

logger = logging.getLogger('shipments')

class TR830ParseError(Exception):
    """Custom exception for TR830 parsing errors"""
    pass

class TR830Entry:
    """Class to represent a single TR830 entry"""
    def __init__(self):
        self.marks = ""  # Vessel/shipment name
        self.description = ""  # Product description
        self.avalue = None  # Quantity in litres
        self.product_type = ""  # PMS or AGO
        self.destination = ""  # Destination
        self.supplier = ""  # Supplier name if found
        self.reference_number = ""  # Any reference number found
        
    def __str__(self):
        return f"TR830Entry(marks='{self.marks}', product='{self.product_type}', dest='{self.destination}', avalue={self.avalue})"

class TR830Parser:
    """FIXED parser for TR830 PDF documents - ONE ENTRY PER DOCUMENT"""
    
    def __init__(self):
        # Enhanced date patterns
        self.date_patterns = [
            r'(?:DATE|Date)\s*[:\-]?\s*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{4})',
            r'(?:ON|on)\s*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{4})',
            r'\b(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{4})\b',
            r'(\d{4}[-\/]\d{1,2}[-\/]\d{1,2})',  # YYYY-MM-DD format
            r'(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4})',  # 24 Jul 2025
        ]
        
        # Product type mappings - EXACTLY as specified
        self.product_mappings = {
            # GASOIL = AGO
            'GASOIL': 'AGO',
            'GAS OIL': 'AGO',
            'DIESEL': 'AGO',
            'AGO': 'AGO',
            'AUTOMOTIVE GAS OIL': 'AGO',
            'AUTO GAS OIL': 'AGO',
            
            # MOGAS = PMS
            'MOGAS': 'PMS',
            'MOTOR GASOLINE': 'PMS',
            'MOTOR SPIRIT': 'PMS',
            'PETROL': 'PMS',
            'PMS': 'PMS',
            'GASOLINE': 'PMS',
            'PREMIUM MOTOR SPIRIT': 'PMS',
        }
        
        # Destination mappings - EXACTLY as specified
        self.destination_mappings = {
            'DR CONGO': 'DRC Congo',
            'DR. CONGO': 'DRC Congo',
            'DRC': 'DRC Congo',
            'DEMOCRATIC REPUBLIC': 'DRC Congo',
            'CONGO': 'DRC Congo',
            'SOUTH SUDAN': 'South Sudan',
            'S. SUDAN': 'South Sudan',
            'S SUDAN': 'South Sudan',
        }
        
        # STRICT vessel patterns - ONLY actual vessel names
        self.vessel_patterns = [
            r'(?:^|\n|\s)(?:MT\.?\s+)([A-Z][A-Z]+(?:\s+[A-Z]+)*)\s*(?:\n|$|\s)',  # MT. SEAENVOY pattern
            r'Marks[\s\n]+([A-Z][A-Z\s]+?)(?:\n|$)',  # Marks field content
        ]
        
        # STRICT quantity patterns - ONLY AVALUE/VL quantities
        self.quantity_patterns = [
            r'(?:AVALUE|A-VALUE)[\s\S]*?VL[\s\n]*(\d+(?:,\d{3})*)',  # AVALUE ... VL 107502
            r'TotalVolume[\s\n]*(\d+(?:,\d{3})*)',  # TotalVolume 107502
            r'VL[\s\n]*(\d+(?:,\d{3})*)',  # VL 107502
        ]
        
        # STRICT description patterns
        self.description_patterns = [
            r'([A-Z]+)\s+IN\s+TRANSIT\s+TO\s+([A-Z\s]+)',  # GASOIL IN TRANSIT TO DR CONGO
        ]

    def parse_pdf(self, pdf_file_path: str) -> Tuple[datetime, List[TR830Entry]]:
        """
        Parse a TR830 PDF file and extract ONE shipment entry
        
        Args:
            pdf_file_path: Path to the PDF file
            
        Returns:
            Tuple of (import_date, [single_entry])
            
        Raises:
            TR830ParseError: If parsing fails
        """
        try:
            with pdfplumber.open(pdf_file_path) as pdf:
                if not pdf.pages:
                    raise TR830ParseError("PDF has no pages")
                
                # Extract text from all pages
                full_text = ""
                
                for page_num, page in enumerate(pdf.pages):
                    try:
                        page_text = page.extract_text()
                        if page_text:
                            full_text += page_text + "\n"
                            
                    except Exception as e:
                        logger.warning(f"TR830: Error processing page {page_num + 1}: {e}")
                        continue
                
                if not full_text.strip():
                    raise TR830ParseError("Could not extract text from PDF")
                
                logger.info(f"TR830: Extracted {len(full_text)} characters from PDF")
                
                # Extract import date
                import_date = self._extract_date(full_text)
                
                # Extract SINGLE entry from the entire document
                entry = self._extract_single_entry(full_text)
                
                if not entry:
                    raise TR830ParseError("No valid entry found in TR830 document")
                
                logger.info(f"TR830: Successfully parsed entry: {entry}")
                return import_date, [entry]  # Always return single entry in list
                
        except pdfplumber.PDFSyntaxError as e:
            raise TR830ParseError(f"Invalid PDF file: {str(e)}")
        except Exception as e:
            logger.error(f"TR830: Error parsing PDF: {str(e)}")
            raise TR830ParseError(f"Failed to parse PDF: {str(e)}")

    def _extract_date(self, text: str) -> datetime:
        """Extract the import date from PDF text"""
        for pattern in self.date_patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                date_str = match.group(1)
                try:
                    # Try different date formats
                    for fmt in ['%d/%m/%Y', '%m/%d/%Y', '%Y-%m-%d', '%d %b %Y']:
                        try:
                            parsed_date = datetime.strptime(date_str, fmt)
                            logger.info(f"TR830: Extracted date: {parsed_date.date()} from '{date_str}'")
                            return parsed_date
                        except ValueError:
                            continue
                except ValueError:
                    continue
        
        # If no date found, use today's date as fallback
        logger.warning("TR830: Could not extract date, using today's date")
        return datetime.now()

    def _extract_single_entry(self, text: str) -> Optional[TR830Entry]:
        """Extract SINGLE entry from the entire TR830 document"""
        entry = TR830Entry()
        
        # 1. Extract vessel name from Marks section
        entry.marks = self._extract_vessel_name(text)
        
        # 2. Extract quantity from AVALUE section
        entry.avalue = self._extract_quantity(text)
        
        # 3. Extract product type and destination from description
        product, destination = self._extract_product_and_destination(text)
        entry.product_type = product
        entry.destination = destination
        
        # 4. Set supplier (default KPC)
        entry.supplier = 'KPC'
        
        # 5. Build description
        if entry.product_type and entry.destination:
            entry.description = f"{entry.product_type} IN TRANSIT TO {entry.destination}"
        
        # 6. Format vessel name
        if entry.marks:
            entry.marks = self._format_vessel_name(entry.marks)
        
        # 7. Validate entry
        if self._is_valid_entry(entry):
            return entry
        else:
            logger.error(f"TR830: Invalid entry - {entry}")
            return None

    def _extract_vessel_name(self, text: str) -> str:
        """Extract ONLY the actual vessel name from Marks section"""
        # Look for the pattern: Marks followed by vessel name
        marks_patterns = [
            r'(?:Marks|MARKS)[\s\n]+(MT\.?\s*[A-Z]+(?:\s+[A-Z]+)*?)(?:\s|$|\n)',
            r'(?:^|\n)(MT\.?\s*[A-Z]+(?:\s+[A-Z]+)*)\s*(?:\n|$)',
        ]
        
        for pattern in marks_patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
            if match:
                vessel_name = match.group(1).strip()
                # Filter out obvious form labels/headers
                if not self._is_form_label(vessel_name):
                    logger.info(f"TR830: Found vessel name: {vessel_name}")
                    return vessel_name
        
        return ""

    def _is_form_label(self, text: str) -> bool:
        """Check if text is a form label rather than a vessel name"""
        form_labels = [
            'COMMODITY', 'LOCATION', 'FINANCIAL', 'BANKING', 'DELIVERY', 'TERMS',
            'REGION', 'DESTINATION', 'CODE', 'DATE', 'EXIT', 'VALUATION', 'METHOD',
            'REFERENCE', 'NUMBER', 'TOTAL', 'EXPORTER', 'PIN', 'CUSTOMS', 'OFFICE'
        ]
        
        text_upper = text.upper()
        return any(label in text_upper for label in form_labels)

    def _extract_quantity(self, text: str) -> Optional[Decimal]:
        """Extract quantity from AVALUE section"""
        for pattern in self.quantity_patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                quantity_str = match.group(1)
                quantity = self._parse_quantity(quantity_str)
                if quantity and quantity > 1000:  # Reasonable minimum
                    logger.info(f"TR830: Found quantity: {quantity} from '{quantity_str}'")
                    return quantity
        
        return None

    def _extract_product_and_destination(self, text: str) -> Tuple[str, str]:
        """Extract product type and destination from description"""
        # Look for "PRODUCT IN TRANSIT TO DESTINATION" pattern
        for pattern in self.description_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                product_text = match.group(1).strip()
                destination_text = match.group(2).strip()
                
                product_type = self._map_product_type(product_text)
                destination = self._map_destination(destination_text)
                
                logger.info(f"TR830: Found product: {product_type}, destination: {destination}")
                return product_type, destination
        
        # Fallback: extract separately
        product_type = self._extract_product_type(text)
        destination = self._extract_destination(text)
        
        return product_type, destination

    def _extract_product_type(self, text: str) -> str:
        """Extract product type from text"""
        text_upper = text.upper()
        for product_key, product_value in self.product_mappings.items():
            if product_key in text_upper:
                logger.info(f"TR830: Found product type: {product_value} from '{product_key}'")
                return product_value
        return ""

    def _extract_destination(self, text: str) -> str:
        """Extract destination from text"""
        text_upper = text.upper()
        for dest_key, dest_value in self.destination_mappings.items():
            if dest_key in text_upper:
                logger.info(f"TR830: Found destination: {dest_value} from '{dest_key}'")
                return dest_value
        return ""

    def _map_product_type(self, product_text: str) -> str:
        """Map product text to standard product type"""
        product_upper = product_text.upper()
        for key, value in self.product_mappings.items():
            if key in product_upper:
                return value
        return product_text

    def _map_destination(self, dest_text: str) -> str:
        """Map destination text to standard destination"""
        dest_upper = dest_text.upper().strip()
        for key, value in self.destination_mappings.items():
            if key in dest_upper:
                return value
        return dest_text

    def _parse_quantity(self, quantity_str: str) -> Optional[Decimal]:
        """Parse quantity string to Decimal"""
        if not quantity_str:
            return None
        
        try:
            # Clean the string
            cleaned = re.sub(r'[^\d,.]', '', str(quantity_str))
            cleaned = cleaned.replace(',', '')
            
            if cleaned:
                quantity = Decimal(cleaned)
                # Validate reasonable range for fuel quantities
                if 1000 <= quantity <= 1000000000:  # 1K to 1B litres
                    return quantity
        except (InvalidOperation, ValueError):
            pass
        
        return None

    def _format_vessel_name(self, vessel_name: str) -> str:
        """Format vessel name consistently"""
        if not vessel_name:
            return ""
        
        # Clean and normalize
        formatted = re.sub(r'\s+', ' ', vessel_name.strip())
        formatted = formatted.upper()
        
        # Ensure proper MT. prefix
        if not formatted.startswith(('MT.', 'MT ', 'MV.', 'MV ')):
            formatted = f"MT. {formatted}"
        
        # Limit length
        if len(formatted) > 50:
            formatted = formatted[:50]
        
        return formatted.strip()

    def _is_valid_entry(self, entry: TR830Entry) -> bool:
        """Check if entry has minimum required fields"""
        has_vessel = bool(entry.marks)
        has_quantity = bool(entry.avalue) and entry.avalue > 0
        has_product = bool(entry.product_type)
        has_destination = bool(entry.destination)
        
        is_valid = has_vessel and has_quantity and has_product and has_destination
        
        if not is_valid:
            logger.debug(f"TR830: Entry validation failed - Vessel:{has_vessel}, QTY:{has_quantity}, Product:{has_product}, Dest:{has_destination}")
        
        return is_valid

    def get_parsing_summary(self, entries: List[TR830Entry]) -> Dict:
        """Generate summary of parsed entries"""
        if not entries:
            return {
                'total_entries': 0,
                'pms_entries': 0,
                'ago_entries': 0,
                'total_quantity': Decimal('0'),
                'destinations': [],
                'suppliers': [],
                'errors': ['No entries found']
            }
        
        entry = entries[0]  # Should only be one entry
        
        summary = {
            'total_entries': len(entries),
            'pms_entries': 1 if entry.product_type == 'PMS' else 0,
            'ago_entries': 1 if entry.product_type == 'AGO' else 0,
            'total_quantity': entry.avalue or Decimal('0'),
            'destinations': [entry.destination] if entry.destination else [],
            'suppliers': [entry.supplier] if entry.supplier else [],
            'vessels': [entry.marks] if entry.marks else [],
            'errors': []
        }
        
        return summary

    def validate_parsing_result(self, entries: List[TR830Entry]) -> Tuple[bool, List[str]]:
        """Validate parsing results"""
        errors = []
        
        if not entries:
            errors.append("No entries found in TR830 document")
            return False, errors
        
        if len(entries) > 1:
            errors.append(f"Expected 1 entry but found {len(entries)} - TR830 should create only one shipment")
        
        entry = entries[0]
        
        if not entry.marks:
            errors.append("Missing vessel name")
        
        if not entry.avalue or entry.avalue <= 0:
            errors.append("Missing or invalid quantity")
        
        if not entry.product_type:
            errors.append("Missing product type")
        
        if not entry.destination:
            errors.append("Missing destination")
        
        return len(errors) == 0, errors