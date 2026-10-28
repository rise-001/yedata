export const presets = {
  sub2: { loginPath: '/api/v1/auth/login', mePath: '/api/v1/auth/me', usagePath: '/api/v1/usage/dashboard/trend', balanceField: 'balance', balanceDivisor: 1, currency: 'CNY' },
  newapi: { loginPath: '/api/user/login', mePath: '/api/user/self', usagePath: '', balanceField: 'quota', balanceDivisor: 500000, currency: 'CNY' },
};

export const typeNames = { auto: '自动识别', sub2: 'Sub2', newapi: 'New API', custom: '自定义' };

export function hydrateSite(site) {
  return {
    name: '', baseUrl: '', timezone: 'Asia/Shanghai', loginPath: '', mePath: '', usagePath: '',
    siteType: 'auto', authMode: 'password', email: '', password: '', token: '', sessionCookie: '', usageToken: '',
    newApiUserId: '', threshold: 100, intervalSeconds: 300, balanceField: 'auto',
    balanceDivisor: '', balanceMultiplier: 1, currency: '', enabled: true, notifyEnabled: true,
    ...site,
    balanceDivisor: site.balanceDivisor ?? '',
    balanceMultiplier: site.balanceMultiplier ?? 1,
  };
}

export function newSite(number) {
  return hydrateSite({
    id: globalThis.crypto?.randomUUID?.() || `site-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    name: `站点 ${number}`, siteType: 'sub2', ...presets.sub2, _new: true,
  });
}

export function serializeSite(site) {
  const { _new, ...data } = site;
  for (const key of ['name', 'baseUrl', 'timezone', 'loginPath', 'mePath', 'usagePath', 'email', 'newApiUserId', 'currency', 'balanceField']) {
    data[key] = String(data[key] ?? '').trim();
  }
  data.threshold = Number(data.threshold);
  data.intervalSeconds = Math.min(86400, Math.max(30, Number(data.intervalSeconds) || 300));
  data.balanceDivisor = data.balanceDivisor === '' ? null : Number(data.balanceDivisor);
  data.balanceMultiplier = data.balanceMultiplier === '' ? 1 : Number(data.balanceMultiplier);
  return data;
}

export function validateSites(sites) {
  if (sites.length > 100) throw new Error('站点数量不能超过 100 个');
  for (const site of sites) {
    let url;
    try { url = new URL(site.baseUrl); } catch { /* Report a readable site-specific error below. */ }
    if (!url || !['http:', 'https:'].includes(url.protocol)) {
      throw new Error(`请为“${site.name}”填写有效的 HTTP 或 HTTPS 地址`);
    }
    const validAuth = site.authMode === 'token' ? site.token || site.hasToken
      : site.authMode === 'cookie' ? site.sessionCookie || site.hasSessionCookie
        : site.email && (site.password || site.hasPassword);
    if (!validAuth) throw new Error(`请完整填写“${site.name}”的认证信息`);
    if (!Number.isFinite(site.threshold) || site.threshold < 0) throw new Error(`“${site.name}”的预警阈值必须大于或等于 0`);
    if ((site.balanceDivisor !== null && (!Number.isFinite(site.balanceDivisor) || site.balanceDivisor <= 0))
      || !Number.isFinite(site.balanceMultiplier) || site.balanceMultiplier <= 0) {
      throw new Error(`“${site.name}”的换算除数和余额倍率必须大于 0`);
    }
  }
}

export function siteStatus(site, result) {
  if (!site.enabled) return { kind: 'disabled', text: '已停用' };
  if (!result) return { kind: 'pending', text: '待检查' };
  if (!result.ok) return { kind: 'failed', text: '检查失败' };
  return Number(result.balance) <= Number(site.threshold)
    ? { kind: 'warning', text: '余额预警' } : { kind: 'healthy', text: '正常' };
}

export function mergeResults(current, incoming) {
  const merged = { ...current };
  for (const [id, result] of Object.entries(incoming)) {
    if (result && (!current[id]?.checkedAt || !result.checkedAt
      || Date.parse(result.checkedAt) >= Date.parse(current[id].checkedAt))) merged[id] = result;
  }
  return merged;
}

export const formatBalance = (value) => value == null || !Number.isFinite(Number(value)) ? '—'
  : Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
export const formatTime = (value) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '尚未检查';
export const formatInterval = (value) => value % 3600 === 0 ? `${value / 3600} 小时`
  : value % 60 === 0 ? `${value / 60} 分钟` : `${value} 秒`;
