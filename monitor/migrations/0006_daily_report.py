from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('monitor', '0005_move_usage_key_from_path'),
    ]

    operations = [
        migrations.CreateModel(
            name='DailyReport',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('timezone', models.CharField(max_length=64)),
                ('day_key', models.CharField(max_length=10)),
                ('sent_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'app_label': 'monitor',
                'unique_together': {('timezone', 'day_key')},
            },
        ),
    ]
