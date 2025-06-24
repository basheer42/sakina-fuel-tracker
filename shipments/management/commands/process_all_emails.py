# shipments/management/commands/process_all_emails.py

import logging
from django.core.management.base import BaseCommand
from django.core.management import call_command
from django.utils import timezone

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Runs both KPC email processors (status and BoL) in sequence'

    def add_arguments(self, parser):
        parser.add_argument(
            '--skip-status',
            action='store_true',
            help='Skip processing status emails'
        )
        parser.add_argument(
            '--skip-bol',
            action='store_true',
            help='Skip processing BoL emails'
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Enable verbose output'
        )

    def handle(self, *args, **options):
        start_time = timezone.now()
        self.stdout.write(
            self.style.SUCCESS(
                f"Starting combined email processing at {start_time.strftime('%Y-%m-%d %H:%M:%S')}"
            )
        )
        logger.info(f"Combined Email Processing: Cycle started at {start_time}")

        errors = []
        
        # Process status emails
        if not options['skip_status']:
            try:
                self.stdout.write("\n" + "="*60)
                self.stdout.write(self.style.HTTP_INFO("PROCESSING STATUS EMAILS"))
                self.stdout.write("="*60)
                
                call_command('process_status_emails')
                
                self.stdout.write(self.style.SUCCESS("✓ Status emails processed successfully"))
                logger.info("Combined Email Processing: Status emails completed successfully")
                
            except Exception as e:
                error_msg = f"Error processing status emails: {e}"
                errors.append(error_msg)
                self.stdout.write(self.style.ERROR(f"✗ {error_msg}"))
                logger.error(f"Combined Email Processing: Status emails failed: {e}", exc_info=True)
        else:
            self.stdout.write(self.style.WARNING("⚠ Skipping status emails (--skip-status flag)"))

        # Process BoL emails
        if not options['skip_bol']:
            try:
                self.stdout.write("\n" + "="*60)
                self.stdout.write(self.style.HTTP_INFO("PROCESSING BOL EMAILS"))
                self.stdout.write("="*60)
                
                call_command('process_bol_emails')
                
                self.stdout.write(self.style.SUCCESS("✓ BoL emails processed successfully"))
                logger.info("Combined Email Processing: BoL emails completed successfully")
                
            except Exception as e:
                error_msg = f"Error processing BoL emails: {e}"
                errors.append(error_msg)
                self.stdout.write(self.style.ERROR(f"✗ {error_msg}"))
                logger.error(f"Combined Email Processing: BoL emails failed: {e}", exc_info=True)
        else:
            self.stdout.write(self.style.WARNING("⚠ Skipping BoL emails (--skip-bol flag)"))

        # Final summary
        end_time = timezone.now()
        duration = end_time - start_time
        
        self.stdout.write("\n" + "="*60)
        self.stdout.write(self.style.HTTP_INFO("PROCESSING SUMMARY"))
        self.stdout.write("="*60)
        
        if errors:
            self.stdout.write(self.style.ERROR(f"✗ Completed with {len(errors)} error(s):"))
            for error in errors:
                self.stdout.write(f"  • {error}")
            logger.error(f"Combined Email Processing: Completed with {len(errors)} errors after {duration}")
        else:
            self.stdout.write(self.style.SUCCESS("✓ All email processing completed successfully"))
            logger.info(f"Combined Email Processing: All completed successfully after {duration}")
            
        self.stdout.write(f"Duration: {duration}")
        self.stdout.write(f"Finished at: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        if options['verbose']:
            self.stdout.write(f"\nLog file location: Check Django logs for 'Combined Email Processing' entries")
            
        # Exit with error code if there were errors
        if errors:
            exit(1)
