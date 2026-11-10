from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('monitor', '0007_site_usage_keys')]

    operations = [migrations.CreateModel(
        name='KeyUsageDay',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('site_id', models.CharField(max_length=64)),
            ('fingerprint', models.CharField(max_length=64)),
            ('day_key', models.CharField(max_length=10)),
            ('cost', models.FloatField(null=True)),
            ('success_count', models.PositiveIntegerField(default=0)),
            ('failed_count', models.PositiveIntegerField(default=0)),
            ('updated_at', models.DateTimeField(null=True)),
            ('attempted_at', models.DateTimeField()),
            ('refresh_failed', models.BooleanField(default=False)),
        ],
        options={'constraints': [models.UniqueConstraint(
            fields=('site_id', 'fingerprint', 'day_key'), name='unique_key_usage_day')]},
    )]
