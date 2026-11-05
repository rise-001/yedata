from django.db import migrations, models


def copy_usage_tokens(apps, schema_editor):
    Site = apps.get_model('monitor', 'Site')
    for site in Site.objects.exclude(usage_token=''):
        site.usage_keys = [{'id': 'default', 'name': '默认 Key', 'token': site.usage_token}]
        site.save(update_fields=['usage_keys'])


class Migration(migrations.Migration):
    dependencies = [('monitor', '0006_daily_report')]

    operations = [
        migrations.AddField(
            model_name='site', name='usage_keys',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.RunPython(copy_usage_tokens, migrations.RunPython.noop),
    ]
