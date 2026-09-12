// Synthetic DOM/clipboard contract checks; not a real-browser BFCache test.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../apps/crm/console/static/console/system-users.js', import.meta.url), 'utf8');
function setup(writeText) {
  const calls = [];
  const events = {};
  const node = {textContent: 'synthetic-receipt-only'};
  const status = {textContent: ''};
  const dismiss = {addEventListener(_name, fn) { this.click = fn; }};
  const buttons = ['username', 'password', 'all'].map(copyCredential => ({
    dataset: {copyCredential}, addEventListener(_name, fn) { this.click = fn; },
  }));
  const receipt = {
    removed: false, remove() { this.removed = true; },
    querySelector(selector) { return {
      '[data-credential-username]': {textContent: 'local-fixture'},
      '[data-credential-password]': node, '[data-copy-status]': status,
      '[data-credential-dismiss]': dismiss,
    }[selector]; },
    querySelectorAll() { return buttons; },
  };
  vm.runInNewContext(source, {
    document: {querySelectorAll: () => [], querySelector: () => receipt},
    window: {addEventListener(name, fn) { events[name] = fn; }},
    navigator: {clipboard: {writeText(value) {
      calls.push(value); return writeText ? writeText(value) : Promise.resolve();
    }}},
  });
  return {calls, events, node, status, dismiss, buttons, receipt};
}

test('active receipt copies the selected synthetic value', async () => {
  const s = setup();
  for (const button of s.buttons) await button.click();
  assert.deepEqual(s.calls, ['local-fixture', 'synthetic-receipt-only',
    '登录名：local-fixture\n临时密码：synthetic-receipt-only']);
});

for (const action of ['dismiss', 'pagehide']) {
  test(`${action} removes receipt and disables retained copy handlers`, async () => {
    const s = setup();
    if (action === 'dismiss') s.dismiss.click(); else s.events.pagehide();
    assert.equal(s.node.textContent, '');
    assert.equal(s.receipt.removed, true);
    for (const button of s.buttons) await button.click();
    assert.deepEqual(s.calls, []);
  });
}

test('pending clipboard completion does not update a cleared receipt', async () => {
  let finish;
  const s = setup(() => new Promise(resolve => { finish = resolve; }));
  const pending = s.buttons[1].click();
  s.events.pagehide();
  finish();
  await pending;
  assert.equal(s.status.textContent, '');
  // An already requested clipboard write cannot be revoked by clearing the UI.
  assert.equal(s.calls.length, 1);
});

test('clipboard rejection presents manual-copy feedback while active', async () => {
  const s = setup(() => Promise.reject(new Error('synthetic denial')));
  await s.buttons[1].click();
  assert.match(s.status.textContent, /手动选中/);
});
