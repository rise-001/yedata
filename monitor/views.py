"""
All HTTP views — mirrors every route in server.js.
"""
import hmac
import json
import logging
import mimetypes
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from monitor import state
from monitor.adapters import (
    normalize_site, public_site, validate_site_list, with_saved_secrets,
)
from monitor.balance import check_and_notify, get_token_usage, record_daily_usage, send_wecom, send_wecom_image, summarize_key_usage
from monitor.report_image import render_daily_report
from monitor.usage_cache import read_usage_history, refresh_usage_history, save_today_usage
from monitor.storage import load_daily_report_webhook, save_sites, save_upstream_sessions
from monitor.reconciliation import collect_check

logger = logging.getLogger(__name__)

PUBLIC_DIR = Path(__file__).resolve().parent.parent / 'frontend' / 'dist'
_MIME = {
    '.html': 'text/html; charset=utf-8',
    '.css':  'text/css; charset=utf-8',
    '.js':   'text/javascript; charset=utf-8',
    '.svg':  'image/svg+xml',
}

# ── Helpers ─────────────────────────────────────────────────────────────────

def _json(data: dict, status: int = 200) -> JsonResponse:
    resp = JsonResponse(data, status=status)
    resp['Cache-Control'] = 'no-store'
    resp['X-Content-Type-Options'] = 'nosniff'
    return resp


def _err(message: str, status: int = 400) -> JsonResponse:
    return _json({'ok': False, 'message': message}, status)


def _read_body(request: HttpRequest) -> dict:
    if not request.body:
        return {}
    if len(request.body) > settings.MAX_BODY_BYTES:
        raise ValueError('请求数据过大')
    try:
        return json.loads(request.body)
    except Exception:
        raise ValueError('请求数据不是有效 JSON')


def _safe_equal(a: str, b: str) -> bool:
    ba, bb = a.encode(), b.encode()
    if len(ba) != len(bb):
        # Still run the comparison to avoid timing oracle.
        hmac.compare_digest(ba, ba)
        return False
    return hmac.compare_digest(ba, bb)


def _session_cookie(token: str, max_age: int) -> str:
    secure = '; Secure' if settings.COOKIE_SECURE else ''
    return (f'balance_session={token}; Path=/; HttpOnly; SameSite=Strict; '
            f'Max-Age={max_age}{secure}')


def _require_session(request: HttpRequest) -> bool:
    return request.app_session is not None


# ── Auth endpoints ───────────────────────────────────────────────────────────

@csrf_exempt
def api_login(request: HttpRequest) -> HttpResponse:
    if request.method != 'POST':
        return _err('Method not allowed', 405)
    if not settings.APP_PASSWORD:
        return _err('服务端尚未设置 APP_PASSWORD，请先配置管理员密码')

    ip = (request.META.get('HTTP_X_FORWARDED_FOR') or
          request.META.get('REMOTE_ADDR') or 'unknown').split(',')[0].strip()
    attempt = state.login_attempts.get(ip, {'count': 0, 'blocked_until': 0})
    if attempt['blocked_until'] > time.time():
        return _err('登录尝试过多，请稍后再试')

    try:
        body = _read_body(request)
    except ValueError as exc:
        return _err(str(exc))

    username_ok = _safe_equal(str(body.get('username', '')), settings.APP_USERNAME)
    password_ok = _safe_equal(str(body.get('password', '')), settings.APP_PASSWORD)
    if not (username_ok and password_ok):
        attempt['count'] = attempt.get('count', 0) + 1
        attempt['updated_at'] = time.time()
        if attempt['count'] >= 5:
            attempt['blocked_until'] = time.time() + 10 * 60
            attempt['count'] = 0
        state.login_attempts[ip] = attempt
        return _err('管理员账号或密码错误')

    state.login_attempts.pop(ip, None)
    token = secrets.token_hex(32)
    state.sessions[token] = {'expires_at': time.time() + settings.SESSION_MAX_AGE}
    resp = _json({'ok': True})
    resp['Set-Cookie'] = _session_cookie(token, settings.SESSION_MAX_AGE)
    return resp


@csrf_exempt
def api_logout(request: HttpRequest) -> HttpResponse:
    if request.method != 'POST':
        return _err('Method not allowed', 405)
    session = request.app_session
    if session:
        state.sessions.pop(session.get('token', ''), None)
    resp = _json({'ok': True})
    resp['Set-Cookie'] = _session_cookie('', 0)
    return resp


def api_session(request: HttpRequest) -> HttpResponse:
    return _json({'ok': bool(request.app_session)})


# ── Protected endpoints ──────────────────────────────────────────────────────

@csrf_exempt
def api_sites(request: HttpRequest) -> HttpResponse:
    if not _require_session(request):
        return _err('请先登录管理后台', 401)

    if request.method == 'GET':
        results = {site['id']: state.scheduled_results.get(site['id'])
                   for site in state.saved_sites}
        return _json({
            'ok': True,
            'data': {
                'sites': [public_site(s) for s in state.saved_sites],
                'results': results,
                'settings': {
                    'hasWebhook': bool(state.global_webhook),
                    'hasDailyReportWebhook': bool(state.daily_report_webhook),
                },
            },
        })

    if request.method == 'PUT':
        try:
            body = _read_body(request)
            validate_site_list(body.get('sites', []), settings.MAX_SITES)
            # Merge saved secrets before persisting.
            merged = [normalize_site(with_saved_secrets(s, state.saved_sites))
                      for s in body.get('sites', [])]
        except (ValueError, KeyError) as exc:
            return _err(str(exc))
        for s in merged:
            s.pop('webhook', None)
            s.pop('hasWebhook', None)

        try:
            saved, webhook = save_sites(merged, body.get('settings'), state.global_webhook)
        except ValueError as exc:
            return _err(str(exc))
        state.saved_sites = saved
        state.global_webhook = webhook
        state.daily_report_webhook = load_daily_report_webhook()

        # Clean up orphaned scheduled state.
        active_ids = {s['id'] for s in saved}
        for sid in list(state.scheduled_last_run):
            if sid not in active_ids:
                del state.scheduled_last_run[sid]
                state.scheduled_results.pop(sid, None)
        for s in saved:
            if not s.get('enabled', True):
                state.scheduled_last_run.pop(s['id'], None)

        # Remove upstream sessions for deleted sites.
        changed = False
        for sid in list(state.upstream_sessions):
            if sid not in active_ids:
                del state.upstream_sessions[sid]
                changed = True
        if changed:
            save_upstream_sessions(state.upstream_sessions)

        return _json({
            'ok': True,
            'data': {
                'sites': [public_site(s) for s in saved],
                'settings': {
                    'hasWebhook': bool(webhook),
                    'hasDailyReportWebhook': bool(state.daily_report_webhook),
                },
            },
        })

    return _err('Method not allowed', 405)


@csrf_exempt
def api_check(request: HttpRequest) -> HttpResponse:
    if not _require_session(request):
        return _err('请先登录管理后台', 401)
    if request.method != 'POST':
        return _err('Method not allowed', 405)
    try:
        body = _read_body(request)
        if body.get('enabled') is False:
            return _err('该站点已停用，请先启用后再检查')
        site = with_saved_secrets(body, state.saved_sites)
        result = collect_check(site, check_and_notify)
        return _json({'ok': True, 'data': result})
    except (ValueError, Exception) as exc:
        status = getattr(exc, 'status', 400)
        return _err(str(exc), status)


@csrf_exempt
def api_token_usage(request: HttpRequest) -> HttpResponse:
    if not _require_session(request):
        return _err('请先登录管理后台', 401)
    if request.method != 'POST':
        return _err('Method not allowed', 405)
    try:
        body = _read_body(request)
        site_id = str(body.get('siteId') or '')
        site = next((item for item in state.saved_sites if str(item.get('id')) == site_id), None)
        if not site:
            return _err('未找到已保存的站点配置', 404)
        queried_at = datetime.now(timezone.utc)
        data = get_token_usage(site)
        today_usage = summarize_key_usage(site, data, queried_at)
        try:
            save_today_usage(site, today_usage, queried_at)
        except Exception:
            logger.exception('Failed to cache manual key consumption for site %s', site_id)
        today_error = '未获取到任何 Key 的今日消耗，请检查消耗查询配置' if today_usage['cost'] is None else ''
        previous = state.scheduled_results.get(site_id)
        if previous is not None:
            state.scheduled_results[site_id] = {**previous, 'todayUsage': today_usage, 'todayUsageError': today_error}
        return _json({'ok': True, 'data': data, 'todayUsage': today_usage, 'todayUsageError': today_error})
    except Exception as exc:
        return _err(str(exc), getattr(exc, 'status', 400))


@csrf_exempt
def api_usage_history(request: HttpRequest) -> HttpResponse:
    if not _require_session(request):
        return _err('请先登录管理后台', 401)
    if request.method not in ('GET', 'POST'):
        return _err('Method not allowed', 405)
    sites = [dict(site) for site in state.saved_sites if site.get('enabled', True) is not False]
    try:
        if request.method == 'POST':
            refresh_usage_history(sites, force=True)
        return _json({'ok': True, 'data': read_usage_history(sites)})
    except Exception:
        logger.exception('Daily key consumption storage failed')
        return _err('消耗数据存储暂时不可用，请稍后重试', 503)


@csrf_exempt
def api_check_many(request: HttpRequest) -> HttpResponse:
    if not _require_session(request):
        return _err('请先登录管理后台', 401)
    if request.method != 'POST':
        return _err('Method not allowed', 405)
    try:
        body = _read_body(request)
        validate_site_list(body.get('sites', []), settings.MAX_SITES)
    except ValueError as exc:
        return _err(str(exc))

    sites = body.get('sites', [])
    if not sites:
        return _err('请至少添加一个站点')
    enabled = [s for s in sites if s.get('enabled', True) is not False]
    if not enabled:
        return _err('没有已启用的站点，请先启用后再检查')

    results = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for site_input in enabled:
        site = with_saved_secrets(site_input, state.saved_sites)
        try:
            r = collect_check(site, check_and_notify)
            result = {**r, 'siteId': site.get('id'), 'siteName': site.get('name') or site.get('baseUrl'), 'ok': True, 'checkedAt': now_iso}
        except Exception as exc:
            result = {'siteId': site.get('id'), 'siteName': site.get('name') or site.get('baseUrl'), 'ok': False, 'message': str(exc), 'checkedAt': now_iso}

        sid = str(site.get('id', ''))
        if any(s['id'] == sid for s in state.saved_sites):
            state.scheduled_last_run[sid] = time.time()
            state.scheduled_results[sid] = result
        if result.get('ok'):
            record_daily_usage(site, result)
            try:
                save_today_usage(site, result.get('todayUsage'), datetime.now(timezone.utc))
            except Exception:
                logger.exception('Failed to cache checked key consumption for site %s', sid)
        results.append(result)

    return _json({'ok': True, 'data': results})


@csrf_exempt
def api_reconciliation(request: HttpRequest) -> HttpResponse:
    if not _require_session(request):
        return _err('请先登录管理后台', 401)
    from monitor.reconciliation import read_history, reset_baseline
    try:
        if request.method == 'POST':
            body = _read_body(request)
            site_id = str(body.get('siteId') or '')
            sites = [dict(s) for s in state.saved_sites if s.get('enabled', True)
                     and (not site_id or str(s['id']) == site_id)]
            if not sites:
                return _err('没有匹配的已启用站点', 404)
            action = body.get('action')
            if action == 'reset' and site_id:
                reset_baseline(site_id)
            elif action == 'check':
                failures = 0
                for site in sites:
                    try:
                        result = collect_check(site, check_and_notify)
                        if result.get('reconciliationError'):
                            failures += 1
                        state.scheduled_results[str(site['id'])] = {
                            **result, 'ok': True, 'checkedAt': datetime.now(timezone.utc).isoformat()}
                    except Exception:
                        failures += 1
                return _json({'ok': True, 'data': {'checked': len(sites), 'failed': failures}})
            else:
                return _err('无效的差异分析操作')
            return _json({'ok': True})
        if request.method != 'GET':
            return _err('Method not allowed', 405)
        return _json({'ok': True, 'data': read_history(request.GET)})
    except ValueError as exc:
        return _err(str(exc))
    except Exception:
        logger.exception('Reconciliation storage failed')
        return _err('差异分析数据暂时不可用，请稍后重试', 503)


@csrf_exempt
def api_reconciliation_detail(request: HttpRequest, event_id: int) -> HttpResponse:
    if not _require_session(request):
        return _err('请先登录管理后台', 401)
    if request.method not in ('GET', 'PATCH'):
        return _err('Method not allowed', 405)
    from monitor.models import ReconciliationEvent
    from monitor.reconciliation import RESOLUTIONS, serialize_event
    try:
        row = ReconciliationEvent.objects.select_related('baseline', 'first_snapshot', 'latest_snapshot').get(pk=event_id)
        if request.method == 'PATCH':
            body = _read_body(request)
            note, resolution = body.get('note', ''), body.get('resolution', '')
            if not isinstance(note, str) or len(note) > 2000 or resolution not in RESOLUTIONS:
                return _err('备注最多 2000 字，请选择有效的处理状态')
            # Updating notes must never overwrite a concurrent review's amounts/status.
            ReconciliationEvent.objects.filter(pk=row.pk).update(note=note, resolution=resolution)
            row.refresh_from_db()
        return _json({'ok': True, 'data': serialize_event(row, detail=True)})
    except ReconciliationEvent.DoesNotExist:
        return _err('记录不存在', 404)
    except (ValueError, TypeError):
        return _err('请求数据无效')


@csrf_exempt
def api_notify_test(request: HttpRequest) -> HttpResponse:
    if not _require_session(request):
        return _err('请先登录管理后台', 401)
    if request.method != 'POST':
        return _err('Method not allowed', 405)
    try:
        body = _read_body(request)
        target = body.get('target', 'alert')
        if target not in ('alert', 'dailyReport'):
            return _err('无效的通知类型')
        daily_report = target == 'dailyReport'
        saved_webhook = state.daily_report_webhook if daily_report else state.global_webhook
        webhook = str(body.get('webhook') or '').strip() or saved_webhook
        if not webhook:
            return _err('请填写晨报企业微信 Webhook' if daily_report else '请填写企业微信机器人 Webhook')
        if daily_report:
            from monitor.time_utils import day_key_of
            image = render_daily_report(
                title='数据中心晨报（测试）', date=day_key_of('Asia/Shanghai').replace('-', '/'),
                balance_total='100.00', usage_total='4.35',
                rows=[('示例站点', '100.00', '4.35')],
                notes=['测试数据，仅用于验证晨报图片通知。正式晨报统计昨日消耗。'],
            )
            send_wecom_image(webhook, image)
        else:
            send_wecom(webhook, '**数据中心 · 余额预警通知测试**\n> 余额预警企业微信通知已连接')
        # A successful test with a newly entered URL should also activate it
        # for scheduled notifications. The test button is commonly used as the only
        # save action for notification settings.
        if body.get('webhook'):
            from monitor.storage import save_global_webhook
            value = save_global_webhook(webhook, daily_report=daily_report)
            if daily_report:
                state.daily_report_webhook = value
            else:
                state.global_webhook = value
        return _json({'ok': True})
    except Exception as exc:
        return _err(str(exc))


def api_scheduler(request: HttpRequest) -> HttpResponse:
    if not _require_session(request):
        return _err('请先登录管理后台', 401)
    return _json({
        'ok': True,
        'data': {
            'enabled': True,
            'siteCount': len(state.saved_sites),
            'enabledSiteCount': sum(1 for s in state.saved_sites if s.get('enabled', True) is not False),
            'running': list(state.scheduled_running),
            'nextTickSeconds': 5,
        },
    })


def api_health(request: HttpRequest) -> HttpResponse:
    return _json({'ok': True})


# ── Static file serving ──────────────────────────────────────────────────────

def serve_static(request: HttpRequest, path: str = '') -> HttpResponse:
    # Auth redirect for root/index.
    if not path or path in ('', 'index.html'):
        if not request.app_session:
            resp = HttpResponse(status=302)
            resp['Location'] = '/login.html'
            resp['Cache-Control'] = 'no-store'
            return resp

    if path == 'login.html' and request.app_session:
        resp = HttpResponse(status=302)
        resp['Location'] = '/'
        resp['Cache-Control'] = 'no-store'
        return resp

    # Prevent path traversal.
    file_path = (PUBLIC_DIR / (path or 'index.html')).resolve()
    try:
        file_path.relative_to(PUBLIC_DIR.resolve())
    except ValueError:
        return _json({'message': '禁止访问'}, 403)

    try:
        content = file_path.read_bytes()
    except FileNotFoundError:
        return _json({'message': '页面不存在'}, 404)

    ext = file_path.suffix.lower()
    content_type = _MIME.get(ext, mimetypes.guess_type(str(file_path))[0] or 'application/octet-stream')
    resp = HttpResponse(content, content_type=content_type)
    if ext == '.html':
        resp['Cache-Control'] = 'no-store, no-cache, must-revalidate'
        resp['Pragma'] = 'no-cache'
        resp['Expires'] = '0'
    else:
        resp['Cache-Control'] = 'no-cache'
    resp['X-Content-Type-Options'] = 'nosniff'
    return resp
