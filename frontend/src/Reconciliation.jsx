import { useEffect, useState } from 'react';
import { RefreshCw, ScanLine, X } from 'lucide-react';
import { api, send } from './api.js';
import { formatBalance as amount } from './sites.js';

const statuses = {
  baseline: '建立基准', normal: '正常', pending: '待复核', recharge: '充值（推算）',
  anomaly: '异常减少', recovered: '延迟后恢复', unavailable: '无法判断', reset: '计数重置', cancelled: '停止复核',
};
const resolutions = { '': '未标记', verified_recharge: '已核实充值', adjustment: '平台调账', contacted: '已联系站方', resolved: '已处理' };
const shanghaiDate = (value) => new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
}).format(value);
const timestamp = (value) => value ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '—';
const scopeLabel = (scope) => scope === 'account' ? '账户全部消耗' : '仅已配置 Key';
const resultLabel = (row) => row.status === 'anomaly' && row.scope === 'keys' ? '未解释扣减' : statuses[row.status] || row.status;
const signed = (value) => value == null ? '—' : `${Number(value) > 0 ? '+' : ''}${amount(value)}`;

function Detail({ id, onClose, onSaved }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [note, setNote] = useState('');
  const [resolution, setResolution] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setData(null); setError(''); setSaved(false);
    api(`reconciliation/${id}`, { signal: controller.signal }).then(({ data }) => {
      if (controller.signal.aborted) return;
      setData(data); setNote(data.note); setResolution(data.resolution);
    }).catch((error) => { if (!controller.signal.aborted) setError(error.message); });
    return () => controller.abort();
  }, [id, revision]);
  async function save(event) {
    event.preventDefault(); setSaving(true); setSaved(false); setError('');
    try {
      const result = await send(`reconciliation/${id}`, 'PATCH', { note, resolution });
      setData(result.data); setSaved(true); onSaved();
    } catch (error) { setError(error.message); }
    finally { setSaving(false); }
  }
  return <section className="reconcile-detail" aria-label="计算详情">
    <header><div><h2>计算详情{data ? ` · ${data.siteName}` : ''}</h2><small>采集时间与筛选日期均为北京时间</small></div>
      <button className="icon-button" aria-label="关闭计算详情" onClick={onClose}><X size={16} /></button></header>
    {error && <p className="error-text" role="alert">{error} {!data && <button className="text-button" onClick={() => setRevision((n) => n + 1)}>重新加载详情</button>}</p>}
    {!data && !error && <p role="status">正在读取原始记录…</p>}
    {data && <>
      <div className="reconcile-detail-heading"><span className={`reconcile-badge ${data.status}`}>{resultLabel(data)}</span><span>{scopeLabel(data.scope)} · {data.currency}</span></div>
      <p>{data.reason}</p>
      {data.scope === 'keys' && <p className="reconcile-hint">账户内其他 Key 的消费不在此统计范围内。正差额为净增额度推算，负差额可能包含未配置 Key 的消费。</p>}
      {data.difference != null && <div className="reconcile-formula">
        <small>差额 = 本次余额 − 基准余额 ＋ 区间消耗</small>
        <strong>{amount(data.balance)} − {amount(data.previousBalance)} ＋ {amount(data.intervalUsage)} = {signed(data.difference)} {data.currency}</strong>
        <small>允许误差 {amount(data.tolerance)} · 差额稳定至少 {data.reviewSeconds} 秒后复核确认；待复核不计入正式金额。</small>
      </div>}
      <div className="reconcile-table-scroll"><table className="reconcile-table detail-table"><thead><tr><th>采集记录</th><th>开始 / 完成时间</th><th>余额</th><th>{data.scope === 'account' ? '累计消耗' : '当日消耗'}</th><th>数据完整性</th></tr></thead><tbody>
        {[[data.baseline, '比较基准'], [data.first, '首次发现'], ...(data.first.id !== data.latest.id ? [[data.latest, '最新复核']] : [])].filter(([row]) => row).map(([row, label]) => <tr key={label}>
          <td>{label}</td><td>{timestamp(row.startedAt)}<small>{timestamp(row.capturedAt)}</small></td><td>{amount(row.balance)}</td><td>{amount(row.consumption)}<small>{row.scope === 'keys' ? row.day : '账户累计'}</small></td><td>{row.complete ? '完整' : row.reason}</td>
        </tr>)}
      </tbody></table></div>
      {Object.keys(data.evidence?.dailyTotals || {}).length > 0 && <p className="reconcile-hint">跨天补查的全天消耗：{Object.entries(data.evidence.dailyTotals).map(([day, cost]) => `${day}：${amount(cost)} ${data.currency}`).join('；')}</p>}
      <details className="reconcile-evidence"><summary>查看原始金额与换算依据</summary>
        <dl>{[['原始余额', data.latest.evidence.rawBalance], ['原始账户累计消耗', data.latest.evidence.rawAccountUsage], ['原始 Key 今日消耗', data.latest.evidence.rawKeyUsage], ['换算除数', data.latest.evidence.divisor], ['余额倍率', data.latest.evidence.multiplier], ['冻结额度', data.latest.evidence.frozen], ['站点时区', data.latest.evidence.timezone]].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value ?? '—'}</dd></div>)}</dl>
      </details>
      <form onSubmit={save} className="reconcile-note"><label>处理标记<select value={resolution} onChange={(event) => { setResolution(event.target.value); setSaved(false); }} disabled={saving}>
        {Object.entries(resolutions).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
      </select></label><label>核实备注<textarea value={note} maxLength={2000} rows={3} placeholder="记录核实结果或站方反馈，原始金额会保留。" disabled={saving} onChange={(event) => { setNote(event.target.value); setSaved(false); }} /></label>
        <div><button className="secondary-button" disabled={saving}>{saving ? '保存中…' : '保存备注'}</button>{saved && <span role="status">备注已保存</span>}</div>
      </form>
    </>}
  </section>;
}

export default function Reconciliation({ sites }) {
  const [siteId, setSiteId] = useState('');
  const [status, setStatus] = useState('attention');
  const [start, setStart] = useState(() => shanghaiDate(new Date(Date.now() - 6 * 86400000)));
  const [end, setEnd] = useState(() => shanghaiDate(new Date()));
  const [page, setPage] = useState(1);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState(null);
  useEffect(() => {
    const controller = new AbortController();
    let inFlight = false;
    const load = async (initial = false) => {
      if (inFlight || (!initial && document.hidden)) return;
      inFlight = true;
      if (initial) { setLoading(true); setData(null); }
      try {
        const query = new URLSearchParams({ siteId, status, start, end, page: String(page) });
        const result = await api(`reconciliation?${query}`, { signal: controller.signal });
        if (!controller.signal.aborted) { setData(result.data); setError(''); }
      } catch (error) { if (!controller.signal.aborted) setError(error.message); }
      finally { inFlight = false; if (!controller.signal.aborted) setLoading(false); }
    };
    load(true);
    const timer = setInterval(() => load(), 15000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [siteId, status, start, end, page, revision]);

  function filter(setter, value) { setter(value); setPage(1); setSelected(null); }
  async function action(kind) {
    if (busy) return;
    setBusy(kind); setMessage(''); setError('');
    try {
      const result = await send('reconciliation', 'POST', { action: kind, siteId });
      setMessage(kind === 'reset' ? '基准已清除；下一次完整采集将建立新基准，历史记录保留。'
        : `已检查 ${result.data.checked} 个站点${result.data.failed ? `，${result.data.failed} 个采集或记录失败，请查看明细或服务日志` : '，结果已更新'}。`);
      setRevision((n) => n + 1);
    } catch (error) { setError(error.message); }
    finally { setBusy(''); }
  }
  const currencies = (data?.totals || []).filter((row) => Number(row.recharge) || Number(row.anomaly));
  const enabled = sites.filter((site) => site.enabled && (!siteId || site.id === siteId));
  return <div className="reconciliation">
    <p className="reconcile-intro">余额减少应与消耗增加对应。差额增加记为充值推算，差额减少进入异常复核。首次采集建立基准，历史记录从启用本功能后开始积累。</p>
    <section className="reconcile-stats" aria-label="对账概览">
      <div><span>推算充值</span>{currencies.length ? currencies.map((row) => <strong key={row.currency}>{amount(row.recharge)} <small>{row.currency}</small></strong>) : <strong>{data ? '0.00' : '—'}</strong>}<small>所选日期与站点 · 已复核净增额度</small></div>
      <div><span>异常减少</span>{currencies.length ? currencies.map((row) => <strong className="error-text" key={row.currency}>{amount(row.anomaly)} <small>{row.currency}</small></strong>) : <strong>{data ? '0.00' : '—'}</strong>}<small>含已配置 Key 的未解释扣减</small></div>
      <div><span>待复核记录</span><strong>{data?.pendingCount ?? '—'}</strong><small>暂不计入充值与异常金额</small></div>
      <div><span>当前无法判断的站点</span><strong>{data?.unavailableSiteCount ?? '—'}</strong><small>启用站点 · 不受日期筛选影响</small></div>
    </section>
    <section className="reconcile-panel" aria-label="差异分析记录">
      <div className="reconcile-toolbar"><div><h2>对账明细</h2><small>误差容限 {amount(data?.tolerance ?? 0.01)} · 差额稳定至少 {data?.reviewSeconds ?? 60} 秒后确认</small></div><div className="toolbar-actions">
        <button className="secondary-button" disabled={loading || Boolean(busy)} onClick={() => setRevision((n) => n + 1)}><RefreshCw size={15} />刷新记录</button>
        <button className="primary-button" disabled={Boolean(busy) || !enabled.length} onClick={() => action('check')}><ScanLine size={15} />{busy === 'check' ? '采集中…' : '立即采集'}</button>
      </div></div>
      <div className="reconcile-filters">
        <label>站点<select value={siteId} onChange={(event) => filter(setSiteId, event.target.value)}><option value="">全部站点（含历史）</option>{sites.map((site) => <option key={site.id} value={site.id}>{site.name}{site.enabled ? '' : '（停用）'}</option>)}</select></label>
        <label>结果<select value={status} onChange={(event) => filter(setStatus, event.target.value)}><option value="attention">需关注</option><option value="">全部结果</option>{Object.entries(statuses).map(([key, label]) => <option key={key} value={key}>{key === 'anomaly' ? '异常减少 / 未解释扣减' : label}</option>)}</select></label>
        <label>开始日期（北京时间）<input type="date" value={start} max={end || undefined} onChange={(event) => filter(setStart, event.target.value)} /></label>
        <label>结束日期（北京时间）<input type="date" value={end} min={start || undefined} onChange={(event) => filter(setEnd, event.target.value)} /></label>
      </div>
      {error && <p className="error-text" role="alert">{error}</p>}{message && <p className="reconcile-hint" role="status">{message}</p>}
      {selected && <Detail key={selected} id={selected} onClose={() => setSelected(null)} onSaved={() => setRevision((n) => n + 1)} />}
      {loading ? <div className="empty-state" role="status">正在加载对账记录…</div> : !data ? <div className="empty-state">记录加载失败，请点击“刷新记录”重试。</div> : !data.records.length ? <div className="empty-state"><h3>{status === 'attention' ? '暂无需要关注的记录' : '暂无匹配的对账记录'}</h3><p>可切换“全部结果”查看基准和正常记录，或点击“立即采集”获取新数据。</p></div> : <div className="reconcile-table-scroll"><table className="reconcile-table"><thead><tr>
        <th>站点 / 覆盖范围</th><th>对比时间段（北京时间）</th><th>上次余额</th><th>本次余额</th><th>区间消耗</th><th>差额</th><th>结果</th><th>操作</th>
      </tr></thead><tbody>{data.records.map((row) => <tr key={row.id}>
        <td><strong>{row.siteName}</strong><small>{scopeLabel(row.scope)} · {row.currency}</small></td><td>{timestamp(row.fromTime)}<small>至 {timestamp(row.toTime)}</small></td>
        <td>{amount(row.previousBalance)}</td><td>{amount(row.balance)}</td><td>{amount(row.intervalUsage)}</td><td className={Number(row.difference) < 0 ? 'error-text' : ''}>{signed(row.difference)}</td>
        <td><span className={`reconcile-badge ${row.status}`}>{resultLabel(row)}</span>{row.resolution && <small>{resolutions[row.resolution]}</small>}{row.note && <small title={row.note}>已备注</small>}</td>
        <td><button className="text-button" aria-label={`查看${row.siteName}记录 ${row.id}`} onClick={() => setSelected(row.id)}>查看详情</button></td>
      </tr>)}</tbody></table></div>}
      <footer className="reconcile-pagination"><small>共 {data?.count ?? 0} 条 · 第 {data?.page ?? page} 页</small><div>
        <button className="secondary-button" disabled={loading || (data?.page ?? page) <= 1} onClick={() => setPage((data?.page ?? page) - 1)}>上一页</button>
        <button className="secondary-button" disabled={loading || !data || data.page * data.pageSize >= data.count} onClick={() => setPage(data.page + 1)}>下一页</button>
      </div></footer>
    </section>
    <div className="reconcile-bottom"><p>充值金额为两次采集之间的净增额度，可能包含赠送、退款或调账。仅 Key 对账不能覆盖其他 Key 的消费。失败或不完整数据不会按零处理。</p>
      {siteId && enabled.length > 0 && <button className="text-button" disabled={Boolean(busy)} onClick={() => action('reset')}>{busy === 'reset' ? '正在重建…' : '为所选站点重建基准'}</button>}
    </div>
  </div>;
}
