/* The agent-config card must send a payload the backend actually accepts.

   Two bugs shipped through a green test suite because nothing exercised the
   real front-end functions:

     1. applyAgent()/restoreAgent() posted {client_id}, while the handler only
        read payload["client"] - every click returned HTTP 400 "client is
        required";
     2. the models list was read from an undeclared identifier
        (AGENTS_MODELS instead of AGENT_MODELS), so the browser raised
        "AGENTS_MODELS is not defined" before the request left the page.

   This suite loads the shipped dashboard.html scripts into a stub DOM, calls
   the real applyAgent()/restoreAgent() and asserts what they post, so a
   renamed field or an undeclared identifier fails here instead of in the
   browser. Run with Node.
*/
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', 'dashboard.html'), 'utf8');
const script = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)]
  .map(match => match[1]).join('\n');

// ---- stub DOM -------------------------------------------------------------
const values = {
  agentBaseUrl: 'http://127.0.0.1:8788/v1',
  agentKeySelect: '__global',
  agentModelSelect: 'model-a',
};
const elements = new Map();
const element = id => {
  if (!elements.has(id)) {
    elements.set(id, {
      id, innerHTML: '', textContent: '', className: '', dataset: {},
      style: {setProperty(){}},
      classList: {add(){}, remove(){}, toggle(){}, contains(){ return false; }},
      addEventListener(){}, removeEventListener(){}, querySelector(){ return null; },
      querySelectorAll(){ return []; }, appendChild(){}, removeChild(){}, focus(){},
      setAttribute(){}, getAttribute(){ return ''; }, removeAttribute(){},
      contains(){ return false; }, closest(){ return null; },
    });
  }
  const el = elements.get(id);
  if (Object.prototype.hasOwnProperty.call(values, id)) el.value = values[id];
  return el;
};

global.document = {
  getElementById: element,
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener(){}, removeEventListener(){},
  createElement: () => element('created'),
  body: element('body'), head: element('head'), documentElement: element('html'),
  readyState: 'complete',
};
global.window = {
  addEventListener(){}, removeEventListener(){},
  location: {href: 'http://127.0.0.1:8788/', search: '', hash: '', origin: 'http://127.0.0.1:8788'},
  matchMedia: () => ({matches: false, addEventListener(){}}),
};
global.localStorage = {getItem(){ return null; }, setItem(){}, removeItem(){}};
global.sessionStorage = global.localStorage;
global.navigator = {userAgent: 'node'};
global.setInterval = () => 0;
global.clearInterval = () => {};
global.setTimeout = () => 0;
global.clearTimeout = () => {};
global.location = global.window.location;
global.alert = () => {};
global.confirm = () => true;
global.console.log = () => {};

// ---- capture every request the page makes ---------------------------------
const requests = [];
global.fetch = url => {
  const payload = String(url).includes('/agents')
    ? {clients: [], keys: [], models: [], gateway: {base_url: 'http://127.0.0.1:8788/v1'}}
    : {rows: [], total: 0, accounts: [], slots: [], data: [], results: [], byAccount: []};
  return Promise.resolve({
    status: 200, ok: true,
    json: () => Promise.resolve(payload),
    text: () => Promise.resolve(JSON.stringify(payload)),
  });
};
const realFetch = global.fetch;
global.fetch = (url, opts) => {
  requests.push({url: String(url), opts: opts || {}});
  return realFetch(url, opts);
};

// ---- load the shipped page scripts ---------------------------------------
const api = new Function(script + `
  window.__applyAgent = applyAgent;
  window.__restoreAgent = restoreAgent;
  window.__loadAgents = loadAgents;
  window.__setAgents = function(data, models){ AGENTS_DATA = data; AGENT_MODELS = models; };
  return { applyAgent, restoreAgent, loadAgents, setAgents: window.__setAgents,
           loadAgentsAvailability, switchMainTab,
           agentsAvailable: function(){ return AGENTS_AVAILABLE; },
           currentTab: function(){ return currentMainTab; } };`)();

const CLIENTS = [{
  id: 'claude-code', label: 'Claude Code', desc: 'Anthropic CLI',
  protocol: 'anthropic', config_paths: ['/home/u/.claude/settings.json'],
  installed: true, applied: null,
}];
const MODELS = [
  {id: 'model-a', context_window: 131072},
  {id: 'model-b', context_window: 65536},
];

let checks = 0;
const check = (label, cond, extra) => {
  checks += 1;
  assert.ok(cond, label + (extra ? '  [' + extra + ']' : ''));
};

(async () => {
  api.setAgents({clients: CLIENTS, keys: [], models: MODELS,
                 gateway: {base_url: 'http://127.0.0.1:8788/v1'}}, MODELS);

  // The regression that reached the user: an undeclared identifier inside
  // applyAgent() must not escape as a runtime error.
  requests.length = 0;
  await api.applyAgent('claude-code', null);
  const applyReq = requests.find(r => r.url.indexOf('/agents/apply') !== -1);
  check('applyAgent posts to /agents/apply', !!applyReq,
        JSON.stringify(requests.map(r => r.url)));

  const body = JSON.parse((applyReq && applyReq.opts.body) || '{}');
  check('the payload carries client', body.client === 'claude-code', JSON.stringify(body));
  check('the payload carries client_id too', body.client_id === 'claude-code', JSON.stringify(body));
  check('the payload carries the gateway base_url',
        body.base_url === 'http://127.0.0.1:8788/v1', JSON.stringify(body));
  check('the payload carries a non-empty models list',
        Array.isArray(body.models) && body.models.length === 2, JSON.stringify(body.models));
  check('models are sent as bare ids',
        body.models[0] === 'model-a' && body.models[1] === 'model-b',
        JSON.stringify(body.models));
  check('the payload carries the selected model',
        body.model === 'model-a', JSON.stringify(body.model));
  check('the payload carries the selected key id',
        body.key_id === '__global', JSON.stringify(body.key_id));

  requests.length = 0;
  await api.restoreAgent('claude-code', null);
  const restoreReq = requests.find(r => r.url.indexOf('/agents/restore') !== -1);
  check('restoreAgent posts to /agents/restore', !!restoreReq,
        JSON.stringify(requests.map(r => r.url)));
  const restoreBody = JSON.parse((restoreReq && restoreReq.opts.body) || '{}');
  check('the restore payload carries client',
        restoreBody.client === 'claude-code', JSON.stringify(restoreBody));
  check('the restore payload carries client_id too',
        restoreBody.client_id === 'claude-code', JSON.stringify(restoreBody));

  // The render path must run against a real /agents answer without throwing
  // and must actually fill the card grid and the model picker.
  global.fetch = (url, opts) => {
    if (String(url).indexOf('/agents') !== -1) {
      const payload = {
        clients: CLIENTS.concat([{
          id: 'codex', label: 'Codex CLI', desc: 'OpenAI Codex CLI',
          protocol: 'openai', config_paths: ['/home/u/.codex/config.toml'],
          installed: false, applied: {
            at: '2026-10-09 07:27:30', base_url: 'http://127.0.0.1:8788/v1',
            model: 'model-a', files: 2, external_change: true,
          },
        }]),
        keys: [{id: 'key-1', name: '面板 Key', enabled: true}],
        models: MODELS,
        gateway: {base_url: 'http://127.0.0.1:8788/v1'},
      };
      requests.push({url: String(url), opts: opts || {}});
      return Promise.resolve({
        status: 200, ok: true,
        json: () => Promise.resolve(payload),
        text: () => Promise.resolve(JSON.stringify(payload)),
      });
    }
    return realFetch(url, opts);
  };

  elements.forEach(el => { if (el) el.innerHTML = ''; });
  await api.loadAgents();
  const grid = element('agentCardGrid');
  check('the card grid is rendered', /Claude Code/.test(grid.innerHTML || ''),
        String(grid.innerHTML).slice(0, 160));
  check('a not-installed client is marked as such',
        /Codex CLI/.test(grid.innerHTML || ''), String(grid.innerHTML).slice(0, 160));
  check('an applied client offers the restore button',
        /restoreAgent/.test(grid.innerHTML || ''), String(grid.innerHTML).slice(0, 200));
  check('an externally changed config raises the warning',
        /外部修改|检测到外部修改/.test(grid.innerHTML || ''), String(grid.innerHTML).slice(0, 200));
  const modelSel = element('agentModelSelect');
  check('the model picker is populated',
        /model-a/.test(modelSel.innerHTML || ''), String(modelSel.innerHTML).slice(0, 160));
  const keySel = element('agentKeySelect');
  check('the key picker is populated',
        /key-1/.test(keySel.innerHTML || ''), String(keySel.innerHTML).slice(0, 160));

  // 计数与卡片必须对得上（issue #250）：列表是全部支持的客户端，检测到的只是
  // 其中一部分，所以标题要把两个数字都写出来，而不是只报「已检测到」的那个。
  const detectState = element('agentDetectState');
  check('检测计数同时给出卡片总数与本机检测数',
        /1 \/ 共 2/.test(detectState.textContent || ''), detectState.textContent);

  // An unknown client must not throw before the request is even built.
  requests.length = 0;
  await api.applyAgent('no-such-client', null);
  check('an unknown client is a no-op, not a crash',
        requests.filter(r => r.url.indexOf('/agents/apply') !== -1).length === 0);

  // 服务端形态（看板不在网关本机）时入口整个撤掉（issue #246）：
  // /agents/available 回 enabled:false → Tab 隐藏，?tab=agents 的书签退回网关页。
  global.fetch = (url, opts) => {
    if (String(url).indexOf('/agents/available') !== -1) {
      const payload = {enabled: false};
      return Promise.resolve({
        status: 200, ok: true,
        json: () => Promise.resolve(payload),
        text: () => Promise.resolve(JSON.stringify(payload)),
      });
    }
    return realFetch(url, opts);
  };
  const navAgents = element('btnNavAgents');
  await api.loadAgentsAvailability();
  check('a server-form panel hides the agents tab',
        navAgents.style.display === 'none', JSON.stringify(navAgents.style));
  check('the availability flag flips to false', api.agentsAvailable() === false);
  api.switchMainTab('agents');
  check('a stale agents tab falls back to the gateway page',
        api.currentTab() === 'gateway', api.currentTab());

  process.stdout.write('agent config UI assertions passed (' + checks + ' checks)\n');
})().catch(err => {
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
});
