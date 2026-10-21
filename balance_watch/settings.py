import os
from pathlib import Path
from dotenv import load_dotenv
from balance_watch.bootstrap import load_credentials

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = os.environ.get('DATA_DIR') or str(BASE_DIR / 'data')
_credentials = load_credentials(DATA_DIR)
SECRET_KEY = _credentials['DJANGO_SECRET_KEY']
DEBUG = os.environ.get('DEBUG', 'false').lower() == 'true'
ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '*').split(',')

INSTALLED_APPS = [
    'django.contrib.staticfiles',
    'monitor',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.middleware.common.CommonMiddleware',
    'monitor.middleware.SessionAuthMiddleware',
]

ROOT_URLCONF = 'balance_watch.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'public'],
        'APP_DIRS': False,
        'OPTIONS': {'context_processors': []},
    },
]

WSGI_APPLICATION = 'balance_watch.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': Path(DATA_DIR) / 'db.sqlite3',
    }
}

INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'django.contrib.staticfiles',
    'monitor.apps.MonitorConfig',
]

LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'public']

# App-level config
APP_USERNAME = _credentials['APP_USERNAME']
APP_PASSWORD = _credentials['APP_PASSWORD']
COOKIE_SECURE = os.environ.get('COOKIE_SECURE', 'false').lower() == 'true'
CHECK_CONCURRENCY = min(20, max(1, int(os.environ.get('CHECK_CONCURRENCY', '5'))))
DAILY_REPORT_HOUR = min(23, max(0, int(os.environ.get('DAILY_REPORT_HOUR', '8'))))
SESSION_MAX_AGE = 24 * 60 * 60  # seconds
MAX_SITES = 100
MAX_BODY_BYTES = 1024 * 1024
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
