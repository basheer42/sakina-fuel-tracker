#!/usr/bin/env python3
"""
Always-on Email Processor for PythonAnywhere - UPDATED for AI Matching
Runs continuously and processes emails every 5 minutes
Now includes AI-powered order matching for KPC integration
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

def run_django_command(command_name, description):
    """Run a Django management command"""
    try:
        logger.info(f"Running {description}...")
        
        # Change to project directory
        os.chdir(PROJECT_DIR)
        
        # Run the Django command
        result = subprocess.run(
            [VENV_PYTHON, MANAGE_PY, command_name],
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )
        
        if result.returncode == 0:
            logger.info(f"✅ {description} completed successfully")
            if result.stdout.strip():
                # Log important output (but not too verbose)
                output_lines = result.stdout.strip().split('\n')
                for line in output_lines:
                    if any(keyword in line for keyword in ['Found', 'Processing', 'Successfully', 'ERROR', 'AI CORRECTION']):
                        logger.info(f"  {line}")
        else:
            logger.error(f"❌ {description} failed with return code {result.returncode}")
            if result.stderr:
                logger.error(f"Error: {result.stderr}")
                
        return result.returncode == 0
                
    except subprocess.TimeoutExpired:
        logger.error(f"❌ {description} timed out after 5 minutes")
        return False
    except Exception as e:
        logger.error(f"❌ Unexpected error during {description}: {e}")
        return False

def run_email_processing_cycle():
    """Run both email processors with AI matching"""
    logger.info("🚀 Starting email processing cycle with AI matching...")
    
    success_count = 0
    
    # Process KPC status update emails (truckloading@kpc.co.ke)
    if run_django_command("process_status_emails", "KPC Status Email Processing"):
        success_count += 1
    
    # Small delay between processors
    time.sleep(2)
    
    # Process KPC BoL emails (bolconfirmation@kpc.co.ke)
    if run_django_command("process_bol_emails", "KPC BoL Email Processing"):
        success_count += 1
    
    # Log cycle summary
    if success_count == 2:
        logger.info("✅ Email processing cycle completed successfully (2/2 processors)")
    elif success_count == 1:
        logger.warning("⚠️ Email processing cycle partially successful (1/2 processors)")
    else:
        logger.error("❌ Email processing cycle failed (0/2 processors)")
    
    return success_count

def run_ai_health_check():
    """Run AI matcher health check (once per hour)"""
    try:
        logger.info("🤖 Running AI matcher health check...")
        
        result = subprocess.run(
            [VENV_PYTHON, MANAGE_PY, "test_ai_matcher", "--check-setup"],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode == 0:
            logger.info("✅ AI matcher health check passed")
            # Log key health metrics from output
            output_lines = result.stdout.strip().split('\n')
            for line in output_lines:
                if any(keyword in line for keyword in ['Groq API Key:', 'AI Matcher Enabled:', 'Active Orders Found:']):
                    logger.info(f"  {line}")
        else:
            logger.warning("⚠️ AI matcher health check failed")
            if result.stderr:
                logger.warning(f"Health check error: {result.stderr}")
                
    except Exception as e:
        logger.warning(f"⚠️ AI matcher health check error: {e}")

def main():
    """Main loop for always-on task with AI-enhanced email processing"""
    logger.info("🚀 Always-on email processor starting (AI-Enhanced Version)...")
    logger.info(f"📧 Processing KPC emails every {SLEEP_INTERVAL} seconds (5 minutes)")
    logger.info("📋 Features:")
    logger.info("  - Status emails from: truckloading@kpc.co.ke")
    logger.info("  - BoL emails from: bolconfirmation@kpc.co.ke") 
    logger.info("  - AI-powered order number correction")
    logger.info("  - 3-layer fuzzy matching system")
    
    # Verify paths exist
    if not os.path.exists(VENV_PYTHON):
        logger.error(f"❌ Python executable not found: {VENV_PYTHON}")
        sys.exit(1)
        
    if not os.path.exists(MANAGE_PY):
        logger.error(f"❌ manage.py not found: {MANAGE_PY}")
        sys.exit(1)
    
    cycle_count = 0
    health_check_count = 0
    
    try:
        # Run initial health check
        run_ai_health_check()
        
        while True:
            cycle_count += 1
            start_time = datetime.now()
            
            logger.info(f"=== Email Processing Cycle #{cycle_count} ===")
            logger.info(f"Started at: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
            
            # Run email processing cycle
            success_count = run_email_processing_cycle()
            
            # Run health check every 12 cycles (1 hour)
            if cycle_count % 12 == 0:
                health_check_count += 1
                logger.info(f"🔍 Running periodic health check #{health_check_count}")
                run_ai_health_check()
            
            # Calculate processing time
            end_time = datetime.now()
            processing_time = (end_time - start_time).total_seconds()
            
            # Calculate next run time
            next_run = datetime.now().replace(second=0, microsecond=0)
            if next_run.minute % 5 != 0:
                next_run = next_run.replace(minute=next_run.minute + (5 - next_run.minute % 5))
            else:
                next_run = next_run.replace(minute=next_run.minute + 5)
            
            logger.info(f"⏱️ Processing took {processing_time:.1f} seconds")
            logger.info(f"⏰ Next run scheduled for: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info(f"💤 Sleeping for {SLEEP_INTERVAL} seconds...")
            logger.info(f"" + "="*50)  # Separator line
            
            # Sleep for 5 minutes
            time.sleep(SLEEP_INTERVAL)
            
    except KeyboardInterrupt:
        logger.info("🛑 Always-on email processor stopped by user")
    except Exception as e:
        logger.error(f"💥 Fatal error in main loop: {e}")
        # Log the full traceback for debugging
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)

if __name__ == "__main__":
    main()