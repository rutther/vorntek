import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {JSDOM, VirtualConsole} from 'jsdom';

const script = readFileSync(new URL('../../console/static/console/v2/leads.js', import.meta.url), 'utf8');
// Only transport and the modal library are replaced. Selection, persistence,
// response interpretation and failure rendering execute the real page script.
async function page(saved = {}) {
  const dom = new JSDOM(`<div data-nc-lead-workspace data-nc-user-id="1" data-nc-site-id="1"
    data-nc-list-url="/admin/sales/leads/?page=1" data-nc-bulk-trash-url="/trash/" data-nc-bulk-restore-url="/restore/" data-nc-bulk-max="100">
    <input data-nc-select-page type="checkbox"><ul class="nc-lead-list">
    <li><input data-nc-record-select type="checkbox" value="11"></li>
    <li><input data-nc-record-select type="checkbox" value="12"></li></ul>
    <section data-nc-bulk-bar><strong data-nc-selected-count></strong>
    <button data-nc-bulk-trash>Trash</button><button data-nc-bulk-restore>Restore</button><button data-nc-bulk-clear>Clear</button>
    <div data-nc-bulk-failures hidden><details data-nc-bulk-failure-details><summary><span data-nc-bulk-failure-count></span></summary><ul data-nc-bulk-failure-list></ul></details></div></section>
    <div data-nc-trash-modal hidden><strong data-nc-trash-count></strong><textarea data-nc-trash-reason></textarea><button data-nc-confirm-trash>Confirm</button></div>
    <div data-nc-live></div></div>`, {url:'http://localhost/admin/sales/leads/?page=1', runScripts:'outside-only', pretendToBeVisual:true, virtualConsole:new VirtualConsole()});
  await new Promise(resolve => dom.window.addEventListener('load', resolve, {once:true}));
  const {window} = dom;
  Object.entries(saved).forEach(([k,v]) => window.sessionStorage.setItem(k,v));
  window.bootstrap = {Modal:{getOrCreateInstance(element){return {show(){element.hidden=false;}, hide(){element.hidden=true;}};}}};
  const scriptErrors=[];
  window.addEventListener('error', event=>scriptErrors.push(event.error));
  window.eval(script);
  window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
  if(scriptErrors.length) throw scriptErrors[0];
  const q = selector => window.document.querySelector(selector);
  const select = id => {const input=q(`[data-nc-record-select][value="${id}"]`); input.checked=true; input.dispatchEvent(new window.Event('change'));};
  const snapshot = () => Object.fromEntries(Object.keys(window.sessionStorage).map(k=>[k,window.sessionStorage.getItem(k)]));
  return {dom, window, q, select, snapshot};
}
const settle = () => new Promise(resolve => setTimeout(resolve, 0));

test('trash confirmation shows the current selection count, not an earlier selection', async () => {
  const p=await page();
  try {
    p.select('11'); p.select('12'); p.q('[data-nc-bulk-trash]').click();
    assert.equal(p.q('[data-nc-trash-modal]').hidden,false);
    assert.equal(p.q('[data-nc-trash-count]').textContent,'2');
    p.q('[data-nc-bulk-clear]').click(); p.select('12'); p.q('[data-nc-bulk-trash]').click();
    assert.equal(p.q('[data-nc-trash-count]').textContent,'1');
  } finally {p.dom.window.close();}
});

for (const restore of [false,true]) test(`${restore?'restore':'trash'} partial failure survives canonical queue reload with only failed rows selected`, async () => {
  const p=await page(); let saved;
  try {
    p.window.document.addEventListener('nc:queue-reload',()=>{saved=p.snapshot();},{once:true});
    p.window.fetch=async()=>({ok:true,status:207,json:async()=>({ok:false,records:[{id:11}],failures:[{id:12,message:'当前权限已变化，请核对负责人。'}]})});
    p.select('11');p.select('12');
    p.q(restore?'[data-nc-bulk-restore]':'[data-nc-confirm-trash]').click(); await settle(); await settle();
    assert.ok(saved,'selection and reasons must be persisted before navigation starts');
  } finally {p.dom.window.close();}
  const reopened=await page(saved);
  try {
    assert.equal(reopened.q('[data-nc-record-select][value="11"]').checked,false);
    assert.equal(reopened.q('[data-nc-record-select][value="12"]').checked,true);
    assert.equal(reopened.q('[data-nc-bulk-failures]').hidden,false);
    assert.match(reopened.q('[data-nc-bulk-failure-list]').textContent,/线索 #12.*当前权限已变化/);
    reopened.q('[data-nc-bulk-clear]').click();
    assert.equal(reopened.q('[data-nc-bulk-failures]').hidden,true);
  } finally {reopened.dom.window.close();}
});
