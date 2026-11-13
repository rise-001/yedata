import { expect, test } from '@playwright/test';

test('差异分析支持导航、计算详情、备注及手机布局', async ({ page }) => {
  await mockApi(page);
  const row = { id: 7, siteId: 'alpha', siteName: 'Alpha', status: 'anomaly', scope: 'keys', currency: 'CNY',
    fromTime: '2026-11-13T00:00:00Z', toTime: '2026-11-13T00:05:00Z', previousBalance: '100',
    balance: '70', intervalUsage: '10', difference: '-20', note: '', resolution: '', reason: '仅覆盖已配置 Key' };
  const snapshot = { id: 1, startedAt: row.fromTime, capturedAt: row.fromTime, balance: '100', consumption: '10',
    complete: true, scope: 'keys', day: '2026-11-13', evidence: { rawBalance: '100', divisor: '1', multiplier: '1', timezone: 'Asia/Shanghai' } };
  let note;
  await page.route('**/api/reconciliation**', async (route) => {
    const detail = new URL(route.request().url()).pathname.endsWith('/7');
    if (route.request().method() === 'PATCH') note = route.request().postDataJSON();
    const data = detail ? { ...row, ...note, baseline: snapshot, first: { ...snapshot, id: 2 }, latest: { ...snapshot, id: 3 }, evidence: {}, tolerance: '0.01', reviewSeconds: 60 }
      : { records: [row], count: 1, page: 1, pageSize: 50, totals: [{ currency: 'CNY', recharge: '500', anomaly: '20' }], pendingCount: 0, unavailableSiteCount: 1 };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, data }) });
  });
  await page.goto('/');
  await page.getByRole('link', { name: '数据差异分析' }).click();
  await expect(page.getByRole('heading', { name: '数据差异分析', exact: true })).toBeVisible();
  await expect(page.getByText('未解释扣减', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '查看Alpha记录 7' }).click();
  await expect(page.getByText('70.00 − 100.00 ＋ 10.00 = -20.00 CNY')).toBeVisible();
  await page.getByLabel('处理标记').selectOption('contacted');
  await page.getByLabel('核实备注').fill('已联系站方核实');
  await page.getByRole('button', { name: '保存备注' }).click();
  await expect(page.getByText('备注已保存')).toBeVisible();
  expect(note).toEqual({ resolution: 'contacted', note: '已联系站方核实' });
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.getByRole('heading', { name: '数据差异分析', exact: true }).scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: `test-results/reconciliation-${width}.png`, fullPage: true });
  }
  await page.reload();
  await expect(page.getByRole('heading', { name: '数据差异分析', exact: true })).toBeVisible();
});

test('七天消耗柱状图在桌面和手机上显示每日金额并支持切换币种', async ({ page }) => {
  await mockApi(page);
  const historyMethods = [];
  const days = Array.from({ length: 7 }, (_, index) => ({
    date: `2026-11-0${index + 1}`, cost: index === 1 ? null : index * 1.25,
    successCount: index === 1 ? 0 : 1, failedCount: index === 1 || index === 3 ? 1 : 0,
  }));
  await page.route('**/api/usage/history', (route) => {
    historyMethods.push(route.request().method());
    return route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, data: {
      queriedAt: '2026-11-07T08:00:00Z', series: [
        { currency: 'CNY', timezone: 'Asia/Shanghai', siteCount: 2, days },
        { currency: 'USD', timezone: 'UTC', siteCount: 1, days: days.map((day) => ({ ...day, cost: 2, failedCount: 0 })) },
      ],
    } }),
    });
  });
  await page.goto('/');
  const chart = page.getByRole('region', { name: '最近七天消耗' });
  await expect(chart.getByRole('listitem')).toHaveCount(7);
  expect(historyMethods[0]).toBe('GET');
  await expect(chart.getByLabel('2026-11-01：0.00 CNY', { exact: true })).toBeVisible();
  await expect(chart.getByLabel('2026-11-02：未获取，1 项未获取', { exact: true })).toBeVisible();
  for (const width of [1920, 1440, 390, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    await chart.scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    const bar = chart.locator('.usage-chart-bar').nth(6);
    expect((await bar.boundingBox()).height).toBeGreaterThan(100);
    const offsets = await chart.getByRole('listitem').evaluateAll((items) => items.map((item) => {
      const bar = item.querySelector('.usage-chart-bar').getBoundingClientRect();
      const value = item.querySelector('.usage-chart-value').getBoundingClientRect();
      const date = item.querySelector('.usage-chart-date');
      const range = document.createRange();
      range.selectNode(date.firstChild);
      const dateText = range.getBoundingClientRect();
      const center = (rect) => rect.x + rect.width / 2;
      return { value: Math.abs(center(value) - center(bar)), date: Math.abs(center(dateText) - center(bar)) };
    }));
    for (const offset of offsets) {
      expect(offset.value).toBeLessThan(1);
      expect(offset.date).toBeLessThan(1);
    }
    await chart.screenshot({ path: `test-results/usage-history-${width}.png` });
  }
  await chart.getByLabel('消耗统计币种与时区').selectOption('USD|UTC');
  await expect(chart.getByText('七天总消耗', { exact: true })).toBeVisible();
  await expect(chart.getByLabel('2026-11-01：2.00 USD', { exact: true })).toBeVisible();
  await chart.getByRole('button', { name: '刷新消耗' }).click();
  await expect(chart.getByRole('button', { name: '刷新消耗' })).toBeEnabled();
  expect(historyMethods).toContain('POST');
});

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

for (const siteType of ['sub2', 'newapi']) {
  test(`${siteType} 多 Key 结果以卡片排列，窄屏自动换行`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1600, height: 1000 });
    const costs = [65.951149, 154.470907, 43.224011, 0.000068];
    const keys = costs.map((cost, index) => ({
      keyId: `key-${index}`, keyName: `Key ${index + 1}`, ok: true,
      data: { todayActualCost: cost, usedQuota: cost * 10, remainingQuota: 100, totalQuota: 200,
        unlimitedQuota: false, expiresAt: -1, modelLimitsEnabled: false },
    }));
    await mockApi(page, { sites: [{ ...site, siteType,
      usageKeys: keys.map((key) => ({ id: key.keyId, name: key.keyName, hasToken: true })),
    }], usageResult: { keys, successCount: 4, failedCount: 0,
      todayActualCost: costs.reduce((total, cost) => total + cost, 0) } });
    await page.goto('/');
    await page.getByRole('button', { name: 'Alpha', exact: true }).click();
    await page.getByRole('button', { name: '消耗查询', exact: true }).click();
    const result = page.getByRole('region', { name: '令牌消耗查询结果' });
    const cards = result.getByRole('article');
    await expect(cards).toHaveCount(4);
    await page.getByRole('button', { name: '关闭提示' }).click();
    await expect(cards.nth(3).locator('.usage-primary dd')).toHaveText('0.00');
    const columns = await cards.evaluateAll((items) => items.filter((item) => item.offsetTop === items[0].offsetTop).length);
    expect(columns).toBe(siteType === 'sub2' ? 4 : 3);
    await result.screenshot({ path: testInfo.outputPath('multi-key-desktop.png') });
    for (const width of [390, 320]) {
      await page.setViewportSize({ width, height: 844 });
      const bounds = await cards.evaluateAll((items) => items.map((item) => ({ x: item.getBoundingClientRect().x, width: item.getBoundingClientRect().width })));
      expect(bounds.every((item) => item.x === bounds[0].x && item.width === bounds[0].width)).toBe(true);
      const amountLines = await result.locator('.usage-primary dd').evaluateAll((items) => items.map((item) => {
        const range = document.createRange();
        range.selectNodeContents(item);
        return range.getClientRects().length;
      }));
      expect(amountLines.every((lines) => lines === 1)).toBe(true);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    }
    await result.screenshot({ path: testInfo.outputPath('multi-key-mobile.png') });
  });
}

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
  await expect(result).toContainText('今日消耗金额3.00');
  await expect(result).toContainText('累计消耗金额136.46');
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
  const usageTotals = page.getByRole('region', { name: '今日总消耗' });
  await expect(usageTotals.locator('.balance-total')).toContainText('23.45');
  await expect(usageTotals).toContainText('已汇总 1 / 4 个启用站点');
  await expect(usageTotals).toContainText('部分数据未获取，当前为成功项小计');
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
    await expect(usageTotals.locator('.balance-total')).toBeVisible();
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
