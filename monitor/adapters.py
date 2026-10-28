"""
Site type adapters and site configuration helpers.
Mirrors siteAdapters, normalizeSite, siteConfig, publicSite, withSavedSecrets from server.js.
"""
import hashlib
import re
import secrets

SITE_ADAPTERS = {
    'sub2': {
        'loginPath': '/api/v1/auth/login',
        'mePath': '/api/v1/auth/me',
        'usagePath': '/api/v1/usage/dashboard/trend',
        'balanceField': 'balance',
        'balanceDivisor': 1,
        'currency': 'CNY',
    },
    'newapi': {
        'loginPath': '/api/user/login',
        'mePath': '/api/user/self',
        'balanceField': 'quota',
        'balanceDivisor': 500000,
        'currency': 'USD',
    },
}

SECRET_FIELDS = ('password', 'token', 'sessionCookie', 'usageToken')


def normalize_site(site: dict) -> dict:
    result = dict(site)
    result['id'] = str(site.get('id') or secrets.token_hex(8))
    result['name'] = str(site.get('name') or site.get('baseUrl') or '未命名站点')[:80]
    result['enabled'] = site.get('enabled', True) is not False
    interval = site.get('intervalSeconds', 300)
    try:
        interval = int(interval)
    except (TypeError, ValueError):
        interval = 300
    result['intervalSeconds'] = min(86400, max(30, interval))
    return result


def infer_adapter(site: dict) -> dict:
    site_type = site.get('siteType', '')
    if site_type in SITE_ADAPTERS:
        return SITE_ADAPTERS[site_type]
    me_path = site.get('mePath', '')
    for adapter in SITE_ADAPTERS.values():
        if me_path and adapter['mePath'] == me_path:
            return adapter
    return {}


def site_config(site: dict) -> dict:
    adapter = infer_adapter(site)
    result = {**adapter, **site}
    result['loginPath'] = site.get('loginPath') or adapter.get('loginPath', '')
    result['mePath'] = site.get('mePath') or adapter.get('mePath', '')
    result['usagePath'] = site.get('usagePath') or adapter.get('usagePath', '')
    bf = site.get('balanceField', '')
    result['balanceField'] = bf if (bf and bf != 'auto') else adapter.get('balanceField', bf)
    result['balanceDivisor'] = site.get('balanceDivisor') or adapter.get('balanceDivisor')
    result['currency'] = site.get('currency') or adapter.get('currency', '')
    return result


def public_site(site: dict) -> dict:
    result = {k: v for k, v in site.items() if k not in SECRET_FIELDS}
    result['hasPassword'] = bool(site.get('password'))
    result['hasToken'] = bool(site.get('token'))
    result['hasSessionCookie'] = bool(site.get('sessionCookie'))
    result['hasUsageToken'] = bool(site.get('usageToken'))
    return result


def with_saved_secrets(site: dict, saved_sites: list[dict]) -> dict:
    existing = next((s for s in saved_sites if s.get('id') == str(site.get('id', ''))), None)
    if not existing:
        return site
    merged = dict(site)
    for field in SECRET_FIELDS:
        if not merged.get(field):
            merged[field] = existing.get(field, '')
    return merged


def upstream_fingerprint(site: dict) -> str:
    parts = [
        site.get('siteType', ''),
        site.get('baseUrl', ''),
        site.get('loginPath', ''),
        site.get('email', ''),
        site.get('password', ''),
        site.get('newApiUserId', ''),
        site.get('authMode', ''),
        site.get('token', ''),
        site.get('sessionCookie', ''),
    ]
    return hashlib.sha256('\n'.join(str(p or '') for p in parts).encode()).hexdigest()


def sanitize_cookie(raw: str) -> str:
    """Reduce a pasted browser Cookie header to a clean name=value; ... string."""
    text = str(raw or '').replace('\r\n', '; ').replace('\n', '; ')
    text = re.sub(r'^\s*cookie\s*:', '', text, flags=re.IGNORECASE)
    pairs = []
    for part in text.split(';'):
        part = part.strip()
        if '=' not in part:
            continue
        name, _, value = part.partition('=')
        name = name.strip()
        value = value.strip()
        if name and value:
            pairs.append(f'{name}={value}')
    return '; '.join(pairs)


def url_for(base: str, path: str) -> str:
    base = str(base or '').rstrip('/')
    if not re.match(r'^https?://', base, re.IGNORECASE):
        raise ValueError('接口地址必须以 http:// 或 https:// 开头')
    return f'{base}/{str(path or "").lstrip("/")}'


def validate_site_list(sites: list, max_sites: int = 100) -> None:
    if not isinstance(sites, list):
        raise ValueError('站点配置格式无效')
    if len(sites) > max_sites:
        raise ValueError(f'站点数量不能超过 {max_sites} 个')
    ids: set = set()
    for site in sites:
        if not isinstance(site, dict):
            raise ValueError('站点配置格式无效')
        sid = str(site.get('id', ''))
        if sid:
            if sid in ids:
                raise ValueError('站点 ID 不能重复')
            ids.add(sid)
        if site.get('baseUrl'):
            url_for(site['baseUrl'], '')
