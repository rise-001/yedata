import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import App from './App.jsx';

const initialSite = { id: 'alpha', name: 'Alpha', baseUrl: 'https://example.com', enabled: true, siteType: 'sub2', authMode: 'token', hasToken: true, intervalSeconds: 300, threshold: 100, currency: 'CNY' };
function mockApi(config = {}) {
  const state = { authenticated: true, sites: [initialSite], results: {}, settings: { hasWebhook: true }, ...config };
  const fetch = vi.fn(async (url, options = {}) => {
    const path = url.replace('/api/', '');
    const body = options.body ? JSON.parse(options.body) : null;
    const response = (data, status = 200) => ({ ok: status === 200, status, json: async () => structuredClone(data) });
    if (path === 'session') return response({ ok: state.authenticated });
    if (path === 'login') return response(body.password === 'correct' ? { ok: true } : { ok: false, message: '管理员账号或密码错误' });
    if (state.expired) return response({ ok: false }, 401);
    if (path === 'sites' && options.method === 'PUT') {
      state.sites = body.sites.map(({ password, token, sessionCookie, usageToken, ...site }) => ({ ...site, hasPassword: Boolean(password || site.hasPassword), hasToken: Boolean(token || site.hasToken), hasSessionCookie: Boolean(sessionCookie || site.hasSessionCookie), hasUsageToken: Boolean(usageToken || site.hasUsageToken) }));
      return response({ ok: true, data: { sites: state.sites, settings: state.settings } });
    }
    if (path === 'sites') return response(state.loadError ? { ok: false, message: '读取失败' } : { ok: true, data: { sites: state.sites, results: state.results, settings: state.settings } });
    if (path === 'check-many') return response({ ok: true, data: body.sites.map((site) => ({ siteId: site.id, ok: true, balance: 0, currency: 'CNY', checkedAt: '2026-10-21T02:00:00Z', ...state.checkResult })) });
    if (path === 'usage/token') return response({ ok: true, data: state.usageResult || { name: 'production', usedQuota: 1200, remainingQuota: 3800, totalQuota: 5000, unlimitedQuota: false, expiresAt: -1, modelLimitsEnabled: true, modelLimits: ['gpt-4o'] } });
    return response({ ok: true });
  });
  vi.stubGlobal('fetch', fetch);
  vi.spyOn(document, 'hidden', 'get').mockReturnValue(false);
  return { state, fetch };
}

async function openSiteSettings(user) {
  await user.click(await screen.findByRole('button', { name: /Alpha/ }));
  return screen.findByLabelText('站点名称');
}

describe('React 页面流程', () => {
  it('显示登录错误，并在成功登录后打开看板', async () => {
    mockApi({ authenticated: false, sites: [] });
    const user = userEvent.setup();
    render(<App />);
    await user.type(await screen.findByLabelText('管理员密码'), 'wrong');
    await user.click(screen.getByRole('button', { name: /登录/ }));
    expect(await screen.findByText('管理员账号或密码错误')).toBeInTheDocument();
    await user.clear(screen.getByLabelText('管理员密码'));
    await user.type(screen.getByLabelText('管理员密码'), 'correct');
    await user.click(screen.getByRole('button', { name: /登录/ }));
    expect(await screen.findByText('开始监控你的第一个站点')).toBeInTheDocument();
    expect(location.pathname).toBe('/');
  });

  it('站点读取失败时可以重试，不会误保存空配置', async () => {
    const { state } = mockApi({ loadError: true });
    const user = userEvent.setup();
    render(<App />);
    expect(await screen.findByText('读取失败')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '保存配置' })).not.toBeInTheDocument();
    state.loadError = false;
    await user.click(screen.getByRole('button', { name: '重新加载' }));
    expect(await screen.findByRole('button', { name: /Alpha/ })).toBeInTheDocument();
  });

  it('编辑并保存配置，保留已保存的 Token', async () => {
    const { fetch } = mockApi();
    const user = userEvent.setup();
    render(<App />);
    const name = await openSiteSettings(user);
    await user.clear(name);
    await user.type(name, '新站点');
    expect(screen.getByLabelText('访问令牌 / Bearer Token')).toHaveAttribute('placeholder', '已安全保存，留空保持不变');
    await user.click(screen.getAllByRole('button', { name: '保存配置' })[0]);
    expect(await screen.findByText('配置已保存，后台监控已生效')).toBeInTheDocument();
    const [, options] = fetch.mock.calls.find(([, options]) => options.method === 'PUT');
    expect(JSON.parse(options.body).sites[0]).toMatchObject({ name: '新站点', token: '', hasToken: true });
    expect(screen.queryByText('配置有修改，请保存以应用到后台监控。')).not.toBeInTheDocument();
  });

  it('同步余额时不会覆盖未保存的表单修改', async () => {
    const { state, fetch } = mockApi();
    const user = userEvent.setup();
    render(<App />);
    const name = await openSiteSettings(user);
    await user.clear(name); await user.type(name, '尚未保存');
    state.sites = [{ ...initialSite, name: '服务端旧名称' }];
    state.results = { alpha: { ok: true, balance: 12.34, currency: 'CNY', checkedAt: '2026-10-21T02:00:00Z' } };
    fireEvent(document, new Event('visibilitychange'));
    await waitFor(() => expect(fetch.mock.calls.filter(([url]) => url === '/api/sites')).toHaveLength(2));
    expect(await screen.findByText('12.34')).toBeInTheDocument();
    expect(screen.getByLabelText('站点名称')).toHaveValue('尚未保存');
    expect(screen.getByText('有未保存的修改')).toBeInTheDocument();
  });

  it('先保存再检查，显示零余额和预警状态', async () => {
    const { fetch } = mockApi();
    const user = userEvent.setup();
    render(<App />); await openSiteSettings(user);
    await user.click(screen.getByRole('button', { name: '检查全部' }));
    expect(await screen.findByText('1 个站点检查完成')).toBeInTheDocument();
    expect(fetch.mock.calls.findIndex(([, options]) => options.method === 'PUT')).toBeLessThan(fetch.mock.calls.findIndex(([url]) => url === '/api/check-many'));
    expect(screen.getAllByText('余额预警').length).toBeGreaterThan(0);
    expect(screen.getByText('0.00')).toBeInTheDocument();
  });

  it('切换认证方式和预设，保留高级配置入口', async () => {
    mockApi(); const user = userEvent.setup();
    render(<App />); await openSiteSettings(user);
    await user.selectOptions(screen.getByLabelText('网站类型'), 'newapi');
    expect(screen.getByLabelText('New-Api-User ID')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /高级设置/ }));
    expect(screen.getByLabelText('余额路径')).toHaveValue('/api/user/self');
    expect(screen.getByLabelText('用量接口')).toHaveValue('');
    await user.selectOptions(screen.getByLabelText('认证方式'), 'cookie');
    expect(screen.getByLabelText('登录 Cookie')).toBeInTheDocument();
    expect(screen.queryByLabelText('New-Api-User ID')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('访问令牌 / Bearer Token')).not.toBeInTheDocument();
  });

  it('New API 站点使用独立 Key 查询消耗，不受登录认证方式限制', async () => {
    const newApiSite = { ...initialSite, siteType: 'newapi', authMode: 'password', email: 'admin@example.com', hasPassword: true, hasToken: false, hasUsageToken: true, newApiUserId: '1' };
    const { fetch } = mockApi({ sites: [newApiSite] });
    const user = userEvent.setup();
    render(<App />); await openSiteSettings(user);
    expect(screen.getByLabelText('消耗查询 API Key')).toHaveAttribute('placeholder', '已安全保存，留空保持不变');
    await user.click(screen.getByRole('button', { name: '消耗查询' }));
    expect(await screen.findByText('production')).toBeInTheDocument();
    expect(screen.getByText('1,200')).toBeInTheDocument();
    expect(screen.getByText('3,800')).toBeInTheDocument();
    expect(screen.getByText('永不过期')).toBeInTheDocument();
    expect(screen.getByText('gpt-4o')).toBeInTheDocument();
    const call = fetch.mock.calls.find(([url]) => url === '/api/usage/token');
    expect(JSON.parse(call[1].body)).toEqual({ siteId: 'alpha' });
  });

  it('停用站点时保存配置并禁止手动检查', async () => {
    const { state } = mockApi(); const user = userEvent.setup();
    render(<App />); await openSiteSettings(user);
    await user.click(screen.getByRole('button', { name: '停用' }));
    expect(await screen.findByText('站点已停用')).toBeInTheDocument();
    expect(state.sites[0].enabled).toBe(false);
    expect(screen.getByRole('button', { name: '检查', exact: true })).toBeDisabled();
  });

  it('复用已保存的 Webhook 发送测试通知', async () => {
    const { fetch } = mockApi(); const user = userEvent.setup();
    render(<App />); await openSiteSettings(user);
    await user.click(screen.getByRole('button', { name: '测试通知' }));
    expect(await screen.findByText('测试通知已发送')).toBeInTheDocument();
    expect(JSON.parse(fetch.mock.calls.find(([url]) => url === '/api/notify-test')[1].body)).toEqual({ webhook: '' });
  });

  it('概览直接显示通知失败，即使余额查询成功', async () => {
    mockApi({ results: { alpha: { ok: true, balance: 10, notificationError: '企业微信错误 93000：invalid webhook url' } } });
    render(<App />);
    expect(await screen.findByText('通知失败：企业微信错误 93000：invalid webhook url')).toBeInTheDocument();
    expect(screen.getAllByText('10.00').length).toBeGreaterThan(0);
  });

  it('手动检查明确提示通知发送失败', async () => {
    mockApi({ checkResult: { notificationError: '企业微信请求超时' } });
    const user = userEvent.setup();
    render(<App />); await openSiteSettings(user);
    await user.click(screen.getByRole('button', { name: '检查全部' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('1 个通知发送失败');
    expect(screen.getByText('通知失败：企业微信请求超时')).toBeInTheDocument();
  });

  it('站点详情显示未触发通知的原因', async () => {
    mockApi({ results: { alpha: { ok: true, balance: 10, notificationMessage: '未发送：请先配置全局企业微信 Webhook' } } });
    const user = userEvent.setup();
    render(<App />); await openSiteSettings(user);
    expect(screen.getByText('未发送：请先配置全局企业微信 Webhook')).toBeInTheDocument();
  });

  it('会话过期后回到登录页并清除工作区', async () => {
    const { state } = mockApi();
    render(<App />);
    await screen.findByRole('button', { name: /Alpha/ });
    state.expired = true;
    fireEvent(document, new Event('visibilitychange'));
    expect(await screen.findByLabelText('管理员密码')).toBeInTheDocument();
    expect(screen.getByText('登录已过期，请重新登录')).toBeInTheDocument();
    expect(screen.queryByRole('navigation')).not.toBeInTheDocument();
  });
});
