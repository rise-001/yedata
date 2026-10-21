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

async function mockApi(page, { authenticated = true, results = {} } = {}) {
  const state = {
    authenticated,
    sites: [site],
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
      state.sites = body.sites.map(({ password, token, sessionCookie, ...item }) => ({
        ...item,
        hasPassword: Boolean(password || item.hasPassword),
        hasToken: Boolean(token || item.hasToken),
        hasSessionCookie: Boolean(sessionCookie || item.hasSessionCookie),
      }));
      response = { ok: true, data: { sites: state.sites, settings: state.settings } };
    } else if (path === 'sites') {
      response = { ok: true, data: { sites: state.sites, results: state.results, settings: state.settings } };
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
