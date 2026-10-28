from django.db import migrations
from django.db.models import Q


def move_usage_key_from_path(apps, schema_editor):
    Site = apps.get_model('monitor', 'Site')
    new_api = Q(site_type='newapi') | Q(me_path='/api/user/self') | Q(balance_field='quota')
    for site in Site.objects.filter(new_api).exclude(usage_path=''):
        value = str(site.usage_path or '').strip()
        key = value[7:].strip() if value.lower().startswith('bearer ') else value
        if key.lower().startswith('sk-') and len(key) > 10:
            if not site.usage_token:
                site.usage_token = key
            site.usage_path = ''
            site.save(update_fields=['usage_token', 'usage_path'])


class Migration(migrations.Migration):

    dependencies = [
        ('monitor', '0004_newapi_currency_cny'),
    ]

    operations = [
        migrations.RunPython(move_usage_key_from_path, migrations.RunPython.noop),
    ]
