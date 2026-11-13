from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('monitor', '0009_key_usage_day_finalized')]

    operations = [
        migrations.AddField(
            model_name='globalsettings',
            name='daily_report_webhook',
            field=models.TextField(blank=True, default=''),
        ),
    ]
