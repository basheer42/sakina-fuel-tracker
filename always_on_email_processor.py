#!/usr/bin/env python3
"""
Always-on Email Processor for PythonAnywhere - UPDATED for AI Matching
Runs continuously and processes emails every 1 minute (updated from 5 minutes)
Now includes AI-powered order matching for KPC integration
Includes automatic log cleanup for files older than 2 days
NEW: Midnight fallback processing for previously read emails
"""
import os
import sys
import time
import subprocess
import logging
import glob
from datetime import datetime, timedelta

# Configuration
PROJECT_DIR = "/home/Basheer42/sakina-fuel-tracker"
VENV_PYTHON = "/home/Basheer42/.virtualenvs/mysite-virtualenv/bin/python"
MANAGE_PY = os.path.join(PROJECT_DIR, "manage.py")
SLEEP_INTERVAL = 60  # 1 minute in seconds (changed from 300)

# Log cleanup configuration
LOG_CLEANUP_ENABLED = True
LOG_RETENTION_DAYS = 2  # Keep logs for 2 days
LOG_DIRECTORIES = [
    os.path.join(PROJECT_DIR, "logs"),
    "/home/Basheer42/logs",
    PROJECT_DIR  # For logs in the project root
]

# Fallback processing configuration
FALLBACK_ENABLED = True
FALLBACK_HOUR = 0  # Run fallback at midnight (0:00)
FALLBACK_MINUTE = 5  # Run at 00:05 to avoid conflicts

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

def cleanup_old_logs():
    """Clean up log files older than LOG_RETENTION_DAYS"""
    if not LOG_CLEANUP_ENABLED:
        return
    
    try:
        cutoff_time = datetime.now() - timedelta(days=LOG_RETENTION_DAYS)
        total_deleted = 0
        total_size_freed = 0
        
        logger.info(f"🧹 Starting log cleanup - removing files older than {LOG_RETENTION_DAYS} days")
        
        # Log file patterns to look for
        log_patterns = [
            "*.log",
            "email_processor*.log", 
            "always_on_email_processor*.log",
            "django*.log",
            "whatsapp*.log",
            "*.log.*"  # Rotated logs
        ]
        
        for log_dir in LOG_DIRECTORIES:
            if not os.path.exists(log_dir):
                continue
                
            logger.info(f"  Checking directory: {log_dir}")
            
            for pattern in log_patterns:
                log_files = glob.glob(os.path.join(log_dir, pattern))
                
                for log_file in log_files:
                    try:
                        # Get file stats
                        file_stats = os.stat(log_file)
                        file_mtime = datetime.fromtimestamp(file_stats.st_mtime)
                        file_size = file_stats.st_size
                        
                        # Check if file is older than cutoff
                        if file_mtime < cutoff_time:
                            # Don't delete the current processor log file
                            if log_file.endswith('always_on_email_processor.log'):
                                continue
                                
                            os.remove(log_file)
                            total_deleted += 1
                            total_size_freed += file_size
                            
                            logger.info(f"    🗑️ Deleted: {os.path.basename(log_file)} ({file_size / 1024:.1f} KB, {file_mtime.strftime('%Y-%m-%d %H:%M')})")
                            
                    except (OSError, IOError) as e:
                        logger.warning(f"    ⚠️ Could not delete {log_file}: {e}")
        
        if total_deleted > 0:
            logger.info(f"✅ Log cleanup completed: {total_deleted} files deleted, {total_size_freed / 1024:.1f} KB freed")
        else:
            logger.info("✅ Log cleanup completed: No old log files found")
            
    except Exception as e:
        logger.error(f"❌ Error during log cleanup: {e}")

def run_fallback_processing():
    """Run fallback processing for previously read emails"""
    if not FALLBACK_ENABLED:
        return
    
    try:
        logger.info("🔄 Starting FALLBACK processing for previously read emails...")
        
        # Run status email fallback
        logger.info("📧 Running status email fallback...")
        result_status = subprocess.run(
            [VENV_PYTHON, MANAGE_PY, "process_status_emails_fallback", "--days-back=1"],
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout
            cwd=PROJECT_DIR
        )
        
        if result_status.returncode == 0:
            logger.info("✅ Status email fallback completed successfully")
        else:
            logger.error(f"❌ Status email fallback failed with return code {result_status.returncode}")
            if result_status.stderr:
                logger.error(f"Status fallback error: {result_status.stderr}")
        
        # Small delay between fallback processors
        time.sleep(5)
        
        # Run BoL email fallback
        logger.info("📧 Running BoL email fallback...")
        result_bol = subprocess.run(
            [VENV_PYTHON, MANAGE_PY, "process_bol_emails_fallback", "--days-back=1"],
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout
            cwd=PROJECT_DIR
        )
        
        if result_bol.returncode == 0:
            logger.info("✅ BoL email fallback completed successfully")
        else:
            logger.error(f"❌ BoL email fallback failed with return code {result_bol.returncode}")
            if result_bol.stderr:
                logger.error(f"BoL fallback error: {result_bol.stderr}")
        
        # Log fallback summary
        if result_status.returncode == 0 and result_bol.returncode == 0:
            logger.info("✅ FALLBACK processing completed successfully (2/2 processors)")
        elif result_status.returncode == 0 or result_bol.returncode == 0:
            logger.warning("⚠️ FALLBACK processing partially successful (1/2 processors)")
        else:
            logger.error("❌ FALLBACK processing failed (0/2 processors)")
            
    except subprocess.TimeoutExpired:
        logger.error("❌ Fallback processing timed out after 10 minutes")
    except Exception as e:
        logger.error(f"❌ Error during fallback processing: {e}")

def should_run_fallback():
    """Check if it's time to run fallback processing"""
    if not FALLBACK_ENABLED:
        return False
    
    now = datetime.now()
    return (now.hour == FALLBACK_HOUR and 
            now.minute == FALLBACK_MINUTE and 
            now.second < 60)  # Run only in the first minute of the fallback time

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
    logger.info("🚀 Always-on email processor starting (AI-Enhanced Version with Fallback)...")
    logger.info(f"📧 Processing KPC emails every {SLEEP_INTERVAL} seconds (1 minute)")
    logger.info("📋 Features:")
    logger.info("  - Status emails from: truckloading@kpc.co.ke")
    logger.info("  - BoL emails from: bolconfirmation@kpc.co.ke") 
    logger.info("  - AI-powered order number correction")
    logger.info("  - 3-layer fuzzy matching system")
    logger.info(f"  - Automatic log cleanup (>{LOG_RETENTION_DAYS} days)")
    logger.info(f"  - Midnight fallback processing at {FALLBACK_HOUR:02d}:{FALLBACK_MINUTE:02d}")  # New feature
    
    # Verify paths exist
    if not os.path.exists(VENV_PYTHON):
        logger.error(f"❌ Python executable not found: {VENV_PYTHON}")
        sys.exit(1)
        
    if not os.path.exists(MANAGE_PY):
        logger.error(f"❌ manage.py not found: {MANAGE_PY}")
        sys.exit(1)
    
    cycle_count = 0
    health_check_count = 0
    last_log_cleanup = datetime.now() - timedelta(hours=25)  # Force cleanup on first run
    last_fallback_run = datetime.now() - timedelta(days=1)  # Track last fallback run
    
    try:
        # Run initial health check
        run_ai_health_check()
        
        # Run initial log cleanup
        cleanup_old_logs()
        last_log_cleanup = datetime.now()
        
        while True:
            cycle_count += 1
            start_time = datetime.now()
            
            logger.info(f"=== Email Processing Cycle #{cycle_count} ===")
            logger.info(f"Started at: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
            
            # Check if it's time to run fallback processing
            if should_run_fallback() and (datetime.now() - last_fallback_run).total_seconds() > 3600:
                logger.info("🕛 MIDNIGHT FALLBACK TIME - Running fallback processing...")
                run_fallback_processing()
                last_fallback_run = datetime.now()
            else:
                # Run regular email processing cycle
                success_count = run_email_processing_cycle()
            
            # Run health check every 60 cycles (1 hour with 1-minute intervals)
            if cycle_count % 60 == 0:  # Changed from 12 to 60
                health_check_count += 1
                logger.info(f"🔍 Running periodic health check #{health_check_count}")
                run_ai_health_check()
            
            # Run log cleanup once every 24 hours
            if datetime.now() - last_log_cleanup > timedelta(hours=24):
                cleanup_old_logs()
                last_log_cleanup = datetime.now()
            
            # Calculate processing time
            end_time = datetime.now()
            processing_time = (end_time - start_time).total_seconds()
            
            # Calculate next run time (every minute)
            next_run = datetime.now().replace(second=0, microsecond=0) + timedelta(minutes=1)
            
            logger.info(f"⏱️ Processing took {processing_time:.1f} seconds")
            logger.info(f"⏰ Next run scheduled for: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
            
            # Show next fallback time
            if FALLBACK_ENABLED:
                next_fallback = datetime.now().replace(hour=FALLBACK_HOUR, minute=FALLBACK_MINUTE, second=0, microsecond=0)
                if next_fallback <= datetime.now():
                    next_fallback += timedelta(days=1)
                logger.info(f"🔄 Next fallback: {next_fallback.strftime('%Y-%m-%d %H:%M:%S')}")
            
            logger.info(f"💤 Sleeping for {SLEEP_INTERVAL} seconds...")
            logger.info(f"" + "="*50)  # Separator line
            
            # Sleep for 1 minute
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