"""
SQLite-backed persistence via Django ORM.
Replaces the three JSON files (sites.json, upstream-sessions.json, daily-usage.json).
The public API (function signatures) is identical to the old file-based storage.py
so all callers in balance.py, scheduler.py, and views.py work unchanged.
"""
import logging

from monitor.adapters import normalize_site

logger = logging.getLogger(__name__)


# ── Sites ──────────────────────────────────────────────────────────────────

def load_sites() -> tuple[list[dict], str]:
    """Return (sites_as_dicts, global_webhook)."""
    from monitor.models import Site, GlobalSettings
    webhook = GlobalSettings.get().webhook
    sites = [s.to_dict() for s in Site.objects.all()]
    return sites, webhook


def save_sites(sites: list[dict], settings_data: dict | None = None,
               current_webhook: str = '') -> tuple[list[dict], str]:
    """Persist sites list; return (saved_sites, webhook)."""
    from monitor.models import Site, GlobalSettings

    webhook = str((settings_data or {}).get('webhook', '')).strip() or current_webhook

    # Update global webhook.
    gs = GlobalSettings.get()
    gs.webhook = webhook
    gs.save(update_fields=['webhook'])

    # Build an id->dict map for merging.
    incoming = {str(s.get('id', '')): s for s in sites if s.get('id')}
    existing_objs = {obj.site_id: obj for obj in Site.objects.all()}

    saved = []
    order = 0
    for site_dict in sites:
        norm = normalize_site(site_dict)
        norm.pop('webhook', None)
        norm.pop('hasWebhook', None)
        sid = str(norm.get('id', ''))
        obj = existing_objs.get(sid)
        if obj is None:
            obj = Site()
        # Populate all fields from the normalized dict.
        obj.site_id = sid
        obj.name = norm.get('name', '')[:80]
        obj.enabled = norm.get('enabled', True) is not False
        obj.interval_seconds = int(norm.get('intervalSeconds', 300))
        obj.base_url = str(norm.get('baseUrl') or '')
        obj.site_type = str(norm.get('siteType') or '')
        obj.email = str(norm.get('email') or '')
        # Only overwrite secrets when a non-empty value is provided.
        for js_key, py_attr in (('password', 'password'), ('token', 'token'), ('sessionCookie', 'session_cookie')):
            val = norm.get(js_key)
            if val:
                setattr(obj, py_attr, str(val))
        obj.auth_mode = str(norm.get('authMode') or 'password')
        obj.new_api_user_id = str(norm.get('newApiUserId') or '')
        obj.login_path = str(norm.get('loginPath') or '')
        obj.me_path = str(norm.get('mePath') or '')
        obj.usage_path = str(norm.get('usagePath') or '')
        obj.balance_field = str(norm.get('balanceField') or '')
        bd = norm.get('balanceDivisor')
        obj.balance_divisor = float(bd) if bd is not None else None
        bm = norm.get('balanceMultiplier')
        obj.balance_multiplier = float(bm) if bm is not None else None
        obj.currency = str(norm.get('currency') or '')
        thr = norm.get('threshold')
        obj.threshold = float(thr) if thr is not None else None
        obj.notify_enabled = norm.get('notifyEnabled', True) is not False
        obj.timezone = str(norm.get('timezone') or 'Asia/Shanghai')
        obj.order = order
        obj.save()
        order += 1
        saved.append(obj.to_dict())

    # Delete sites no longer in the list.
    incoming_ids = set(incoming.keys())
    Site.objects.exclude(site_id__in=incoming_ids).delete()

    return saved, webhook


# ── Upstream sessions ──────────────────────────────────────────────────────

def load_upstream_sessions() -> dict:
    """Return {cache_key: session_dict}."""
    from monitor.models import UpstreamSession
    result = {}
    for obj in UpstreamSession.objects.all():
        if obj.fingerprint and (obj.token or obj.session_cookie):
            result[obj.cache_key] = obj.to_dict()
    return result


def save_upstream_sessions(sessions: dict) -> None:
    """Persist the full upstream-session dict (upsert all, delete removed)."""
    from monitor.models import UpstreamSession
    existing = {obj.cache_key: obj for obj in UpstreamSession.objects.all()}
    for key, data in sessions.items():
        if not isinstance(data, dict):
            continue
        obj = existing.get(key) or UpstreamSession(cache_key=key)
        obj.fingerprint = str(data.get('fingerprint') or '')
        obj.token = str(data.get('token') or '')
        obj.session_cookie = str(data.get('sessionCookie') or '')
        obj.new_api_user_id = str(data.get('newApiUserId') or '')
        obj.save()
    # Remove sessions no longer tracked.
    UpstreamSession.objects.exclude(cache_key__in=sessions.keys()).delete()


# ── Daily usage ────────────────────────────────────────────────────────────

def load_daily_usage() -> dict[str, dict[str, float]]:
    """Return {site_id: {day_key: float}}."""
    from monitor.models import DailyUsage
    result: dict[str, dict[str, float]] = {}
    for row in DailyUsage.objects.all():
        result.setdefault(row.site_id, {})[row.day_key] = row.used_balance
    return result


def save_daily_usage(usage: dict[str, dict[str, float]]) -> None:
    """Upsert all entries; do NOT delete old rows here (trimming is handled by balance.py)."""
    from monitor.models import DailyUsage
    for site_id, days in usage.items():
        for day_key, value in days.items():
            DailyUsage.objects.update_or_create(
                site_id=str(site_id),
                day_key=str(day_key),
                defaults={'used_balance': float(value)},
            )
