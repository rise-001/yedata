"""
Balance fetching, WeCom notifications, and the daily-report logic.
Mirrors getBalance, checkAndNotify, sendWecom, runDailyReport from server.js.
"""
import logging
import time
from math import isfinite
from datetime import datetime
from datetime import timezone as dt_timezone

import requests
from django.conf import settings

from monitor import state
from monitor.adapters import (
    infer_adapter, is_newapi_site, sanitize_cookie, site_config, upstream_fingerprint, url_for,
)
from monitor.storage import save_upstream_sessions, save_daily_usage
from monitor.time_utils import day_key_of, shift_day_key, local_parts

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15  # seconds


# ── HTTP helpers ────────────────────────────────────────────────────────────

def fetch_json(url: str, *, method: str = 'GET', headers: dict | None = None,
               json_body: dict | None = None, return_meta: bool = False):
    resp = requests.request(
        method, url,
        headers={'Accept': 'application/json', **(headers or {})},
        json=json_body,
        timeout=REQUEST_TIMEOUT,
    )
    try:
        data = resp.json() if resp.text else {}
    except Exception:
        data = {'message': resp.text[:300]}

    if not resp.ok:
        msg = data.get('message') if isinstance(data, dict) else None
        raise _api_error(msg or f'接口返回 HTTP {resp.status_code}', resp.status_code)

    if isinstance(data, dict):
        if data.get('success') is False:
            raise _api_error(data.get('message') or '接口返回失败', resp.status_code)
        code = data.get('code')
        if code is not None and code is not True:
            if code is False:
                raise _api_error(data.get('message') or '接口返回失败', resp.status_code)
            try:
                code_ok = int(code) in (0, 200)
            except (TypeError, ValueError):
                code_ok = False
            if not code_ok:
                raise _api_error(data.get('message') or '接口返回失败', resp.status_code)

    if not return_meta:
        return data
    set_cookie = resp.headers.get('Set-Cookie', '')
    auth_header = resp.headers.get('Authorization') or resp.headers.get('X-Auth-Token') or ''
    return {'data': data, 'set_cookies': [set_cookie] if set_cookie else [], 'authorization': auth_header}


def get_token_usage(site: dict):
    """Fetch New API token usage using the token saved for a site."""
    cfg = site_config(site)
    if not is_newapi_site(cfg):
        raise ValueError('消耗查询仅支持 New API 站点')
    token = str(cfg.get('usageToken') or '').strip()
    if not token:
        raise ValueError('请先在站点设置中填写消耗查询 API Key 并保存')

    if token.lower().startswith('bearer '):
        token = token[7:].strip()
    raw = fetch_json(
        url_for(cfg.get('baseUrl', ''), '/api/usage/token/'),
        headers={'Authorization': f'Bearer {token}'},
    )
    inner = raw.get('data') if isinstance(raw, dict) else None
    data = inner if isinstance(inner, dict) else raw if isinstance(raw, dict) else {}

    def number(*keys):
        for key in keys:
            value = data.get(key)
            if value is not None and value != '':
                try:
                    return float(value)
                except (TypeError, ValueError):
                    pass
        return None

    used = number('used_quota', 'usedQuota', 'used')
    remaining = number('remain_quota', 'remaining_quota', 'remainQuota', 'remaining')
    total = number('total_quota', 'quota', 'totalQuota', 'total')
    unlimited = bool(data.get('unlimited_quota') or data.get('unlimitedQuota'))
    if total is None and used is not None and remaining is not None:
        total = used + remaining
    if remaining is None and total is not None and used is not None:
        remaining = max(0, total - used)

    expires = data.get('expired_time') or data.get('expiredTime') or data.get('expired_at') or data.get('expires_at')
    try:
        expires = int(expires) if expires not in (None, '') else None
    except (TypeError, ValueError):
        expires = None

    model_limits = data.get('model_limits') or data.get('modelLimits') or []
    if isinstance(model_limits, str):
        model_limits = [item.strip() for item in model_limits.split(',') if item.strip()]
    elif not isinstance(model_limits, list):
        model_limits = []

    return {
        'name': data.get('name') or data.get('token_name') or data.get('tokenName') or '',
        'usedQuota': used,
        'remainingQuota': remaining,
        'totalQuota': total,
        'unlimitedQuota': unlimited,
        'expiresAt': expires,
        'modelLimitsEnabled': bool(data.get('model_limits_enabled') or data.get('modelLimitsEnabled')),
        'modelLimits': model_limits,
    }


def _api_error(msg: str, status: int) -> Exception:
    err = ValueError(msg)
    err.status = status  # type: ignore[attr-defined]
    return err


# ── Token / user-ID extraction ──────────────────────────────────────────────

def _find_token(value, _depth=0):
    if not isinstance(value, dict) or _depth > 5:
        return None
    for key in ('access_token', 'accessToken', 'token', 'id_token'):
        if isinstance(value.get(key), str) and value[key]:
            return value[key]
    for child in value.values():
        found = _find_token(child, _depth + 1)
        if found:
            return found
    return None


def _find_user_id(value, _depth=0):
    if not isinstance(value, dict) or _depth > 5:
        return ''
    for key in ('id', 'user_id', 'userId'):
        v = value.get(key)
        if v is not None and str(v):
            return str(v)
    for child in value.values():
        found = _find_user_id(child, _depth + 1)
        if found:
            return found
    return ''


def _value_at_path(source, path: str):
    if not path:
        return None
    val = source
    for key in path.split('.'):
        if not isinstance(val, dict):
            return None
        val = val.get(key)
    return val


# ── Upstream authentication ─────────────────────────────────────────────────

_AUTH_ERROR_PATTERNS = (
    'invalid token', 'unauthorized', 'jwt', 'token expired',
    '未登录', '登录已过期', '无权进行此操作',
)


def _is_auth_error(exc) -> bool:
    status = getattr(exc, 'status', 0)
    if status in (401, 403):
        return True
    msg = str(exc).lower()
    return any(p in msg for p in _AUTH_ERROR_PATTERNS)


def _is_session_limit_error(exc) -> bool:
    msg = str(exc).lower()
    patterns = ('活跃登录会话', '登录会话数量', '会话数', 'active login sessions', 'session')
    return any(p in msg for p in patterns) and ('上限' in msg or 'limit' in msg)


def _login_upstream(site: dict, base_url: str, login_path: str) -> dict:
    if not site.get('email') or not site.get('password'):
        raise ValueError('请输入账号和密码，或直接填写访问令牌')
    meta = fetch_json(url_for(base_url, login_path), method='POST',
                      headers={'Content-Type': 'application/json'},
                      json_body={
                          'email': site['email'], 'username': site['email'],
                          'account': site['email'], 'password': site['password'],
                      },
                      return_meta=True)
    login = meta['data']
    inner = login.get('data') if isinstance(login, dict) else login
    raw_token = _find_token(inner) or _find_token(login) or meta.get('authorization', '')
    token = str(raw_token or '').replace('Bearer ', '').replace('bearer ', '').strip()
    cookie = '; '.join(c.split(';')[0] for c in meta.get('set_cookies', []) if c)
    new_api_user_id = (str(site.get('newApiUserId') or '').strip()
                       or (_find_user_id(inner) or _find_user_id(login)
                           if site.get('siteType') == 'newapi' else ''))
    if not token and not cookie:
        raise ValueError('登录响应中没有找到 Token 或会话 Cookie，请检查登录接口格式')
    return {'token': token, 'sessionCookie': cookie, 'newApiUserId': new_api_user_id}


def _login_with_guard(site: dict, base_url: str, login_path: str, cache_key: str) -> dict:
    blocked_until = state.upstream_login_blocked.get(cache_key, 0)
    if blocked_until > time.time():
        mins = int((blocked_until - time.time()) / 60) + 1
        raise ValueError(f'目标网站登录会话已达上限，请先退出其他登录（约 {mins} 分钟后重试）')
    try:
        creds = _login_upstream(site, base_url, login_path)
        state.upstream_login_blocked.pop(cache_key, None)
        return creds
    except Exception as exc:
        if _is_session_limit_error(exc):
            state.upstream_login_blocked[cache_key] = time.time() + 10 * 60
        raise


# ── Core balance check ──────────────────────────────────────────────────────

def get_balance(site_input: dict, *, usage_day: str | None = None) -> dict:
    cfg = site_config(site_input)
    base_url = cfg.get('baseUrl', 'https://ezaiclub.com')
    login_path = cfg.get('loginPath', '/api/v1/auth/login')
    me_path = cfg.get('mePath', '/api/v1/auth/me')
    timezone = cfg.get('timezone', 'Asia/Shanghai')
    auth_mode = cfg.get('authMode', 'password')
    cookie_auth = auth_mode == 'cookie'
    token_auth = auth_mode == 'token'
    password_auth = not token_auth and not cookie_auth

    raw_token = str(cfg.get('token') or '').strip().lstrip('Bearer ').lstrip('bearer ').strip()
    session_cookie = sanitize_cookie(str(cfg.get('sessionCookie') or ''))

    if not password_auth and not raw_token and not session_cookie:
        raise ValueError('认证方式选择了 Cookie，但没有填写登录 Cookie'
                         if cookie_auth else '认证方式选择了 Token，但没有填写 Bearer Token')

    cache_key = str(cfg.get('id') or f"{base_url}|{cfg.get('email', '')}")
    fingerprint = upstream_fingerprint(cfg)

    if password_auth:
        cached = state.upstream_sessions.get(cache_key)
        if cached and cached.get('fingerprint') == fingerprint:
            credentials = cached
        else:
            creds = _login_with_guard(cfg, base_url, login_path, cache_key)
            credentials = {**creds, 'fingerprint': fingerprint,
                           'updatedAt': datetime.now(dt_timezone.utc).isoformat()}
            state.upstream_sessions[cache_key] = credentials
            save_upstream_sessions(state.upstream_sessions)
    else:
        credentials = {
            'token': raw_token,
            'sessionCookie': session_cookie,
            'newApiUserId': str(cfg.get('newApiUserId') or '').strip(),
        }

    def auth_headers(auth: dict, use_bearer: bool = True) -> dict:
        hdrs: dict = {}
        if auth.get('token'):
            hdrs['Authorization'] = f'Bearer {auth["token"]}' if use_bearer else auth['token']
        elif auth.get('sessionCookie'):
            hdrs['Cookie'] = auth['sessionCookie']
        if cfg.get('siteType') == 'newapi' and auth.get('newApiUserId') and auth_mode != 'cookie':
            hdrs['New-Api-User'] = auth['newApiUserId']
        return hdrs

    me_url = url_for(base_url, me_path)
    if '?' not in me_url:
        me_url += f'?timezone={timezone}'
    else:
        me_url += f'&timezone={timezone}'

    def fetch_profile(auth: dict, use_bearer: bool = True):
        return fetch_json(me_url, headers=auth_headers(auth, use_bearer))

    def reauthenticate() -> dict:
        state.upstream_sessions.pop(cache_key, None)
        save_upstream_sessions(state.upstream_sessions)
        creds = _login_with_guard(cfg, base_url, login_path, cache_key)
        stored = {**creds, 'fingerprint': fingerprint,
                  'updatedAt': datetime.now(dt_timezone.utc).isoformat()}
        state.upstream_sessions[cache_key] = stored
        save_upstream_sessions(state.upstream_sessions)
        return stored

    # New API access tokens go bare; password-login JWTs need Bearer.
    use_bearer = not (cfg.get('siteType') == 'newapi' and not password_auth)

    me_data = None
    try:
        me_data = fetch_profile(credentials, use_bearer)
    except Exception as exc:
        status = getattr(exc, 'status', 0)
        new_api = cfg.get('siteType') == 'newapi'
        nuid = credentials.get('newApiUserId')
        if new_api and nuid and status == 409:
            try:
                me_data = fetch_profile({**credentials, 'newApiUserId': ''})
            except Exception:
                raise ValueError('New API 返回 409 Conflict：New-Api-User ID 可能不正确，请填写该账号对应的数字 ID')
        elif new_api and status == 409:
            raise ValueError('New API 返回 409 Conflict：请填写该账号对应的数字 New-Api-User ID')
        elif password_auth and _is_auth_error(exc):
            credentials = reauthenticate()
            me_data = fetch_profile(credentials)
        elif cookie_auth and _is_auth_error(exc):
            raise ValueError('登录 Cookie 无效或已过期，请在浏览器重新登录后复制新的 Cookie')
        elif not password_auth and _is_auth_error(exc) and not use_bearer:
            try:
                me_data = fetch_profile(credentials, True)
            except Exception:
                raise ValueError('访问令牌无效或已过期，请填写新的令牌')
        elif not password_auth and _is_auth_error(exc):
            raise ValueError('Token 无效或已过期，请填写新的 Token')
        else:
            raise

    profile = me_data.get('data') if isinstance(me_data, dict) else me_data
    if not isinstance(profile, dict):
        profile = me_data or {}

    configured_field = str(cfg.get('balanceField') or '').strip()
    balance_field = configured_field if (configured_field and configured_field != 'auto') else ''
    if not balance_field:
        if isinstance(profile.get('balance'), (int, float)):
            balance_field = 'balance'
        elif isinstance(profile.get('quota'), (int, float)):
            balance_field = 'quota'

    raw_balance = _value_at_path(profile, balance_field) or _value_at_path(me_data, balance_field)
    raw_balance = float(raw_balance or 0)

    configured_divisor = cfg.get('balanceDivisor')
    try:
        divisor = float(configured_divisor)
        if not (divisor > 0):
            divisor = None
    except (TypeError, ValueError):
        divisor = None
    if divisor is None:
        divisor = 500000.0 if balance_field == 'quota' else 1.0

    configured_multiplier = cfg.get('balanceMultiplier')
    try:
        multiplier = float(configured_multiplier)
        if not (multiplier > 0):
            multiplier = 1.0
    except (TypeError, ValueError):
        multiplier = 1.0

    balance = raw_balance / divisor * multiplier
    currency = str(cfg.get('currency') or '').strip() or 'CNY'

    if not isinstance(balance, float) or balance != balance:  # NaN check
        raise ValueError('余额接口响应中没有有效的 balance 字段')

    # Daily usage via usage endpoint (Sub2-style), only when caller requests it.
    usage_result = None
    if usage_day and cfg.get('usagePath'):
        try:
            usage_url = url_for(base_url, cfg['usagePath'])
            sep = '&' if '?' in usage_url else '?'
            usage_url += (f'{sep}timezone={timezone}&granularity=day'
                          f'&start_date={usage_day}&end_date={usage_day}')
            usage_data = fetch_json(usage_url, headers=auth_headers(credentials, use_bearer))
            inner = usage_data.get('data') if isinstance(usage_data, dict) else usage_data
            if isinstance(inner, dict):
                trend = inner.get('trend', [])
            else:
                trend = []
            if isinstance(trend, list):
                total = sum(float(pt.get('actual_cost') or 0)
                            for pt in trend
                            if str(pt.get('date', '')).startswith(usage_day))
                usage_result = {'date': usage_day, 'cost': total, 'currency': currency}
        except Exception as exc:
            logger.error('Usage request failed for %s: %s',
                         cfg.get('name') or base_url, exc)

    used_quota = float(profile.get('used_quota') or
                       _value_at_path(me_data, 'used_quota') or 0)

    return {
        'balance': balance,
        'rawBalance': raw_balance,
        'balanceField': balance_field,
        'balanceDivisor': divisor,
        'balanceMultiplier': multiplier,
        'currency': currency,
        'usage': usage_result,
        'usedBalance': used_quota / divisor * multiplier,
        'frozenBalance': float(profile.get('frozen_balance') or 0) / divisor * multiplier,
        'email': profile.get('email') or cfg.get('email') or '',
        'displayName': (profile.get('display_name') or profile.get('username') or
                        profile.get('email') or '账户'),
        'status': profile.get('status') or '',
        'updatedAt': datetime.now(dt_timezone.utc).isoformat(),
        'raw': {
            'id': profile.get('id'),
            'concurrency': profile.get('concurrency'),
            'totalRecharged': profile.get('total_recharged'),
            'quota': profile.get('quota'),
            'usedQuota': profile.get('used_quota'),
        },
    }


# ── WeCom notification ──────────────────────────────────────────────────────

def send_wecom(webhook: str, content: str) -> None:
    try:
        resp = requests.post(
            webhook,
            json={'msgtype': 'markdown', 'markdown': {'content': content}},
            timeout=10,
            allow_redirects=False,
        )
    except requests.Timeout:
        raise ValueError('企业微信请求超时，请检查服务器网络后重试') from None
    except requests.RequestException:
        # requests exceptions can contain the URL, including the secret key.
        raise ValueError('无法连接企业微信，请检查服务器网络和 Webhook 地址') from None
    if not resp.ok or 300 <= resp.status_code < 400:
        raise ValueError(f'企业微信返回 HTTP {resp.status_code}')
    try:
        result = resp.json()
    except ValueError:
        raise ValueError('企业微信未返回有效 JSON，请检查 Webhook 地址或服务器代理') from None
    if not isinstance(result, dict) or type(result.get('errcode')) is not int:
        raise ValueError('企业微信响应缺少有效 errcode，无法确认通知是否发送成功')
    if result['errcode'] != 0:
        raise ValueError(f'企业微信错误 {result["errcode"]}：{result.get("errmsg") or "发送失败"}')


# ── Check + notify ──────────────────────────────────────────────────────────

def check_and_notify(site_input: dict) -> dict:
    result = get_balance(site_input)
    threshold = site_input.get('threshold')
    webhook = state.global_webhook
    site_name = str(site_input.get('name') or site_input.get('baseUrl') or '未命名站点').strip()
    tz = site_input.get('timezone', 'Asia/Shanghai')

    notified = False
    notification_error = ''
    notification_message = ''

    try:
        threshold_f = float(threshold)
        if not isfinite(threshold_f):
            threshold_f = None
    except (TypeError, ValueError):
        threshold_f = None

    if site_input.get('notifyEnabled', True) is False:
        notification_message = '未发送：该站点未启用余额预警'
    elif not webhook:
        notification_message = '未发送：请先配置全局企业微信 Webhook'
    elif threshold_f is None:
        notification_message = '未发送：请先设置有效的预警阈值'
    elif result['balance'] > threshold_f:
        notification_message = '未发送：余额高于预警阈值'
    else:
        site_key = site_input.get('id') or site_input.get('baseUrl', '')
        account_key = result.get('email') or site_input.get('email', '')
        key = f'{webhook}|{site_key}|{account_key}'
        previous = state.alerts.get(key, 0)
        six_hours = 6 * 3600
        if time.time() - previous > six_hours:
            try:
                ts = datetime.now(dt_timezone.utc).astimezone(
                    __import__('zoneinfo').ZoneInfo(tz)
                ).strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                ts = datetime.now(dt_timezone.utc).isoformat()
            msg = (f'**余额预警 · {site_name}**\n'
                   f'> 网站：{site_name}\n'
                   f'> 账户：{result["displayName"]}\n'
                   f'> 当前余额：**{result["balance"]:.2f} {result["currency"]}**\n'
                   f'> 预警阈值：{threshold_f:.2f} {result["currency"]}\n'
                   f'> 检查时间：{ts}')
            try:
                send_wecom(webhook, msg)
                state.alerts[key] = time.time()
                notified = True
                notification_message = '企业微信已确认接收预警'
            except Exception as exc:
                notification_error = str(exc)
                logger.error('WeCom balance alert failed for %s: %s', site_name, exc)
        else:
            notification_message = '未发送：该站点账户已在 6 小时内发送过预警'

    return {**result, 'notified': notified, 'notificationError': notification_error,
            'notificationMessage': notification_message}


# ── Daily usage snapshots ───────────────────────────────────────────────────

def record_daily_usage(site: dict, result: dict) -> None:
    site_id = site.get('id')
    used = result.get('usedBalance')
    if not site_id or not isinstance(used, (int, float)):
        return
    sid = str(site_id)
    day = day_key_of(site.get('timezone', 'Asia/Shanghai'))
    cutoff = shift_day_key(day, -40)

    # Update in-memory cache.
    days = state.daily_usage.setdefault(sid, {})
    days[day] = float(used)
    for k in list(days):
        if k < cutoff:
            del days[k]

    # Persist to DB: upsert today's value and trim old rows.
    try:
        from monitor.models import DailyUsage
        DailyUsage.objects.update_or_create(
            site_id=sid, day_key=day,
            defaults={'used_balance': float(used)},
        )
        DailyUsage.objects.filter(site_id=sid, day_key__lt=cutoff).delete()
    except Exception as exc:
        logger.error('Failed to persist daily usage for %s: %s', sid, exc)


def usage_for_day(site_id: str, day_key: str) -> float | None:
    prev_key = shift_day_key(day_key, -1)
    # Prefer in-memory cache (populated by record_daily_usage this session).
    days = state.daily_usage.get(str(site_id))
    if days:
        end = days.get(day_key)
        start = days.get(prev_key)
        if end is not None and start is not None:
            delta = end - start
            return delta if delta >= 0 else None

    # Fall back to DB.
    try:
        from monitor.models import DailyUsage
        rows = {
            row.day_key: row.used_balance
            for row in DailyUsage.objects.filter(
                site_id=str(site_id), day_key__in=[day_key, prev_key]
            )
        }
        end = rows.get(day_key)
        start = rows.get(prev_key)
        if end is not None and start is not None:
            delta = end - start
            return delta if delta >= 0 else None
    except Exception:
        pass
    return None


# ── Daily report ────────────────────────────────────────────────────────────

def run_daily_report(*, timezone: str | None = None) -> bool:
    enabled_sites = [s for s in state.saved_sites
                     if s.get('enabled', True) is not False
                     and (timezone is None or (s.get('timezone') or 'Asia/Shanghai') == timezone)]
    if not enabled_sites or not state.global_webhook:
        return False

    tz = timezone or enabled_sites[0].get('timezone') or 'Asia/Shanghai'
    today = day_key_of(tz)
    yesterday = shift_day_key(today, -1)

    lines = []
    balance_totals: dict[str, float] = {}
    usage_totals: dict[str, float] = {}
    ok_count = 0
    usage_known = 0

    for site in enabled_sites:
        site_name = str(site.get('name') or site.get('baseUrl') or '未命名站点').strip()
        try:
            result = get_balance(site, usage_day=yesterday)
            record_daily_usage(site, result)
            ok_count += 1
            threshold_f = None
            try:
                threshold_f = float(site.get('threshold'))
            except (TypeError, ValueError):
                pass
            low = threshold_f is not None and result['balance'] <= threshold_f
            currency = result.get('currency', '')
            amount = f'{result["balance"]:.2f} {currency}'.strip()
            cfg = site_config(site)
            yesterday_usage = (result['usage']['cost'] if result.get('usage')
                               else (None if cfg.get('usagePath')
                                     else usage_for_day(str(site.get('id', '')), yesterday)))
            if yesterday_usage is not None:
                usage_known += 1
                usage_totals[currency] = usage_totals.get(currency, 0) + yesterday_usage
            balance_totals[currency] = balance_totals.get(currency, 0) + result['balance']
            threshold_text = (f'（阈值 {threshold_f:.2f}）' if threshold_f is not None else '')
            usage_text = ('昨日用量未知（需连续运行两天）' if yesterday_usage is None
                          else f'昨日用量 {yesterday_usage:.2f} {currency}'.strip())
            prefix = '⚠️ ' if low else ''
            lines.append(f'> {prefix}**{site_name}**：{amount}{threshold_text}，{usage_text}')
        except Exception as exc:
            lines.append(f'> ❌ **{site_name}**：查询失败 - {str(exc) or "未知错误"}')

    def sum_text(totals: dict) -> str:
        if not totals:
            return '--'
        return ' / '.join(f'{v:.2f} {c}'.strip() for c, v in totals.items())

    failed = len(enabled_sites) - ok_count
    try:
        ts = datetime.now(dt_timezone.utc).astimezone(
            __import__('zoneinfo').ZoneInfo(tz)
        ).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        ts = datetime.now(dt_timezone.utc).isoformat()

    content = '\n'.join([
        f'**余额日报 · {ts}**',
        *lines,
        f'> 共 {len(enabled_sites)} 个站点{"，" + str(failed) + " 个查询失败" if failed else ""}',
        f'> **总余额**：{sum_text(balance_totals) or "--"}',
        f'> **{yesterday} 总用量**：{"暂无数据" if not usage_known else sum_text(usage_totals)}',
    ])

    try:
        send_wecom(state.global_webhook, content)
        logger.info('Daily balance report sent for %d site(s)', len(enabled_sites))
        return True
    except Exception as exc:
        logger.error('Daily balance report failed: %s', exc)
        return False
