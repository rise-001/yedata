"""Persistent balance reconciliation. Reads never contact upstream services."""
import hashlib
import json
import logging
import threading
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from django.db import transaction

from monitor.adapters import get_usage_keys, is_newapi_site, is_sub2_site, normalize_site, site_config
from monitor.time_utils import day_key_of

logger = logging.getLogger(__name__)
TOLERANCE = Decimal('0.01')
REVIEW_SECONDS = 60
STATUSES = {'baseline', 'normal', 'pending', 'recharge', 'anomaly', 'recovered', 'unavailable', 'reset', 'cancelled'}
RESOLUTIONS = {'', 'verified_recharge', 'adjustment', 'contacted', 'resolved'}
_write_lock = threading.RLock()
_site_locks = {}
_locks_lock = threading.Lock()


def decimal_amount(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and abs(number) < Decimal('1e17') else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def fingerprint(site):
    cfg = site_config(site)
    identity = {key: cfg.get(key) for key in (
        'baseUrl', 'siteType', 'mePath', 'balanceField', 'balanceDivisor',
        'balanceMultiplier', 'currency', 'timezone', 'authMode', 'email',
        'token', 'password', 'sessionCookie', 'newApiUserId')}
    # ORM FloatFields and JSON numbers must produce the same identity: 1 == 1.0.
    for key in ('balanceDivisor', 'balanceMultiplier'):
        value = decimal_amount(cfg.get(key))
        if value is None or value <= 0:
            value = Decimal(1) if key == 'balanceMultiplier' else None
        identity[key] = str(value.normalize()) if value is not None else None
    for key in ('token', 'password', 'sessionCookie', 'email', 'newApiUserId'):
        identity[key] = str(cfg.get(key) or '')
    identity['timezone'] = cfg.get('timezone') or 'Asia/Shanghai'
    identity['authMode'] = cfg.get('authMode') or 'password'
    identity['keys'] = sorted(key['token'] for key in get_usage_keys(cfg))
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def observation(site, result, started_at, captured_at):
    """Use account totals only when explicitly provided with account quota."""
    cfg = site_config(site)
    accounting = result.get('accounting') or {}
    raw_balance = decimal_amount(accounting.get('balance'))
    divisor = decimal_amount(result.get('balanceDivisor'))
    multiplier = decimal_amount(result.get('balanceMultiplier'))
    scale = multiplier / divisor if divisor and divisor > 0 and multiplier and multiplier > 0 else None
    balance = raw_balance * scale if raw_balance is not None and scale else None
    used = decimal_amount(accounting.get('usedQuota'))
    field = result.get('balanceField')
    account = is_newapi_site(cfg) and field == 'quota' and used is not None
    scope = 'account' if account else 'keys'
    usage = result.get('todayUsage') or {}
    cost = decimal_amount(usage.get('cost'))
    day = day_key_of(cfg.get('timezone') or 'Asia/Shanghai', captured_at)
    consumption = used * scale if account and scale else None
    reason = ''
    if balance is None:
        reason = '余额数据缺失或无效'
    elif decimal_amount(accounting.get('frozen')) != Decimal(0):
        reason = '存在冻结额度或冻结数据无效，暂不推算充值和扣减'
    elif account:
        if used < 0:
            reason = '账户累计消耗无效'
    elif not ((is_newapi_site(cfg) and field == 'quota') or (is_sub2_site(cfg) and field == 'balance')):
        reason = '此余额字段尚未配置可对应的消耗口径'
    elif (usage.get('source') != 'keys' or cost is None or cost < 0
          or usage.get('failedCount', 0) or not usage.get('successCount')
          or usage['successCount'] != len(get_usage_keys(cfg))):
        reason = '已配置 Key 的消耗未全部获取，不能使用部分小计对账'
    elif usage.get('date') != day or day_key_of(cfg.get('timezone') or 'Asia/Shanghai', started_at) != day:
        reason = '本轮采集跨越零点或消耗日期不匹配，等待下一轮完整数据'
    elif (captured_at - started_at).total_seconds() > 120:
        reason = '余额与消耗采集间隔超过 120 秒，等待重新采集'
    else:
        # Match the balance's unit conversion, only inside reconciliation.
        consumption = cost * scale * (500000 if is_newapi_site(cfg) else 1)
    evidence = {
        'rawBalance': accounting.get('balance'), 'rawAccountUsage': accounting.get('usedQuota'),
        'rawKeyUsage': str(cost) if cost is not None else None,
        'divisor': str(divisor) if divisor else None, 'multiplier': str(multiplier) if multiplier else None,
        'frozen': accounting.get('frozen'), 'balanceCapturedAt': accounting.get('balanceCapturedAt'),
        'timezone': cfg.get('timezone') or 'Asia/Shanghai',
        'keyCount': len(get_usage_keys(cfg)), 'successCount': usage.get('successCount', 0),
        'failedCount': usage.get('failedCount', 0), 'accountId': str((result.get('raw') or {}).get('id') or ''),
    }
    return dict(site_id=str(site['id']), site_name=str(site.get('name') or '未命名站点')[:80],
                fingerprint=fingerprint(site), started_at=started_at, captured_at=captured_at,
                currency=cfg.get('currency') or 'CNY', scope=scope, day_key=day,
                balance=balance, consumption=consumption, complete=not reason,
                reason=reason, evidence=evidence)


def interval_consumption(site, baseline, current):
    """Bridge calendar days with freshly fetched complete days, never partial caches."""
    if baseline.scope == 'account' or baseline.day_key == current.day_key:
        return current.consumption - baseline.consumption, {}
    from monitor.balance import get_key_usage_for_day
    first, last = date.fromisoformat(baseline.day_key), date.fromisoformat(current.day_key)
    days = (last - first).days
    if not 0 < days <= 31:
        raise ValueError('对比区间超过 31 天或日期倒退，请重建基准')
    evidence = {}
    total = Decimal(0)
    factor = Decimal(current.evidence['multiplier']) / Decimal(current.evidence['divisor'])
    if is_newapi_site(site_config(site)):
        factor *= 500000
    for offset in range(days):
        day = (first + timedelta(days=offset)).isoformat()
        point = get_key_usage_for_day(site, day)
        cost = decimal_amount(point.get('cost'))
        if (point.get('date') != day or point.get('source') != 'keys' or cost is None or cost < 0
                or point.get('failedCount', 0) or point.get('successCount') != len(get_usage_keys(site))):
            raise ValueError(f'{day} 的完整消耗未获取，暂不能跨天对账')
        normalized = cost * factor
        if offset == 0 and normalized < baseline.consumption - TOLERANCE:
            raise ValueError(f'{day} 的全天消耗小于已采集值，等待上游修正')
        evidence[day] = str(normalized)
        total += normalized
    return total - baseline.consumption + current.consumption, evidence


def _event(snapshot, status, baseline=None, **kwargs):
    from monitor.models import ReconciliationEvent
    return ReconciliationEvent.objects.create(
        site_id=snapshot.site_id, baseline=baseline, first_snapshot=snapshot,
        latest_snapshot=snapshot, status=status, created_at=snapshot.captured_at,
        updated_at=snapshot.captured_at, stable_since=snapshot.captured_at, **kwargs)


def record_observation(site, values, *, require_saved=False):
    from monitor.models import ReconciliationSnapshot, ReconciliationState
    # Site collection is serialized by collect_check. Network requests for bridging
    # are performed before entering the short database write transaction.
    cursor = ReconciliationState.objects.select_related('baseline').filter(site_id=values['site_id']).first()
    candidate = ReconciliationSnapshot(**values)
    interval, bridge, bridge_error = None, {}, ''
    if (cursor and cursor.baseline and cursor.fingerprint == values['fingerprint'] and values['complete']
            and cursor.baseline.scope == values['scope']):
        try:
            interval, bridge = interval_consumption(site, cursor.baseline, candidate)
        except Exception as exc:
            bridge_error = str(exc) if isinstance(exc, ValueError) else '历史日消耗获取失败，等待重试'
    # Configuration may have changed during a slow historical lookup.
    if require_saved and not _matches_saved(site):
        return None
    with _write_lock, transaction.atomic():
        cursor, _ = ReconciliationState.objects.select_related('baseline', 'pending', 'latest_snapshot').get_or_create(site_id=values['site_id'])
        if cursor.latest_snapshot and values['started_at'] <= cursor.latest_snapshot.started_at:
            return None  # Repeated or older in-flight responses cannot move the cursor.
        snapshot = ReconciliationSnapshot.objects.create(**values)
        cursor.latest_snapshot = snapshot
        changed = cursor.fingerprint != snapshot.fingerprint
        if changed:
            _cancel_pending(cursor, snapshot.captured_at, '统计配置改变，原待复核记录停止推算')
            cursor.baseline = None
            cursor.fingerprint = snapshot.fingerprint
        baseline = cursor.baseline
        reason = snapshot.reason
        if baseline and baseline.evidence.get('accountId') != snapshot.evidence.get('accountId') and snapshot.complete:
            _cancel_pending(cursor, snapshot.captured_at, '上游账户身份改变')
            cursor.baseline = baseline = None
            changed = True
        if baseline and baseline.scope != snapshot.scope and snapshot.complete:
            reason = '账户累计消耗口径发生变化，等待原口径恢复或手动重建基准'
        if bridge_error:
            reason = bridge_error
        if not snapshot.complete or reason:
            event = _event(snapshot, 'unavailable', baseline, reason=reason)
        elif baseline is None:
            event = _event(snapshot, 'baseline', reason='建立有效基准；配置或账户改变时重新开始比较' if changed else '首次有效采集，建立基准')
            cursor.baseline = snapshot
        elif interval is not None and interval < -TOLERANCE:
            _cancel_pending(cursor, snapshot.captured_at, '累计消耗回退或清零')
            event = _event(snapshot, 'reset', baseline, reason='消耗计数回退或清零，本次重新建立基准')
            cursor.baseline = snapshot
        elif interval is None:
            event = _event(snapshot, 'unavailable', baseline, reason='没有可比较的完整消耗')
        else:
            difference = snapshot.balance - baseline.balance + interval
            pending = cursor.pending
            event = pending or _event(snapshot, 'normal', baseline)
            previous_difference = event.difference
            event.latest_snapshot = snapshot
            event.interval_usage = interval
            event.difference = difference
            event.evidence = {'dailyTotals': bridge}
            event.updated_at = snapshot.captured_at
            if abs(difference) <= TOLERANCE:
                event.status = 'recovered' if pending else 'normal'
                event.reason = '延迟后恢复，未计入充值或异常' if pending else '余额变化与消耗一致'
                cursor.baseline, cursor.pending = snapshot, None
            else:
                stable = pending and previous_difference is not None and abs(previous_difference - difference) <= TOLERANCE
                if not stable:
                    event.stable_since = snapshot.captured_at
                confirmed = stable and (snapshot.captured_at - event.stable_since).total_seconds() >= REVIEW_SECONDS
                event.status = ('recharge' if difference > 0 else 'anomaly') if confirmed else 'pending'
                event.reason = ('净增额度，可能包含充值、赠送、退款或调账' if difference > 0 else
                                ('余额扣减未被账户消耗解释' if snapshot.scope == 'account' else '仅覆盖已配置 Key，差额可能包含其他 Key 消耗'))
                if confirmed:
                    cursor.baseline, cursor.pending = snapshot, None
                else:
                    cursor.pending = event
            event.save(update_fields=['latest_snapshot', 'interval_usage', 'difference', 'evidence',
                                      'updated_at', 'stable_since', 'status', 'reason'])
        cursor.save()
        return event


def _cancel_pending(cursor, now, reason):
    if cursor.pending_id:
        cursor.pending.status, cursor.pending.reason = 'cancelled', reason
        cursor.pending.updated_at = now
        cursor.pending.save(update_fields=['status', 'reason', 'updated_at'])
        cursor.pending = None


def collect_check(site, check):
    """Wrap every manual/scheduled paired check, preserving the original result."""
    sid = str(site.get('id') or '')
    with _locks_lock:
        lock = _site_locks.setdefault(sid, threading.RLock())
    with lock:
        started = datetime.now(timezone.utc)
        try:
            result = check(site)
        except Exception:
            _record_safely(site, None, started)
            raise
        _record_safely(site, result, started)
        return result


def _record_safely(site, result, started):
    # Unsaved previews and a response from a replaced configuration are excluded.
    try:
        if not _matches_saved(site):
            return
        now = datetime.now(timezone.utc)
        # Notification delivery happens after the paired read and must not change
        # its accounting date (especially around midnight).
        if result and result.get('updatedAt'):
            captured = datetime.fromisoformat(result['updatedAt'])
            if captured.tzinfo is not None and started <= captured <= now:
                now = captured
        values = observation(site, result or {}, started, now)
        if result is None:
            values.update(complete=False, reason='本次余额采集失败，保留上一有效基准')
        record_observation(site, values, require_saved=True)
    except Exception:
        logger.exception('Failed to persist reconciliation for site %s', site.get('id'))
        if result is not None:
            result['reconciliationError'] = '差异分析记录保存失败，请检查服务日志'


def _matches_saved(site):
    from monitor import state
    saved = next((s for s in state.saved_sites if str(s['id']) == str(site.get('id'))), None)
    return saved and saved.get('enabled', True) and fingerprint(saved) == fingerprint(site)


def pending_site_ids():
    from monitor.models import ReconciliationState
    return set(ReconciliationState.objects.filter(pending__isnull=False).values_list('site_id', flat=True))


def reset_baseline(site_id):
    from monitor.models import ReconciliationState
    with _locks_lock:
        lock = _site_locks.setdefault(site_id, threading.RLock())
    with lock, _write_lock, transaction.atomic():
        cursor = ReconciliationState.objects.select_related('pending').filter(site_id=site_id).first()
        if cursor:
            _cancel_pending(cursor, datetime.now(timezone.utc), '手动重建基准，保留原始记录')
            cursor.baseline = None
            cursor.save()


def serialize_snapshot(row):
    if row is None:
        return None
    return {'id': row.id, 'siteId': row.site_id, 'siteName': row.site_name, 'scope': row.scope,
            'currency': row.currency, 'startedAt': row.started_at.isoformat(), 'capturedAt': row.captured_at.isoformat(),
            'day': row.day_key, 'balance': row.balance, 'consumption': row.consumption,
            'complete': row.complete, 'reason': row.reason, 'evidence': row.evidence}


def serialize_event(row, detail=False):
    latest = row.latest_snapshot
    data = {'id': row.id, 'siteId': row.site_id, 'siteName': latest.site_name,
            'status': row.status, 'currency': latest.currency, 'scope': latest.scope,
            'createdAt': row.created_at.isoformat(), 'updatedAt': row.updated_at.isoformat(),
            'fromTime': row.baseline.captured_at.isoformat() if row.baseline else None,
            'toTime': latest.captured_at.isoformat(), 'previousBalance': row.baseline.balance if row.baseline else None,
            'balance': latest.balance, 'intervalUsage': row.interval_usage, 'difference': row.difference,
            'reason': row.reason, 'note': row.note, 'resolution': row.resolution}
    if detail:
        data.update(baseline=serialize_snapshot(row.baseline), first=serialize_snapshot(row.first_snapshot),
                    latest=serialize_snapshot(latest), evidence=row.evidence, tolerance=row.tolerance,
                    reviewSeconds=REVIEW_SECONDS, stableSince=row.stable_since.isoformat())
    return data


def read_history(params):
    from django.db.models import OuterRef, Q, Subquery, Sum
    from monitor.models import ReconciliationEvent, ReconciliationState
    from monitor import state
    query = ReconciliationEvent.objects.select_related('baseline', 'latest_snapshot')
    if params.get('siteId'):
        query = query.filter(site_id=params['siteId'])
    # Date filters are Shanghai calendar dates, made explicit in the UI.
    from zoneinfo import ZoneInfo
    for key, lookup, offset in [('start', 'updated_at__gte', 0), ('end', 'updated_at__lt', 1)]:
        if params.get(key):
            try:
                day = date.fromisoformat(params[key]) + timedelta(days=offset)
            except (ValueError, OverflowError):
                raise ValueError('日期格式无效')
            query = query.filter(**{lookup: datetime.combine(day, datetime.min.time(), ZoneInfo('Asia/Shanghai'))})
    if params.get('start') and params.get('end') and params['start'] > params['end']:
        raise ValueError('开始日期不能晚于结束日期')
    totals = list(query.values('latest_snapshot__currency').annotate(
        recharge=Sum('difference', filter=Q(status='recharge')),
        anomaly=Sum('difference', filter=Q(status='anomaly'))))
    pending = query.filter(status='pending').count()
    enabled = {str(s['id']): s for s in state.saved_sites if s.get('enabled', True)}
    if params.get('siteId'):
        enabled = {sid: site for sid, site in enabled.items() if sid == params['siteId']}
    latest = ReconciliationEvent.objects.filter(site_id=OuterRef('site_id')).order_by('-updated_at', '-id')
    cursors = {c.site_id: c for c in ReconciliationState.objects.filter(site_id__in=enabled).select_related('latest_snapshot').annotate(
        latest_status=Subquery(latest.values('status')[:1]))}
    # Query status (not just raw completeness) also covers a missing cross-day bridge.
    now = datetime.now(timezone.utc)
    def unavailable(site_id, site):
        cursor = cursors.get(site_id)
        return (not cursor or not cursor.baseline_id or not cursor.latest_snapshot
                or cursor.fingerprint != fingerprint(site) or cursor.latest_status == 'unavailable'
                or (now - cursor.latest_snapshot.captured_at).total_seconds() > max(120, normalize_site(site)['intervalSeconds'] * 2))
    unavailable_count = sum(bool(unavailable(sid, site)) for sid, site in enabled.items())
    status = params.get('status', '')
    if status == 'attention':
        query = query.filter(status__in=['anomaly', 'pending', 'unavailable'])
    elif status:
        if status not in STATUSES:
            raise ValueError('无效的结果筛选')
        query = query.filter(status=status)
    try:
        page = max(1, int(params.get('page', 1)))
    except (TypeError, ValueError):
        raise ValueError('页码无效')
    count = query.count()
    page = min(page, max(1, (count + 49) // 50))
    rows = query.order_by('-updated_at', '-id')[(page - 1) * 50:page * 50]
    return {'records': [serialize_event(row) for row in rows], 'count': count, 'page': page, 'pageSize': 50,
            'totals': [{'currency': r['latest_snapshot__currency'], 'recharge': r['recharge'] or Decimal(0),
                        'anomaly': abs(r['anomaly'] or Decimal(0))} for r in totals],
            'pendingCount': pending, 'unavailableSiteCount': unavailable_count,
            'tolerance': TOLERANCE, 'reviewSeconds': REVIEW_SECONDS}
