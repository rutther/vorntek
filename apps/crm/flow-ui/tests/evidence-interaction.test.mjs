import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
import {JSDOM} from 'jsdom';
import React,{act} from 'react';
const dom=new JSDOM('<div id="root"></div>',{url:'https://example.test/'});
globalThis.window=dom.window;globalThis.document=dom.window.document;
globalThis.HTMLElement=dom.window.HTMLElement;globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const {createRoot}=await import('react-dom/client');
const compiled=await build({entryPoints:['src/RuntimePanel.jsx'],bundle:true,write:false,platform:'node',format:'cjs',external:['react'],loader:{'.css':'empty'},logLevel:'silent'});
const module={exports:{}};new Function('require','module','exports',compiled.outputFiles[0].text)(createRequire(import.meta.url),module,module.exports);
const base={revisionKey:'revision-test',inquiries:1,serverAccepted:1,pending:0,failed:0,skipped:0,recentEvents:[{eventId:'synthetic-event-one',status:'sent',at:'2026-10-09T07:00:00Z'}],observations:[]};
const button=text=>[...document.querySelectorAll('button')].find(b=>b.textContent.includes(text));
const click=async node=>act(async()=>node.click());
async function choose(selector,value){await act(async()=>{const el=document.querySelector(selector);el.value=value;el.dispatchEvent(new window.Event('change',{bubbles:true}));});}
async function mount(overrides={},direct=true){
 let evidence=structuredClone(base), calls=[];
 const api={runtimeLoad:async()=>({evidence,canOperate:true}),runtimeCommand:async(id,payload)=>{calls.push(payload);evidence={...evidence,observations:[{...payload,level:'manual',currentCredential:true,at:'2026-10-09T07:01:00Z'}]};return {evidence,canOperate:true};},...overrides};
 const root=createRoot(document.getElementById('root'));
 const props={loop:{id:'flow',configs:{dataset:{externalId:'222222222'}}},active:true,api,direct};
 await act(async()=>root.render(React.createElement(module.exports.RuntimeEvidence,props)));
 return {calls,props,async render(){await act(async()=>root.render(React.createElement(module.exports.RuntimeEvidence,props)));},async close(){await act(async()=>root.unmount());}};
}
test('manual observation is visible, never preselected, and requires full event comparison',async()=>{
 const ui=await mount();try{
 assert(document.querySelector('.evidence-workbench'),'action must not be hidden inside details');
 assert.equal(document.querySelector('[aria-label="实际观察结果"]').value,'');
 assert(button('保存本项观察').disabled);
 await choose('[aria-label="核对的事件编号"]','synthetic-event-one');
 await choose('[aria-label="实际观察结果"]','meta_browser');
 assert(button('保存本项观察').disabled);
 await click(document.querySelector('input[name="eventMatched"]'));
 await click(button('保存本项观察'));
 assert.equal(ui.calls.length,1);
 assert.deepEqual(ui.calls[0],{eventId:'synthetic-event-one',source:'meta_browser',revisionKey:'revision-test'});
 assert.match(document.querySelector('[role="status"]').textContent,/已保存/);
 assert.match(document.querySelector('.evidence-next').textContent,/服务器/);
 assert(button('本项已登记').disabled);
 }finally{await ui.close();}
});

test('guide shows readable reference images even when there is no active runtime',async()=>{
 const ui=await mount({runtimeLoad:async()=>({evidence:null,canOperate:true})},false);try{
 const pictures=[...document.querySelectorAll('.guide-evidence-workbench .guide-reference-figure')];
 assert.equal(pictures.length,3,'browser, server and dedup help must be inline before events exist');
 for(const picture of pictures){
   assert(picture.querySelector('a[target="_blank"] img'));
   assert.equal(picture.closest('details,[hidden]'),null);
 }
 assert.equal(document.querySelector('form'),null);
 assert.match(document.querySelector('.guide-evidence-workbench').textContent,/完整事件编号/);
 assert.equal(ui.calls.length,0);
 }finally{await ui.close();}
});

test('guide shared action bar returns without saving and submits the selected observation through its form',async()=>{
 const ui=await mount({},false);let returned=0;
 try{
  ui.props.onReturn=()=>returned++;await ui.render();
  const back=button('返回测试步骤');
  assert.ok(back,'focused evidence needs a return action beside save');
  await choose('[aria-label="核对的事件编号"]','synthetic-event-one');
  await click(document.querySelector('[data-observation="meta_server"]'));
  await click(document.querySelector('input[name="observationDecision"][value="observed"]'));
  await click(document.querySelector('input[name="eventMatched"]'));
  await click(back);
  assert.equal(returned,1);assert.equal(ui.calls.length,0,'return must not submit the observation');
  assert.equal(document.querySelector('[aria-label="核对的事件编号"]').value,'synthetic-event-one');
  const save=button('保存本项观察');
  assert.equal(save.closest('footer'),back.closest('footer'),'primary and return belong to one action bar');
  assert.ok(save.closest('footer'));
  assert.equal(save.form,document.querySelector('form.evidence-register'),'external submit must target the guarded form');
  await click(save);
  assert.deepEqual(ui.calls,[{eventId:'synthetic-event-one',source:'meta_server',revisionKey:'revision-test'}]);
  assert.match(document.querySelector('[role="status"]').textContent,/已保存/);
 }finally{await ui.close();}
});

test('guide channel navigation never carries confirmation into another observation',async()=>{
 const ui=await mount({},false);try{
 await choose('[aria-label="核对的事件编号"]','synthetic-event-one');
 const pick=key=>document.querySelector(`[data-observation="${key}"]`);
 assert(pick('meta_browser'),'guide needs an operable channel selector');
 await click(pick('meta_browser'));
 assert.equal(pick('meta_browser').getAttribute('aria-pressed'),'true');
 await click(document.querySelector('input[name="observationDecision"][value="observed"]'));
 await click(document.querySelector('input[name="eventMatched"]'));
 await click(pick('meta_server'));
 assert.equal(document.querySelector('input[name="eventMatched"]').checked,false);
 assert(button('保存本项观察').disabled);
 assert.equal(document.querySelector('input[name="observationDecision"][value="observed"]').checked,false);
 await click(document.querySelector('input[name="observationDecision"][value="observed"]'));
 await click(document.querySelector('input[name="eventMatched"]'));
 await click(button('保存本项观察'));
 assert.deepEqual(ui.calls,[{eventId:'synthetic-event-one',source:'meta_server',revisionKey:'revision-test'}]);
 assert.match(pick('meta_server').textContent,/已登记/);
 assert.match(pick('meta_browser').textContent,/待核对/);
 await click(button('刷新证据'));
 assert.match(pick('meta_server').textContent,/已登记/);
 assert.equal(document.querySelector('input[name="eventMatched"]').checked,false);
 }finally{await ui.close();}
});

test('guide save failure preserves input and synchronous repeated submit sends only once',async()=>{
 let rejectSave,commands=0;
 const ui=await mount({runtimeCommand:()=>{commands++;return new Promise((_,reject)=>{rejectSave=reject;});}},false);
 try{
 await choose('[aria-label="核对的事件编号"]','synthetic-event-one');
 await click(document.querySelector('[data-observation="meta_dedup"]'));
 await click(document.querySelector('input[name="observationDecision"][value="observed"]'));
 await click(document.querySelector('input[name="eventMatched"]'));
 await act(async()=>{const form=document.querySelector('form');form.dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));form.dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));});
 assert.equal(commands,1);
 assert(button('保存中').disabled);
 await act(async()=>rejectSave(Error('模拟网络失败，请重试')));
 assert.match(document.querySelector('[role="alert"]').textContent,/模拟网络失败/);
 assert.equal(document.querySelector('[data-observation="meta_dedup"]').getAttribute('aria-pressed'),'true');
 assert.equal(document.querySelector('input[name="observationDecision"][value="observed"]').checked,true);
 assert.equal(document.querySelector('[aria-label="核对的事件编号"]').value,'synthetic-event-one');
 assert.equal(document.querySelector('input[name="eventMatched"]').checked,true);
 assert.equal(button('保存本项观察').disabled,false);
 }finally{await ui.close();}
});

test('guide uncertain decision clears confirmation and never writes a positive observation',async()=>{
 const ui=await mount({},false);try{
 await choose('[aria-label="核对的事件编号"]','synthetic-event-one');
 await click(document.querySelector('[data-observation="meta_dedup"]'));
 const observed=document.querySelector('input[name="observationDecision"][value="observed"]');
 const pending=document.querySelector('input[name="observationDecision"][value="pending"]');
 assert(observed&&pending,'reference-matched observed/pending radio decisions must be available');
 assert.equal(observed.checked,false);
 await click(observed);
 await click(document.querySelector('input[name="eventMatched"]'));
 assert.equal(button('保存本项观察').disabled,false);
 await click(pending);
 assert.equal(document.querySelector('input[name="eventMatched"]').checked,false);
 assert(button('保存本项观察').disabled);
 await act(async()=>document.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true})));
 assert.equal(ui.calls.length,0);
 assert.equal(document.querySelector('.evidence-pending-link').getAttribute('href'),'#/flow/flow?view=verification');
 }finally{await ui.close();}
});

test('late guide evidence response cannot replace a newly selected instance',async()=>{
 let finishOld;
 const ui=await mount({runtimeLoad:async id=>id==='flow'?new Promise(resolve=>{finishOld=resolve;}):({canOperate:true,evidence:{...base,revisionKey:'revision-new',recentEvents:[{eventId:'new-instance-event',status:'sent'}]}})},false);
 try{
 ui.props.loop={id:'new-flow',configs:{dataset:{externalId:'333333333'}}};await ui.render();
 await act(async()=>finishOld({canOperate:true,evidence:base}));
 const select=document.querySelector('[aria-label="核对的事件编号"]');
 assert.match(select.textContent,/new-instance-event/);
 assert.doesNotMatch(select.textContent,/synthetic-event-one/);
 assert.match(document.querySelector('.evidence-context').textContent,/333333333/);
 assert.equal(select.value,'');
 assert(button('保存本项观察').disabled);
 }finally{await ui.close();}
});
test('failed save preserves selections and shows a distinct recoverable failure',async()=>{
 const ui=await mount({runtimeCommand:async()=>{throw Error('登记未保存，请重试。');}});try{
 await choose('[aria-label="核对的事件编号"]','synthetic-event-one');
 await choose('[aria-label="实际观察结果"]','meta_browser');
 await click(document.querySelector('input[name="eventMatched"]'));
 await click(button('保存本项观察'));
 assert.match(document.querySelector('[role="alert"]').textContent,/登记未保存/);
 assert.equal(document.querySelector('[aria-label="核对的事件编号"]').value,'synthetic-event-one');
 assert.equal(document.querySelector('[aria-label="实际观察结果"]').value,'meta_browser');
 assert(!button('保存本项观察').disabled);
 }finally{await ui.close();}
});
test('no events and read failure do not show fake zeros or allow registration',async()=>{
 const ui=await mount({runtimeLoad:async()=>{throw Error('读取失败');}});try{
 assert.match(document.querySelector('[role="alert"]').textContent,/读取失败/);
 assert.equal(document.querySelector('form'),null);
 }finally{await ui.close();}
});
test('all comparison instructions and pictures are available before any selection',async()=>{
 const ui=await mount();try{
 const rows=[...document.querySelectorAll('.evidence-checklist li')];
 assert.equal(rows.length,3);
 for(const row of rows){
 assert(row.querySelector('button[aria-haspopup="dialog"]'),'each check needs a visible picture entry before selection');
 assert(row.querySelector('.evidence-check-instruction'),'each check explains what to compare before selection');
 assert.match(row.textContent,/选事件后显示/);
 }
 assert.match(rows[0].textContent,/浏览器/);
 assert.match(rows[1].textContent,/完整事件编号/);
 assert.match(rows[2].textContent,/不是本次询盘发送失败/);
 assert.equal(rows[1].querySelector('img').getAttribute('src'),'/static/console/meta-help/meta-server-event-expanded-redacted-20261009.svg');
 assert.equal(rows[2].querySelector('img').getAttribute('src'),'/static/console/meta-help/meta-dedup-tooltip-20261009.svg');
 await choose('[aria-label="核对的事件编号"]','synthetic-event-one');
 assert.match(rows[1].textContent,/待核对/);
 await choose('[aria-label="实际观察结果"]','meta_server');
 await choose('[aria-label="实际观察结果"]','meta_dedup');
 assert.match(document.querySelector('.evidence-checklist').textContent,/仅看到「已复制」三个字不要直接登记通过/);
 assert.equal(ui.calls.length,0);
 assert(button('保存本项观察').disabled);
 }finally{await ui.close();}
});
