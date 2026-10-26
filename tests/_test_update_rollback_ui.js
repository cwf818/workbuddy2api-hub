/* 设置页「版本回退」卡片的契约。
 *
 * 这一片是纯前端消费：后端 GET /updates/backups 给出「有哪些本地备份、哪些
 * 完整」，POST /updates/rollback 只负责排队。这里钉住五件事：
 *
 *   1. 备份表按后端给的行渲染，版本/时间/文件数/完整性都在，不完整的行写出原因；
 *   2. 不可点的行必须真的 disabled：备份不完整、就是当前版本、更新服务没装、
 *      已有更新任务在跑——宁可少一个按钮，也不给一个必然失败的入口；
 *   3. 空列表要能自解释：份数设成 0 与「还没更新过」是两种不同的话；
 *   4. 回退必须先经过人工确认，取消确认不得提交请求；
 *   5. 「保留份数」走既有 /settings/save，提交的是整数，不是字符串。
 *
 * Run with Node: node tests/_test_update_rollback_ui.js
 */
'use strict';

const assert = require('assert');
const {dashboardScript} = require('./_dashboard_source.js');

const script = dashboardScript();

const dom = require('./_dom_stub.js');
const element = id => dom.byId(id);

const calls = [];
let backupsPayload = {};
let backupsStatus = 200;
let saveReply = {ok: true};
let saveStatus = 200;
let confirmAnswer = false;
let confirmCalls = 0;
let reloads = 0;

const SETTINGS = {
  panel_password_is_default: false,
  api_keys: [],
  deleted_api_keys: [],
  limits: {},
  pricing_enabled: false,
  update_check_enabled: true,
  keep_backups: 2,
  keep_backups_max: 3,
  version: '1.7.24',
  accounts_dir: '/data/accounts',
  usage_dir: '/data/usage',
  settings_file: '/data/settings.json',
};

dom.installDom({
  confirm: () => { confirmCalls += 1; return confirmAnswer; },
  fetch: (url, options) => {
    const target = String(url);
    calls.push({url: target, method: (options && options.method) || 'GET',
                body: options && options.body});
    const reply = (status, payload) => Promise.resolve({
      status: status, ok: status === 200 || status === 202,
      json: () => Promise.resolve(payload),
      text: () => Promise.resolve(JSON.stringify(payload)),
    });
    if(target === '/settings') return reply(200, SETTINGS);
    if(target === '/updates') return reply(200, {current_version: '1.7.24'});
    if(target === '/updates/backups') return reply(backupsStatus, backupsPayload);
    if(target === '/settings/save') return reply(saveStatus, saveReply);
    if(target === '/updates/rollback') return reply(202, {ok: true, state: 'queued'});
    return reply(200, {});
  },
});

const toasts = [];
let api;
const realLog = console.log;
console.log = () => {};
try {
  api = new Function(script + `
    toast = function(msg, kind){ global.__toasts.push([msg, kind]); };
    window.location.reload = function(){ global.__reloads += 1; };
    return {
      loadSettings: loadSettings,
      loadUpdateBackups: loadUpdateBackups,
      renderRollbackRows: renderRollbackRows,
      saveKeepBackups: saveKeepBackups,
      rollbackToVersion: rollbackToVersion,
      applyUpdateStatus: applyUpdateStatus,
    };`)();
} catch(e) {
  console.log = realLog;
  console.error('脚本求值失败:', e.message);
  process.exit(1);
}
global.__toasts = toasts;
global.__reloads = 0;
Object.defineProperty(global, '__reloads', {
  get() { return reloads; }, set(v) { reloads = v; },
});

const settled = () => new Promise(resolve => process.nextTick(resolve));
const posts = () => calls.filter(c => c.method === 'POST');
const rowsHtml = () => String(element('setRollbackRows').innerHTML);

const BACKUPS = {
  current_version: '1.7.24',
  keep_backups: 2,
  keep_backups_max: 3,
  rollback_enabled: true,
  source: 'local',
  backups: [
    {version: '1.7.23', created_at: '2026-10-20 09:00:00', files: 12,
     complete: true, reason: '', size_mb: 5.1},
    {version: '1.7.22', created_at: '2026-10-19 09:00:00', files: 12,
     complete: false, reason: '备份缺少文件 wb_proxy.py', size_mb: 4.9},
  ],
};

(async () => {
  let checks = 0;
  const check = (label, cond, extra) => {
    checks += 1;
    assert.ok(cond, label + (extra !== undefined ? '  [' + extra + ']' : ''));
  };

  // ---- 1. 打开设置页：备份表按后端给的行渲染 ----
  calls.length = 0;
  await api.loadSettings(true);
  await settled();
  check('打开设置页会读备份列表',
        calls.some(c => c.method === 'GET' && c.url === '/updates/backups'),
        calls.map(c => c.method + ' ' + c.url).join(', '));
  check('份数下拉跟随 /settings 的值',
        String(element('setKeepBackups').value) === '2', element('setKeepBackups').value);

  // ---- 2. 完整与不完整的行 ----
  api.renderRollbackRows(BACKUPS);
  check('两行都画出来', rowsHtml().indexOf('1.7.23') !== -1 &&
        rowsHtml().indexOf('1.7.22') !== -1, rowsHtml());
  check('完整的行标「完整」', rowsHtml().indexOf('完整') !== -1, rowsHtml());
  check('不完整的行标「不完整」并写出原因',
        rowsHtml().indexOf('不完整') !== -1 &&
        rowsHtml().indexOf('备份缺少文件 wb_proxy.py') !== -1, rowsHtml());
  const buttons = rowsHtml().split('<button').slice(1);
  check('两行各有一个回退按钮', buttons.length === 2, String(buttons.length));
  check('完整那行可以点', buttons[0].indexOf('disabled') === -1, buttons[0]);
  check('不完整那行禁用并说明原因',
        buttons[1].indexOf('disabled') !== -1 &&
        buttons[1].indexOf('备份缺少文件 wb_proxy.py') !== -1, buttons[1]);
  check('卡片标题带上当前版本',
        element('setRollbackState').textContent === '(当前 1.7.24)',
        element('setRollbackState').textContent);

  // ---- 3. 当前版本、更新服务缺失、任务进行中都不能点 ----
  api.renderRollbackRows({
    current_version: '1.7.23', keep_backups: 1, rollback_enabled: true,
    backups: [{version: '1.7.23', complete: true, files: 12}],
  });
  check('就是当前版本的行禁用',
        rowsHtml().indexOf('这就是当前版本') !== -1 &&
        rowsHtml().indexOf('disabled') !== -1, rowsHtml());

  api.renderRollbackRows({...BACKUPS, rollback_enabled: false});
  check('更新服务未安装时全部禁用',
        rowsHtml().split('<button').slice(1).every(b => b.indexOf('disabled') !== -1),
        rowsHtml());

  api.applyUpdateStatus({installation: {state: 'applying'}});
  api.renderRollbackRows(BACKUPS);
  check('已有更新任务时全部禁用',
        rowsHtml().split('<button').slice(1).every(b => b.indexOf('disabled') !== -1),
        rowsHtml());
  api.applyUpdateStatus({installation: {state: 'idle'}});

  // ---- 4. 空列表要能自解释 ----
  api.renderRollbackRows({current_version: '1.7.24', keep_backups: 0, backups: []});
  check('空列表说明「没有可回退的本地备份」',
        rowsHtml().indexOf('没有可回退的本地备份') !== -1, rowsHtml());
  check('份数设成 0 时说明回退不可用',
        element('setRollbackHint').textContent === '当前设置为不保留备份，回退不可用',
        element('setRollbackHint').textContent);

  api.renderRollbackRows({current_version: '1.7.24', keep_backups: 1, backups: []});
  check('还没更新过时给的是另一句话',
        element('setRollbackHint').textContent === '更新成功后才会留下可回退的备份',
        element('setRollbackHint').textContent);

  // ---- 5. 回退/更新两个新状态有中文文案 ----
  api.applyUpdateStatus({installation: {state: 'restoring', version: '1.7.23'}});
  check('restoring 显示「正在回退到历史版本」',
        element('setUpdateInstallStatus').textContent === '正在回退到历史版本',
        element('setUpdateInstallStatus').textContent);
  api.applyUpdateStatus({installation: {state: 'restored', version: '1.7.23'}});
  check('restored 显示「回退完成」',
        element('setUpdateInstallStatus').textContent === '回退完成',
        element('setUpdateInstallStatus').textContent);

  // ---- 6. 保留份数走 /settings/save，提交整数 ----
  calls.length = 0;
  toasts.length = 0;
  element('setKeepBackups').value = '3';
  await api.saveKeepBackups({disabled: false});
  await settled();
  const save = posts().filter(c => c.url === '/settings/save');
  check('份数保存走 POST /settings/save', save.length === 1,
        calls.map(c => c.method + ' ' + c.url).join(', '));
  check('提交的是整数 keep_backups',
        save.length === 1 && JSON.parse(save[0].body).keep_backups === 3,
        save.length ? save[0].body : '(no save)');
  check('保存成功给出成功提示', toasts.length === 1 && toasts[0][1] === 'ok',
        JSON.stringify(toasts));

  saveStatus = 500;
  calls.length = 0;
  toasts.length = 0;
  await api.saveKeepBackups({disabled: false});
  await settled();
  saveStatus = 200;
  check('保存失败给出失败提示', toasts.length === 1 && toasts[0][1] === 'bad',
        JSON.stringify(toasts));

  // ---- 7. 回退必须先确认；取消确认不提交 ----
  api.renderRollbackRows(BACKUPS);
  calls.length = 0;
  const before = confirmCalls;
  confirmAnswer = false;
  await api.rollbackToVersion({disabled: false}, '1.7.23');
  await settled();
  check('点回退先经过人工确认', confirmCalls === before + 1, String(confirmCalls));
  check('取消确认不提交回退请求',
        posts().filter(c => c.url === '/updates/rollback').length === 0,
        calls.map(c => c.method + ' ' + c.url).join(', '));
  check('面板只访问自己网关的相对路径',
        calls.every(c => c.url.charAt(0) === '/'),
        calls.map(c => c.url).join(', '));

  // 确认后提交的是 {version}，且带 ok:true 时不报错。
  calls.length = 0;
  toasts.length = 0;
  confirmAnswer = true;
  api.rollbackToVersion({disabled: false}, '1.7.23'); // 轮询会一直等着，故意不 await
  await settled();
  await settled();
  const rollback = posts().filter(c => c.url === '/updates/rollback');
  check('确认后提交 POST /updates/rollback', rollback.length === 1,
        calls.map(c => c.method + ' ' + c.url).join(', '));
  check('提交体只有目标版本',
        rollback.length === 1 && JSON.parse(rollback[0].body).version === '1.7.23',
        rollback.length ? rollback[0].body : '(no request)');
  check('提交成功给出成功提示',
        toasts.some(([m, kind]) => kind === 'ok' && String(m).indexOf('已开始回退') === 0),
        JSON.stringify(toasts));

  // ---- 8. 读不到备份列表只降级这张卡片 ----
  backupsStatus = 500;
  calls.length = 0;
  await api.loadSettings(true);
  await settled();
  check('读取失败时表格给出说明',
        rowsHtml().indexOf('读取备份列表失败') !== -1, rowsHtml());
  check('读取失败不影响设置页其它区块',
        element('setVersion').textContent === '1.7.24',
        element('setVersion').textContent);
  backupsStatus = 200;

  console.log = realLog;
  realLog('update rollback UI assertions passed (' + checks + ' checks)');
})().catch(e => {
  console.log = realLog;
  console.error(e && e.stack || e);
  process.exit(1);
});
