/* 剩余积分的两个显示维度：总剩余（全部账号）与可用（不含被禁用/触发护栏的）。
 *
 * 区域卡片的大数字是总剩余——停用账号的积分也算在里面；副行新增「可用 N」，
 * 只算 isAcctActive（启用且未触发保留积分/日限额/积分限额护栏）的账号，与
 * 旁边的 N/M 账号计数同一口径。这里钉住 updateUI 的分维度求和逻辑。
 *
 * Run with Node: node tests/_test_credits_dimensions.js
 */
'use strict';
const assert = require('assert');
const {dashboardHtml, dashboardScript} = require('./_dashboard_source.js');
const dom = require('./_dom_stub.js');

const html = dashboardHtml();
const script = dashboardScript();

/* ---- 结构：两张区域卡片都有可用积分的挂点 --------------------------- */

assert.ok(html.includes('id="tabIntlAvailCred"'), '国际版卡片应有 tabIntlAvailCred');
assert.ok(html.includes('id="tabCnAvailCred"'), '国内版卡片应有 tabCnAvailCred');
assert.ok(html.includes('id="tabIntlCredits"'), '国际版卡片应保留总剩余挂点');
assert.ok(!/0\/0 可用/.test(html), '旧文案「0/0 可用」应已改为「0/0 账号」');

/* ---- 行为：updateUI 按维度求和 ------------------------------------- */

dom.installDom({fetch: () => Promise.resolve({ok: true, status: 200,
  json: () => Promise.resolve({}), text: () => Promise.resolve('')})});

const realLog = console.log;
console.log = () => {};
let api;
try {
  api = new Function(script + `
    return { updateUI: updateUI };`)();
} catch(e) {
  console.log = realLog;
  console.error('脚本求值失败:', e.message);
  process.exit(1);
}
console.log = realLog;

const el = id => document.getElementById(id);

window.ACCOUNTS = [
  // 国际版：正常 100 + 停用 50 + 日限额 25 + 积分限额 5 → 总 180 / 可用 100
  {uid: 'i1', realm: 'intl', enabled: true,  credits: {remain: 100}},
  {uid: 'i2', realm: 'intl', enabled: false, credits: {remain: 50}},
  {uid: 'i3', realm: 'intl', enabled: true,  credits: {remain: 25}, dailyLimitBlocked: true},
  {uid: 'i4', realm: 'intl', enabled: true,  credits: {remain: 5},  creditLimitReached: true},
  // 没有积分数据的账号不参与求和，也不把「可用」打成 0
  {uid: 'i5', realm: 'intl', enabled: true,  credits: null},
  // 国内版：正常 200 + 停用 30 → 总 230 / 可用 200
  {uid: 'c1', realm: 'cn', enabled: true,  credits: {remain: 200}},
  {uid: 'c2', realm: 'cn', enabled: false, credits: {remain: 30}},
];

api.updateUI();

function text(id){ const n = el(id); return n ? String(n.textContent) : ''; }
/* innerHTML 设置在假 DOM 上不反映到 textContent，走 innerHTML 读。 */
function inner(id){ const n = el(id); return n ? String(n.innerHTML) : ''; }

assert.strictEqual(text('tabIntlCredits'), '180', '国际版总剩余应为全部账号之和');
assert.ok(inner('tabIntlAvailCred').includes('100'), '国际版可用应排除停用与触发护栏的账号: ' + inner('tabIntlAvailCred'));
assert.ok(inner('tabIntlAvailCred').includes('可用'), '可用挂点应带「可用」标签');
assert.ok(inner('tabIntlAccts').includes('>2</b>/5'), '账号计数应与可用同口径（2/5）: ' + inner('tabIntlAccts'));
assert.ok(inner('tabIntlAccts').includes('账号'), '账号计数应以「账号」结尾');

assert.strictEqual(text('tabCnCredits'), '230', '国内版总剩余应为全部账号之和');
assert.ok(inner('tabCnAvailCred').includes('200'), '国内版可用应排除停用账号: ' + inner('tabCnAvailCred'));
assert.ok(inner('tabCnAccts').includes('>1</b>/2'), '国内版账号计数应为 1/2');

/* 全都没有积分数据时显示「—」，可用也不显示成 0 */
window.ACCOUNTS = [{uid: 'x1', realm: 'intl', enabled: true, credits: null}];
api.updateUI();
assert.strictEqual(text('tabIntlCredits'), '—', '没有积分数据时总剩余显示 —');
assert.ok(inner('tabIntlAvailCred').includes('—'), '没有积分数据时可用显示 —');

console.log('_test_credits_dimensions.js: all assertions passed');
