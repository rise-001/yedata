import { describe, expect, it } from 'vitest';
import { hydrateSite, mergeResults, presets, serializeSite, siteStatus, validateSites } from './sites.js';

const site = hydrateSite({ id: 'alpha', name: 'Alpha', baseUrl: 'https://example.com', authMode: 'token', hasToken: true });

describe('站点配置与余额状态', () => {
  it('保存空凭据以复用服务器上的密钥，且不传递界面状态', () => {
    const data = serializeSite({ ...site, _new: true, balanceDivisor: '', intervalSeconds: 10 });
    expect(data).not.toHaveProperty('_new');
    expect(data).toMatchObject({ token: '', hasToken: true, balanceDivisor: null, intervalSeconds: 30 });
    expect(() => validateSites([data])).not.toThrow();
  });
  it.each(['password', 'token', 'cookie'])('拒绝缺少凭据的 %s 认证', (authMode) => {
    expect(() => validateSites([serializeSite({ ...site, authMode, hasToken: false })])).toThrow('认证信息');
  });
  it('保存密码时保留前后空格', () => {
    expect(serializeSite({ ...site, password: ' secret ' }).password).toBe(' secret ');
  });
  it('拒绝危险协议和无效数字', () => {
    expect(() => validateSites([serializeSite({ ...site, baseUrl: 'javascript:alert(1)' })])).toThrow('HTTP');
    expect(() => validateSites([serializeSite({ ...site, balanceMultiplier: -1 })])).toThrow('余额倍率');
    expect(() => validateSites([serializeSite({ ...site, threshold: 'bad' })])).toThrow('预警阈值');
  });
  it('区分预警、失败、停用和未检查，零余额正确触发预警', () => {
    expect(siteStatus(site, { ok: true, balance: 0 }).kind).toBe('warning');
    expect(siteStatus(site, { ok: true, balance: 150 }).kind).toBe('healthy');
    expect(siteStatus(site, { ok: false }).kind).toBe('failed');
    expect(siteStatus({ ...site, enabled: false }, { ok: false }).kind).toBe('disabled');
    expect(siteStatus(site, null).kind).toBe('pending');
  });
  it('后台旧结果不会覆盖手动检查的新结果', () => {
    const recent = { ok: true, balance: 50, checkedAt: '2026-10-21T02:00:00Z' };
    expect(mergeResults({ alpha: recent }, { alpha: { ok: true, balance: 60, checkedAt: '2026-10-21T01:00:00Z' } }).alpha).toEqual(recent);
  });
  it('切换为 New API 时清除 Sub2 用量接口', () => {
    expect({ ...presets.sub2, ...presets.newapi }).toMatchObject({ usagePath: '', balanceDivisor: 500000, currency: 'USD' });
  });
});
