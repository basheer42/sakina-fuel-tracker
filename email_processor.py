#!/usr/bin/env python
import os
import sys
import time
import django
from pathlib import Path

# Add project to path
project_path = Path(__file__).resolve().parent
sys.path.insert(0, str(project_path))

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'fuel_project.settings')
django.setup()

from django.core.management import call_command
import logging

# Set up logging
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('/home/Basheer42/logs/email_processor.log')
    ]
)
logger = logging.getLogger(__name__)

def process_emails():
    """Process both status emails and BOL emails"""
    try:
        logger.info("=== Starting email processing cycle ===")
        
        # Process status emails
        logger.info("Processing KPC status emails...")
        try:
            call_command('process_status_emails')
            logger.info("✅ Status emails processed successfully")
        except Exception as e:
            logger.error(f"❌ Error processing status emails: {e}")
        
        # Process BOL emails
        logger.info("Processing BOL emails...")
        try:
            call_command('process_bol_emails')
            logger.info("✅ BOL emails processed successfully")
        except Exception as e:
            logger.error(f"❌ Error processing BOL emails: {e}")
            
        logger.info("=== Email processing cycle completed ===")
        
    except Exception as e:
        logger.error(f"❌ Critical error in email processing: {e}")

def main():
    logger.info("🚀 Starting continuous email processor (every 1 minute)")
    logger.info("📧 Processing both KPC status emails and BOL emails")
    
    # Process immediately on startup
    process_emails()
    
    # Then continue with 1-minute intervals
    while True:
        try:
            time.sleep(60)  # Wait 1 minute
            process_emails()
            
        except KeyboardInterrupt:
            logger.info("🛑 Email processor stopped by user")
            break
        except Exception as e:
            logger.error(f"❌ Unexpected error: {e}")
            logger.info("⏳ Waiting 30 seconds before retry...")
            time.sleep(30)

if __name__ == "__main__":
    main()
