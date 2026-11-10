"""Database cache for daily API key consumption; reads never query upstream."""
import hashlib
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from django.db import close_old_connections, transaction

from monitor.adapters import get_usage_keys, normalize_site, site_config
from monitor.time_utils import day_key_of, shift_day_key

logger = logging.getLogger(__name__)
_refresh_lock = threading.Lock()
_write_lock = threading.Lock()


def usage_fingerprint(site):
    cfg = site_config(site)
    # Labels/order do not alter amounts. Credentials, key sets and boundaries do.
    identity = {key: cfg.get(key) for key in (
        'baseUrl', 'siteType', 'mePath', 'balanceField', 'timezone', 'currency',
        'token', 'newApiUserId')}
    identity['keys'] = sorted(key['token'] for key in get_usage_keys(cfg))
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def _days(site, now):
    today = day_key_of(site.get('timezone') or 'Asia/Shanghai', now)
    return [shift_day_key(today, offset) for offset in range(-6, 1)]


def save_usage_points(site, points, now):
    from monitor.models import KeyUsageDay
    fingerprint = usage_fingerprint(site)
    with _write_lock, transaction.atomic():
        for point in points:
            row, _ = KeyUsageDay.objects.get_or_create(
                site_id=str(site['id']), fingerprint=fingerprint, day_key=point['date'],
                defaults={'attempted_at': now})
            if row.attempted_at > now:
                continue
            # Never replace a complete/previously larger successful subset with
            # an incomplete result. Keep its timestamp so retained data is clear.
            failed = point.get('failedCount', 0)
            successes = point.get('successCount', 0)
            keep = row.cost is not None and (point.get('cost') is None or (
                failed > 0 and (row.failed_count == 0 or successes < row.success_count)))
            if not keep:
                row.cost = point.get('cost')
                row.success_count = successes
                row.failed_count = failed
                row.updated_at = now if row.cost is not None else None
                row.finalized = point['date'] < _days(site, now)[-1] and row.cost is not None
            row.attempted_at = now
            row.refresh_failed = keep or failed > 0 or point.get('cost') is None
            row.save()


def save_today_usage(site, usage, now):
    if usage and usage.get('source') == 'keys' and usage.get('date') == _days(site, now)[-1]:
        save_usage_points(site, [usage], now)


def refresh_site_usage(site, *, force=False, now=None):
    from monitor.balance import get_weekly_key_usage
    from monitor.models import KeyUsageDay
    now = now or datetime.now(timezone.utc)
    days = _days(site, now)
    fingerprint = usage_fingerprint(site)
    rows = {row.day_key: row for row in KeyUsageDay.objects.filter(
        site_id=str(site['id']), fingerprint=fingerprint, day_key__in=days)}
    interval = normalize_site(site)['intervalSeconds']
    needed = []
    for day in days:
        row = rows.get(day)
        age = (now - row.attempted_at).total_seconds() if row else float('inf')
        if force or row is None or (
            age >= interval and (day == days[-1] or not row.finalized or row.refresh_failed or row.cost is None)
        ) or (day == days[-2] and age >= max(interval, 3600)):
            needed.append(day)
    if needed:
        try:
            points = get_weekly_key_usage(site, now, requested_days=needed)
        except Exception:
            logger.exception('Daily key consumption refresh failed for site %s', site['id'])
            points = [{'date': day, 'cost': None, 'successCount': 0,
                       'failedCount': max(1, len(get_usage_keys(site)))} for day in needed]
        save_usage_points(site, points, now)
    # Bound history for all configurations without deleting a newer cache if an
    # older in-flight query finishes after a configuration change.
    with _write_lock:
        KeyUsageDay.objects.filter(site_id=str(site['id']), day_key__lt=shift_day_key(days[-1], -40)).delete()


def refresh_usage_history(sites, *, force=False):
    """Serialize manual refreshes and skip overlapping background jobs."""
    if not _refresh_lock.acquire(blocking=force):
        return
    try:
        def refresh(site):
            close_old_connections()
            try:
                refresh_site_usage(site, force=force)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(refresh, sites))
    finally:
        _refresh_lock.release()


def read_usage_history(sites, now=None):
    from monitor.models import KeyUsageDay
    now = now or datetime.now(timezone.utc)
    # One indexed query for every site and all seven days.
    rows = {(row.site_id, row.fingerprint, row.day_key): row for row in KeyUsageDay.objects.filter(
        site_id__in=[str(site['id']) for site in sites],
        day_key__gte=shift_day_key(day_key_of('UTC', now), -7))}
    groups = {}
    timestamps = []
    for site in sites:
        cfg = site_config(site)
        days = _days(site, now)
        today = days[-1]
        interval = normalize_site(site)['intervalSeconds']
        fingerprint = usage_fingerprint(site)
        currency = cfg.get('currency') or 'CNY'
        zone = cfg.get('timezone') or 'Asia/Shanghai'
        group = groups.setdefault((currency, zone), {
            'currency': currency, 'timezone': zone, 'siteCount': 0, 'days': {}})
        group['siteCount'] += 1
        for day in days:
            point = group['days'].setdefault(day, {
                'date': day, 'cost': None, 'successCount': 0, 'failedCount': 0,
                'staleCount': 0, 'pendingCount': 0, 'updatedAt': None})
            row = rows.get((str(site['id']), fingerprint, day))
            if row is None:
                point['pendingCount'] += 1
                point['failedCount'] += max(1, len(get_usage_keys(site)))
                continue
            if row.cost is not None:
                point['cost'] = (point['cost'] or 0) + row.cost
                timestamps.append(row.updated_at)
                timestamp = row.updated_at.isoformat()
                point['updatedAt'] = min(point['updatedAt'], timestamp) if point['updatedAt'] else timestamp
            point['successCount'] += row.success_count
            point['failedCount'] += row.failed_count
            stale = row.refresh_failed or (day < today and not row.finalized) or (
                day == today and (now - row.attempted_at).total_seconds() > interval * 2)
            point['staleCount'] += int(stale)
    return {'series': [{**group, 'days': list(group['days'].values())} for group in groups.values()],
            'queriedAt': min(timestamps).isoformat() if timestamps else None,
            'source': 'database'}
