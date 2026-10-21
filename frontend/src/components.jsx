import { useEffect, useState } from 'react';
import { ChevronDown, ChevronUp, ExternalLink, Plus, Power, RefreshCw, Trash2 } from 'lucide-react';
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

export function SiteCard({ site, result, index, busy, checking, selected, onChange, onCheck, onToggle, onDelete, onOpen }) {
  const [expanded, setExpanded] = useState(Boolean(site._new || selected));
  const [advanced, setAdvanced] = useState(Boolean(site._new));
  useEffect(() => { if (selected) setExpanded(true); }, [selected]);
  const update = (key) => (value) => onChange(site.id, { [key]: value });
  const field = (key, label, props = {}) => <Field label={label} value={site[key]} onChange={update(key)} {...props} />;
  const showUserId = site.authMode !== 'cookie' && (site.siteType === 'newapi' || site.authMode === 'token'
    || (site.siteType === 'auto' && (site.mePath === '/api/user/self' || site.balanceField === 'quota')));
  const secretPlaceholder = (key) => site[`has${key[0].toUpperCase()}${key.slice(1)}`]
    ? '已安全保存，留空保持不变' : '输入凭据';
  return <article id={`site-${site.id}`} className={`site-card ${!site.enabled ? 'muted-card' : ''} ${selected ? 'selected-card' : ''}`}>
    <header className="site-header">
      <div className="site-title"><span className="section-index">{String(index + 1).padStart(2, '0')}</span><h3>{site.name || '未命名站点'}</h3><Status site={site} result={result} checking={checking} />
        {!expanded && <span className="mini-balance">{result?.ok ? formatBalance(result.balance) : '—'} <small>{result?.currency || site.currency}</small></span>}</div>
      <div className="site-actions">
        <button className="text-button" aria-expanded={expanded} aria-controls={`fields-${site.id}`} onClick={() => setExpanded(!expanded)}>{expanded ? '收起' : '展开'}</button>
        <button className="text-button" disabled={busy} onClick={() => onOpen(site)}>进入 <ExternalLink size={12} aria-hidden="true" /></button>
        <button className="text-button" disabled={busy || !site.enabled} onClick={() => onCheck(site.id)}><RefreshCw size={12} aria-hidden="true" />检查</button>
        <button className="text-button" disabled={busy} onClick={() => onToggle(site)}><Power size={12} aria-hidden="true" />{site.enabled ? '停用' : '启用'}</button>
        <button className="text-button danger" disabled={busy} onClick={() => onDelete(site)}><Trash2 size={12} aria-hidden="true" />删除</button>
      </div>
    </header>
    {expanded && <div id={`fields-${site.id}`}>
      <div className="site-result"><div><small>可用余额</small><strong>{result?.ok ? formatBalance(result.balance) : '—'} <em>{result?.currency || site.currency}</em></strong><small>{result?.displayName || result?.email || '等待连接'}</small></div>
        <div><small>最近状态</small><p>{!site.enabled ? '监控已停止' : result?.ok ? result.notified ? '已发送预警' : siteStatus(site, result).text : result?.message || '等待检查'}</p>
          <small className={result?.notificationError ? 'error-text' : ''}>{result?.notificationError ? `通知失败：${result.notificationError}` : formatTime(result?.checkedAt)}</small></div></div>
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
          {field('threshold', '预警阈值', { type: 'number', min: 0, step: '0.01' })}
          {field('intervalSeconds', '查询间隔（秒）', { type: 'number', min: 30, max: 86400, step: 1 })}
          {field('balanceMultiplier', '余额倍率', { type: 'number', min: '0.000001', step: 'any' })}
          {advanced && <>
            <Field label="时区"><select value={site.timezone} onChange={(event) => update('timezone')(event.target.value)}>{[...new Set([site.timezone, 'Asia/Shanghai', 'UTC', 'Asia/Tokyo'])].map((zone) => <option key={zone}>{zone}</option>)}</select></Field>
            {field('loginPath', '登录路径')}{field('mePath', '余额路径')}{field('usagePath', '用量接口', { placeholder: '留空表示不查询', wide: true })}
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
  const statuses = sites.map((site) => siteStatus(site, results[site.id]));
  const counts = [sites.length, statuses.filter((s) => s.kind === 'healthy').length,
    statuses.filter((s) => ['warning', 'failed'].includes(s.kind)).length,
    sites.filter((site) => site.enabled).length];
  const summary = [['监控站点', '已配置站点'], ['正常余额', '高于预警阈值'], ['需要关注', '余额预警或检查失败'], ['正在监控', '后端持续自动检查']];
  return <>
    <section className="summary-grid" aria-label="监控统计">{summary.map(([title, hint], index) => <article key={title} className={`summary-card ${index === 2 ? 'warning-card' : index === 3 ? 'accent-card' : ''}`}><span>{title}</span><strong>{counts[index]}</strong><small>{hint}</small></article>)}</section>
    <section className="balance-dashboard"><header className="panel-header"><div><span className="section-index">看板</span><h2>网站余额</h2></div><small>每 15 秒同步检查结果</small></header>
      {!sites.length ? <div className="empty-state"><span className="empty-icon">◈</span><h3>开始监控你的第一个站点</h3><p>添加网站和认证信息，余额与预警状态会显示在这里。</p><button className="primary-button" onClick={onAdd}><Plus size={15} aria-hidden="true" />添加站点</button></div>
        : <div className="table-wrap"><table><thead><tr>{['网站', '可用余额', '预警线', '状态', '查询间隔', '最近检查', '操作'].map((title) => <th key={title}>{title}</th>)}</tr></thead>
          <tbody>{sites.map((site) => { const result = results[site.id]; const currency = result?.currency || site.currency; return <tr key={site.id} className={!site.enabled ? 'disabled-row' : ''}>
            <td data-label="网站"><button className="site-link" onClick={() => onEdit(site.id)}>{site.name}<em>{typeNames[site.siteType] || '自动识别'}</em></button><small className="site-url">{site.baseUrl}</small></td>
            <td data-label="可用余额" className="balance-cell">{result?.ok ? formatBalance(result.balance) : '—'} <small>{currency}</small></td>
            <td data-label="预警线">{formatBalance(site.threshold)} <small>{currency}</small></td>
            <td data-label="状态" title={result?.message}><Status site={site} result={result} checking={checkingIds.includes(site.id)} /></td>
            <td data-label="查询间隔">{formatInterval(site.intervalSeconds)}</td><td data-label="最近检查">{formatTime(result?.checkedAt)}</td>
            <td data-label="操作"><button className="secondary-button small-button" onClick={() => onOpen(site)}>进入网站 <ExternalLink size={12} aria-hidden="true" /></button></td>
          </tr>; })}</tbody></table></div>}
    </section>
  </>;
}
