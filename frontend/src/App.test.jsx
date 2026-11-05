import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
      if (state.rejectNameSave && body.sites.some((site) => site.usageKeys?.some((key) => key.name === 'gemini'))) {
        return response({ ok: false, message: '保存失败' });
      }
      state.sites = body.sites.map(({ password, token, sessionCookie, usageToken, usageKeys, ...site }) => ({
        ...site, hasPassword: Boolean(password || site.hasPassword), hasToken: Boolean(token || site.hasToken),
        hasSessionCookie: Boolean(sessionCookie || site.hasSessionCookie),
        hasUsageToken: usageKeys?.some((key) => key.token || key.hasToken) || Boolean(usageToken),
        usageKeys: usageKeys?.map(({ token, ...key }) => ({ ...key, hasToken: Boolean(token || key.hasToken) })),
      }));
      return response({ ok: true, data: { sites: state.sites, settings: state.settings } });
    }
    if (path === 'sites') return response(state.loadError ? { ok: false, message: '读取失败' } : { ok: true, data: { sites: state.sites, results: state.results, settings: state.settings } });
    if (path === 'check-many') return response({ ok: true, data: body.sites.map((site) => ({ siteId: site.id, ok: true, balance: 0, currency: 'CNY', checkedAt: '2026-10-21T02:00:00Z', ...state.checkResult })) });
    if (path === 'usage/token') return response({ ok: true, todayUsage: state.usageSummary, data: state.usageResult || { name: 'production', usedQuota: 1200, remainingQuota: 3800, totalQuota: 5000, unlimitedQuota: false, expiresAt: -1, modelLimitsEnabled: true, modelLimits: ['gpt-4o'] } });
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
  it('概览显示今日消耗并随余额同步，零消耗有效，历史与失败数据不冒充今日消耗', async () => {
    const date = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
    const { state, fetch } = mockApi({
      sites: [initialSite, { ...initialSite, id: 'beta', name: 'Beta' }, { ...initialSite, id: 'gamma', name: 'Gamma' }, { ...initialSite, id: 'delta', name: 'Delta' }],
      results: {
        alpha: { ok: true, balance: 100, todayUsage: { date, source: 'keys', cost: 0 } },
        beta: { ok: true, balance: 200, todayUsage: { date: '2000-01-01', source: 'keys', cost: 12 } },
        gamma: { ok: false, todayUsage: { date, source: 'keys', cost: 99 } },
        delta: { ok: true, balance: 100, todayUsage: { date, cost: 88 } },
      },
    });
    render(<App />);
    expect(await screen.findByRole('columnheader', { name: '今日实时消耗' })).toBeInTheDocument();
    const row = (name) => screen.getByRole('button', { name, exact: true }).closest('tr');
    expect(within(row('Alpha')).getByText('0.00')).toBeInTheDocument();
    expect(row('Beta').querySelector('.overview-usage strong')).toHaveTextContent('—');
    expect(row('Gamma').querySelector('.overview-usage strong')).toHaveTextContent('—');
    expect(row('Delta').querySelector('.overview-usage strong')).toHaveTextContent('—');
    const totals = screen.getByRole('region', { name: '今日总消耗' });
    expect(totals.querySelector('dd')).toHaveTextContent('0.00');
    expect(totals).toHaveTextContent('已汇总 1 / 4 个启用站点');
    state.results.alpha = { ok: true, balance: 90, todayUsage: { date, source: 'keys', cost: 10.25, successCount: 1, failedCount: 1 } };
    fireEvent(document, new Event('visibilitychange'));
    expect(await within(row('Alpha')).findByText('10.25')).toBeInTheDocument();
    expect(within(row('Alpha')).getByText('小计 · 1 个 Key 未获取')).toBeInTheDocument();
    expect(totals.querySelector('dd')).toHaveTextContent('10.25');
    expect(totals).toHaveTextContent('部分数据未获取，当前为成功项小计');
    expect(totals).toHaveTextContent('1 个 Key 未获取');
    expect(fetch.mock.calls.filter(([url]) => url === '/api/sites')).toHaveLength(2);
    state.results.alpha = { ok: true, balance: 90, todayUsage: null, todayUsageError: '用量接口不可用' };
    fireEvent(document, new Event('visibilitychange'));
    await waitFor(() => expect(row('Alpha').querySelector('.overview-usage strong')).toHaveTextContent('—'));
    expect(row('Alpha').querySelector('.overview-balance strong')).toHaveTextContent('90.00');
    expect(totals).toHaveTextContent('等待站点返回今日消耗数据');
    expect(totals.querySelector('dd')).toBeNull();
  });

  it('今日总消耗按币种相加，忽略停用站点和无效金额，筛选不改变总额', async () => {
    const date = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
    const usage = (cost) => ({ ok: true, balance: 100, todayUsage: { date, source: 'keys', cost } });
    mockApi({
      sites: [initialSite, { ...initialSite, id: 'beta', name: 'Beta' },
        { ...initialSite, id: 'usd', name: 'USD', currency: 'USD' },
        { ...initialSite, id: 'disabled', enabled: false },
        { ...initialSite, id: 'invalid' }],
      results: { alpha: usage(1.25), beta: usage(2.75), usd: usage(3.5), disabled: usage(99), invalid: usage('invalid') },
    });
    const user = userEvent.setup();
    render(<App />);
    const totals = await screen.findByRole('region', { name: '今日总消耗' });
    const currencies = totals.querySelectorAll('.balance-total');
    expect(currencies).toHaveLength(2);
    expect(currencies[0]).toHaveTextContent('CNY4.00');
    expect(currencies[1]).toHaveTextContent('USD3.50');
    expect(totals).toHaveTextContent('已汇总 3 / 4 个启用站点');
    await user.type(screen.getByRole('searchbox', { name: '搜索站点' }), 'Beta');
    expect(totals.querySelector('dd')).toHaveTextContent('4.00');
  });

  it('手动 Key 消耗查询后立即更新概览中的合计', async () => {
    const date = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
    mockApi({ sites: [{ ...initialSite, usageKeys: [{ id: 'one', name: '生产', hasToken: true }] }],
      results: { alpha: { ok: true, balance: 100 } },
      usageResult: { keys: [{ keyId: 'one', keyName: '生产', ok: true, data: { todayActualCost: 2.75 } }], todayActualCost: 2.75, successCount: 1, failedCount: 0 },
      usageSummary: { date, source: 'keys', cost: 2.75, successCount: 1, failedCount: 0 },
    });
    const user = userEvent.setup();
    render(<App />); await openSiteSettings(user);
    await user.click(screen.getByRole('button', { name: '消耗查询' }));
    await screen.findByRole('region', { name: '令牌消耗查询结果' });
    await user.click(screen.getByRole('link', { name: '余额概览' }));
    const row = screen.getByRole('button', { name: 'Alpha', exact: true }).closest('tr');
    expect(row.querySelector('.overview-usage strong')).toHaveTextContent('2.75');
    expect(row.querySelector('.overview-balance strong')).toHaveTextContent('100.00');
    expect(screen.getByRole('region', { name: '今日总消耗' }).querySelector('dd')).toHaveTextContent('2.75');
  });

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

  it('拖动站点后保存新的显示顺序', async () => {
    const sites = [initialSite, { ...initialSite, id: 'beta', name: 'Beta', baseUrl: 'https://beta.example.com' }];
    const { fetch } = mockApi({ sites });
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole('button', { name: /Alpha/ }));
    const handles = await screen.findAllByRole('button', { name: /拖动排序/ });
    const dataTransfer = { effectAllowed: '', dropEffect: '', setData: vi.fn(), getData: vi.fn() };
    fireEvent.dragStart(handles[0], { dataTransfer });
    const betaCard = document.getElementById('site-beta');
    fireEvent.dragOver(betaCard, { dataTransfer });
    fireEvent.drop(betaCard, { dataTransfer });
    expect(await screen.findByText('配置有修改，请保存以应用到后台监控。')).toBeInTheDocument();
    await user.click(screen.getAllByRole('button', { name: '保存配置' })[0]);
    await screen.findByText('配置已保存，后台监控已生效');
    const [, options] = fetch.mock.calls.find(([, request]) => request.method === 'PUT');
    expect(JSON.parse(options.body).sites.map((site) => site.id)).toEqual(['beta', 'alpha']);
  });

  it('可使用上下方向键调整站点顺序', async () => {
    const sites = [initialSite, { ...initialSite, id: 'beta', name: 'Beta', baseUrl: 'https://beta.example.com' }];
    const { fetch } = mockApi({ sites });
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole('button', { name: /Alpha/ }));
    const handles = await screen.findAllByRole('button', { name: /拖动排序/ });
    handles[0].focus();
    await user.keyboard('{ArrowDown}');
    await user.click(screen.getAllByRole('button', { name: '保存配置' })[0]);
    await screen.findByText('配置已保存，后台监控已生效');
    const [, options] = fetch.mock.calls.find(([, request]) => request.method === 'PUT');
    expect(JSON.parse(options.body).sites.map((site) => site.id)).toEqual(['beta', 'alpha']);
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
    expect(screen.queryByLabelText('用量接口')).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText('认证方式'), 'cookie');
    expect(screen.getByLabelText('登录 Cookie')).toBeInTheDocument();
    expect(screen.getByLabelText('New-Api-User ID')).toBeInTheDocument();
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

  it('New API 不再显示容易误填 Key 的用量接口', async () => {
    mockApi({ sites: [{ ...initialSite, siteType: 'newapi', hasUsageToken: true }] });
    const user = userEvent.setup();
    render(<App />); await openSiteSettings(user);
    await user.click(screen.getByRole('button', { name: /高级设置/ }));
    expect(screen.getByLabelText('消耗查询 API Key')).toBeInTheDocument();
    expect(screen.queryByLabelText('用量接口')).not.toBeInTheDocument();
  });

  it('New API 展示接口名称与换算后的消耗，不限额仍显示消耗金额', async () => {
    const { state } = mockApi({ sites: [{ ...initialSite, siteType: 'newapi', authMode: 'cookie', hasSessionCookie: true,
      newApiUserId: '5', usageKeys: [{ id: 'gemini', name: 'Key 1', hasToken: true }] }],
    usageResult: { keys: [{ keyId: 'gemini', keyName: 'Key 1', ok: true, data: {
      name: 'gemini 文本', usedQuota: 136.460558, remainingQuota: -9.73614,
      totalQuota: 126.724418, unlimitedQuota: true, expiresAt: 0,
      modelLimitsEnabled: false, modelLimits: [],
      todayActualCost: 3, todayQuota: 1500000,
      todayRange: { startTimestamp: Date.parse('2026-11-04T16:00:00Z') / 1000,
        endTimestamp: Date.parse('2026-11-04T16:05:00Z') / 1000, timezone: 'Asia/Shanghai' },
    } }], successCount: 1, failedCount: 0, todayActualCost: 3,
      todaySuccessCount: 1, todayFailedCount: 0 } });
    const user = userEvent.setup();
    render(<App />); await openSiteSettings(user);
    expect(screen.getByLabelText('New-Api-User ID')).toHaveValue('5');
    await user.click(screen.getByRole('button', { name: '消耗查询' }));
    expect(await screen.findByText('gemini 文本')).toBeInTheDocument();
    expect(screen.getByLabelText('Key 名称 1')).toHaveValue('gemini 文本');
    expect(state.sites[0].usageKeys[0]).toMatchObject({ id: 'gemini', name: 'gemini 文本', hasToken: true });
    expect(screen.getByText('累计消耗金额')).toBeInTheDocument();
    expect(screen.getByText('今日消耗金额')).toBeInTheDocument();
    expect(screen.getByText('今日用量：1 个成功，0 个未获取')).toBeInTheDocument();
    expect(screen.getByText(/统计范围：.*00:00:00.*00:05:00/)).toBeInTheDocument();
    expect(screen.getByText('136.460558')).toBeInTheDocument();
    expect(screen.getAllByText('不限额')).toHaveLength(2);
    expect(screen.getByText('永不过期')).toBeInTheDocument();
    expect(screen.getByText('未限制')).toBeInTheDocument();
  });

  it.each(['默认 Key', '', '自定义备注'])('New API 查询后填入默认名称并保留自定义名称：%s', async (name) => {
    const { state } = mockApi({ sites: [{ ...initialSite, siteType: 'newapi',
      usageKeys: [{ id: 'saved', name, hasToken: true }, { id: 'failed', name: 'Key 2', hasToken: true }] }],
    usageResult: { keys: [
      { keyId: 'saved', keyName: name, ok: true, data: { name: 'gemini', todayActualCost: 0 } },
      { keyId: 'failed', keyName: 'Key 2', ok: false, message: '查询失败' },
    ], successCount: 1, failedCount: 1, todayActualCost: 0 } });
    const user = userEvent.setup();
    const { unmount } = render(<App />); await openSiteSettings(user);
    await user.click(screen.getByRole('button', { name: '消耗查询' }));
    await screen.findByRole('region', { name: '令牌消耗查询结果' });
    const expectedName = name === '自定义备注' ? name : 'gemini';
    expect(screen.getByLabelText('Key 名称 1')).toHaveValue(expectedName);
    expect(state.sites[0].usageKeys[0]).toMatchObject({ id: 'saved', name: expectedName, hasToken: true });
    expect(state.sites[0].usageKeys[1].name).toBe('Key 2');
    unmount();
    render(<App />);
    await user.click(await screen.findByRole('button', { name: '展开', exact: true }));
    expect(screen.getByLabelText('Key 名称 1')).toHaveValue(expectedName);
  });

  it('自动名称保存失败时保留今日消耗，并允许手动重新保存', async () => {
    const { state } = mockApi({ rejectNameSave: true,
      sites: [{ ...initialSite, siteType: 'newapi', usageKeys: [{ id: 'saved', name: '默认 Key', hasToken: true }] }],
      usageResult: { keys: [{ keyId: 'saved', keyName: '默认 Key', ok: true,
        data: { name: 'gemini', usedQuota: 100, todayActualCost: 3 } }],
      successCount: 1, failedCount: 0, todayActualCost: 3 } });
    const user = userEvent.setup();
    render(<App />); await openSiteSettings(user);
    await user.click(screen.getByRole('button', { name: '消耗查询' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Key 名称未保存');
    expect(screen.getByText('今日消耗金额')).toBeInTheDocument();
    expect(screen.getByText('100')).toBeInTheDocument();
    expect(screen.getByLabelText('Key 名称 1')).toHaveValue('gemini');
    expect(state.sites[0].usageKeys[0].name).toBe('默认 Key');
    state.rejectNameSave = false;
    await user.click(screen.getAllByRole('button', { name: '保存配置' })[0]);
    await waitFor(() => expect(state.sites[0].usageKeys[0].name).toBe('gemini'));
  });

  it('New API 今日查询失败仍显示累计消耗，并提示今日结果不完整', async () => {
    mockApi({ sites: [{ ...initialSite, siteType: 'newapi', newApiUserId: '5',
      usageKeys: [{ id: 'saved', name: 'Key 1', hasToken: true }] }], usageResult: {
      keys: [{ keyId: 'saved', keyName: 'Key 1', ok: true,
        data: { name: 'gemini', usedQuota: 136.460558, todayActualCost: null, todayError: '用量接口请求超时' } }],
      successCount: 1, failedCount: 0, todayActualCost: null, todaySuccessCount: 0, todayFailedCount: 1,
    } });
    const user = userEvent.setup();
    render(<App />); await openSiteSettings(user);
    await user.click(screen.getByRole('button', { name: '消耗查询' }));
    expect(await screen.findByText('今日用量未获取：用量接口请求超时')).toBeInTheDocument();
    expect(screen.getByText('136.460558')).toBeInTheDocument();
    expect(screen.getByText(/今日使用金额小计（仅成功项）/)).toBeInTheDocument();
    expect(screen.getByText('今日用量：0 个成功，1 个未获取')).toBeInTheDocument();
    expect(await screen.findByRole('alert')).toHaveTextContent('1 个今日用量未获取');
  });

  it('Sub2 添加多个 Key 后先保存再查询，显示今日金额与失败项小计', async () => {
    const { fetch } = mockApi({ usageResult: {
      keys: [
        { keyId: 'one', keyName: '生产', ok: true, data: { todayActualCost: 0 } },
        { keyId: 'two', keyName: '测试', ok: true, data: { todayActualCost: 1.234567 } },
        { keyId: 'three', keyName: '开发', ok: false, message: '用量接口请求超时' },
      ], successCount: 2, failedCount: 1, todayActualCost: 1.234567,
    } });
    const user = userEvent.setup();
    render(<App />); await openSiteSettings(user);
    expect(screen.getByRole('button', { name: '消耗查询' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: '添加 API Key' }));
    await user.clear(screen.getByLabelText('Key 名称 1'));
    await user.type(screen.getByLabelText('Key 名称 1'), '生产');
    await user.type(screen.getByLabelText('消耗查询 API Key'), 'sk-first');
    await user.click(screen.getByRole('button', { name: '添加 API Key' }));
    await user.type(screen.getByLabelText('消耗查询 API Key 2'), 'sk-second');
    await user.click(screen.getByRole('button', { name: '添加 API Key' }));
    await user.type(screen.getByLabelText('消耗查询 API Key 3'), 'sk-third');
    await user.click(screen.getByRole('button', { name: '消耗查询' }));
    expect(await screen.findByText('2 个成功，1 个失败')).toBeInTheDocument();
    expect(screen.getByText('用量接口请求超时')).toBeInTheDocument();
    expect(screen.getByText(/今日使用金额小计（仅成功项）/)).toBeInTheDocument();
    expect(screen.getByText('0')).toBeInTheDocument();
    expect(screen.getAllByText('1.234567')).toHaveLength(2);
    expect(screen.getByLabelText('消耗查询 API Key')).toHaveValue('');
    expect(screen.getByLabelText('消耗查询 API Key')).toHaveAttribute('placeholder', '已安全保存，留空保持不变');
    const saveIndex = fetch.mock.calls.findIndex(([, options]) => options.method === 'PUT');
    expect(saveIndex).toBeLessThan(fetch.mock.calls.findIndex(([url]) => url === '/api/usage/token'));
    expect(JSON.parse(fetch.mock.calls[saveIndex][1].body).sites[0].usageKeys.map((key) => key.token)).toEqual(['sk-first', 'sk-second', 'sk-third']);
  });

  it('可删除全部已保存的 Key，不会重新恢复旧值', async () => {
    const { state } = mockApi({ sites: [{ ...initialSite, usageKeys: [
      { id: 'saved', name: '旧 Key', hasToken: true },
    ] }] });
    const user = userEvent.setup();
    render(<App />); await openSiteSettings(user);
    await user.click(screen.getByRole('button', { name: '删除 API Key 1' }));
    await user.click(screen.getAllByRole('button', { name: '保存配置' })[0]);
    await screen.findByText('配置已保存，后台监控已生效');
    expect(state.sites[0].usageKeys).toEqual([]);
    expect(screen.queryByLabelText('消耗查询 API Key')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '消耗查询' })).toBeDisabled();
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
