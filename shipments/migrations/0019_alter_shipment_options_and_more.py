# Generated by Django 5.2.1 on 2025-06-11 08:43

import django.db.models.deletion
import django.utils.timezone
import simple_history.models
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shipments', '0018_shipment_shipments_s_product_634bbf_idx_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='shipment',
            options={'ordering': ['-import_date', '-created_at']},
        ),
        migrations.AlterModelOptions(
            name='shipmentdepletion',
            options={'ordering': ['-created_at']},
        ),
        migrations.AlterField(
            model_name='loadingcompartment',
            name='density',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=7, null=True),
        ),
        migrations.CreateModel(
            name='HistoricalTrip',
            fields=[
                ('id', models.BigIntegerField(auto_created=True, blank=True, db_index=True, verbose_name='ID')),
                ('kpc_order_number', models.CharField(db_index=True, help_text='KPC Loading Order No (e.g., Sxxxxx) from Authority PDF. Required and must be unique.', max_length=100)),
                ('bol_number', models.CharField(blank=True, db_index=True, max_length=100, null=True, verbose_name='Final BoL No / KPC Shipment No')),
                ('loading_date', models.DateField(default=django.utils.timezone.now, verbose_name='Authority/BOL Date')),
                ('loading_time', models.TimeField(default=django.utils.timezone.now, verbose_name='Authority/BOL Time')),
                ('status', models.CharField(choices=[('PENDING', 'Pending KPC Action'), ('KPC_APPROVED', 'KPC Approved (Stock Booked)'), ('KPC_REJECTED', 'KPC Rejected'), ('LOADING', 'Loading @ Depot'), ('LOADED', 'Loaded from Depot (BoL Received)'), ('GATEPASSED', 'Gatepassed from Depot'), ('TRANSIT', 'In Transit to Customer'), ('DELIVERED', 'Delivered to Customer'), ('CANCELLED', 'Cancelled')], default='PENDING', max_length=30)),
                ('notes', models.TextField(blank=True, null=True)),
                ('kpc_comments', models.TextField(blank=True, help_text='Comments from KPC emails', null=True)),
                ('created_at', models.DateTimeField(blank=True, editable=False)),
                ('updated_at', models.DateTimeField(blank=True, editable=False)),
                ('history_id', models.AutoField(primary_key=True, serialize=False)),
                ('history_date', models.DateTimeField(db_index=True)),
                ('history_change_reason', models.CharField(max_length=100, null=True)),
                ('history_type', models.CharField(choices=[('+', 'Created'), ('~', 'Changed'), ('-', 'Deleted')], max_length=1)),
                ('customer', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='shipments.customer')),
                ('destination', models.ForeignKey(blank=True, db_constraint=False, help_text='Destination for this loading.', null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='shipments.destination')),
                ('history_user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('product', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='shipments.product')),
                ('user', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('vehicle', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='shipments.vehicle')),
            ],
            options={
                'verbose_name': 'historical trip',
                'verbose_name_plural': 'historical trips',
                'ordering': ('-history_date', '-history_id'),
                'get_latest_by': ('history_date', 'history_id'),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
    ]
