const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const list = $('#sitesList');
const toast = $('#toast');
let nextNumber = 1;
let loaded = false;
let refreshInFlight = false;
let hasGlobalWebhook = false;

const sitePresets = {
  sub2: { loginPath: '/api/v1/auth/login', mePath: '/api/v1/auth/me', usagePath: '/api/v1/usage/dashboard/trend', balanceField: 'balance', balanceDivisor: 1, currency: 'CNY' },
  newapi: { loginPath: '/api/user/login', mePath: '/api/user/self', balanceField: 'quota', balanceDivisor: 500000, currency: 'USD' }
};

function setView(view, updateHash = true) {
  const selected = view === 'sites' ? 'sites' : 'dashboard';
  $('#dashboardView').classList.toggle('active', selected === 'dashboard');
  $('#sitesView').classList.toggle('active', selected === 'sites');
  $$('.nav-item[data-view]').forEach(item => item.classList.toggle('active', item.dataset.view === selected));
  $('#pageTitle').textContent = selected === 'dashboard' ? '余额概览' : '站点设置';
  $('#refreshBtn').title = selected === 'dashboard' ? '刷新余额数据' : '检查全部站点';
  if (updateHash) history.replaceState(null, '', selected === 'dashboard' ? '#dashboard' : '#sites');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

$$('.nav-item[data-view]').forEach(item => item.addEventListener('click', event => {
  event.preventDefault();
  setView(item.dataset.view);
}));

function notify(message, error = false) {
  toast.textContent = message;
  toast.style.background = error ? '#a9462d' : '#183d35';
  toast.classList.add('show');
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => toast.classList.remove('show'), 3500);
}

function handleUnauthorized(response) {
  if (response.status !== 401) return false;
  loaded = false;
  location.replace('/login.html?reason=expired');
  return true;
}

async function checkSession() {
  const response = await fetch('/api/session');
  const result = await response.json();
  if (!result.ok) return location.replace('/login.html');
  await loadSites();
}

$('#logoutBtn').addEventListener('click', async () => {
  await fetch('/api/logout', { method: 'POST' });
  location.replace('/login.html');
});

function openSite(url) {
  try {
    const target = new URL(url);
    if (!['http:', 'https:'].includes(target.protocol)) throw new Error('网站地址无效');
    window.open(target.href, '_blank', 'noopener,noreferrer');
  } catch (error) { notify(error.message || '网站地址无效', true); }
}

const saveButton = document.createElement('button');
saveButton.className = 'secondary-btn';
saveButton.id = 'saveSitesBtn';
saveButton.type = 'button';
saveButton.textContent = '保存配置';
$('.toolbar-actions').insertBefore(saveButton, $('#checkAllBtn'));

const schedulerStatus = document.createElement('span');
schedulerStatus.className = 'scheduler-status';
schedulerStatus.innerHTML = '<i></i><span>后端任务待配置</span>';
$('.section-toolbar > div:first-child').append(schedulerStatus);

function newId() {
  return globalThis.crypto?.randomUUID?.() || `site-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function addSite(values = {}, result = null) {
  const card = document.importNode($('#siteTemplate').content, true).firstElementChild;
  card.dataset.id = String(values.id || newId());
  card.dataset.enabled = String(values.enabled !== false);
  $('.site-frozen', card).remove();
  $('.password', card).autocomplete = 'new-password';
  card.dataset.hasPassword = String(Boolean(values.hasPassword));
  card.dataset.hasToken = String(Boolean(values.hasToken));
  card.dataset.hasSessionCookie = String(Boolean(values.hasSessionCookie));
  card.classList.toggle('show-advanced', !values.id);

  const toggleButton = document.createElement('button');
  toggleButton.className = 'link-btn toggle-site';
  toggleButton.type = 'button';
  const startsCollapsed = Boolean(values.id);
  card.classList.toggle('collapsed', startsCollapsed);
  toggleButton.innerHTML = `<span>${startsCollapsed ? '展开' : '收起'}</span><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 15l-6-6-6 6"/></svg>`;
  toggleButton.setAttribute('aria-expanded', String(!startsCollapsed));
  $('.site-actions', card).prepend(toggleButton);

  const enabledButton = document.createElement('button');
  enabledButton.className = 'link-btn toggle-enabled';
  enabledButton.type = 'button';
  $('.site-actions', card).append(enabledButton);

  const enterButton = document.createElement('button');
  enterButton.className = 'link-btn enter-site';
  enterButton.type = 'button';
  enterButton.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 5h5v5M10 14l9-9M19 14v5H5V5h5"/></svg><span>进入</span>';
  enterButton.disabled = !values.id;
  enterButton.title = values.id ? '进入网站' : '请先保存站点';
  enterButton.addEventListener('click', () => openSite($('.base-url', card).value));
  $('.check-one', card).before(enterButton);

  const collapsedBalance = document.createElement('span');
  collapsedBalance.className = 'collapsed-balance';
  collapsedBalance.textContent = '余额 --';
  $('.site-title', card).append(collapsedBalance);

  for (const selector of ['.base-url', '.timezone', '.login-path', '.me-path', '.usage-path']) {
    $(selector, card).closest('label').classList.add('advanced-field');
  }

  const advancedButton = document.createElement('button');
  advancedButton.className = 'link-btn advanced-toggle';
  advancedButton.type = 'button';
  advancedButton.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h10M18 7h2M4 17h2M10 17h10M14 4v6M7 14v6"/></svg><span>${values.id ? '高级设置' : '隐藏高级设置'}</span>`;
  advancedButton.setAttribute('aria-expanded', String(!values.id));
  $('.site-footer', card).append(advancedButton);
  advancedButton.addEventListener('click', () => {
    const visible = card.classList.toggle('show-advanced');
    advancedButton.setAttribute('aria-expanded', String(visible));
    $('span', advancedButton).textContent = visible ? '隐藏高级设置' : '高级设置';
  });

  const authLabel = document.createElement('label');
  authLabel.innerHTML = '认证方式<select class="auth-mode"><option value="password">账号密码登录</option><option value="token">访问令牌 / Bearer Token</option><option value="cookie">登录 Cookie（手动粘贴）</option></select>';
  $('.email', card).closest('label').before(authLabel);

  const typeLabel = document.createElement('label');
  typeLabel.innerHTML = '网站类型<select class="site-type"><option value="auto">自动识别</option><option value="sub2">Sub2</option><option value="newapi">New API</option><option value="custom">自定义</option></select>';
  $('.base-url', card).closest('label').before(typeLabel);

  const newApiUserLabel = document.createElement('label');
  newApiUserLabel.className = 'new-api-user-field';
  newApiUserLabel.innerHTML = 'New-Api-User ID<input class="new-api-user-id" inputmode="numeric" placeholder="账号的数字 ID，在网站用户信息中查看">';
  $('.email', card).closest('label').before(newApiUserLabel);

  const intervalLabel = document.createElement('label');
  intervalLabel.innerHTML = '查询间隔（秒）<input class="interval-seconds" type="number" min="30" max="86400" step="1" value="300">';
  $('.threshold', card).closest('label').before(intervalLabel);

  const balanceFieldLabel = document.createElement('label');
  balanceFieldLabel.innerHTML = '余额字段<input class="balance-field" placeholder="自动识别 balance / quota" value="auto">';
  intervalLabel.before(balanceFieldLabel);
  const divisorLabel = document.createElement('label');
  divisorLabel.innerHTML = '换算除数<input class="balance-divisor" type="number" min="0.000001" step="any" placeholder="quota 默认 500000">';
  intervalLabel.before(divisorLabel);
  const multiplierLabel = document.createElement('label');
  multiplierLabel.innerHTML = '余额倍率<input class="balance-multiplier" type="number" min="0.000001" step="any" value="1">';
  intervalLabel.before(multiplierLabel);
  const currencyLabel = document.createElement('label');
  currencyLabel.innerHTML = '币种<input class="currency" maxlength="8" placeholder="自动识别">';
  intervalLabel.before(currencyLabel);
  const usagePathLabel = document.createElement('label');
  usagePathLabel.className = 'advanced-field';
  usagePathLabel.innerHTML = '用量接口<input class="usage-path" placeholder="留空表示不查询（Sub2 填 trend 接口）">';
  intervalLabel.before(usagePathLabel);

  $('.site-name', card).value = values.name || `站点 ${nextNumber}`;
  for (const key of ['baseUrl', 'timezone', 'loginPath', 'mePath', 'usagePath', 'email', 'password', 'token', 'threshold', 'intervalSeconds', 'balanceField', 'balanceDivisor', 'balanceMultiplier', 'currency', 'newApiUserId']) {
    if (values[key] === undefined) continue;
    $(`.${key.replace(/[A-Z]/g, letter => `-${letter.toLowerCase()}`)}`, card).value = values[key];
  }
  $('.auth-mode', card).value = values.authMode || 'password';
  $('.site-type', card).value = values.siteType || inferSiteType(values);
  $('.notify-enabled', card).checked = values.notifyEnabled !== false;
  updateSecretPlaceholders(card);

  const applyPreset = () => {
    const preset = sitePresets[$('.site-type', card).value];
    if (!preset) return;
    $('.login-path', card).value = preset.loginPath;
    $('.me-path', card).value = preset.mePath;
    $('.usage-path', card).value = preset.usagePath || '';
    $('.balance-field', card).value = preset.balanceField;
    $('.balance-divisor', card).value = preset.balanceDivisor;
    $('.currency', card).value = preset.currency;
  };
  // The numeric ID is needed whenever New API is the target or a bare access token is used.
  // Cookie mode is excluded: the session identifies the user by itself, and some forks
  // reject the request with 409 when the header contradicts the session.
  const syncNewApiUserVisibility = () => {
    const type = $('.site-type', card).value;
    const isNewApi = type === 'newapi'
      || (type === 'auto' && ($('.me-path', card).value.trim() === '/api/user/self' || $('.balance-field', card).value.trim() === 'quota'));
    const authMode = $('.auth-mode', card).value;
    const needsId = (isNewApi || authMode === 'token') && authMode !== 'cookie';
    $('.new-api-user-field', card).classList.toggle('visible', needsId);
  };
  $('.site-type', card).addEventListener('change', () => { applyPreset(); syncNewApiUserVisibility(); markDirty(); });
  syncNewApiUserVisibility();

  const syncAuthMode = () => {
    const authMode = $('.auth-mode', card).value;
    const useToken = authMode === 'token';
    const useCookie = authMode === 'cookie';
    $('.email', card).disabled = useCookie || useToken;
    $('.password', card).disabled = useCookie || useToken;
    $('.token', card).disabled = !useToken;
    $('.session-cookie', card).disabled = !useCookie;
    $('.token-field', card).classList.toggle('hidden-field', useCookie);
    $('.cookie-field', card).classList.toggle('hidden-field', !useCookie);
    $('.token', card).placeholder = useToken && $('.new-api-user-field', card).classList.contains('visible')
      ? '网站生成的访问令牌，可带或省略 Bearer 前缀'
      : (card.dataset.hasToken === 'true' ? 'Token 已安全保存，留空则保持不变' : '填写后可跳过账号密码');
    $('.session-cookie', card).placeholder = card.dataset.hasSessionCookie === 'true'
      ? 'Cookie 已安全保存，留空则保持不变'
      : '从浏览器 F12 → 网络 → 请求头 → Cookie 整段复制粘贴到这里';
    syncNewApiUserVisibility();
  };
  $('.auth-mode', card).addEventListener('change', syncAuthMode);
  syncAuthMode();
  $('.me-path', card).addEventListener('input', syncNewApiUserVisibility);

  toggleButton.addEventListener('click', () => {
    const collapsed = card.classList.toggle('collapsed');
    $('span', toggleButton).textContent = collapsed ? '展开' : '收起';
    toggleButton.setAttribute('aria-expanded', String(!collapsed));
  });
  $('.check-one', card).addEventListener('click', () => checkCards([card]));
  enabledButton.addEventListener('click', async () => {
    const wasEnabled = card.dataset.enabled !== 'false';
    card.dataset.enabled = String(!wasEnabled);
    syncEnabledState(card);
    markDirty();
    try {
      await saveSites(true);
      notify(wasEnabled ? '站点已停用，不再执行余额检查和预警' : '站点已启用，后端定时任务已恢复');
    } catch (error) {
      card.dataset.enabled = String(wasEnabled);
      syncEnabledState(card);
      notify(error.message, true);
    }
  });
  $('.remove-site', card).addEventListener('click', () => {
    card.remove();
    if (!$$('.site-card').length) addSite();
    updateNumbers();
    markDirty();
  });
  card.addEventListener('input', markDirty);
  card.addEventListener('change', markDirty);

  list.append(card);
  nextNumber += 1;
  if (result) renderResult(card, result);
  syncEnabledState(card);
  updateNumbers();
  renderDashboard();
}

function markDirty() {
  schedulerStatus.classList.add('dirty');
  $('span', schedulerStatus).textContent = '配置有修改，请保存';
  renderDashboard();
}

function markSaved() {
  schedulerStatus.classList.remove('dirty');
  schedulerStatus.classList.add('active');
  $('span', schedulerStatus).textContent = '后端定时任务运行中';
}

function updateNumbers() {
  $$('.site-card').forEach((card, index) => $('.site-number', card).textContent = String(index + 1).padStart(2, '0'));
  $('#siteCount').textContent = $$('.site-card').length;
}

function inferSiteType(values) {
  if (values.siteType) return values.siteType;
  if (values.mePath === '/api/user/self' || values.balanceField === 'quota') return 'newapi';
  if (values.mePath === '/api/v1/auth/me' || values.balanceField === 'balance') return 'sub2';
  return 'auto';
}

function cardPayload(card) {
  const value = (className) => $(`.${className}`, card).value.trim();
  return {
    id: card.dataset.id,
    enabled: card.dataset.enabled !== 'false',
    name: value('site-name'),
    baseUrl: value('base-url'),
    timezone: value('timezone'),
    loginPath: value('login-path'),
    mePath: value('me-path'),
    usagePath: value('usage-path'),
    siteType: value('site-type'),
    newApiUserId: value('new-api-user-id'),
    authMode: value('auth-mode'),
    email: value('email'),
    password: value('password'),
    token: value('token'),
    sessionCookie: value('session-cookie'),
    hasPassword: card.dataset.hasPassword === 'true',
    hasToken: card.dataset.hasToken === 'true',
    hasSessionCookie: card.dataset.hasSessionCookie === 'true',
    threshold: Number(value('threshold')),
    balanceField: value('balance-field') || 'auto',
    balanceDivisor: value('balance-divisor') ? Number(value('balance-divisor')) : null,
    balanceMultiplier: value('balance-multiplier') ? Number(value('balance-multiplier')) : 1,
    currency: value('currency'),
    intervalSeconds: Math.min(86400, Math.max(30, Number(value('interval-seconds')) || 300)),
    notifyEnabled: $('.notify-enabled', card).checked
  };
}

function validateSites(sites) {
  const invalid = sites.find(site => {
    if (!site.baseUrl) return true;
    if (site.authMode === 'token') return !site.token && !site.hasToken;
    if (site.authMode === 'cookie') return !site.sessionCookie && !site.hasSessionCookie;
    return !site.email || (!site.password && !site.hasPassword);
  });
  if (invalid) throw new Error(`请完整填写“${invalid.name || '未命名站点'}”的地址和认证信息`);
}

function updateSecretPlaceholders(card) {
  $('.password', card).placeholder = card.dataset.hasPassword === 'true' ? '已安全保存，留空则保持不变' : '输入密码';
  $('.token', card).placeholder = card.dataset.hasToken === 'true' ? 'Token 已安全保存，留空则保持不变' : '填写后可跳过账号密码';
  $('.session-cookie', card).placeholder = card.dataset.hasSessionCookie === 'true'
    ? 'Cookie 已安全保存，留空则保持不变'
    : '从浏览器 F12 → 网络 → 请求头 → Cookie 整段复制粘贴到这里';
}

function applySavedSecretState(sites, settings = {}) {
  hasGlobalWebhook = Boolean(settings.hasWebhook);
  $('#globalWebhook').value = '';
  $('#globalWebhook').placeholder = hasGlobalWebhook ? 'Webhook 已安全保存，留空则保持不变' : 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?...';
  for (const site of sites) {
    const card = $$('.site-card').find(candidate => candidate.dataset.id === String(site.id));
    if (!card) continue;
    card.dataset.hasPassword = String(Boolean(site.hasPassword));
    card.dataset.hasToken = String(Boolean(site.hasToken));
    card.dataset.hasSessionCookie = String(Boolean(site.hasSessionCookie));
    $('.enter-site', card).disabled = false;
    $('.enter-site', card).title = '进入网站';
    $('.password', card).value = '';
    $('.token', card).value = '';
    $('.session-cookie', card).value = '';
    updateSecretPlaceholders(card);
  }
}

function renderResult(card, result) {
  if (!result) return;
  const threshold = Number($('.threshold', card).value);
  const warning = result.ok && Number(result.balance) <= threshold;
  $('.site-state', card).textContent = result.ok ? (warning ? '需要关注' : '正常') : '检查失败';
  $('.site-state', card).className = `site-state ${warning ? 'alert' : result.ok ? 'good' : 'error'}`;
  if (result.ok) {
    const balance = Number(result.balance);
    const currency = result.currency || 'CNY';
    $('.site-balance-value', card).textContent = balance.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    $('.collapsed-balance', card).textContent = `余额 ${balance.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    $('.site-balance small', card).childNodes[0].nodeValue = `${currency} · `;
    $('.site-account', card).textContent = result.displayName || result.email || '账户';
    $('.site-status', card).textContent = result.notified ? '已发送预警' : warning ? '低于预警线' : '余额充足';
    $('.site-error', card).textContent = result.notificationError ? `通知失败：${result.notificationError}` : formatCheckedAt(result.checkedAt);
  } else {
    $('.site-status', card).textContent = '请求失败';
    $('.site-error', card).textContent = result.message || '未知错误';
  }
  card._latestResult = result;
  if (card.dataset.enabled === 'false') syncEnabledState(card);
  renderDashboard();
}

function syncEnabledState(card) {
  const enabled = card.dataset.enabled !== 'false';
  card.classList.toggle('disabled-site', !enabled);
  const button = $('.toggle-enabled', card);
  if (button) {
    button.textContent = enabled ? '停用' : '启用';
    button.classList.toggle('enable-action', !enabled);
    button.title = enabled ? '停止该站点的定时检查和预警' : '恢复该站点的定时检查和预警';
  }
  $('.check-one', card).disabled = !enabled;
  if (!enabled) {
    $('.site-state', card).textContent = '已停用';
    $('.site-state', card).className = 'site-state disabled';
    $('.site-status', card).textContent = '监控已停止';
    $('.site-error', card).textContent = '不会执行定时检查或发送预警';
  } else if (card._latestResult) {
    renderResult(card, card._latestResult);
  } else {
    $('.site-state', card).textContent = '待检查';
    $('.site-state', card).className = 'site-state';
    $('.site-status', card).textContent = '等待检查';
    $('.site-error', card).textContent = '';
  }
}

function formatCheckedAt(value) {
  if (!value) return '';
  return `检查于 ${new Date(value).toLocaleString('zh-CN', { hour12: false })}`;
}

function updateSummary(results = null) {
  const cards = $$('.site-card').filter(card => card.dataset.enabled !== 'false');
  const good = cards.filter(card => $('.site-state', card).classList.contains('good')).length;
  const warning = cards.filter(card => ['alert', 'error'].some(name => $('.site-state', card).classList.contains(name))).length;
  $('#healthyCount').textContent = good || '--';
  $('#warningCount').textContent = warning || '--';
  if (results) $('#checkedCount').textContent = Object.values(results).filter(Boolean).length;
}

function formatInterval(seconds) {
  const value = Number(seconds) || 300;
  if (value % 3600 === 0) return `${value / 3600} 小时`;
  if (value % 60 === 0) return `${value / 60} 分钟`;
  return `${value} 秒`;
}

function formatBalance(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '--';
}

function renderDashboard() {
  const body = $('#dashboardBody');
  const cards = $$('.site-card');
  body.innerHTML = '';
  $('#dashboardEmpty').hidden = cards.length > 0;
  let latest = null;

  for (const card of cards) {
    const site = cardPayload(card);
    const result = card._latestResult;
    const enabled = site.enabled !== false;
    const warning = result?.ok && Number(result.balance) <= site.threshold;
    const currency = result?.currency || site.currency || '--';
    const stateClass = !enabled ? 'disabled' : !result ? 'pending' : result.ok ? (warning ? 'warning' : 'healthy') : 'failed';
    const stateText = !enabled ? '已停用' : !result ? '待检查' : result.ok ? (warning ? '余额预警' : '正常') : '检查失败';
    const row = document.createElement('tr');
    row.dataset.siteId = site.id;
    row.innerHTML = `
      <td data-label="网站"><strong>${escapeHtml(site.name || '未命名站点')}<em>${siteTypeLabel(site.siteType)}</em></strong><small>${escapeHtml(site.baseUrl || '--')}</small></td>
      <td data-label="可用余额" class="dashboard-balance">${formatBalance(result?.balance)} <small>${escapeHtml(currency)}</small></td>
      <td data-label="预警线">${formatBalance(site.threshold)} ${escapeHtml(currency)}</td>
      <td data-label="状态"><span class="dashboard-state ${stateClass}"><i></i>${stateText}</span></td>
      <td data-label="查询间隔">${formatInterval(site.intervalSeconds)}</td>
      <td data-label="最近检查">${result?.checkedAt ? new Date(result.checkedAt).toLocaleString('zh-CN', { hour12: false }) : '--'}</td>
      <td data-label="操作"><button class="dashboard-enter" type="button"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 5h5v5M10 14l9-9M19 14v5H5V5h5"/></svg>进入网站</button></td>`;
    $('.dashboard-enter', row).addEventListener('click', event => {
      event.stopPropagation();
      openSite(site.baseUrl);
    });
    row.addEventListener('click', () => {
      setView('sites');
      card.classList.remove('collapsed');
      const toggle = $('.toggle-site', card);
      $('span', toggle).textContent = '收起';
      toggle.setAttribute('aria-expanded', 'true');
      card.scrollIntoView({ behavior: 'smooth', block: 'center' });
      card.classList.add('dashboard-focus');
      setTimeout(() => card.classList.remove('dashboard-focus'), 1400);
    });
    body.append(row);
    if (result?.checkedAt && (!latest || result.checkedAt > latest)) latest = result.checkedAt;
  }
  $('#dashboardUpdated').textContent = latest ? `更新于 ${new Date(latest).toLocaleString('zh-CN', { hour12: false })}` : '等待后端检查';
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[character]);
}

function siteTypeLabel(type) {
  return ({ sub2: 'SUB2', newapi: 'NEW API', custom: 'CUSTOM', auto: 'AUTO' })[type] || 'AUTO';
}

async function loadSites(force = false) {
  if (loaded && !force) return;
  const response = await fetch('/api/sites');
  if (handleUnauthorized(response)) return;
  const result = await response.json();
  if (!response.ok || !result.ok) throw new Error(result.message || '读取站点配置失败');
  list.innerHTML = '';
  nextNumber = 1;
  const sites = result.data.sites.length ? result.data.sites : [{ name: 'Ezaiclub 主站', intervalSeconds: 300 }];
  sites.forEach(site => addSite(site, result.data.results[site.id]));
  applySavedSecretState(result.data.sites, result.data.settings);
  loaded = true;
  if (result.data.sites.length) markSaved(); else markDirty();
  updateSummary(result.data.results);
  renderDashboard();
}

async function saveSites(silent = false) {
  const sites = $$('.site-card').map(cardPayload);
  validateSites(sites);
  const response = await fetch('/api/sites', { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ sites, settings: { webhook: $('#globalWebhook').value.trim() } }) });
  if (handleUnauthorized(response)) throw new Error('请重新登录');
  const result = await response.json();
  if (!response.ok || !result.ok) throw new Error(result.message || '保存失败');
  applySavedSecretState(result.data.sites, result.data.settings);
  markSaved();
  if (!silent) notify('配置已保存，后端定时任务已生效');
}

async function checkCards(cards) {
  cards = cards.filter(card => card.dataset.enabled !== 'false');
  if (!cards.length) return notify('没有已启用的站点，请先启用后再检查', true);
  const sites = cards.map(cardPayload);
  try { validateSites(sites); await saveSites(true); }
  catch (error) { notify(error.message, true); return; }
  cards.forEach(card => { $('.site-state', card).textContent = '检查中…'; $('.check-one', card).disabled = true; });
  try {
    const response = await fetch('/api/check-many', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ sites }) });
    if (handleUnauthorized(response)) return;
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.message || '检查失败');
    result.data.forEach(item => { const card = cards.find(candidate => candidate.dataset.id === String(item.siteId)); if (card) renderResult(card, item); });
    $('#checkedCount').textContent = result.data.length;
    $('#lastSync').textContent = `刚刚 · ${new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`;
    updateSummary();
    const failed = result.data.filter(item => !item.ok).length;
    notify(failed ? `${result.data.length - failed} 个完成，${failed} 个失败` : `${result.data.length} 个站点检查完成`, failed > 0);
  } catch (error) { notify(error.message, true); }
  finally { cards.forEach(card => $('.check-one', card).disabled = false); }
}

async function refreshScheduledResults() {
  if (!loaded || document.hidden || refreshInFlight) return;
  refreshInFlight = true;
  try {
    const response = await fetch('/api/sites');
    if (handleUnauthorized(response)) return;
    const result = await response.json();
    if (!response.ok || !result.ok) return;
    let latest = null;
    for (const [id, scheduledResult] of Object.entries(result.data.results)) {
      const card = $$('.site-card').find(candidate => candidate.dataset.id === id);
      if (card && scheduledResult) renderResult(card, scheduledResult);
      if (scheduledResult?.checkedAt && (!latest || scheduledResult.checkedAt > latest)) latest = scheduledResult.checkedAt;
    }
    if (latest) $('#lastSync').textContent = new Date(latest).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    updateSummary(result.data.results);
    renderDashboard();
  } catch { /* The next refresh will retry. */ }
  finally { refreshInFlight = false; }
}

async function testGlobalNotify() {
  const webhook = $('#globalWebhook').value.trim();
  if (!webhook && !hasGlobalWebhook) return notify('请先填写全局企业微信 Webhook', true);
  $('#globalNotifyResult').textContent = '发送中…';
  try {
    const response = await fetch('/api/notify-test', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ webhook }) });
    if (handleUnauthorized(response)) return;
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.message || '发送失败');
    $('#globalNotifyResult').textContent = '已发送';
    notify('测试通知已发送');
  } catch (error) { $('#globalNotifyResult').textContent = error.message; notify(error.message, true); }
}

$('#addSiteBtn').addEventListener('click', () => { addSite(); markDirty(); });
$('#checkAllBtn').addEventListener('click', () => checkCards($$('.site-card')));
$('#refreshBtn').addEventListener('click', () => {
  if ($('#sitesView').classList.contains('active')) checkCards($$('.site-card'));
  else refreshScheduledResults();
});
saveButton.addEventListener('click', async () => { try { await saveSites(); } catch (error) { notify(error.message, true); } });
$('#globalWebhook').addEventListener('input', markDirty);
$('#testGlobalNotify').addEventListener('click', testGlobalNotify);

setView(location.hash === '#sites' ? 'sites' : 'dashboard', false);
checkSession().catch(error => notify(error.message, true));
setInterval(refreshScheduledResults, 15000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshScheduledResults(); });
