import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
const code = readFileSync(new URL('../../console/static/console/meta-editor-v2a.js', import.meta.url), 'utf8');
function setup(failed=false, earlyResetCheckpoint=false) {
  const dom = new JSDOM(`<div class="mi-editor" data-error-section="${failed?'identity':''}"><form data-mi-edit id="one"><input name="token"><footer class="mi-actions"><span data-mi-status></span><button>Save</button></footer></form><details class="mi-setting-row"><summary><span class="mi-status mi-good">已填写</span></summary><form data-mi-edit id="two"><input name="name" value="old"><input type="hidden" name="_section" value="identity"><footer class="mi-actions"><span data-mi-status>已保存</span><button>Save</button></footer></form></details><form data-mi-diagnose><span id="mi-diagnostic-note"></span><button>Check</button></form></div>`, {runScripts:'outside-only'});
  if (earlyResetCheckpoint) dom.window.queueMicrotask = callback => callback();
  dom.window.eval(code);
  const doc=dom.window.document;
  return {dom,doc,change(id,value){const input=doc.querySelector(`#${id} input`);input.value=value;input.dispatchEvent(new dom.window.Event('input',{bubbles:true}));},submit(form){return form.dispatchEvent(new dom.window.Event('submit',{cancelable:true,bubbles:true}));}};
}
test('diagnostic cannot discard dirty credentials',()=>{
  const s=setup(); s.change('one','unsaved');
  assert.equal(s.submit(s.doc.querySelector('[data-mi-diagnose]')),false);
  assert.equal(s.doc.querySelector('#one input').value,'unsaved');
  assert.equal(s.doc.querySelector('#mi-diagnostic-note').getAttribute('role'),'alert');
});
test('revert one region unblocks save of another without a dead end',()=>{
  const s=setup();s.dom.window.alert=()=>{};s.change('one','secret');s.change('two','changed');
  assert.equal(s.submit(s.doc.querySelector('#one')),false);
  const reset=s.doc.querySelector('#two button[type=reset]');
  assert.ok(reset,'Dirty regions need an explicit undo action');
  reset.click();
  assert.equal(s.doc.querySelector('#two input').value,'old');
  assert.equal(s.submit(s.doc.querySelector('#one')),true);
});
test('accepted submission prevents a duplicate and shows pending state',()=>{
  const s=setup();s.change('one','secret');const form=s.doc.querySelector('#one');
  assert.equal(s.submit(form),true);
  assert.equal(form.getAttribute('aria-busy'),'true');
  assert.equal(s.submit(form),false);
});

test('reset feedback waits for native reset default action even with an early microtask checkpoint',async()=>{
  const s=setup(false,true);s.change('two','changed');
  s.doc.querySelector('#two button[type=reset]').click();
  await new Promise(resolve=>s.dom.window.setTimeout(resolve,10));
  assert.equal(s.doc.querySelector('#two input').value,'old');
  assert.equal(s.doc.querySelector('#two [data-mi-status]').textContent,'已保存');
  assert.equal(s.doc.querySelector('#two button:not([type])').disabled,true);
  assert.equal(s.doc.querySelector('.mi-setting-row .mi-status').textContent,'已填写');
});

test('rejected section keeps an unsaved warning after edit and reset, never a saved success',async()=>{
  const s=setup(true);
  const status=s.doc.querySelector('#two [data-mi-status]');
  const badge=s.doc.querySelector('.mi-setting-row .mi-status');
  assert.match(status.textContent,/未保存/);
  assert.match(badge.textContent,/未保存/);
  assert.equal(badge.classList.contains('mi-good'),false);
  s.change('two','changed');
  assert.match(status.textContent,/待保存/);
  s.doc.querySelector('#two button[type=reset]').click();
  await new Promise(resolve=>s.dom.window.setTimeout(resolve,10));
  assert.match(status.textContent,/未保存/);
  assert.match(badge.textContent,/未保存/);
});

function credentialSetup(saved=false) {
  const dom=new JSDOM(`<div class="mi-editor" data-credential-saved="${saved}"><details id="mi-credential-edit" open><summary>Edit</summary><form data-mi-edit data-mi-credentials><div data-mi-token-field data-state="empty"><input id="id_primary_secret" name="primary_secret"><span data-mi-token-label></span></div><input id="id_secret_ref" name="secret_ref" value="${saved?'vault:existing':''}"><footer class="mi-actions"><span data-mi-status>Original</span><button type="submit">Save</button></footer></form></details></div>`,{runScripts:'outside-only'});
  dom.window.eval(code);
  const doc=dom.window.document;
  return {dom,doc,change(selector,value){const input=doc.querySelector(selector);input.value=value;input.dispatchEvent(new dom.window.Event('input',{bubbles:true}));}};
}
test('typing a token is pending, never marked saved, and required tracks alternative reference',()=>{
  const s=credentialSetup();
  assert.equal(s.doc.querySelector('#id_primary_secret').required,true);
  assert.equal(s.doc.querySelector('button[type=submit]').disabled,true);
  s.change('#id_primary_secret','fake-preview-only');
  assert.equal(s.doc.querySelector('[data-mi-token-field]').dataset.state,'pending');
  assert.match(s.doc.querySelector('[data-mi-token-label]').textContent,/待保存/);
  assert.equal(s.doc.querySelector('button[type=submit]').disabled,false);
  s.change('#id_primary_secret','');s.change('#id_secret_ref','NEW_ENV_NAME');
  assert.equal(s.doc.querySelector('#id_primary_secret').required,false);
  assert.match(s.doc.querySelector('[data-mi-token-label]').textContent,/已有密钥/);
});
test('cancel replacement restores saved state without changing stored reference',async()=>{
  const s=credentialSetup(true);s.change('#id_primary_secret','fake-preview-only');
  s.doc.querySelector('button[type=reset]').click();
  await new Promise(resolve=>s.dom.window.setTimeout(resolve,10));
  assert.equal(s.doc.querySelector('#id_primary_secret').value,'');
  assert.equal(s.doc.querySelector('#id_secret_ref').value,'vault:existing');
  assert.equal(s.doc.querySelector('#mi-credential-edit').open,false);
  assert.equal(s.doc.querySelector('button[type=submit]').disabled,true);
});
