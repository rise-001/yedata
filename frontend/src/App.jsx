import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowRight, LayoutDashboard, LogOut, Plus, RefreshCw, ScanLine, Settings, X } from 'lucide-react';
import { api, send } from './api.js';
import { Brand, Dashboard, Field, SiteCard } from './components.jsx';
import { formatTime, hydrateSite, mergeResults, newSite, serializeSite, validateSites } from './sites.js';

function Login({ onLogin, expired }) {
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(expired ? '登录已过期，请重新登录' : '');
  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try { await send('login', 'POST', { username: username.trim(), password }); onLogin(); }
    catch (error) { setError(error.message); }
    finally { setBusy(false); }
  }
  return <main className="login-layout"><section className="login-intro"><Brand /><div className="login-copy"><span className="eyebrow">KEEP YOUR BALANCE IN SIGHT</span><h1>每一份余额，<br />心中有数。</h1><p>一个面板，关注所有站点。<br />让自动监控和及时预警，照看每一次用量。</p><div className="login-decoration" aria-hidden="true"><span>余额监控</span><strong>持续在线 <i /></strong><div /><div /><div /></div></div><small>多站点余额监控 · 企业微信预警</small></section>
    <section className="login-form-area"><form className="login-card" onSubmit={submit}><span className="eyebrow">欢迎回来</span><h2>登录管理后台</h2><p>使用管理员账号和密码进入工作区。</p><fieldset disabled={busy}>
      <Field label="管理员账号" value={username} onChange={setUsername} autoComplete="username" autoFocus required />
      <Field label="管理员密码" value={password} onChange={setPassword} type="password" autoComplete="current-password" required />
      <div className="login-error" role="alert">{error}</div><button className="primary-button" type="submit">{busy ? '正在登录…' : '登录'} <ArrowRight size={16} aria-hidden="true" /></button>
    </fieldset><small className="login-hint">首次部署的管理员密码可在服务启动日志中查看。</small></form></section></main>;
}

function Workspace({ onLogout }) {
  const [view, setView] = useState(location.hash === '#sites' ? 'sites' : 'dashboard');
  const [sites, setSites] = useState([]);
  const [results, setResults] = useState({});
  const [webhook, setWebhook] = useState('');
  const [hasWebhook, setHasWebhook] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [syncError, setSyncError] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState('');
  const [checkingIds, setCheckingIds] = useState([]);
  const [selected, setSelected] = useState(null);
  const [toast, setToast] = useState(null);
  const [retry, setRetry] = useState(0);
  const refreshInFlight = useRef(false);
  const operation = useRef(false);

  const notify = useCallback((message, error = false) => setToast({ message, error }), []);
  useEffect(() => { if (!toast) return; const timer = setTimeout(() => setToast(null), 4500); return () => clearTimeout(timer); }, [toast]);

  useEffect(() => {
    const controller = new AbortController();
    setLoadError('');
    api('sites', { signal: controller.signal }).then(({ data }) => {
      setSites(data.sites.map(hydrateSite));
      setResults(data.results);
      setHasWebhook(Boolean(data.settings.hasWebhook));
      setLoaded(true);
    }).catch((error) => { if (!controller.signal.aborted) setLoadError(error.message); });
    return () => controller.abort();
  }, [retry]);

  const refresh = useCallback(async () => {
    if (!loaded || document.hidden || refreshInFlight.current || operation.current) return;
    refreshInFlight.current = true;
    try {
      const { data } = await api('sites');
      setResults((current) => mergeResults(current, data.results));
      setSyncError(false);
    } catch { setSyncError(true); }
    finally { refreshInFlight.current = false; }
  }, [loaded]);

  useEffect(() => {
    const interval = setInterval(refresh, 15000);
    const visible = () => { if (!document.hidden) refresh(); };
    document.addEventListener('visibilitychange', visible);
    return () => { clearInterval(interval); document.removeEventListener('visibilitychange', visible); };
  }, [refresh]);

  useEffect(() => {
    const onHash = () => setView(location.hash === '#sites' ? 'sites' : 'dashboard');
    const onUnload = (event) => { if (dirty) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('hashchange', onHash);
    window.addEventListener('beforeunload', onUnload);
    return () => { window.removeEventListener('hashchange', onHash); window.removeEventListener('beforeunload', onUnload); };
  }, [dirty]);

  useEffect(() => { if (view === 'sites' && selected) document.getElementById(`site-${selected}`)?.scrollIntoView?.({ behavior: 'smooth', block: 'center' }); }, [view, selected]);

  function navigate(next) { setView(next); history.replaceState(null, '', `/#${next}`); }
  function edit(id) { setSelected(id); navigate('sites'); }
  function change(id, update) { if (operation.current) return; setSites((current) => current.map((site) => site.id === id ? { ...site, ...update } : site)); setDirty(true); }
  function add() {
    if (operation.current) return;
    if (sites.length >= 100) return notify('最多可添加 100 个站点', true);
    const site = newSite(sites.length + 1);
    setSites((current) => [...current, site]); setDirty(true); edit(site.id);
  }
  function remove(site) {
    if (!window.confirm(`删除“${site.name}”？保存配置后生效。`)) return;
    setSites((current) => current.filter((item) => item.id !== site.id)); setDirty(true);
  }
  function open(site) {
    try {
      const url = new URL(site.baseUrl);
      if (!['http:', 'https:'].includes(url.protocol)) throw new Error();
      window.open(url.href, '_blank', 'noopener,noreferrer');
    } catch { notify('请先填写有效的网站地址', true); }
  }

  async function save(nextSites = sites) {
    const payload = nextSites.map(serializeSite);
    validateSites(payload);
    const { data } = await send('sites', 'PUT', { sites: payload, settings: { webhook: webhook.trim() } });
    const saved = data.sites.map(hydrateSite);
    setSites(saved); setWebhook(''); setHasWebhook(Boolean(data.settings.hasWebhook)); setDirty(false);
    return saved;
  }

  async function run(name, action) {
    if (operation.current) return;
    operation.current = true; setBusy(name);
    try { await action(); }
    catch (error) { notify(error.message, true); }
    finally { operation.current = false; setBusy(''); setCheckingIds([]); }
  }

  function check(id) {
    const ids = sites.filter((site) => site.enabled && (!id || site.id === id)).map((site) => site.id);
    if (!ids.length) return notify('没有已启用的站点，请先添加或启用站点', true);
    run('check', async () => {
      const saved = await save();
      setCheckingIds(ids);
      const { data } = await send('check-many', 'POST', { sites: saved.filter((site) => ids.includes(site.id)).map(serializeSite) });
      setResults((current) => mergeResults(current, Object.fromEntries(data.map((result) => [result.siteId, result]))));
      const failed = data.filter((result) => !result.ok).length;
      notify(failed ? `${data.length - failed} 个完成，${failed} 个失败` : `${data.length} 个站点检查完成`, failed > 0);
    });
  }

  function toggle(site) {
    run('save', async () => {
      await save(sites.map((item) => item.id === site.id ? { ...item, enabled: !item.enabled } : item));
      notify(site.enabled ? '站点已停用' : '站点已启用');
    });
  }

  function testNotification() {
    if (!webhook.trim() && !hasWebhook) return notify('请先填写企业微信 Webhook', true);
    run('notify', async () => { await send('notify-test', 'POST', { webhook: webhook.trim() }); notify('测试通知已发送'); });
  }

  async function logout() {
    if (dirty && !window.confirm('还有未保存的配置，确定退出？')) return;
    run('logout', async () => { await api('logout', { method: 'POST' }); onLogout(); });
  }
  const latest = Object.values(results).filter((item) => item?.checkedAt).sort((a, b) => Date.parse(b.checkedAt) - Date.parse(a.checkedAt))[0]?.checkedAt;

  return <div className={`app-shell ${view === 'dashboard' ? 'dashboard-shell' : ''}`}><aside className="sidebar"><Brand /><span className="side-label">工作区</span><nav aria-label="主导航">
    <a href="/#dashboard" className={view === 'dashboard' ? 'nav-item active' : 'nav-item'} aria-current={view === 'dashboard' ? 'page' : undefined} onClick={(event) => { event.preventDefault(); navigate('dashboard'); }}><LayoutDashboard size={17} aria-hidden="true" />余额概览</a>
    <a href="/#sites" className={view === 'sites' ? 'nav-item active' : 'nav-item'} aria-current={view === 'sites' ? 'page' : undefined} onClick={(event) => { event.preventDefault(); navigate('sites'); }}><Settings size={17} aria-hidden="true" />站点设置</a>
    </nav><div className="sidebar-footer"><i className="online-dot" /><div>后台持续监控<small>凭据仅保存在服务端</small></div></div></aside>
    <main className="main-content"><header className="topbar"><div><span className="eyebrow">{view === 'dashboard' ? 'BALANCE OVERVIEW' : 'MULTI-SITE BALANCE MONITOR'}</span><h1>{view === 'dashboard' ? '余额概览' : '站点设置'}</h1>{view === 'dashboard' && <p className="overview-intro">每一份余额，尽在掌握。</p>}</div><div className="top-actions"><small className="last-sync">{view === 'dashboard' && <span>最近检查</span>}{formatTime(latest)}</small><button className="text-button" disabled={Boolean(busy)} onClick={logout}><LogOut size={14} aria-hidden="true" />退出</button><button className="icon-button" title="刷新余额数据" aria-label="刷新余额数据" disabled={!loaded || Boolean(busy)} onClick={() => view === 'sites' ? check() : refresh()}><RefreshCw size={17} aria-hidden="true" /></button></div></header>
      {syncError && <div className="notice error-notice" role="status">暂时无法同步检查结果，将自动重试。<button className="text-button" onClick={refresh}>立即重试</button></div>}
      {dirty && <div className="notice" role="status">配置有修改，请保存以应用到后台监控。<button className="text-button" disabled={Boolean(busy)} onClick={() => run('save', async () => { await save(); notify('配置已保存，后台监控已生效'); })}>保存配置</button></div>}
      {!loaded ? <div className="empty-state" role="status">{loadError ? <><p className="error-text">{loadError}</p><button className="secondary-button" onClick={() => setRetry((value) => value + 1)}>重新加载</button></> : '正在加载站点配置…'}</div>
        : view === 'dashboard' ? <Dashboard sites={sites} results={results} checkingIds={checkingIds} onEdit={edit} onOpen={open} onAdd={add} />
          : <><section className="section-toolbar"><div><span className="section-index">设置</span><h2>监控站点</h2><small>{dirty ? '有未保存的修改' : '配置已同步'}</small></div><div className="toolbar-actions">
            <button className="secondary-button" disabled={Boolean(busy)} onClick={add}><Plus size={15} aria-hidden="true" />添加站点</button>
            <button className="secondary-button" disabled={Boolean(busy)} onClick={() => run('save', async () => { await save(); notify('配置已保存，后台监控已生效'); })}>{busy === 'save' ? '保存中…' : '保存配置'}</button>
            <button className="primary-button" disabled={Boolean(busy)} onClick={() => check()}><ScanLine size={15} aria-hidden="true" />{busy === 'check' ? '检查中…' : '检查全部'}</button>
          </div></section>
          <section className="notification-panel"><div><h3>企业微信通知</h3><p>所有站点共用此 Webhook，预警可按站点独立设置。</p></div><fieldset disabled={Boolean(busy)}><Field label="全局 Webhook" value={webhook} onChange={(value) => { setWebhook(value); setDirty(true); }} type="password" autoComplete="off" placeholder={hasWebhook ? '已安全保存，留空保持不变' : 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?...'} /></fieldset><button className="secondary-button" disabled={Boolean(busy)} onClick={testNotification}>{busy === 'notify' ? '发送中…' : '测试通知'}</button></section>
          {!sites.length && <div className="empty-state"><p>还没有监控站点，添加一个开始吧。</p><button className="primary-button" disabled={Boolean(busy)} onClick={add}><Plus size={15} aria-hidden="true" />添加站点</button></div>}
          {sites.map((site, index) => <SiteCard key={site.id} site={site} index={index} result={results[site.id]} busy={Boolean(busy)} checking={checkingIds.includes(site.id)} selected={selected === site.id} onChange={change} onCheck={check} onToggle={toggle} onDelete={remove} onOpen={open} />)}
          </>}
      <footer className="page-footer"><span>余额哨兵 · 多站点监控</span><span><i className="online-dot" />{syncError ? '等待恢复连接' : loaded ? '已连接服务端' : '正在连接'}</span></footer>
    </main>{toast && <div className={`toast ${toast.error ? 'error-toast' : ''}`} role={toast.error ? 'alert' : 'status'}>{toast.message}<button aria-label="关闭提示" onClick={() => setToast(null)}><X size={17} aria-hidden="true" /></button></div>}</div>;
}

export default function App() {
  const [authenticated, setAuthenticated] = useState(null);
  const [error, setError] = useState('');
  const [expired, setExpired] = useState(new URLSearchParams(location.search).get('reason') === 'expired');
  const [retry, setRetry] = useState(0);
  function setSession(value) {
    setAuthenticated(value);
    history.replaceState(null, '', value ? `/${location.hash}` : '/login.html');
    document.title = value ? '余额哨兵' : '登录 · 余额哨兵';
  }
  useEffect(() => {
    const controller = new AbortController();
    setError('');
    api('session', { signal: controller.signal }, true).then((result) => setSession(result.ok))
      .catch((error) => { if (!controller.signal.aborted) setError(error.message); });
    const onExpired = () => { setExpired(true); setSession(false); };
    window.addEventListener('session-expired', onExpired);
    return () => { controller.abort(); window.removeEventListener('session-expired', onExpired); };
  }, [retry]);
  if (authenticated === null) return <main className="startup-screen"><Brand /><p role="status">{error || '正在连接工作区…'}</p>{error && <button className="secondary-button" onClick={() => setRetry((value) => value + 1)}>重新连接</button>}</main>;
  return authenticated ? <Workspace onLogout={() => { setExpired(false); setSession(false); }} /> : <Login expired={expired} onLogin={() => { setExpired(false); setSession(true); }} />;
}
