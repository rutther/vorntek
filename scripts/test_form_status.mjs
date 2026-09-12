// Guard-level tests; complete form interaction is verified separately in browser.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// All Vorntek pages now load the same form controller, including /contact/.
for (const file of ['app.js']) {
  const source = readFileSync(new URL(`../apps/website/${file}`, import.meta.url), 'utf8');
  const match = source.match(/form(?:\?)?\.addEventListener\("submit", async \(event\) => \{([\s\S]*?)\n    const formData/);
  assert.ok(match, `Submission guard not located in ${file}; update test for changed structure.`);
  const guard = match[1];

  test(`${file}: a new invalid attempt clears old success before validation`, async () => {
    const state = {kind: 'success', message: 'previous inquiry accepted'};
    let validated = false;
    const context = {
      submitting: false,
      event: {preventDefault() {}},
      setStatus(kind, message) { state.kind = kind; state.message = message; },
      validateForm() {
        assert.equal(state.kind, 'idle');
        assert.equal(state.message, '');
        validated = true;
        return false;
      },
    };
    await vm.runInNewContext(`(async()=>{${guard}})()`, context);
    assert.equal(validated, true);
  });

  test(`${file}: duplicate click while submitting preserves the active request status`, async () => {
    const context = {
      submitting: true, event: {preventDefault() {}},
      setStatus() { assert.fail('Active request status must not be cleared'); },
      validateForm() { assert.fail('Active request must not be submitted again'); },
    };
    await vm.runInNewContext(`(async()=>{${guard}})()`, context);
  });
}
