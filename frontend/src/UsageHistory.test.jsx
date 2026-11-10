import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import UsageHistory from './UsageHistory.jsx';

const series = [{ currency: 'CNY', timezone: 'Asia/Shanghai', siteCount: 2,
  days: Array.from({ length: 7 }, (_, index) => ({ date: `2026-11-0${index + 1}`,
    cost: index === 2 ? null : index === 0 ? 0 : 2, failedCount: index === 2 || index === 3 ? 1 : 0 })) },
{ currency: 'USD', timezone: 'UTC', siteCount: 1,
  days: Array.from({ length: 7 }, (_, index) => ({ date: `2026-11-0${index + 1}`, cost: 3, failedCount: 0 })) }];

it('展示七天柱形、零消耗、缺失与小计，并可切换币种时区', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200,
    json: async () => ({ ok: true, data: { series, queriedAt: '2026-11-07T08:00:00Z' } }) })));
  const user = userEvent.setup();
  render(<UsageHistory />);
  const bars = await screen.findByRole('list', { name: '每日消耗' });
  expect(within(bars).getAllByRole('listitem')).toHaveLength(7);
  expect(within(bars).getByLabelText('2026-11-01：0.00 CNY')).toBeInTheDocument();
  expect(within(bars).getByLabelText('2026-11-03：未获取，1 项未获取')).toBeInTheDocument();
  expect(within(bars).getByLabelText('2026-11-04：2.00 CNY，成功项小计，1 项未获取')).toBeInTheDocument();
  expect(within(bars).getByLabelText(/今日截至查询时刻/)).toBeInTheDocument();
  expect(screen.getByText('七天消耗小计')).toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText('消耗统计币种与时区'), 'USD|UTC');
  expect(screen.getByText('七天总消耗')).toBeInTheDocument();
  expect(screen.getByText('21.00')).toHaveTextContent('21.00 USD');
  expect(within(bars).getByLabelText('2026-11-01：3.00 USD')).toBeInTheDocument();
});

it('请求失败显示重试入口，重试后可展示空状态', async () => {
  const fetch = vi.fn().mockRejectedValueOnce(new Error('连接失败')).mockResolvedValue({ ok: true, status: 200,
    json: async () => ({ ok: true, data: { series: [] } }) });
  vi.stubGlobal('fetch', fetch);
  render(<UsageHistory />);
  expect(await screen.findByRole('alert')).toHaveTextContent('连接失败');
  await userEvent.click(screen.getByRole('button', { name: '重新查询' }));
  expect(await screen.findByText('启用站点并配置消耗查询 API Key 后，可查看每日消耗')).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledTimes(2);
});
