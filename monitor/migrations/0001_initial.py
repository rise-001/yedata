from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='GlobalSettings',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False)),
                ('webhook', models.TextField(blank=True, default='')),
            ],
            options={'app_label': 'monitor'},
        ),
        migrations.CreateModel(
            name='Site',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('site_id', models.CharField(db_index=True, max_length=64, unique=True)),
                ('name', models.CharField(blank=True, max_length=80)),
                ('enabled', models.BooleanField(default=True)),
                ('interval_seconds', models.IntegerField(default=300)),
                ('base_url', models.TextField(blank=True)),
                ('site_type', models.CharField(blank=True, max_length=32)),
                ('email', models.TextField(blank=True)),
                ('password', models.TextField(blank=True)),
                ('token', models.TextField(blank=True)),
                ('session_cookie', models.TextField(blank=True)),
                ('auth_mode', models.CharField(blank=True, default='password', max_length=32)),
                ('new_api_user_id', models.CharField(blank=True, max_length=64)),
                ('login_path', models.TextField(blank=True)),
                ('me_path', models.TextField(blank=True)),
                ('usage_path', models.TextField(blank=True)),
                ('balance_field', models.CharField(blank=True, max_length=64)),
                ('balance_divisor', models.FloatField(blank=True, null=True)),
                ('balance_multiplier', models.FloatField(blank=True, null=True)),
                ('currency', models.CharField(blank=True, max_length=8)),
                ('threshold', models.FloatField(blank=True, null=True)),
                ('notify_enabled', models.BooleanField(default=True)),
                ('timezone', models.CharField(blank=True, default='Asia/Shanghai', max_length=64)),
                ('order', models.IntegerField(default=0)),
            ],
            options={'app_label': 'monitor', 'ordering': ['order', 'id']},
        ),
        migrations.CreateModel(
            name='UpstreamSession',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cache_key', models.CharField(db_index=True, max_length=256, unique=True)),
                ('fingerprint', models.CharField(max_length=64)),
                ('token', models.TextField(blank=True)),
                ('session_cookie', models.TextField(blank=True)),
                ('new_api_user_id', models.CharField(blank=True, max_length=64)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'app_label': 'monitor'},
        ),
        migrations.CreateModel(
            name='DailyUsage',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('site_id', models.CharField(db_index=True, max_length=64)),
                ('day_key', models.CharField(max_length=10)),
                ('used_balance', models.FloatField()),
            ],
            options={
                'app_label': 'monitor',
                'unique_together': {('site_id', 'day_key')},
            },
        ),
        migrations.AddIndex(
            model_name='dailyusage',
            index=models.Index(fields=['site_id', 'day_key'], name='monitor_dai_site_id_idx'),
        ),
    ]
