import { useEffect, useState } from 'react';
import { BarChart3, RefreshCw } from 'lucide-react';
import { api } from './api.js';
import { formatBalance as amount, formatTime } from './sites.js';

export default function UsageHistory() {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const [selection, setSelection] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    api('usage/history', { signal: controller.signal, method: revision ? 'POST' : 'GET' }).then(({ data }) => {
      if (!controller.signal.aborted) setData(data || { series: [] });
    }).catch((error) => {
      if (!controller.signal.aborted) setError(error.message);
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [revision]);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const timer = setInterval(async () => {
      if (document.hidden || loading) return;
      try {
        const { data } = await api('usage/history', { signal: controller.signal, method: 'GET' });
        if (active) { setData(data); setError(''); }
      } catch (error) {
        if (active) setError(error.message);
      }
    }, 15000);
    return () => { active = false; clearInterval(timer); controller.abort(); };
  }, [loading]);

  const series = data?.series || [];
  const groupKey = (item) => `${item.currency}|${item.timezone}`;
  const group = series.find((item) => groupKey(item) === selection) || series[0];
  const days = group?.days || [];
  const values = days.filter((day) => day.cost != null);
  const total = values.reduce((sum, day) => sum + day.cost, 0);
  const partial = days.some((day) => day.failedCount > 0);
  const stale = days.some((day) => day.staleCount > 0);
  const pending = days.some((day) => day.pendingCount > 0);
  const upper = Math.max(0, ...values.map((day) => day.cost)) || 1;
  const lower = Math.min(0, ...values.map((day) => day.cost));
  const span = upper - lower;
  const baseline = -lower / span * 100;

  return <section className="overview-panel usage-history" aria-labelledby="usage-history-title" aria-busy={loading}>
    <header className="overview-panel-header">
      <div className="overview-panel-title"><span className="panel-symbol"><BarChart3 size={20} aria-hidden="true" /></span><div><h2 id="usage-history-title">最近八天消耗</h2><p>按天汇总已启用站点的 API Key 消耗，含今天</p></div></div>
      <div className="usage-history-actions">
        {series.length > 1 && <select aria-label="消耗统计币种与时区" value={groupKey(group)} onChange={(event) => setSelection(event.target.value)}>{series.map((item) => <option key={groupKey(item)} value={groupKey(item)}>{item.currency} · {item.timezone}</option>)}</select>}
        <button className="secondary-button" disabled={loading} onClick={() => setRevision((value) => value + 1)}><RefreshCw size={14} aria-hidden="true" />{loading ? '查询中…' : '刷新消耗'}</button>
      </div>
    </header>
    {error && <div className="notice error-notice" role="alert">八天消耗查询失败：{error}<button className="text-button" onClick={() => setRevision((value) => value + 1)}>重新查询</button></div>}
    {!group ? <div className="usage-history-empty" role="status">{loading ? '正在查询最近八天的消耗…' : error ? '暂时无法获取消耗数据' : '启用站点并配置消耗查询 API Key 后，可查看每日消耗'}</div> : <>
      <div className="usage-history-summary"><div><span>{partial ? '八天消耗小计' : '八天总消耗'}</span><strong>{values.length ? amount(total) : '—'} <small>{group.currency}</small></strong></div><p>{days[0]?.date} 至 {days.at(-1)?.date}<br />{group.timezone} · {group.siteCount} 个启用站点</p></div>
      <div className="usage-chart-scroll"><div className="usage-chart">
        <div className="usage-chart-axis" aria-hidden="true">{[upper, (upper + lower) / 2, lower].map((value, index) => <span key={index}>{amount(value)}</span>)}</div>
        <div className="usage-chart-plot"><div className="usage-chart-grid" aria-hidden="true"><i /><i /><i /></div><div className="usage-chart-zero" style={{ bottom: `calc(30px + ${baseline}% - ${baseline * 0.3}px)` }} aria-hidden="true" />
          <ul className="usage-chart-bars" aria-label="每日消耗">{days.map((day, index) => {
            const known = day.cost != null;
            const label = `${day.date}：${known ? `${amount(day.cost)} ${group.currency}${day.failedCount ? '，成功项小计' : ''}` : '未获取'}${day.failedCount ? `，${day.failedCount} 项未获取` : ''}${day.staleCount ? '，保留上次成功数据' : ''}${index === days.length - 1 ? '，今日截至查询时刻' : ''}`;
            return <li key={day.date} tabIndex={0} aria-label={label} title={`${label}${day.updatedAt ? `；数据更新于 ${formatTime(day.updatedAt)}` : ''}`} className={index === days.length - 1 ? 'is-today' : ''}>
              <div className="usage-chart-column"><span className="usage-chart-value" style={{ bottom: `${baseline + (known ? Math.max(0, day.cost) / span * 100 : 0)}%` }}>{known ? amount(day.cost) : '—'}{known && day.failedCount > 0 ? '*' : ''}</span><div className={`usage-chart-bar ${known ? '' : 'is-missing'}`} style={{ height: `${known ? Math.abs(day.cost) / span * 100 : 0}%`, bottom: `${day.cost < 0 ? baseline + day.cost / span * 100 : baseline}%` }} /></div>
              <span className="usage-chart-date">{day.date.slice(5).replace('-', '/')}{index === days.length - 1 && <small>今天</small>}</span>
            </li>;
          })}</ul>
        </div>
      </div></div>
      <footer className="overview-panel-footer"><span>{pending ? '后台正在补齐数据；— 表示未获取' : partial ? '* 部分数据未获取，金额为成功项小计；— 表示未获取' : '仅统计已配置的 API Key 消耗'}{stale && ' · 部分数据更新失败或已过期，保留上次成功数据'} · 今天截至查询时刻</span><span>{data.queriedAt ? `最早数据更新于 ${formatTime(data.queriedAt)}` : '等待后台首次更新'}</span></footer>
    </>}
  </section>;
}
