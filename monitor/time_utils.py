"""
Timezone-aware date helpers used by the scheduler and daily report.
"""
from datetime import datetime, timedelta, timezone


def local_parts(tz_name: str, dt: datetime | None = None) -> dict[str, str]:
    """Return {'year','month','day','hour'} in the given timezone."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name or 'Asia/Shanghai')
    except Exception:
        tz = timezone.utc
    if dt is None:
        dt = datetime.now(timezone.utc)
    local = dt.astimezone(tz)
    return {
        'year':  local.strftime('%Y'),
        'month': local.strftime('%m'),
        'day':   local.strftime('%d'),
        'hour':  local.strftime('%H'),
    }


def day_key_of(tz_name: str, dt: datetime | None = None) -> str:
    parts = local_parts(tz_name, dt)
    return f'{parts["year"]}-{parts["month"]}-{parts["day"]}'


def shift_day_key(day_key: str, days: int) -> str:
    base = datetime.strptime(day_key, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    shifted = base + timedelta(days=days)
    return shifted.strftime('%Y-%m-%d')
