from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('monitor', '0008_key_usage_day')]

    operations = [migrations.AddField(
        model_name='keyusageday', name='finalized', field=models.BooleanField(default=False))]
