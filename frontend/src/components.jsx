import { useEffect, useState } from 'react';
import { Activity, ArrowUpRight, ChevronDown, ChevronUp, CircleCheck, ExternalLink, Globe2, Layers3, Plus, Power, RefreshCw, Search, ShieldCheck, TriangleAlert, Trash2, WalletCards, X } from 'lucide-react';
import { formatBalance, formatInterval, formatTime, presets, siteStatus, typeNames } from './sites.js';

export function Brand() {
  return <div className="brand"><span className="brand-mark">Y</span><span>余额哨兵<small>BALANCE WATCH</small></span></div>;
}

export function Status({ site, result, checking = false }) {
  const status = checking ? { kind: 'pending', text: '检查中…' } : siteStatus(site, result);
  return <span className={`status ${status.kind}`}><i />{status.text}</span>;
}

export function Field({ label, value, onChange, children, wide, ...props }) {
  return <label className={wide ? 'field wide' : 'field'}><span>{label}</span>{children
    || <input value={value ?? ''} onChange={(event) => onChange(event.target.value)} {...props} />}</label>;
}

export function SiteCard({ site, result, usageResult, usageBusy, index, busy, checking, selected, onChange, onCheck, onUsage, onToggle, onDelete, onOpen }) {
  const [expanded, setExpanded] = useState(Boolean(site._new || selected));
  const [advanced, setAdvanced] = useState(Boolean(site._new));
  useEffect(() => { if (selected) setExpanded(true); }, [selected]);
  const update = (key) => (value) => onChange(site.id, { [key]: value });
  const field = (key, label, props = {}) => <Field label={label} value={site[key]} onChange={update(key)} {...props} />;
  const showUserId = site.authMode !== 'cookie' && (site.siteType === 'newapi' || site.authMode === 'token'
    || (site.siteType === 'auto' && (site.mePath === '/api/user/self' || site.balanceField === 'quota')));
  const isNewApi = site.siteType === 'newapi' || (site.siteType === 'auto'
    && (site.mePath === '/api/user/self' || site.balanceField === 'quota'));
  const secretPlaceholder = (key) => site[`has${key[0].toUpperCase()}${key.slice(1)}`]
    ? '已安全保存，留空保持不变' : '输入凭据';
  const usage = usageResult?.data;
  const quota = (value) => value == null || !Number.isFinite(Number(value)) ? '—' : Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
  const expires = usage?.expiresAt === -1 ? '永不过期' : usage?.expiresAt ? new Date(usage.expiresAt * 1000).toLocaleString('zh-CN', { hour12: false }) : '未提供';
  return <article id={`site-${site.id}`} className={`site-card ${!site.enabled ? 'muted-card' : ''} ${selected ? 'selected-card' : ''}`}>
    <header className="site-header">
      <div className="site-title"><span className="section-index">{String(index + 1).padStart(2, '0')}</span><h3>{site.name || '未命名站点'}</h3><Status site={site} result={result} checking={checking} />
        {!expanded && <span className="mini-balance">{result?.ok ? formatBalance(result.balance) : '—'} <small>{result?.currency || site.currency}</small></span>}</div>
      <div className="site-actions">
        <button className="text-button" aria-expanded={expanded} aria-controls={`fields-${site.id}`} onClick={() => setExpanded(!expanded)}>{expanded ? '收起' : '展开'}</button>
        <button className="text-button" disabled={busy} onClick={() => onOpen(site)}>进入 <ExternalLink size={12} aria-hidden="true" /></button>
        <button className="text-button" disabled={busy || !site.enabled} onClick={() => onCheck(site.id)}><RefreshCw size={12} aria-hidden="true" />检查</button>
        {isNewApi && <button className="text-button" disabled={busy || (!site.usageToken && !site.hasUsageToken)} onClick={() => onUsage(site)}><Activity size={12} aria-hidden="true" />{usageBusy ? '查询中…' : '消耗查询'}</button>}
        <button className="text-button" disabled={busy} onClick={() => onToggle(site)}><Power size={12} aria-hidden="true" />{site.enabled ? '停用' : '启用'}</button>
        <button className="text-button danger" disabled={busy} onClick={() => onDelete(site)}><Trash2 size={12} aria-hidden="true" />删除</button>
      </div>
    </header>
    {expanded && <div id={`fields-${site.id}`}>
      <div className="site-result"><div><small>可用余额</small><strong>{result?.ok ? formatBalance(result.balance) : '—'} <em>{result?.currency || site.currency}</em></strong><small>{result?.displayName || result?.email || '等待连接'}</small></div>
        <div><small>最近状态</small><p>{!site.enabled ? '监控已停止' : result?.ok ? result.notified ? '已发送预警' : siteStatus(site, result).text : result?.message || '等待检查'}</p>
          <small className={result?.notificationError ? 'error-text' : ''}>{result?.notificationError ? `通知失败：${result.notificationError}` : result?.notificationMessage || formatTime(result?.checkedAt)}</small></div></div>
      {isNewApi && usageResult && <section className={`token-usage ${usageResult.ok ? '' : 'error-text'}`} aria-label="令牌消耗查询结果">
        <div><strong>{usage?.name || '令牌消耗'}</strong><small>{usageResult.ok ? '刚刚查询' : usageResult.message}</small></div>
        {usageResult.ok && <dl className="token-usage-grid">
          <div><dt>已用额度</dt><dd>{quota(usage.usedQuota)}</dd></div>
          <div><dt>剩余额度</dt><dd>{usage.unlimitedQuota ? '不限额' : quota(usage.remainingQuota)}</dd></div>
          <div><dt>总额度</dt><dd>{usage.unlimitedQuota ? '不限额' : quota(usage.totalQuota)}</dd></div>
          <div><dt>到期时间</dt><dd>{expires}</dd></div>
          <div className="wide"><dt>模型限制</dt><dd>{usage.modelLimitsEnabled ? (usage.modelLimits?.length ? usage.modelLimits.join('、') : '已启用') : '未限制'}</dd></div>
        </dl>}
      </section>}
      <fieldset disabled={busy}>
        <div className="fields-grid">
          {field('name', '站点名称', { maxLength: 80 })}
          <Field label="网站类型"><select value={site.siteType} onChange={(event) => onChange(site.id, { siteType: event.target.value, ...(presets[event.target.value] || {}) })}>
            {Object.entries(typeNames).map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></Field>
          {field('baseUrl', '接口地址', { type: 'url', placeholder: 'https://example.com', wide: true })}
          <Field label="认证方式"><select value={site.authMode} onChange={(event) => update('authMode')(event.target.value)}><option value="password">账号密码登录</option><option value="token">访问令牌 / Bearer Token</option><option value="cookie">登录 Cookie（手动粘贴）</option></select></Field>
          {showUserId && field('newApiUserId', 'New-Api-User ID', { inputMode: 'numeric', placeholder: '账号的数字 ID' })}
          {site.authMode === 'password' && <>{field('email', '账号 / 邮箱', { autoComplete: 'off' })}{field('password', '密码', { type: 'password', autoComplete: 'new-password', placeholder: secretPlaceholder('password') })}</>}
          {site.authMode === 'token' && field('token', '访问令牌 / Bearer Token', { type: 'password', autoComplete: 'off', placeholder: secretPlaceholder('token'), wide: true })}
          {site.authMode === 'cookie' && <Field label="登录 Cookie" wide><textarea rows={3} value={site.sessionCookie} autoComplete="off" spellCheck={false} onChange={(event) => update('sessionCookie')(event.target.value)} placeholder={secretPlaceholder('sessionCookie')} /></Field>}
          {isNewApi && field('usageToken', '消耗查询 API Key', { type: 'password', autoComplete: 'off', placeholder: site.hasUsageToken ? '已安全保存，留空保持不变' : 'sk-...', wide: true })}
          {field('threshold', '预警阈值', { type: 'number', min: 0, step: '0.01' })}
          {field('intervalSeconds', '查询间隔（秒）', { type: 'number', min: 30, max: 86400, step: 1 })}
          {field('balanceMultiplier', '余额倍率', { type: 'number', min: '0.000001', step: 'any' })}
          {advanced && <>
            <Field label="时区"><select value={site.timezone} onChange={(event) => update('timezone')(event.target.value)}>{[...new Set([site.timezone, 'Asia/Shanghai', 'UTC', 'Asia/Tokyo'])].map((zone) => <option key={zone}>{zone}</option>)}</select></Field>
            {field('loginPath', '登录路径')}{field('mePath', '余额路径')}{!isNewApi && field('usagePath', '用量接口', { placeholder: '留空表示不查询', wide: true })}
            {field('balanceField', '余额字段', { placeholder: 'auto / balance / quota' })}
            {field('balanceDivisor', '换算除数', { type: 'number', min: '0.000001', step: 'any', placeholder: '自动识别' })}
            {field('currency', '币种', { maxLength: 8, placeholder: '自动识别' })}
          </>}
        </div>
        <footer className="site-footer"><label className="checkbox-label"><input type="checkbox" checked={site.notifyEnabled} onChange={(event) => update('notifyEnabled')(event.target.checked)} />启用余额预警</label>
          <button className="text-button" type="button" aria-expanded={advanced} onClick={() => setAdvanced(!advanced)}>{advanced ? '收起高级设置' : '高级设置'}{advanced ? <ChevronUp size={13} aria-hidden="true" /> : <ChevronDown size={13} aria-hidden="true" />}</button></footer>
      </fieldset>
    </div>}
  </article>;
}

export function Dashboard({ sites, results, checkingIds, onEdit, onOpen, onAdd }) {
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('all');
  const statuses = sites.map((site) => siteStatus(site, results[site.id]));
  const healthy = statuses.filter((status) => status.kind === 'healthy').length;
  const attention = statuses.filter((status) => ['warning', 'failed'].includes(status.kind)).length;
  const enabled = sites.filter((site) => site.enabled).length;
  const pending = statuses.filter((status) => status.kind === 'pending').length;
  const balanceTotals = new Map();
  let countedSites = 0;
  for (const site of sites) {
    const result = results[site.id];
    if (!site.enabled || !result?.ok || result.balance == null || !Number.isFinite(Number(result.balance))) continue;
    const currency = String(result.currency || site.currency || '').trim() || '未标注币种';
    balanceTotals.set(currency, (balanceTotals.get(currency) || 0) + Number(result.balance));
    countedSites += 1;
  }
  const summary = [
    { title: '监控站点', value: sites.length, hint: '全部已配置站点', Icon: Globe2, kind: 'total' },
    { title: '正常余额', value: healthy, hint: '余额高于预警阈值', Icon: CircleCheck, kind: 'healthy' },
    { title: '需要关注', value: attention, hint: attention ? '低余额或查询异常' : '暂无余额与查询异常', Icon: TriangleAlert, kind: 'attention' },
    { title: '正在监控', value: enabled, hint: '已启用自动检查', Icon: Activity, kind: 'active' },
  ];
  const filters = [
    { id: 'all', label: '全部站点', count: sites.length },
    { id: 'healthy', label: '正常', count: healthy },
    { id: 'attention', label: '需关注', count: attention },
    { id: 'disabled', label: '已停用', count: sites.length - enabled },
  ];
  const search = query.trim().toLocaleLowerCase();
  const visibleSites = sites.filter((site) => {
    const kind = siteStatus(site, results[site.id]).kind;
    const matchesFilter = filter === 'all' || (filter === 'attention' ? ['warning', 'failed'].includes(kind) : kind === filter);
    return matchesFilter && (!search || `${site.name} ${site.baseUrl}`.toLocaleLowerCase().includes(search));
  });
  const visibleBalanceTotals = new Map();
  let visibleCountedSites = 0;
  for (const site of visibleSites) {
    const result = results[site.id];
    if (!site.enabled || !result?.ok || result.balance == null || !Number.isFinite(Number(result.balance))) continue;
    const currency = String(result.currency || site.currency || '').trim() || '未标注币种';
    visibleBalanceTotals.set(currency, (visibleBalanceTotals.get(currency) || 0) + Number(result.balance));
    visibleCountedSites += 1;
  }
  function clearFilters() { setQuery(''); setFilter('all'); }

  return <div className="dashboard-content">
    <div className="overview-summary">
      <section className="balance-summary" aria-labelledby="balance-summary-title">
        <div className="balance-summary-header"><span className="balance-summary-icon"><WalletCards size={22} strokeWidth={1.6} aria-hidden="true" /></span><span className="balance-summary-tag"><Layers3 size={13} aria-hidden="true" />按币种汇总</span></div>
        <div className="balance-summary-heading"><span>ACCOUNT BALANCE</span><h2 id="balance-summary-title">可用余额总览</h2></div>
        {balanceTotals.size > 0 ? <dl className={`balance-totals ${balanceTotals.size > 1 ? 'multiple-currencies' : ''}`}>
          {[...balanceTotals].map(([currency, amount]) => <div className="balance-total" key={currency}><dt>{currency}</dt><dd>{formatBalance(amount)}</dd></div>)}
        </dl> : <div className="balance-summary-empty"><strong>—</strong><p>{enabled ? '等待站点返回余额数据' : '启用站点后，在这里掌握总余额'}</p></div>}
        <div className="balance-summary-footer"><span>已汇总 <strong>{countedSites}</strong> / {enabled} 个启用站点</span><span><ShieldCheck size={14} aria-hidden="true" />仅统计查询成功的站点</span></div>
      </section>
      <section className="overview-metrics" aria-label="监控统计">{summary.map(({ title, value, hint, Icon, kind }) =>
        <article key={title} className={`overview-metric metric-${kind}`}>
          <div className="metric-heading"><span>{title}</span><span className="metric-icon"><Icon size={19} strokeWidth={1.7} aria-hidden="true" /></span></div>
          <div className="metric-value"><strong>{value}</strong><span>个站点</span></div>
          <div className="metric-caption"><span className="metric-dot" aria-hidden="true" /><small>{hint}</small></div>
        </article>)}
      </section>
    </div>

    <section className="overview-panel" aria-labelledby="balance-panel-title">
      <header className="overview-panel-header"><div className="overview-panel-title"><span className="panel-symbol"><Globe2 size={20} strokeWidth={1.7} aria-hidden="true" /></span><div><h2 id="balance-panel-title">网站余额<span className="panel-site-count">{sites.length}</span></h2><p>集中查看每个站点的余额与运行状态</p></div></div>
        {sites.length > 0 && <button className="primary-button dashboard-add" onClick={onAdd}><Plus size={16} aria-hidden="true" />添加站点</button>}
      </header>
      {!sites.length ? <div className="overview-empty"><span className="overview-empty-icon"><Globe2 size={32} strokeWidth={1.4} aria-hidden="true" /></span><h3>开始监控你的第一个站点</h3><p>添加网站和认证信息，<br />在这里统一查看余额、接收预警。</p><button className="primary-button" onClick={onAdd}><Plus size={16} aria-hidden="true" />添加站点</button><small><ShieldCheck size={14} aria-hidden="true" />凭据仅保存在服务器端</small></div>
        : <>
          <div className="overview-toolbar"><div className="overview-filters" role="group" aria-label="按站点状态筛选">{filters.map((item) => <button key={item.id} className={filter === item.id ? 'overview-filter is-selected' : 'overview-filter'} aria-pressed={filter === item.id} onClick={() => setFilter(item.id)}>{item.label}<span>{item.count}</span></button>)}</div>
            <div className="overview-search"><Search size={16} aria-hidden="true" /><input aria-label="搜索站点" placeholder="搜索站点名称或网址" value={query} onChange={(event) => setQuery(event.target.value)} type="search" />{query && <button aria-label="清空搜索" onClick={() => setQuery('')}><X size={14} aria-hidden="true" /></button>}</div>
          </div>
          {!visibleSites.length ? <div className="overview-empty filtered-empty"><span className="overview-empty-icon"><Search size={27} strokeWidth={1.5} aria-hidden="true" /></span><h3>没有找到匹配的站点</h3><p>试试其他名称、网址，或调整状态筛选。</p><button className="secondary-button" onClick={clearFilters}>清除筛选</button></div>
            : <div className="overview-table-wrap"><table className="overview-table"><caption className="sr-only">网站余额及监控状态</caption><thead><tr>{['站点信息', '可用余额', '预警阈值', '监控状态', '最近检查', '操作'].map((title) => <th key={title} scope="col">{title}</th>)}</tr></thead>
              <tbody>{visibleSites.map((site) => {
                const result = results[site.id];
                const status = siteStatus(site, result);
                const currency = result?.currency || site.currency;
                const siteName = site.name || '未命名站点';
                return <tr key={site.id} className={`overview-row row-${status.kind}`}>
                  <td className="overview-site" data-label="站点"><span className={`site-avatar avatar-${site.siteType}`} aria-hidden="true">{siteName.trim().charAt(0).toLocaleUpperCase()}</span><div className="overview-site-info"><button className="overview-site-link" onClick={() => onEdit(site.id)}>{siteName}</button><span className="overview-site-meta"><span className="site-type-tag">{typeNames[site.siteType] || '自动识别'}</span><span className="overview-site-url" title={site.baseUrl}>{site.baseUrl.replace(/^https?:\/\//, '').replace(/\/$/, '')}</span></span></div></td>
                  <td className="overview-balance" data-label="可用余额"><strong>{result?.ok ? formatBalance(result.balance) : '—'}</strong><span>{currency || '—'}</span></td>
                  <td className="overview-threshold" data-label="预警阈值"><span>{formatBalance(site.threshold)}</span><small>{currency || '—'}</small></td>
                  <td className="overview-status" data-label="监控状态"><Status site={site} result={result} checking={checkingIds.includes(site.id)} />{site.enabled && result?.ok === false && <small className="overview-error" title={result.message}>{result.message || '请检查站点配置'}</small>}{site.enabled && result?.notificationError && <small className="overview-error" title={result.notificationError}>通知失败：{result.notificationError}</small>}</td>
                  <td className="overview-checked" data-label="最近检查"><time dateTime={result?.checkedAt || undefined}>{formatTime(result?.checkedAt)}</time><small><RefreshCw size={11} aria-hidden="true" />每 {formatInterval(site.intervalSeconds)}检查</small></td>
                  <td className="overview-action" data-label="操作"><button className="overview-open" onClick={() => onOpen(site)}>进入网站 <ArrowUpRight size={16} aria-hidden="true" /></button></td>
                </tr>;
              })}</tbody>
              {visibleSites.length > 0 && <tfoot><tr className="overview-total-row"><th scope="row">合计余额</th><td colSpan={5}><div className="overview-total-content">
                <div className="overview-total-amounts">{[...visibleBalanceTotals].length > 0
                  ? [...visibleBalanceTotals].map(([currency, amount]) => <span className="overview-total-amount" key={currency}><small>{currency}</small><strong>{formatBalance(amount)}</strong></span>)
                  : <strong className="overview-total-empty">暂无可汇总余额</strong>}</div>
                <span className="overview-total-caption">已汇总 {visibleCountedSites} 个启用且查询成功的站点</span>
              </div></td></tr></tfoot>}
              </table></div>}
          <footer className="overview-panel-footer"><span role="status">显示 {visibleSites.length} / {sites.length} 个站点{pending > 0 && <span className="overview-pending"> · {pending} 个待检查</span>}</span><span><RefreshCw size={12} aria-hidden="true" />每 15 秒同步检查结果</span></footer>
        </>}
    </section>
    <p className="overview-note"><ShieldCheck size={14} aria-hidden="true" />自动监控在后台持续运行，关闭页面后仍会按配置检查。</p>
  </div>;
}
