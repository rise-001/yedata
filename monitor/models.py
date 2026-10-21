"""
Django ORM models for SQLite-backed persistence.
Replaces sites.json, upstream-sessions.json, and daily-usage.json.
"""
import json

from django.db import models


class GlobalSettings(models.Model):
    """Single-row table for global configuration (webhook, etc.)."""
    webhook = models.TextField(blank=True, default='')

    class Meta:
        app_label = 'monitor'

    @classmethod
    def get(cls) -> 'GlobalSettings':
        obj, _ = cls.objects.get_or_create(pk=1, defaults={'webhook': ''})
        return obj


class Site(models.Model):
    """One monitored site."""
    site_id = models.CharField(max_length=64, unique=True, db_index=True)
    name = models.CharField(max_length=80, blank=True)
    enabled = models.BooleanField(default=True)
    interval_seconds = models.IntegerField(default=300)
    base_url = models.TextField(blank=True)
    site_type = models.CharField(max_length=32, blank=True)
    email = models.TextField(blank=True)
    # Secret fields — stored in DB, never returned to the browser.
    password = models.TextField(blank=True)
    token = models.TextField(blank=True)
    session_cookie = models.TextField(blank=True)
    auth_mode = models.CharField(max_length=32, blank=True, default='password')
    new_api_user_id = models.CharField(max_length=64, blank=True)
    login_path = models.TextField(blank=True)
    me_path = models.TextField(blank=True)
    usage_path = models.TextField(blank=True)
    balance_field = models.CharField(max_length=64, blank=True)
    balance_divisor = models.FloatField(null=True, blank=True)
    balance_multiplier = models.FloatField(null=True, blank=True)
    currency = models.CharField(max_length=8, blank=True)
    threshold = models.FloatField(null=True, blank=True)
    notify_enabled = models.BooleanField(default=True)
    timezone = models.CharField(max_length=64, blank=True, default='Asia/Shanghai')
    # Display order
    order = models.IntegerField(default=0)

    class Meta:
        app_label = 'monitor'
        ordering = ['order', 'id']

    # ── Conversion helpers ────────────────────────────────────────────────

    _FIELD_MAP = {
        'baseUrl': 'base_url', 'siteType': 'site_type',
        'intervalSeconds': 'interval_seconds', 'newApiUserId': 'new_api_user_id',
        'loginPath': 'login_path', 'mePath': 'me_path', 'usagePath': 'usage_path',
        'balanceField': 'balance_field', 'balanceDivisor': 'balance_divisor',
        'balanceMultiplier': 'balance_multiplier', 'notifyEnabled': 'notify_enabled',
        'authMode': 'auth_mode', 'sessionCookie': 'session_cookie',
    }
    _FIELD_MAP_INV = {v: k for k, v in _FIELD_MAP.items()}

    @classmethod
    def from_dict(cls, data: dict, order: int = 0) -> 'Site':
        """Build (unsaved) Site from a camelCase site dict."""
        obj = cls()
        obj.site_id = str(data.get('id', ''))
        obj.name = str(data.get('name', ''))[:80]
        obj.enabled = data.get('enabled', True) is not False
        obj.order = order
        for js_key, py_key in cls._FIELD_MAP.items():
            val = data.get(js_key)
            if val is not None:
                setattr(obj, py_key, val)
        # Simple same-name fields
        for field in ('email', 'password', 'token', 'currency', 'timezone', 'threshold'):
            val = data.get(field)
            if val is not None:
                setattr(obj, field, val)
        return obj

    def to_dict(self) -> dict:
        """Return a camelCase dict (mirrors the saved JSON shape)."""
        d = {
            'id': self.site_id,
            'name': self.name,
            'enabled': self.enabled,
            'intervalSeconds': self.interval_seconds,
            'email': self.email,
            'password': self.password,
            'token': self.token,
            'sessionCookie': self.session_cookie,
            'authMode': self.auth_mode,
            'timezone': self.timezone,
        }
        for js_key, py_key in self._FIELD_MAP.items():
            if js_key not in d:
                d[js_key] = getattr(self, py_key, None)
        for field in ('currency', 'threshold'):
            d[field] = getattr(self, field)
        return d


class UpstreamSession(models.Model):
    """Cached upstream login session (token or cookie)."""
    cache_key = models.CharField(max_length=256, unique=True, db_index=True)
    fingerprint = models.CharField(max_length=64)
    token = models.TextField(blank=True)
    session_cookie = models.TextField(blank=True)
    new_api_user_id = models.CharField(max_length=64, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = 'monitor'

    def to_dict(self) -> dict:
        return {
            'fingerprint': self.fingerprint,
            'token': self.token,
            'sessionCookie': self.session_cookie,
            'newApiUserId': self.new_api_user_id,
            'updatedAt': self.updated_at.isoformat() if self.updated_at else '',
        }


class DailyUsage(models.Model):
    """Per-site per-day usedBalance snapshot (for computing yesterday's spend)."""
    site_id = models.CharField(max_length=64, db_index=True)
    day_key = models.CharField(max_length=10)   # YYYY-MM-DD
    used_balance = models.FloatField()

    class Meta:
        app_label = 'monitor'
        unique_together = [('site_id', 'day_key')]
        indexes = [models.Index(fields=['site_id', 'day_key'])]
