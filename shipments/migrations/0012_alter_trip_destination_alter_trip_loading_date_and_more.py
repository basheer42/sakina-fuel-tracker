# Generated by Django 5.2.1 on 2025-05-20 11:41

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shipments', '0011_remove_trip_depot_remove_trip_kpc_loading_order_no_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='trip',
            name='destination',
            field=models.ForeignKey(blank=True, help_text='Destination for this loading.', null=True, on_delete=django.db.models.deletion.PROTECT, to='shipments.destination'),
        ),
        migrations.AlterField(
            model_name='trip',
            name='loading_date',
            field=models.DateField(default=django.utils.timezone.now, verbose_name='Authority/BOL Date'),
        ),
        migrations.AlterField(
            model_name='trip',
            name='loading_time',
            field=models.TimeField(default=django.utils.timezone.now, verbose_name='Authority/BOL Time'),
        ),
        migrations.AlterField(
            model_name='trip',
            name='status',
            field=models.CharField(choices=[('PENDING', 'Pending KPC Action'), ('KPC_APPROVED', 'KPC Approved (Stock Booked)'), ('KPC_REJECTED', 'KPC Rejected'), ('LOADING', 'Loading @ Depot'), ('LOADED', 'Loaded from Depot'), ('GATEPASSED', 'Gatepassed from Depot'), ('TRANSIT', 'In Transit to Customer'), ('DELIVERED', 'Delivered to Customer'), ('CANCELLED', 'Cancelled')], default='PENDING', max_length=20),
        ),
    ]
