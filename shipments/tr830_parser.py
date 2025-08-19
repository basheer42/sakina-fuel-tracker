# shipments/tr830_parser.py - ENHANCED VERSION
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
    """Enhanced parser for TR830 PDF documents with robust regex patterns and fallbacks"""
    
    def __init__(self):
        # Enhanced date patterns based on your BoL parser style
        self.date_patterns = [
            r'(?:DATE|Date)\s*[:\-]?\s*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{4})',
            r'(?:ON|on)\s*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{4})',
            r'\b(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{4})\b',
            r'(\d{4}[-\/]\d{1,2}[-\/]\d{1,2})',  # YYYY-MM-DD format
            r'(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4})',  # 24 Jul 2025
        ]
        
        # Product type mappings - more comprehensive than original
        self.product_mappings = {
            # Gasoline/PMS variants
            'GASOIL': 'AGO',
            'GAS OIL': 'AGO', 
            'DIESEL': 'AGO',
            'AGO': 'AGO',
            'AUTOMOTIVE GAS OIL': 'AGO',
            'FUEL OIL': 'AGO',
            'DISTILLATE': 'AGO',
            
            # Motor gasoline/PMS variants
            'MOGAS': 'PMS',
            'MOTOR GASOLINE': 'PMS',
            'MOTOR SPIRIT': 'PMS',
            'PETROL': 'PMS',
            'PMS': 'PMS',
            'GASOLINE': 'PMS',
            'PREMIUM MOTOR SPIRIT': 'PMS',
            'SUPER': 'PMS',
            'UNLEADED': 'PMS',
        }
        
        # Enhanced destination patterns based on your system
        self.destination_patterns = [
            r'(?:TRANSIT\s+TO|TO|DESTINATION)\s+([A-Z\s]+?)(?:\s|$|\n)',
            r'(?:CONSIGNEE|FINAL\s+DESTINATION)[:\s]*([A-Z\s]+?)(?:\s|$|\n)',
            r'(?:DR\.?\s*CONGO|DRC|DEMOCRATIC\s+REPUBLIC)',  # DR Congo variants
            r'(?:SOUTH\s+SUDAN|S\.?\s*SUDAN)',  # South Sudan variants
            r'(?:LOCAL|NAIROBI|KENYA|MOMBASA)',  # Local variants
            r'IN\s+TRANSIT\s+TO\s+([A-Z\s]+)',
        ]
        
        # Enhanced supplier patterns
        self.supplier_patterns = [
            r'(?:KUWAIT\s+PETROLEUM\s+CORPORATION|KPC)',
            r'(?:CONSIGNOR|EXPORTER)[:\s]*([A-Z\s&.,]+?)(?:\n|$)',
            r'(?:SUPPLIER|SHIPPER)[:\s]*([A-Z\s&.,]+?)(?:\n|$)',
            r'(?:FROM|SOURCE)[:\s]*([A-Z\s&.,]+?)(?:\n|$)',
        ]
        
        # Vessel/Marks patterns - more robust
        self.vessel_patterns = [
            r'(?:MT\.?\s*|MV\.?\s*|VESSEL\s*[:\-]?\s*)([A-Z\s\d]+)',  # MT. VESSEL NAME
            r'(?:MARKS|VESSEL)[:\s]*([A-Z\s\d]+?)(?:\n|$)',
            r'(?:SHIP\s*NAME|VESSEL\s*NAME)[:\s]*([A-Z\s\d]+?)(?:\n|$)',
            r'\b(MT\s+[A-Z\s\d]+)\b',  # MT followed by name
        ]
        
        # Enhanced quantity patterns - based on your BoL parser
        self.quantity_patterns = [
            # Direct AVALUE patterns
            r'(?:AVALUE|A-VALUE|Total\s*Volume)\s*[:\-]?\s*(\d+(?:,\d{3})*(?:\.\d{2})?)',
            r'(?:TOTAL|QUANTITY|QTY)[:\s]*(\d+(?:,\d{3})*(?:\.\d{2})?)\s*(?:LITRES?|L|LTR)',
            r'(?:VOLUME|VOL)[:\s]*(\d+(?:,\d{3})*(?:\.\d{2})?)',
            
            # Table-based patterns
            r'(\d+(?:,\d{3})*(?:\.\d{2})?)\s*(?:LITRES?|L|LTR)',
            
            # Fallback numeric patterns
            r'\b(\d{6,})\b',  # Large numbers (likely quantities)
        ]
        
        # Reference number patterns
        self.reference_patterns = [
            r'(?:REF|REFERENCE|REF\.?|NO\.?)[:\s]*([A-Z\d\-\/]+)',
            r'(?:TR\s*830|TR830)[:\s]*([A-Z\d\-\/]+)',
            r'(?:DOCUMENT|DOC)[:\s]*(?:NO\.?|NUMBER)[:\s]*([A-Z\d\-\/]+)',
            r'\b([A-Z]{2,}\d{4,})\b',  # Pattern like ABC1234
        ]

    def parse_pdf(self, pdf_file_path: str) -> Tuple[datetime, List[TR830Entry]]:
        """
        Parse a TR830 PDF file and extract shipment entries with enhanced error handling
        
        Args:
            pdf_file_path: Path to the PDF file
            
        Returns:
            Tuple of (import_date, list_of_entries)
            
        Raises:
            TR830ParseError: If parsing fails
        """
        try:
            with pdfplumber.open(pdf_file_path) as pdf:
                if not pdf.pages:
                    raise TR830ParseError("PDF has no pages")
                
                # Extract text from all pages
                full_text = ""
                all_tables = []
                
                for page_num, page in enumerate(pdf.pages):
                    try:
                        page_text = page.extract_text()
                        if page_text:
                            full_text += page_text + "\n"
                        
                        # Extract tables for structured data
                        page_tables = page.extract_tables()
                        if page_tables:
                            all_tables.extend(page_tables)
                            
                    except Exception as e:
                        logger.warning(f"Error extracting from page {page_num + 1}: {e}")
                        continue
                
                if not full_text.strip():
                    raise TR830ParseError("Could not extract text from PDF")
                
                logger.info(f"TR830: Extracted {len(full_text)} characters from PDF")
                logger.debug(f"TR830: First 500 chars: {full_text[:500]}")
                
                # Clean text for better parsing
                cleaned_text = self._clean_text(full_text)
                
                # Parse the date with multiple fallbacks
                import_date = self._extract_date_with_fallbacks(cleaned_text)
                
                # Parse entries using multiple methods
                entries = self._extract_entries_comprehensive(cleaned_text, all_tables)
                
                if not entries:
                    raise TR830ParseError("No valid entries found in TR830 document")
                
                logger.info(f"TR830: Successfully parsed {len(entries)} entries")
                return import_date, entries
                
        except Exception as e:
            logger.error(f"TR830: Error parsing PDF: {str(e)}")
            raise TR830ParseError(f"Failed to parse PDF: {str(e)}")

    def _clean_text(self, text: str) -> str:
        """Clean and normalize text for better parsing"""
        # Remove excessive whitespace but preserve line breaks
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n\s*\n', '\n', text)
        
        # Normalize common separators
        text = text.replace(':', ': ')
        text = text.replace('-', ' - ')
        
        return text.strip()

    def _extract_date_with_fallbacks(self, text: str) -> datetime:
        """Extract date with multiple fallback strategies"""
        logger.info("TR830: Attempting to extract date...")
        
        # Primary date extraction
        for i, pattern in enumerate(self.date_patterns):
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                date_str = match.group(1)
                parsed_date = self._try_parse_date(date_str)
                if parsed_date:
                    logger.info(f"TR830: Found date '{parsed_date.date()}' using pattern {i+1}")
                    return parsed_date
        
        # Fallback 1: Look for any date-like strings
        fallback_patterns = [
            r'\b(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})\b',
            r'\b(\d{4}[\/\-\.]\d{1,2}[\/\-\.]\d{1,2})\b',
        ]
        
        for pattern in fallback_patterns:
            matches = re.finditer(pattern, text)
            for match in matches:
                date_str = match.group(1)
                parsed_date = self._try_parse_date(date_str)
                if parsed_date:
                    logger.info(f"TR830: Found date '{parsed_date.date()}' using fallback")
                    return parsed_date
        
        # Final fallback: use today's date
        logger.warning("TR830: Could not extract date, using today's date")
        return datetime.now()

    def _try_parse_date(self, date_str: str) -> Optional[datetime]:
        """Try to parse date string with multiple formats"""
        formats = [
            '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y', '%m-%d-%Y',
            '%d.%m.%Y', '%m.%d.%Y', '%Y-%m-%d', '%Y/%m/%d',
            '%d %b %Y', '%d %B %Y', '%b %d %Y', '%B %d %Y',
            '%d/%m/%y', '%m/%d/%y', '%d-%m-%y', '%m-%d-%y',
        ]
        
        for fmt in formats:
            try:
                parsed = datetime.strptime(date_str.strip(), fmt)
                # Validate reasonable year range
                if 1990 <= parsed.year <= 2050:
                    return parsed
            except ValueError:
                continue
        
        return None

    def _extract_entries_comprehensive(self, text: str, tables: List[List]) -> List[TR830Entry]:
        """Extract entries using comprehensive approach with multiple fallbacks"""
        entries = []
        
        # Method 1: Try table-based extraction first
        table_entries = self._extract_from_tables(tables)
        if table_entries:
            entries.extend(table_entries)
            logger.info(f"TR830: Extracted {len(table_entries)} entries from tables")
        
        # Method 2: Text-based extraction as fallback
        if not entries:
            text_entries = self._extract_from_text(text)
            if text_entries:
                entries.extend(text_entries)
                logger.info(f"TR830: Extracted {len(text_entries)} entries from text")
        
        # Method 3: Pattern-based extraction as final fallback
        if not entries:
            pattern_entries = self._extract_using_patterns(text)
            if pattern_entries:
                entries.extend(pattern_entries)
                logger.info(f"TR830: Extracted {len(pattern_entries)} entries using patterns")
        
        # Validate and clean entries
        validated_entries = []
        for entry in entries:
            if self._validate_and_enhance_entry(entry, text):
                validated_entries.append(entry)
        
        return validated_entries

    def _extract_from_tables(self, tables: List[List]) -> List[TR830Entry]:
        """Extract entries from structured table data"""
        entries = []
        
        for table_idx, table in enumerate(tables):
            if not table or len(table) < 2:
                continue
            
            logger.info(f"TR830: Processing table {table_idx + 1} with {len(table)} rows")
            
            # Look for header row
            header_row = table[0] if table[0] else []
            header_text = " ".join([str(cell or "").strip() for cell in header_row]).upper()
            
            # Check if this looks like a TR830 table
            if any(keyword in header_text for keyword in [
                'MARKS', 'DESCRIPTION', 'AVALUE', 'QUANTITY', 'VESSEL', 'PRODUCT'
            ]):
                logger.info(f"TR830: Found TR830-style table header: {header_text[:100]}...")
                
                # Process data rows
                for row_idx, row in enumerate(table[1:], 1):
                    if not row or not any(cell for cell in row):
                        continue
                    
                    entry = self._extract_entry_from_table_row(row, header_row)
                    if entry:
                        entries.append(entry)
                        logger.debug(f"TR830: Extracted entry from table row {row_idx}: {entry}")
        
        return entries

    def _extract_entry_from_table_row(self, row: List, headers: List) -> Optional[TR830Entry]:
        """Extract a single entry from a table row"""
        try:
            entry = TR830Entry()
            
            # Convert headers to lowercase for easier matching
            header_map = {}
            for i, header in enumerate(headers):
                if header:
                    header_clean = str(header).strip().lower()
                    header_map[header_clean] = i
            
            # Extract marks/vessel
            for marks_key in ['marks', 'vessel', 'ship', 'name']:
                if marks_key in header_map and len(row) > header_map[marks_key]:
                    cell_value = row[header_map[marks_key]]
                    if cell_value:
                        entry.marks = str(cell_value).strip()
                        break
            
            # Extract description
            for desc_key in ['description', 'product', 'commodity']:
                if desc_key in header_map and len(row) > header_map[desc_key]:
                    cell_value = row[header_map[desc_key]]
                    if cell_value:
                        entry.description = str(cell_value).strip()
                        entry.product_type = self._identify_product_type(entry.description)
                        break
            
            # Extract quantity (AVALUE)
            for qty_key in ['avalue', 'quantity', 'total', 'volume']:
                if qty_key in header_map and len(row) > header_map[qty_key]:
                    cell_value = row[header_map[qty_key]]
                    if cell_value:
                        quantity = self._parse_quantity(str(cell_value))
                        if quantity:
                            entry.avalue = quantity
                            break
            
            # If no specific mapping, try to extract from any cell
            if not entry.avalue:
                for cell in row:
                    if cell:
                        quantity = self._parse_quantity(str(cell))
                        if quantity and quantity > 1000:  # Reasonable minimum
                            entry.avalue = quantity
                            break
            
            return entry if entry.marks or entry.description or entry.avalue else None
            
        except Exception as e:
            logger.warning(f"TR830: Error extracting from table row: {e}")
            return None

    def _extract_from_text(self, text: str) -> List[TR830Entry]:
        """Extract entries from unstructured text"""
        entry = TR830Entry()
        
        # Extract vessel/marks
        for pattern in self.vessel_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                entry.marks = match.group(1).strip()
                logger.info(f"TR830: Found vessel marks: {entry.marks}")
                break
        
        # Extract product and quantity
        entry.avalue = self._extract_quantity_from_text(text)
        entry.product_type = self._extract_product_type_from_text(text)
        entry.destination = self._extract_destination_from_text(text)
        entry.supplier = self._extract_supplier_from_text(text)
        entry.reference_number = self._extract_reference_from_text(text)
        
        # Build description
        if entry.product_type and entry.destination:
            entry.description = f"{entry.product_type} IN TRANSIT TO {entry.destination}"
        elif entry.product_type:
            entry.description = entry.product_type
        
        return [entry] if self._is_valid_entry(entry) else []

    def _extract_using_patterns(self, text: str) -> List[TR830Entry]:
        """Extract using specific TR830 patterns as final fallback"""
        entries = []
        
        # Look for specific TR830 patterns in the text
        tr830_pattern = r'(MT\.?\s*[A-Z\s]+).*?(\d+(?:,\d{3})*)\s*(?:LITRES?|L)'
        matches = re.finditer(tr830_pattern, text, re.IGNORECASE | re.DOTALL)
        
        for match in matches:
            entry = TR830Entry()
            entry.marks = match.group(1).strip()
            entry.avalue = self._parse_quantity(match.group(2))
            entry.product_type = self._extract_product_type_from_text(text)
            entry.destination = self._extract_destination_from_text(text)
            
            if self._is_valid_entry(entry):
                entries.append(entry)
        
        return entries

    def _extract_quantity_from_text(self, text: str) -> Optional[Decimal]:
        """Extract quantity using multiple patterns"""
        for pattern in self.quantity_patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                quantity_str = match.group(1)
                quantity = self._parse_quantity(quantity_str)
                if quantity and quantity > 1000:  # Reasonable minimum for fuel quantities
                    logger.info(f"TR830: Found quantity: {quantity}")
                    return quantity
        
        return None

    def _extract_product_type_from_text(self, text: str) -> str:
        """Extract and normalize product type"""
        for product_key, normalized in self.product_mappings.items():
            if re.search(r'\b' + re.escape(product_key) + r'\b', text, re.IGNORECASE):
                logger.info(f"TR830: Found product type: {normalized}")
                return normalized
        
        # Fallback: look for common patterns
        if re.search(r'\b(?:GAS|DIESEL|AGO)\b', text, re.IGNORECASE):
            return 'AGO'
        elif re.search(r'\b(?:PETROL|PMS|GASOLINE|MOTOR)\b', text, re.IGNORECASE):
            return 'PMS'
        
        logger.warning("TR830: Could not determine product type")
        return ""

    def _extract_destination_from_text(self, text: str) -> str:
        """Extract destination with fallbacks"""
        for pattern in self.destination_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                if len(match.groups()) > 0:
                    dest = match.group(1).strip()
                else:
                    dest = match.group(0).strip()
                
                # Normalize common destinations
                dest_clean = self._normalize_destination(dest)
                if dest_clean:
                    logger.info(f"TR830: Found destination: {dest_clean}")
                    return dest_clean
        
        return ""

    def _extract_supplier_from_text(self, text: str) -> str:
        """Extract supplier information"""
        for pattern in self.supplier_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                if 'KPC' in pattern or 'KUWAIT' in pattern:
                    return "KPC"
                elif len(match.groups()) > 0:
                    supplier = match.group(1).strip()
                    return supplier[:100]  # Limit length
        
        return ""

    def _extract_reference_from_text(self, text: str) -> str:
        """Extract reference number"""
        for pattern in self.reference_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                ref = match.group(1).strip()
                logger.info(f"TR830: Found reference: {ref}")
                return ref
        
        return ""

    def _parse_quantity(self, quantity_str: str) -> Optional[Decimal]:
        """Parse quantity string to decimal with error handling"""
        if not quantity_str:
            return None
        
        try:
            # Clean quantity string
            clean_qty = re.sub(r'[^\d.,]', '', str(quantity_str))
            clean_qty = clean_qty.replace(',', '')
            
            if not clean_qty:
                return None
            
            quantity = Decimal(clean_qty)
            
            # Validate reasonable range
            if Decimal('100') <= quantity <= Decimal('1000000'):
                return quantity
            
        except (InvalidOperation, ValueError) as e:
            logger.debug(f"TR830: Could not parse quantity '{quantity_str}': {e}")
        
        return None

    def _identify_product_type(self, description: str) -> str:
        """Identify product type from description"""
        if not description:
            return ""
        
        desc_upper = description.upper()
        for product_key, normalized in self.product_mappings.items():
            if product_key in desc_upper:
                return normalized
        
        return ""

    def _normalize_destination(self, destination: str) -> str:
        """Normalize destination names"""
        if not destination:
            return ""
        
        dest_upper = destination.upper().strip()
        
        # Common destination mappings
        dest_mappings = {
            'DR CONGO': 'DRC',
            'DEMOCRATIC REPUBLIC': 'DRC',
            'CONGO': 'DRC', 
            'SOUTH SUDAN': 'South Sudan',
            'S SUDAN': 'South Sudan',
            'SUDAN': 'South Sudan',
            'KENYA': 'Local Nairobi',
            'NAIROBI': 'Local Nairobi',
            'LOCAL': 'Local Nairobi',
            'MOMBASA': 'Local Nairobi',
        }
        
        for key, normalized in dest_mappings.items():
            if key in dest_upper:
                return normalized
        
        return dest_upper[:50]  # Return cleaned, limited version

    def _validate_and_enhance_entry(self, entry: TR830Entry, full_text: str) -> bool:
        """Validate entry and enhance with missing data"""
        if not self._is_valid_entry(entry):
            return False
        
        # Enhance missing fields
        if not entry.product_type:
            entry.product_type = self._extract_product_type_from_text(full_text)
        
        if not entry.destination:
            entry.destination = self._extract_destination_from_text(full_text)
        
        if not entry.supplier:
            entry.supplier = self._extract_supplier_from_text(full_text)
        
        if not entry.description and entry.product_type:
            if entry.destination:
                entry.description = f"{entry.product_type} IN TRANSIT TO {entry.destination}"
            else:
                entry.description = entry.product_type
        
        # Format vessel ID
        if entry.marks:
            entry.marks = self.format_vessel_id(entry.marks)
        
        return True

    def _is_valid_entry(self, entry: TR830Entry) -> bool:
        """Check if an entry has the minimum required fields"""
        has_identifier = bool(entry.marks or entry.description or entry.reference_number)
        has_quantity = bool(entry.avalue) and entry.avalue > 0
        has_product = bool(entry.product_type)
        
        is_valid = has_identifier and has_quantity and has_product
        
        if not is_valid:
            logger.debug(f"TR830: Entry validation failed - ID:{has_identifier}, QTY:{has_quantity}, PROD:{has_product}")
        
        return is_valid

    def format_vessel_id(self, marks: str) -> str:
        """Format the vessel ID for consistency"""
        if not marks:
            return ""
        
        # Clean and normalize
        vessel_id = re.sub(r'\s+', ' ', marks.strip())
        vessel_id = vessel_id.upper()
        
        # Remove common prefixes if they make it too long
        vessel_id = re.sub(r'^(MT\.?\s*|MV\.?\s*)', '', vessel_id)
        
        # Limit length
        if len(vessel_id) > 50:
            vessel_id = vessel_id[:50]
        
        return vessel_id.strip()

    def get_parsing_summary(self, entries: List[TR830Entry]) -> Dict:
        """Generate a comprehensive summary of parsed entries"""
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
        
        summary = {
            'total_entries': len(entries),
            'pms_entries': len([e for e in entries if e.product_type == 'PMS']),
            'ago_entries': len([e for e in entries if e.product_type == 'AGO']),
            'total_quantity': sum(e.avalue for e in entries if e.avalue),
            'destinations': list(set(e.destination for e in entries if e.destination)),
            'suppliers': list(set(e.supplier for e in entries if e.supplier)),
            'vessels': list(set(e.marks for e in entries if e.marks)),
            'references': list(set(e.reference_number for e in entries if e.reference_number)),
            'products_found': list(set(e.product_type for e in entries if e.product_type)),
            'errors': []
        }
        
        # Add validation warnings
        missing_fields = []
        if not any(e.marks for e in entries):
            missing_fields.append('vessel marks')
        if not any(e.destination for e in entries):
            missing_fields.append('destinations')
        if not any(e.supplier for e in entries):
            missing_fields.append('suppliers')
        
        if missing_fields:
            summary['warnings'] = [f"Missing: {', '.join(missing_fields)}"]
        
        return summary

    def validate_parsing_result(self, entries: List[TR830Entry]) -> Tuple[bool, List[str]]:
        """Validate the overall parsing result"""
        errors = []
        
        if not entries:
            errors.append("No entries found in TR830 document")
            return False, errors
        
        # Check for minimum data quality
        entries_with_vessel = [e for e in entries if e.marks]
        entries_with_quantity = [e for e in entries if e.avalue and e.avalue > 0]
        entries_with_product = [e for e in entries if e.product_type]
        
        if len(entries_with_vessel) == 0:
            errors.append("No vessel information found")
        
        if len(entries_with_quantity) == 0:
            errors.append("No valid quantities found")
        
        if len(entries_with_product) == 0:
            errors.append("No product types identified")
        
        # Check for reasonable quantities
        total_quantity = sum(e.avalue for e in entries_with_quantity)
        if total_quantity < 1000:
            errors.append(f"Total quantity seems too low: {total_quantity}L")
        elif total_quantity > 1000000:
            errors.append(f"Total quantity seems too high: {total_quantity}L")
        
        success = len(errors) == 0
        return success, errors

# Usage example and testing function
def test_tr830_parser():
    """Test function to validate the parser with sample data"""
    parser = TR830Parser()
    
    # Test text parsing
    sample_text = """
    TR830 DOCUMENT
    DATE: 24/07/2025
    
    MT. SAKINA VESSEL
    DESCRIPTION: GASOIL IN TRANSIT TO DR CONGO
    AVALUE: 200,000
    CONSIGNOR: KUWAIT PETROLEUM CORPORATION
    """
    
    try:
        # Test text-based extraction
        entries = parser._extract_from_text(sample_text)
        print(f"Extracted {len(entries)} entries from sample text")
        
        for entry in entries:
            print(f"  - {entry}")
        
        # Test validation
        success, errors = parser.validate_parsing_result(entries)
        print(f"Validation: {'PASSED' if success else 'FAILED'}")
        if errors:
            for error in errors:
                print(f"  Error: {error}")
        
        # Test summary
        summary = parser.get_parsing_summary(entries)
        print(f"Summary: {summary}")
        
    except Exception as e:
        print(f"Test failed: {e}")

if __name__ == "__main__":
    test_tr830_parser()