# shipments/migrations/000X_populate_shipment_quantity_remaining.py 
# (Replace 000X with the actual number of this new file)

from django.db import migrations
from django.db.models import F # Import F expression

def set_quantity_remaining(apps, schema_editor):
    Shipment = apps.get_model('shipments', 'Shipment')
    # Set quantity_remaining equal to quantity_litres for all existing shipments
    # This assumes no depletions have occurred yet on these historical records.
    # If a shipment had 0 quantity_litres, quantity_remaining will also be 0.
    Shipment.objects.all().update(quantity_remaining=F('quantity_litres'))

def reverse_set_quantity_remaining(apps, schema_editor):
    # This function is called if you ever unapply this migration.
    # We can simply set quantity_remaining back to 0 or leave it as is.
    # For simplicity, we'll do nothing on reverse, or you could set it to 0.
    pass # Or:
    # Shipment = apps.get_model('shipments', 'Shipment')
    # Shipment.objects.all().update(quantity_remaining=0)


class Migration(migrations.Migration):

    dependencies = [
        # IMPORTANT: Replace '0003_alter_shipment_options_and_more' 
        # with the EXACT name of the migration file created in the
        # *previous* `makemigrations` step (the one listed in your output above).
        ('shipments', '0003_alter_shipment_options_and_more'), 
    ]

    operations = [
        migrations.RunPython(set_quantity_remaining, reverse_set_quantity_remaining),
    ]