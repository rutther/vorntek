import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';

const script=readFileSync(new URL('../../console/static/console/v2/tasks.js',import.meta.url),'utf8');
const message='已分配 1 条；登录或操作权限已变化，剩余 1 条未执行。请刷新并重新登录后核对。';

for(const forbidden of [false,true]) test(`task batch ${forbidden?'rejected':'partial'} authority response preserves selection and gives the actual recovery reason`,async()=>{
  const dom=new JSDOM(`<main data-nc-task-page><input data-nc-task-select type="checkbox" data-task-id="1" data-task-version="v1" checked>
    <input data-nc-task-select type="checkbox" data-task-id="2" data-task-version="v2" checked>
    <form data-nc-task-bulk-form action="/batch/"><input name="items_json"><input name="csrfmiddlewaretoken" value="synthetic-only">
    <strong data-nc-task-selected-count></strong><div data-nc-form-error hidden></div><div data-nc-form-status hidden></div>
    <button data-nc-submit>保存</button></form></main>`,{url:'http://localhost/admin/sales/tasks/',runScripts:'outside-only'});
  const {window}=dom;
  const q=selector=>window.document.querySelector(selector);
  try{
    window.fetch=async()=>({ok:!forbidden,status:forbidden?403:207,json:async()=>({ok:!forbidden,partial:true,
      succeeded:forbidden?0:1,total:2,message,results:forbidden?
        [{id:1,status:'authorization_changed'},{id:2,status:'authorization_changed'}]:
        [{id:1,status:'succeeded'},{id:2,status:'authorization_changed'}]})});
    window.eval(script);
    q('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));
    await new Promise(resolve=>setTimeout(resolve,0));
    assert.equal(q('[data-task-id="1"]').checked,forbidden);
    assert.equal(q('[data-task-id="2"]').checked,true);
    const feedback=q(forbidden?'[data-nc-form-error]':'[data-nc-form-status]');
    assert.equal(feedback.hidden,false);
    assert.equal(feedback.textContent,message);
    assert.equal(q('[data-nc-task-selected-count]').textContent,forbidden?'2':'1');
    assert.equal(q('form').getAttribute('aria-busy'),'false');
  }finally{dom.window.close();}
});
