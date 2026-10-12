/* 数据看板：token 紧凑单位（K/M/B）+ 缓存 token 展示 + 总计等价花费。
 *
 *   1. fmtCompact：<1 万精确（千分位），≥1 万 K、≥100 万 M、≥10 亿 B；
 *      小数位随量级收缩、尾零去掉；单位是大写 K/M/B，与表头排序解析器
 *      （cellSortValue 的 K/M/G 换算）同一口径，带单位的单元格照样按数值排。
 *   2. renderAnalytics：两张 token 卡的大数字与输入/输出/思考/缓存明细都走
 *      紧凑单位；缓存命中率卡的副行带缓存 token 数；花费卡在「价估算开着、
 *      范围不是全部历史」时给出总计（全部历史）。
 *
 * Run with Node: node tests/_test_token_units.js
 */
'use strict';
const assert = require('assert');
const {dashboardHtml, dashboardScript} = require('./_dashboard_source.js');
const dom = require('./_dom_stub.js');

dom.installDom({fetch: () => Promise.resolve({ok: true, status: 200,
  json: () => Promise.resolve({}), text: () => Promise.resolve('')})});

const realLog = console.log;
console.log = () => {};
let api;
try {
  api = new Function(dashboardScript() + `
    return {
      fmtCompact: fmtCompact,
  cellSortValue: cellSortValue,
      ms: ms,
      get PRICING_ON(){ return PRICING_ON; },
      set PRICING_ON(v){ PRICING_ON = v; },
      renderAnalytics: renderAnalytics,
    };`)();
} catch(e) {
  console.log = realLog;
  console.error('脚本求值失败:', e.message);
  process.exit(1);
}
console.log = realLog;

/* ---- 0. 排序解析器要认得 fmtCompact 输出的每一个单位 --------------- */

// 带单位的格子必须按数值排序：fmtCompact 会输出 B，cellSortValue 就得认 B，
// 否则十亿那一格会按 1.5 排到 999M 下面去。
{
  const cell = (text) => ({textContent: text, getAttribute: () => null,
                           querySelector: () => null});
  assert.strictEqual(api.cellSortValue(cell('1.5B'), 'num'), 1.5e9);
  assert.strictEqual(api.cellSortValue(cell('999M'), 'num'), 999e6);
  assert.ok(api.cellSortValue(cell('1.5B'), 'num') > api.cellSortValue(cell('999M'), 'num'),
    'B 与 M 之间必须可比');
}

/* ---- 1. fmtCompact：单位换算 ---------------------------------------- */

const cases = [
  [0, '0'],
  [999, '999'],
  [9999, '9,999'],
  [10000, '10K'],
  [12345, '12.3K'],
  [123456, '123K'],
  [1000000, '1M'],
  [1234567, '1.23M'],
  [12345678, '12.3M'],
  [123456789, '123M'],
  [2500000000, '2.5B'],
];
for(const [input, want] of cases){
  assert.strictEqual(api.fmtCompact(input), want,
    'fmtCompact(' + input + ') 应为 ' + want + '，实际 ' + api.fmtCompact(input));
}

/* ---- 1b. ms()：延迟/耗时的自动单位（≥1000ms → s，否则 ms） ---------- */

const msCases = [
  [undefined, '—'],
  [null, '—'],
  [0, '0 ms'],
  [890, '890 ms'],
  [999.6, '1000 ms'],
  [1000, '1.00 s'],
  [1397, '1.40 s'],
  [65432.1, '65.43 s'],
];
for(const [input, want] of msCases){
  assert.strictEqual(api.ms(input), want,
    'ms(' + input + ') 应为 ' + want + '，实际 ' + api.ms(input));
}

/* ---- 2. renderAnalytics：KPI 展示 ----------------------------------- */

const el = id => document.getElementById(id);
window.VIEW_REALM = 'intl';

const PAYLOAD = {
  usd_cny: 7.1,
  window: {since: null, until: null},
  realm: 'all',
  summary: {
    window: {
      total_tokens: 1234567, prompt_tokens: 2000000,
      completion_tokens: 300000, reasoning_tokens: 100000,
      cached_tokens: 800000, cache_hit_pct: 40.0,
      requests: 5, errors: 0,
    },
    all_time: {
      total_tokens: 123456789, prompt_tokens: 90000000,
      completion_tokens: 30000000, reasoning_tokens: 3000000,
      cached_tokens: 900000, cache_hit_pct: 1.0,
      cost_cny: 12.5,
    },
  },
  accounts: [],
  models: [],
  keys: [],
};

PAYLOAD.summary.window.ttft_ms_avg = 1397.6;
PAYLOAD.summary.window.elapsed_ms_avg = 890.4;
api.PRICING_ON = true;
api.renderAnalytics(PAYLOAD);
assert.strictEqual(el('kpiAvgLatency').textContent, '1.40 s', '首字延迟 ≥1000ms 应显示 s');
assert.ok(el('kpiAvgElapsed').textContent.includes('890 ms'), '耗时 <1000ms 应显示 ms');
assert.ok(el('kpiAvgElapsed').textContent.includes('平均耗时'), '耗时副行保留「平均耗时」标签');

assert.strictEqual(el('kpiTodayTokens').textContent, '1.23M', '范围 token 大数字应紧凑显示');
assert.ok(el('kpiTodayTokensDetail').innerHTML.includes('缓存 800K'), '范围明细应含缓存 token');
assert.strictEqual(el('kpiTotalTokens').textContent, '123M', '全部历史 token 大数字应紧凑显示');
assert.ok(el('kpiTotalTokensDetail').innerHTML.includes('缓存 900K'), '全部历史明细应含缓存 token');
assert.ok(el('kpiCacheDetail').textContent.includes('缓存 800K'), '缓存命中率卡副行应带缓存 token 数');
assert.ok(el('kpiCostDetail').innerHTML.includes('总计（全部历史）'), '花费卡应给出总计行');
assert.ok(el('kpiCostDetail').innerHTML.includes('￥12.50'), '总计应为全部历史人民币金额');

/* 价估算关闭时：数值回「—」，总计行也不出现 */
api.PRICING_ON = false;
api.renderAnalytics(PAYLOAD);
assert.ok(el('kpiCostValue').innerHTML.includes('—'), '价估算关闭时花费显示 —');
assert.ok(!el('kpiCostDetail').innerHTML.includes('总计'), '价估算关闭时不应有总计行');

console.log('_test_token_units.js: all assertions passed');
