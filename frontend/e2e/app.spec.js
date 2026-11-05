import { expect, test } from '@playwright/test';

const site = {
  id: 'alpha',
  name: 'Alpha',
  baseUrl: 'https://example.com',
  enabled: true,
  siteType: 'sub2',
  authMode: 'token',
  hasToken: true,
  intervalSeconds: 300,
  threshold: 100,
  currency: 'CNY',
};

async function mockApi(page, { authenticated = true, results = {}, sites = [site], usageResult } = {}) {
  const state = {
    authenticated,
    sites,
    results,
    settings: { hasWebhook: true },
    calls: [],
  };
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace('/api/', '');
    const body = request.postDataJSON?.() ?? null;
    state.calls.push({ path, method: request.method(), body });
    let response;
    if (path === 'session') response = { ok: state.authenticated };
    else if (path === 'login') {
      if (body?.password === 'correct') {
        state.authenticated = true;
        response = { ok: true };
      } else response = { ok: false, message: '管理员账号或密码错误' };
    } else if (path === 'sites' && request.method() === 'PUT') {
      state.sites = body.sites.map(({ password, token, sessionCookie, usageToken, usageKeys, ...item }) => ({
        ...item,
        hasPassword: Boolean(password || item.hasPassword),
        hasToken: Boolean(token || item.hasToken),
        hasSessionCookie: Boolean(sessionCookie || item.hasSessionCookie),
        hasUsageToken: usageKeys?.some((key) => key.token || key.hasToken) || Boolean(usageToken),
        usageKeys: usageKeys?.map(({ token, ...key }) => ({ ...key, hasToken: Boolean(token || key.hasToken) })),
      }));
      response = { ok: true, data: { sites: state.sites, settings: state.settings } };
    } else if (path === 'sites') {
      response = { ok: true, data: { sites: state.sites, results: state.results, settings: state.settings } };
    } else if (path === 'usage/token') {
      response = { ok: true, data: usageResult };
    } else if (path === 'check-many') {
      response = {
        ok: true,
        data: body.sites.map((item) => ({
          siteId: item.id,
          ok: true,
          balance: 42.5,
          currency: 'CNY',
          checkedAt: '2026-10-21T02:00:00Z',
        })),
      };
    } else response = { ok: true };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(response) });
  });
  return state;
}

test('移动端 Sub2 多 Key 查询显示今日使用金额和部分失败', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const state = await mockApi(page, { sites: [{ ...site, usageKeys: [
    { id: 'production', name: '生产', hasToken: true },
    { id: 'testing', name: '测试', hasToken: true },
  ] }], usageResult: {
    keys: [
      { keyId: 'production', keyName: '生产', ok: true, data: { todayActualCost: 1.25 } },
      { keyId: 'testing', keyName: '测试', ok: false, message: '用量接口请求超时' },
    ], successCount: 1, failedCount: 1, todayActualCost: 1.25,
  } });
  await page.goto('/');
  await page.getByRole('button', { name: 'Alpha', exact: true }).click();
  await expect(page.getByLabel('消耗查询 API Key', { exact: true })).toHaveAttribute('placeholder', '已安全保存，留空保持不变');
  await page.getByRole('button', { name: '消耗查询', exact: true }).click();
  const result = page.getByRole('region', { name: '令牌消耗查询结果' });
  await expect(result).toContainText('1 个成功，1 个失败');
  await expect(result).toContainText('今日使用金额小计（仅成功项）：1.25');
  await expect(result).toContainText('用量接口请求超时');
  expect(state.calls.findIndex((call) => call.path === 'sites' && call.method === 'PUT'))
    .toBeLessThan(state.calls.findIndex((call) => call.path === 'usage/token'));
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('移动端 New API 同时展示今日与累计金额及统计时区', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page, { sites: [{ ...site, siteType: 'newapi', newApiUserId: '5',
    usageKeys: [{ id: 'gemini', name: 'Key 1', hasToken: true }],
  }], usageResult: {
    keys: [{ keyId: 'gemini', keyName: 'Key 1', ok: true, data: {
      name: 'gemini 文本', usedQuota: 136.460558, unlimitedQuota: true, expiresAt: 0,
      todayActualCost: 3, todayQuota: 1500000,
      todayRange: { startTimestamp: Date.parse('2026-11-04T16:00:00Z') / 1000,
        endTimestamp: Date.parse('2026-11-04T16:05:00Z') / 1000, timezone: 'Asia/Shanghai' },
    } }], successCount: 1, failedCount: 0, todayActualCost: 3, todaySuccessCount: 1, todayFailedCount: 0,
  } });
  await page.goto('/');
  await page.getByRole('button', { name: 'Alpha', exact: true }).click();
  await page.getByRole('button', { name: '消耗查询', exact: true }).click();
  const result = page.getByRole('region', { name: '令牌消耗查询结果' });
  await expect(result).toContainText('gemini 文本');
  await expect(result).toContainText('今日消耗金额3');
  await expect(result).toContainText('累计消耗金额136.460558');
  await expect(result).toContainText('Asia/Shanghai');
  await expect(page.getByLabel('Key 名称 1')).toHaveValue('gemini 文本');
  await page.reload();
  await page.getByRole('button', { name: '展开', exact: true }).click();
  await expect(page.getByLabel('Key 名称 1')).toHaveValue('gemini 文本');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('桌面端完成登录、编辑和余额检查', async ({ page }, testInfo) => {
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const state = await mockApi(page, { authenticated: false });
  await page.goto('/login.html');

  await page.getByLabel('管理员密码').fill('wrong');
  await page.getByRole('button', { name: /^登录/ }).click();
  await expect(page.getByRole('alert')).toHaveText('管理员账号或密码错误');
  await page.getByLabel('管理员密码').fill('correct');
  await page.getByRole('button', { name: /^登录/ }).click();
  await expect(page.getByRole('button', { name: /Alpha/ })).toBeVisible();

  await page.getByRole('button', { name: /Alpha/ }).click();
  await page.getByLabel('站点名称').fill('生产站点');
  await page.getByRole('button', { name: '检查全部' }).click();
  await expect(page.getByText('1 个站点检查完成')).toBeVisible();
  await expect(page.getByText('42.50')).toBeVisible();

  const putIndex = state.calls.findIndex((call) => call.path === 'sites' && call.method === 'PUT');
  const checkIndex = state.calls.findIndex((call) => call.path === 'check-many');
  expect(putIndex).toBeGreaterThan(-1);
  expect(checkIndex).toBeGreaterThan(putIndex);
  expect(state.calls[putIndex].body.sites[0].name).toBe('生产站点');
  expect(errors).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath('desktop-workspace.png'), fullPage: true });
});

test('移动端看板没有横向溢出', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page, {
    results: {
      alpha: { ok: true, balance: 128.36, currency: 'CNY', checkedAt: '2026-10-21T02:00:00Z' },
    },
  });
  await page.goto('/');
  await expect(page.getByRole('button', { name: /Alpha/ })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  await page.screenshot({ path: testInfo.outputPath('mobile-dashboard.png'), fullPage: true });
});

test('概览按状态与名称筛选，并适配混合状态的多站点布局', async ({ page }, testInfo) => {
  const checkedAt = '2026-10-21T02:00:00Z';
  const date = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
  await page.setViewportSize({ width: 1440, height: 1000 });
  await mockApi(page, {
    sites: [
      { ...site, name: '主力服务站' },
      { ...site, id: 'beta', name: '备用 API', siteType: 'newapi', currency: 'USD', threshold: 10, baseUrl: 'https://backup.example.com' },
      { ...site, id: 'gamma', name: '团队工作空间', baseUrl: 'https://team.example.com' },
      { ...site, id: 'delta', name: '新接入站点', baseUrl: 'https://new.example.com' },
      { ...site, id: 'epsilon', name: '已归档站点', enabled: false, baseUrl: 'https://archive.example.com' },
    ],
    results: {
      alpha: { ok: true, balance: 1280.36, currency: 'CNY', todayUsage: { date, source: 'keys', cost: 23.45, successCount: 1, failedCount: 1 }, checkedAt },
      beta: { ok: true, balance: 4.5, currency: 'USD', checkedAt },
      gamma: { ok: false, message: '登录会话已过期，请检查认证信息', checkedAt },
      epsilon: { ok: true, balance: 320, currency: 'CNY', checkedAt },
    },
  });
  await page.goto('/');
  const rows = page.locator('.overview-table tbody tr');
  await expect(rows).toHaveCount(5);
  await expect(rows.getByText('1,280.36')).toBeVisible();
  await expect(rows.getByText('4.50')).toBeVisible();
  await expect(page.getByRole('columnheader', { name: '今日实时消耗' })).toBeVisible();
  await expect(rows.locator('.overview-usage').getByText('23.45')).toBeVisible();
  await expect(rows.locator('.overview-usage').getByText('小计 · 1 个 Key 未获取')).toBeVisible();
  const totals = page.getByRole('region', { name: '可用余额总览' });
  await expect(totals.locator('.balance-total').filter({ hasText: 'CNY' })).toContainText('1,280.36');
  await expect(totals.locator('.balance-total').filter({ hasText: 'USD' })).toContainText('4.50');
  await expect(totals).toContainText('已汇总 2 / 4 个启用站点');
  const totalRow = page.locator('.overview-total-row');
  await expect(totalRow).toContainText('合计余额');
  await expect(totalRow).toContainText('1,280.36');
  await expect(totalRow).toContainText('4.50');
  await expect(totalRow).toContainText('已汇总 2 个启用且查询成功的站点');
  await page.getByRole('button', { name: /^需关注/ }).click();
  await expect(rows).toHaveCount(2);
  await expect(totalRow).toContainText('已汇总 1 个启用且查询成功的站点');
  await expect(page.getByRole('button', { name: '备用 API', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '团队工作空间', exact: true })).toBeVisible();
  await page.getByRole('searchbox', { name: '搜索站点' }).fill('backup.example.com');
  await expect(rows).toHaveCount(1);
  await page.getByRole('searchbox', { name: '搜索站点' }).fill('无匹配内容');
  await expect(page.getByText('没有找到匹配的站点')).toBeVisible();
  await page.getByRole('button', { name: '清除筛选' }).click();
  await expect(rows).toHaveCount(5);
  await page.getByRole('button', { name: /^已停用/ }).click();
  await expect(rows).toHaveCount(1);
  await expect(page.getByRole('button', { name: '已归档站点', exact: true })).toBeVisible();
  await page.getByRole('button', { name: /^全部站点/ }).click();
  await page.screenshot({ path: testInfo.outputPath('dashboard-desktop.png'), fullPage: true });
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    await expect(rows.locator('.overview-usage').getByText('23.45')).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
    await expect(page.getByRole('button', { name: '添加站点', exact: true })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath(`dashboard-mobile-${width}.png`), fullPage: true });
  }
});

test('未配置站点时显示清晰的添加入口', async ({ page }, testInfo) => {
  await mockApi(page, { sites: [] });
  await page.goto('/');
  await expect(page.getByText('开始监控你的第一个站点')).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('dashboard-empty.png'), fullPage: true });
  await page.getByRole('button', { name: '添加站点', exact: true }).click();
  await expect(page.getByLabel('站点名称')).toBeVisible();
});
