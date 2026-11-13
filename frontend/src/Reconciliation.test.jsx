import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import Reconciliation from './Reconciliation.jsx';

const row = { id: 7, siteId: 'a', siteName: 'Alpha', status: 'anomaly', scope: 'keys', currency: 'CNY',
  fromTime: '2026-11-13T00:00:00Z', toTime: '2026-11-13T00:05:00Z', previousBalance: '100',
  balance: '70', intervalUsage: '10', difference: '-20', note: '', resolution: '', reason: '仅覆盖已配置 Key' };
const snapshot = { id: 1, startedAt: row.fromTime, capturedAt: row.fromTime, balance: '100', consumption: '10',
  complete: true, scope: 'keys', day: '2026-11-13', evidence: { rawBalance: '100', divisor: '1', multiplier: '1', timezone: 'Asia/Shanghai' } };

function mockApi() {
  const fetch = vi.fn(async (url, options = {}) => {
    let data;
    if (url === '/api/reconciliation/7') {
      data = { ...row, baseline: snapshot, first: { ...snapshot, id: 2 }, latest: { ...snapshot, id: 3 }, evidence: {}, tolerance: '0.01', reviewSeconds: 60,
        ...(options.method === 'PATCH' ? JSON.parse(options.body) : {}) };
    } else if (options.method === 'POST') data = { checked: 1, failed: 0 };
    else data = { records: [row], count: 1, page: 1, pageSize: 50, totals: [{ currency: 'CNY', recharge: '500', anomaly: '20' }], pendingCount: 0, unavailableSiteCount: 1 };
    return { ok: true, status: 200, json: async () => ({ ok: true, data }) };
  });
  vi.stubGlobal('fetch', fetch);
  return fetch;
}

describe('数据差异分析', () => {
  it('shows scoped results, filters history without collecting, and saves notes with original evidence', async () => {
    const fetch = mockApi();
    const user = userEvent.setup();
    render(<Reconciliation sites={[{ id: 'a', name: 'Alpha', enabled: true }]} />);
    expect(await screen.findByText('未解释扣减')).toBeInTheDocument();
    expect(screen.getByText('500.00')).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText('结果'), 'recharge');
    await waitFor(() => expect(fetch.mock.calls.some(([url]) => url.includes('status=recharge'))).toBe(true));
    expect(fetch.mock.calls.every(([, options]) => !options.method)).toBe(true);
    await user.click(screen.getByRole('button', { name: '查看Alpha记录 7' }));
    expect(await screen.findByText('70.00 − 100.00 ＋ 10.00 = -20.00 CNY')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '计算详情' })).toHaveTextContent('仅已配置 Key');
    await user.selectOptions(screen.getByLabelText('处理标记'), 'contacted');
    await user.type(screen.getByLabelText('核实备注'), '已联系站方核实');
    await user.click(screen.getByRole('button', { name: '保存备注' }));
    expect(await screen.findByText('备注已保存')).toBeInTheDocument();
    const call = fetch.mock.calls.find(([, options]) => options.method === 'PATCH');
    expect(JSON.parse(call[1].body)).toEqual({ resolution: 'contacted', note: '已联系站方核实' });
    await user.click(screen.getByRole('button', { name: '立即采集' }));
    expect(await screen.findByText(/已检查 1 个站点/)).toBeInTheDocument();
  });

  it('distinguishes loading failures from empty history and permits retry', async () => {
    const fetch = vi.fn().mockRejectedValueOnce(new Error('服务暂不可用')).mockResolvedValue({
      ok: true, status: 200, json: async () => ({ ok: true, data: { records: [], totals: [], count: 0, page: 1, pageSize: 50, pendingCount: 0, unavailableSiteCount: 1 } }),
    });
    vi.stubGlobal('fetch', fetch);
    render(<Reconciliation sites={[]} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('服务暂不可用');
    expect(screen.queryByText('暂无需要关注的记录')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '刷新记录' }));
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(screen.getByText('暂无需要关注的记录')).toBeInTheDocument();
  });
});
