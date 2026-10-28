from django.db import migrations
from django.db.models import Q


def newapi_currency_to_cny(apps, schema_editor):
    Site = apps.get_model('monitor', 'Site')
    Site.objects.filter(
        Q(site_type='newapi') | Q(me_path='/api/user/self') | Q(balance_field='quota'),
        currency='USD',
    ).update(currency='CNY')


class Migration(migrations.Migration):

    dependencies = [
        ('monitor', '0003_site_usage_token'),
    ]

    operations = [
        migrations.RunPython(newapi_currency_to_cny, migrations.RunPython.noop),
    ]
