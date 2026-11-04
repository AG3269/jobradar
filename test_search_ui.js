const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, 'dashboard', 'index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const searchCode = script.slice(script.indexOf('let searchPollTimer'), script.indexOf('function setView('));
new vm.Script(script);

function setup(fetch) {
  const classes = new Set();
  const elements = {
    'run-btn': {
      disabled: false,
      classList: {
        toggle(name, enabled) { enabled ? classes.add(name) : classes.delete(name); },
        contains(name) { return classes.has(name); },
      },
    },
    'search-status': { textContent: '', style: {} },
  };
  const timers = new Map();
  const toasts = [];
  const refreshed = [];
  let timerId = 0;
  const context = vm.createContext({
    API: '/api',
    fetch,
    document: { getElementById: id => elements[id] },
    console: { error() {} },
    setTimeout(callback, delay) { timers.set(++timerId, { callback, delay }); return timerId; },
    clearTimeout(id) { timers.delete(id); },
    showToast: (message, error) => toasts.push({ message, error }),
    loadStats: async () => refreshed.push('stats'),
    loadJobs: async () => refreshed.push('jobs'),
    loadHistory: async () => refreshed.push('history'),
  });
  vm.runInContext(searchCode, context);
  return { context, elements, timers, toasts, refreshed };
}

function response(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

test('running searches do not falsely complete after thirty seconds', async () => {
  const app = setup(async () => response({ status: 'running', message: 'Scoring 70 jobs...' }));
  await app.context.refreshSearchStatus();
  for (let tick = 0; tick < 16; tick++) {
    const [id, timer] = app.timers.entries().next().value;
    app.timers.delete(id);
    assert.equal(timer.delay, 2000);
    await timer.callback();
  }
  assert.equal(app.elements['run-btn'].disabled, true);
  assert.equal(app.elements['search-status'].textContent, 'Scoring 70 jobs...');
  assert.equal(app.toasts.length, 0);
  assert.equal(app.refreshed.length, 0);
});

test('actual completion enables the button and refreshes results and history', async () => {
  const app = setup(async () => response({
    status: 'completed', summary: { total_jobs_found: 12 },
  }));
  await app.context.refreshSearchStatus(true);
  assert.equal(app.elements['run-btn'].disabled, false);
  assert.match(app.elements['search-status'].textContent, /12 jobs matched/);
  assert.deepEqual(app.refreshed, ['stats', 'jobs', 'history']);
  assert.equal(app.toasts.length, 1);
  assert.equal(app.timers.size, 0);
});

test('worker failures are displayed instead of a completion notification', async () => {
  const app = setup(async () => response({ status: 'failed', error: 'Search unavailable' }));
  await app.context.refreshSearchStatus(true);
  assert.equal(app.elements['run-btn'].disabled, false);
  assert.match(app.elements['search-status'].textContent, /Search unavailable/);
  assert.equal(app.toasts[0].error, true);
  assert.equal(app.refreshed.length, 0);
});

test('failed trigger requests never claim the search has started', async () => {
  const app = setup(async () => response({ error: 'Cannot start worker' }, 500));
  await app.context.triggerSearch();
  assert.match(app.elements['search-status'].textContent, /Cannot start worker/);
  assert.equal(app.toasts.length, 1);
  assert.equal(app.toasts[0].error, true);
  assert.equal(app.elements['run-btn'].disabled, false);
});

test('duplicate requests reconnect to the existing search', async () => {
  const app = setup(async url => url.endsWith('/run')
    ? response({ error: 'Already running' }, 409)
    : response({ status: 'running', message: 'Searching job boards...' }));
  await app.context.triggerSearch();
  assert.equal(app.elements['run-btn'].disabled, true);
  assert.equal(app.elements['search-status'].textContent, 'Searching job boards...');
  assert.equal(app.toasts.length, 0);
});

test('polling failures keep an active search disabled and retry status checks', async () => {
  const app = setup(async () => { throw new Error('Network unavailable'); });
  app.context.setSearchUI(true, 'Scoring...');
  await app.context.refreshSearchStatus(true);
  assert.equal(app.elements['run-btn'].disabled, true);
  assert.match(app.elements['search-status'].textContent, /Unable to check search status/);
  assert.equal(app.timers.size, 1);
  assert.equal(app.toasts.length, 0);
});
