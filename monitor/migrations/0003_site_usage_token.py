from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('monitor', '0002_rename_monitor_dai_site_id_idx_monitor_dai_site_id_f38e86_idx_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='site',
            name='usage_token',
            field=models.TextField(blank=True),
        ),
    ]
