/* 账号行要能一眼看出档位（#505）。
 *
 * 档位读自上游计费摘要（`IsPaidUser` / `SubscriptionPackageName`），是唯一可信
 * 来源：`modelTiers` 的 Pro Only 名单会误判，11158 只能证明「这个号不能用这个
 * 模型」。结论落在账号 json 的 `tier` 上，面板此前只在「积分与套餐明细」弹窗里
 * 露出来，列表层看不到——运营要逐个点开弹窗才知道哪些号是免费档。
 *
 * 胶囊文字用上游包名原样（国际版 `Free`、国内版 `体验版`），与 workbuddy 官方
 * 叫法一致，不自造「免费 / 付费」标签；`tier` 为空 = 还没读过，那是「未知」而不是
 * 免费，所以一个胶囊都不画（企业号与被风控限制的号也是空）。
 *
 * Run with Node: node tests/_test_account_tier_badge.js
 */
'use strict';
const assert = require('assert');
const {dashboardScript} = require('./_dashboard_source.js');
const dom = require('./_dom_stub.js');

const script = dashboardScript();
dom.installDom({
  fetch: () => Promise.resolve({
    status: 200, ok: true,
    json: () => Promise.resolve({}),
    text: () => Promise.resolve('{}'),
  }),
});

const api = new Function(script + `
  return {accountRow: accountRow};`)();

const NOW = Math.floor(Date.now() / 1000);
const credits = {remain: 562.47, size: 650, updated_at: NOW};
const row = extra => api.accountRow(Object.assign(
  {uid: 'acct-0001', nickname: '某账号', realm: 'intl', enabled: true, credits: credits},
  extra));

function check(label, ok, detail) {
  assert.ok(ok, label + (detail === undefined ? '' : '  -> ' + detail));
}

/* ---- 1. 包名原样显示，国际版 / 国内版各一 --------------------------------- */

const freeIntl = row({tier: {paid: false, package: 'Free', checked_at: NOW, source: 'billing'}});
check('国际版免费号要显示包名 Free', freeIntl.includes('>Free<'), freeIntl);
check('免费号胶囊不带 ok 高亮', !freeIntl.includes('class="badge mini ok"'), freeIntl);

const freeCn = row({realm: 'cn', tier: {paid: false, package: '体验版', checked_at: NOW, source: 'billing'}});
check('国内版免费号要显示包名 体验版', freeCn.includes('体验版'), freeCn);

const paid = row({tier: {paid: true, package: 'Pro', checked_at: NOW, source: 'billing'}});
check('付费号显示包名', paid.includes('>Pro<'), paid);
check('付费号胶囊带 ok 高亮', paid.includes('class="badge mini ok"'), paid);

/* ---- 2. 胶囊的位置：版本格里、区域徽章下方 ------------------------------- */

const realmTdIdx = freeIntl.indexOf('data-label="版本"');
const acctTdIdx = freeIntl.indexOf('data-label="账号"');
const tierIdx = freeIntl.indexOf('>Free<');
check('胶囊落在版本格', tierIdx > realmTdIdx && tierIdx < acctTdIdx, freeIntl);
check('胶囊排在区域徽章后面', tierIdx > freeIntl.indexOf('国际版'), freeIntl);
check('胶囊在「禁用模型」那行之前（不挤在账号格底部）',
  tierIdx < freeIntl.indexOf('+ 禁用模型'), freeIntl);

const creditCell = freeIntl.slice(freeIntl.indexOf('data-label="积分"'), freeIntl.indexOf('data-label="请求"'));
check('积分格里不再出现档位胶囊', !creditCell.includes('Free'), creditCell);

const mobileGroup = (freeIntl.match(/<span class="acct-mobile-badges">[\s\S]*?<\/span><\/div>/) || [''])[0];
check('窄屏卡片视图也带上档位（移动端徽章组）', mobileGroup.includes('Free'), mobileGroup);

const noCredits = api.accountRow({
  uid: 'acct-0002', nickname: '未查询号', realm: 'intl', enabled: true,
  tier: {paid: false, package: 'Free', checked_at: NOW},
});
check('积分未查询时档位胶囊照常显示', noCredits.includes('>Free<'), noCredits);

/* ---- 3. 悬停提示带上读取时刻（i18n 靠整句/片段命中） ---------------------- */

check('悬停写明会员类型', /title="会员类型: Free/.test(freeIntl), freeIntl);
check('悬停带上读取时刻', freeIntl.includes('读取于'), freeIntl);

const noStamp = row({tier: {paid: false, package: 'Free'}});
check('没有 checked_at 时只写包名，不编造时间',
  noStamp.includes('>Free<') && !noStamp.includes('读取于'), noStamp);

/* ---- 4. 未知档位不画任何胶囊（空 = 未知，不是免费） ---------------------- */

for (const [label, extra] of [
  ['没有 tier 字段', {}],
  ['tier 是 null', {tier: null}],
  ['tier 是空对象', {tier: {}}],
  ['包名为空串', {tier: {paid: false, package: ''}}],
  ['tier 不是对象', {tier: 'Free'}],
]) {
  const out = row(extra);
  check(label + '时不画档位胶囊', !out.includes('会员类型'), out);
}

/* ---- 5. 上游包名要转义，不能当 HTML 用 ---------------------------------- */

const nasty = row({tier: {paid: false, package: '<b>"x"</b>', checked_at: NOW}});
check('包名里的尖括号要转义', nasty.includes('&lt;b&gt;') && !nasty.includes('会员类型: <b>'), nasty);

console.log('account tier badge assertions passed');
