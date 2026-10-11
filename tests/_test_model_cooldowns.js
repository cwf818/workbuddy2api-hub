/* Exercise the account row renderer shipped in dashboard.html. Run with Node. */
const assert = require('assert');
const {dashboardScript} = require('./_dashboard_source.js');

const script = dashboardScript();
// One shared fake DOM for every dashboard suite: tests/_dom_stub.js.
const dom = require('./_dom_stub.js');
dom.installDom({
  fetch: () => Promise.resolve({ok: true, status: 200, json: () => Promise.resolve({})}),
});

const {accountRow, coolPills, fmtCoolAt, disabledRows, renderDisabled} = new Function(script + `
  return {accountRow, coolPills, fmtCoolAt, disabledRows, renderDisabled};`)();
const base = overrides => Object.assign({
  uid: 'uid-cn-0001', nickname: 'synthetic', realm: 'cn', enabled: true,
  source: 'oauth', expiresIn: '24 hours', lastError: '', inCooldown: false,
  cooldownFor: null, machineId: '', credits: null, product: 'cli',
}, overrides);
const until = Math.floor(Date.now() / 1000) + 600;
const d = new Date(until * 1000);
const pad = n => String(n).padStart(2, '0');
const fmtAt = epoch => {
  const dd = new Date(epoch * 1000);
  return dd.getFullYear() + '-' + pad(dd.getMonth() + 1) + '-' + pad(dd.getDate())
    + ' ' + pad(dd.getHours()) + ':' + pad(dd.getMinutes());
};
const localTime = fmtAt(until);

assert.equal(coolPills(base({})), '');
assert.equal(coolPills(base({modelCooldowns: []})), '');
assert.equal(fmtCoolAt(until), localTime);
assert.equal(fmtCoolAt('bad-value'), '');

const row = accountRow(base({modelCooldowns: [{model: 'glm-5.3', expiresAt: until}]}));
assert(row.includes('glm-5.3 · ' + localTime + ' 恢复'));
assert(row.includes('时间为本地时间'));
assert(row.includes('>可用</span>')); // The account still serves other models.
assert(!row.includes('冷却 600s'));
const throttled = accountRow(base({lastError: 'HTTP 429 (model throttled)',
  modelCooldowns: [{model: 'glm-5.3', expiresAt: until}]}));
assert(!throttled.includes('HTTP 429 (model throttled)'));
const otherError = accountRow(base({lastError: 'HTTP 401',
  modelCooldowns: [{model: 'glm-5.3', expiresAt: until}]}));
assert(otherError.includes('HTTP 401'));

const multi = accountRow(base({modelCooldowns: [
  {model: 'glm-5.2', expiresAt: until}, {model: 'glm-5.3', expiresAt: until + 60},
]}));
assert(multi.indexOf('glm-5.2') < multi.indexOf('glm-5.3'));
assert.equal((multi.match(/class="cool-pill"/g) || []).length, 2);

// 6004 撞线（cap 持久化，重启加载后仍在）的 pill：撞线时间 + 恢复时间都要画。
const hit = until - 7200;
const capPill = accountRow(base({modelCooldowns: [
  {model: 'hy3', expiresAt: until, cappedAt: hit},
]}));
assert(capPill.includes('hy3 · 撞线 ' + fmtAt(hit) + ' · 恢复 ' + localTime));
assert(capPill.includes('上游判定额度用满'));
assert(capPill.includes('>可用</span>'));  // 同账号其他模型照常。
// 撞线时间坏值时回退成普通 pill，不能画成「撞线 」。
const badCap = accountRow(base({modelCooldowns: [
  {model: 'hy3', expiresAt: until, cappedAt: 'bad-value'},
]}));
assert(badCap.includes('hy3 · ' + localTime + ' 恢复'));
assert(!badCap.includes('撞线'));

// 「当前禁用账号与模型」：撞线行在「撞线时间」列画出撞线时刻，恢复时间列照旧；
// 没有撞线记录的临时限流窗口该列留空（渲染成「—」）。
window.ACCOUNTS = [base({modelCooldowns: [
  {model: 'hy3', expiresAt: until, cappedAt: hit},
  {model: 'glm-5.3', expiresAt: until},
]})];
window.VIEW_REALM = 'cn';
const disabled = disabledRows();
const capEntry = disabled.find(r => r.scope === 'hy3');
const plainEntry = disabled.find(r => r.scope === 'glm-5.3');
assert.equal(capEntry.cappedAt, fmtAt(hit));
assert.equal(capEntry.until, localTime);
assert.equal(plainEntry.cappedAt, '');
assert.equal(plainEntry.until, localTime);
renderDisabled();
const table = dom.byId('disabledList').innerHTML;
assert(table.includes('撞线时间'), '禁用总览表头带撞线时间列');
assert(table.includes('hy3') && table.includes(fmtAt(hit)), '撞线行画出撞线时间');

const unsafe = accountRow(base({modelCooldowns: [
  {model: '"><img src=x onerror=alert(1)>', expiresAt: until},
  {model: 'invalid', expiresAt: 'bad-value'},
]}));
assert(!unsafe.includes('<img src=x'));
assert(unsafe.includes('&quot;&gt;&lt;img'));
assert(!unsafe.includes('invalid ·'));

console.log('model cooldown dashboard assertions passed');
