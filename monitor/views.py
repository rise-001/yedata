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
from monitor.balance import check_and_notify, get_token_usage, record_daily_usage, send_wecom
from monitor.storage import save_sites, save_upstream_sessions

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
                'settings': {'hasWebhook': bool(state.global_webhook)},
            },
        })

    if request.method == 'PUT':
        try:
            body = _read_body(request)
            validate_site_list(body.get('sites', []), settings.MAX_SITES)
        except (ValueError, KeyError) as exc:
            return _err(str(exc))

        # Merge saved secrets before persisting.
        merged = [normalize_site(with_saved_secrets(s, state.saved_sites))
                  for s in body.get('sites', [])]
        for s in merged:
            s.pop('webhook', None)
            s.pop('hasWebhook', None)

        saved, webhook = save_sites(merged, body.get('settings'), state.global_webhook)
        state.saved_sites = saved
        state.global_webhook = webhook

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
                'settings': {'hasWebhook': bool(webhook)},
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
        result = check_and_notify(site)
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
        return _json({'ok': True, 'data': get_token_usage(site)})
    except Exception as exc:
        return _err(str(exc), getattr(exc, 'status', 400))


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
            r = check_and_notify(site)
            result = {**r, 'siteId': site.get('id'), 'siteName': site.get('name') or site.get('baseUrl'), 'ok': True, 'checkedAt': now_iso}
        except Exception as exc:
            result = {'siteId': site.get('id'), 'siteName': site.get('name') or site.get('baseUrl'), 'ok': False, 'message': str(exc), 'checkedAt': now_iso}

        sid = str(site.get('id', ''))
        if any(s['id'] == sid for s in state.saved_sites):
            state.scheduled_last_run[sid] = time.time()
            state.scheduled_results[sid] = result
        if result.get('ok'):
            record_daily_usage(site, result)
        results.append(result)

    return _json({'ok': True, 'data': results})


@csrf_exempt
def api_notify_test(request: HttpRequest) -> HttpResponse:
    if not _require_session(request):
        return _err('请先登录管理后台', 401)
    if request.method != 'POST':
        return _err('Method not allowed', 405)
    try:
        body = _read_body(request)
        webhook = str(body.get('webhook') or state.global_webhook).strip()
        if not webhook:
            return _err('请填写企业微信机器人 Webhook')
        send_wecom(webhook, '**余额监控测试**\n> 全局企业微信通知已连接')
        # A successful test with a newly entered URL should also activate it
        # for scheduled alerts. The test button is commonly used as the only
        # save action for notification settings.
        if body.get('webhook'):
            from monitor.storage import save_global_webhook
            state.global_webhook = save_global_webhook(webhook)
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
    resp['Cache-Control'] = 'no-cache'
    resp['X-Content-Type-Options'] = 'nosniff'
    return resp
