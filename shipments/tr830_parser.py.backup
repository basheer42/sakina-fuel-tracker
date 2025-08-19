# shipments/tr830_parser.py
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
        
    def __str__(self):
        return f"TR830Entry(marks='{self.marks}', product='{self.product_type}', dest='{self.destination}', avalue={self.avalue})"

class TR830Parser:
    """Parser for TR830 PDF documents"""
    
    def __init__(self):
        # Patterns for extracting data from your TR830 format
        self.date_patterns = [
            r'(\d{1,2}/\d{1,2}/\d{4})',  # 24/07/2025 format
            r'(\d{4}-\d{1,2}-\d{1,2})',  # 2025-07-24 format
            r'DATE:\s*(\d{1,2}[/-]\d{1,2}[/-]\d{4})',
        ]
        
        # Product type mappings based on your TR830
        self.product_mappings = {
            'GASOIL': 'AGO',
            'GAS OIL': 'AGO',
            'DIESEL': 'AGO',
            'AGO': 'AGO',
            'MOGAS': 'PMS',
            'MOTOR GASOLINE': 'PMS',
            'PETROL': 'PMS',
            'PMS': 'PMS',
        }
        
        # Common destination patterns
        self.destination_patterns = [
            r'TRANSIT TO ([A-Z\s]+)',  # "TRANSIT TO DR CONGO"
            r'DESTINATION[:\s]*([A-Z\s]+)',
            r'TO[:\s]*([A-Z\s]+)',
        ]
        
        # Supplier patterns (consignor section)
        self.supplier_patterns = [
            r'KUWAIT PETROLEUM CORPORATION',
            r'KPC',
            r'Consignor/Exporter[:\s]*([A-Z\s&.,]+)',
            r'2\s+Consignor/Exporter[:\s]*([A-Z\s&.,]+)',
        ]

    def parse_pdf(self, pdf_file_path: str) -> Tuple[datetime, List[TR830Entry]]:
        """
        Parse a TR830 PDF file and extract shipment entries
        
        Args:
            pdf_file_path: Path to the PDF file
            
        Returns:
            Tuple of (import_date, list_of_entries)
            
        Raises:
            TR830ParseError: If parsing fails
        """
        try:
            with pdfplumber.open(pdf_file_path) as pdf:
                # Extract text from all pages
                full_text = ""
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        full_text += page_text + "\n"
                
                if not full_text.strip():
                    raise TR830ParseError("Could not extract text from PDF")
                
                logger.info(f"Extracted text length: {len(full_text)} characters")
                logger.debug(f"First 500 chars: {full_text[:500]}")
                
                # Parse the date
                import_date = self._extract_date(full_text)
                
                # Parse entries
                entries = self._extract_entries(full_text)
                
                if not entries:
                    raise TR830ParseError("No valid entries found in TR830 document")
                
                logger.info(f"Successfully parsed {len(entries)} entries from TR830")
                return import_date, entries
                
        except Exception as e:
            logger.error(f"Error parsing TR830 PDF: {str(e)}")
            raise TR830ParseError(f"Failed to parse PDF: {str(e)}")

    def _extract_date(self, text: str) -> datetime:
        """Extract the import date from PDF text"""
        # Look for date patterns in the entire document
        for pattern in self.date_patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                date_str = match.group(1)
                try:
                    # Try different date formats
                    for fmt in ['%d/%m/%Y', '%m/%d/%Y', '%Y-%m-%d']:
                        try:
                            parsed_date = datetime.strptime(date_str, fmt)
                            logger.info(f"Extracted date: {parsed_date.date()} from '{date_str}'")
                            return parsed_date
                        except ValueError:
                            continue
                except ValueError:
                    continue
        
        # If no date found, use today's date as fallback
        logger.warning("Could not extract date from TR830, using today's date")
        return datetime.now()

    def _extract_entries(self, text: str) -> List[TR830Entry]:
        """Extract all entries from the PDF text"""
        entries = []
        
        # Look for the NAME/AVALUE table section
        entry = TR830Entry()
        
        # Extract vessel name from MARKS field
        marks_match = re.search(r'MT\.\s*([A-Z\s]+)', text, re.IGNORECASE)
        if marks_match:
            entry.marks = marks_match.group(0).strip()
            logger.info(f"Found vessel marks: {entry.marks}")
        
        # Extract AVALUE (Total Volume)
        avalue_patterns = [
            r'TotalVolume\s+(\d+)',  # From your TR830: TotalVolume 200000
            r'AVALUE\s*(\d+)',
            r'(\d+)\s*LTR',  # 200000 LTR
        ]
        
        for pattern in avalue_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    entry.avalue = Decimal(match.group(1))
                    logger.info(f"Found AVALUE: {entry.avalue}")
                    break
                except (InvalidOperation, ValueError):
                    continue
        
        # Extract product type from description
        if 'GASOIL' in text.upper():
            entry.product_type = 'AGO'
            entry.description = "GASOIL"
        elif 'MOGAS' in text.upper() or 'MOTOR GASOLINE' in text.upper():
            entry.product_type = 'PMS'
            entry.description = "MOGAS"
        else:
            # Default based on common patterns
            entry.product_type = 'AGO'  # Default to AGO if unclear
            entry.description = "PETROLEUM PRODUCT"
        
        # Extract destination
        dest_match = re.search(r'TRANSIT TO ([A-Z\s]+)', text, re.IGNORECASE)
        if dest_match:
            entry.destination = dest_match.group(1).strip().title()
        else:
            # Look for common destinations
            common_destinations = ['MOMBASA', 'NAKURU', 'KISUMU', 'ELDORET', 'NAIROBI', 'DR CONGO', 'CONGO']
            for dest in common_destinations:
                if dest in text.upper():
                    entry.destination = dest.title()
                    break
            else:
                entry.destination = 'Mombasa'  # Default destination
        
        # Extract supplier (consignor)
        if 'KUWAIT PETROLEUM CORPORATION' in text.upper():
            entry.supplier = 'Kuwait Petroleum Corporation'
        elif 'KPC' in text:
            entry.supplier = 'KPC'
        else:
            entry.supplier = 'Unknown Supplier'
        
        # Add full description from the document
        desc_match = re.search(r'(GASOIL IN TRANSIT TO [A-Z\s]+)', text, re.IGNORECASE)
        if desc_match:
            entry.description = desc_match.group(1)
        
        logger.info(f"Parsed entry: {entry}")
        
        if self._is_valid_entry(entry):
            entries.append(entry)
        else:
            logger.warning(f"Invalid entry found: {entry}")
        
        return entries

    def _is_valid_entry(self, entry: TR830Entry) -> bool:
        """Check if an entry has the minimum required fields"""
        is_valid = (
            bool(entry.marks or entry.description) and 
            bool(entry.avalue) and 
            entry.avalue > 0 and
            bool(entry.product_type)
        )
        logger.debug(f"Entry validation: {is_valid} for {entry}")
        return is_valid

    def format_vessel_id(self, marks: str) -> str:
        """Format the vessel ID for consistency"""
        # Remove extra spaces and standardize format
        vessel_id = re.sub(r'\s+', ' ', marks.strip())
        
        # If it's too long, truncate it
        if len(vessel_id) > 50:
            vessel_id = vessel_id[:50]
        
        return vessel_id

    def get_parsing_summary(self, entries: List[TR830Entry]) -> Dict:
        """Generate a summary of parsed entries"""
        summary = {
            'total_entries': len(entries),
            'pms_entries': len([e for e in entries if e.product_type == 'PMS']),
            'ago_entries': len([e for e in entries if e.product_type == 'AGO']),
            'total_quantity': sum(e.avalue for e in entries if e.avalue),
            'destinations': list(set(e.destination for e in entries if e.destination)),
            'suppliers': list(set(e.supplier for e in entries if e.supplier)),
        }
        return summary