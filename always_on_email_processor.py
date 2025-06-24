#!/usr/bin/env python3
"""
Always-on Email Processor for PythonAnywhere
Runs continuously and processes emails every 5 minutes
"""

import os
import sys
import time
import subprocess
import logging
from datetime import datetime

# Configuration
PROJECT_DIR = "/home/Basheer42/sakina-fuel-tracker"
VENV_PYTHON = "/home/Basheer42/.virtualenvs/mysite-virtualenv/bin/python"
MANAGE_PY = os.path.join(PROJECT_DIR, "manage.py")
SLEEP_INTERVAL = 300  # 5 minutes in seconds

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(PROJECT_DIR, 'always_on_email_processor.log')),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

def run_email_processor():
    """Run the email processor command"""
    try:
        logger.info("Starting email processing cycle...")
        
        # Change to project directory
        os.chdir(PROJECT_DIR)
        
        # Run the Django command
        result = subprocess.run(
            [VENV_PYTHON, MANAGE_PY, "process_all_emails"],
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )
        
        if result.returncode == 0:
            logger.info("Email processing completed successfully")
            if result.stdout:
                logger.info(f"Output: {result.stdout}")
        else:
            logger.error(f"Email processing failed with return code {result.returncode}")
            if result.stderr:
                logger.error(f"Error: {result.stderr}")
                
    except subprocess.TimeoutExpired:
        logger.error("Email processing timed out after 5 minutes")
    except Exception as e:
        logger.error(f"Unexpected error during email processing: {e}")

def main():
    """Main loop for always-on task"""
    logger.info("Always-on email processor starting...")
    logger.info(f"Will process emails every {SLEEP_INTERVAL} seconds (5 minutes)")
    
    # Verify paths exist
    if not os.path.exists(VENV_PYTHON):
        logger.error(f"Python executable not found: {VENV_PYTHON}")
        sys.exit(1)
        
    if not os.path.exists(MANAGE_PY):
        logger.error(f"manage.py not found: {MANAGE_PY}")
        sys.exit(1)
    
    cycle_count = 0
    
    try:
        while True:
            cycle_count += 1
            start_time = datetime.now()
            
            logger.info(f"=== Email Processing Cycle #{cycle_count} ===")
            
            # Run email processor
            run_email_processor()
            
            # Calculate next run time
            next_run = datetime.now().replace(second=0, microsecond=0)
            if next_run.minute % 5 != 0:
                next_run = next_run.replace(minute=next_run.minute + (5 - next_run.minute % 5))
            else:
                next_run = next_run.replace(minute=next_run.minute + 5)
            
            logger.info(f"Next run scheduled for: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info(f"Sleeping for {SLEEP_INTERVAL} seconds...")
            
            # Sleep for 5 minutes
            time.sleep(SLEEP_INTERVAL)
            
    except KeyboardInterrupt:
        logger.info("Always-on email processor stopped by user")
    except Exception as e:
        logger.error(f"Fatal error in main loop: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
