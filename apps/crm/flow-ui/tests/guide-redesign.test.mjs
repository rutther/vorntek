import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
import {JSDOM} from 'jsdom';
import React, {act} from 'react';
import {readFileSync} from 'node:fs';
import {createServer} from 'node:http';
import {createApiClient} from '../src/apiClient.mjs';

const dom = new JSDOM('<div id="root"></div>', {url:'https://example.test/'});
globalThis.window=dom.window; globalThis.document=dom.window.document;
globalThis.HTMLElement=dom.window.HTMLElement;
globalThis.requestAnimationFrame=fn=>fn(); globalThis.IS_REACT_ACT_ENVIRONMENT=true;
dom.window.HTMLElement.prototype.scrollIntoView=function(){};
dom.window.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};
dom.window.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
const {createRoot}=await import('react-dom/client');
const result=await build({entryPoints:[fileURLToPath(new URL('../src/Guidance.jsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',external:['react'],loader:{'.css':'empty','.png':'dataurl','.svg':'dataurl'},logLevel:'silent'});
const module={exports:{}};
new Function('require','module','exports',result.outputFiles[0].text)(createRequire(import.meta.url),module,module.exports);
const {Guidance}=module.exports;
const fixture=()=>({id:'guide-one',name:'隔离任务',provider:'meta',preset:'website',configs:{},drafts:{},positions:{},version:1,revision:1,sources:[],history:[],taskKeys:['dataset','website','pixel','form','crm','server','ad']});
async function mount(extra={}) {
  const props={loop:fixture(),selectedKey:'website',guard:{current:null},crm:true,busy:false,api:{},update:async()=>true,navigate:()=>{},...extra};
  const root=createRoot(document.getElementById('root'));
  await act(async()=>root.render(React.createElement(Guidance,props)));
  return {props,render:()=>act(async()=>root.render(React.createElement(Guidance,props))),close:()=>act(async()=>root.unmount())};
}
const click=element=>act(async()=>element.click());
const visible=element=>element&&!element.closest('[hidden]');

test('real HTTP body timeout releases App save lock and retry confirms the same committed operation',async()=>{
 // Fault server, not CRM persistence: the real App/client/native fetch are exercised.
 // Removing the body deadline or regenerating the retry ID breaks this contract.
 const appBuild=await build({entryPoints:[fileURLToPath(new URL('../src/App.jsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',external:['react','react-dom'],loader:{'.css':'empty','.png':'dataurl','.svg':'dataurl'},logLevel:'silent'});
 const appModule={exports:{}};new Function('require','module','exports',appBuild.outputFiles[0].text)(createRequire(import.meta.url),appModule,appModule.exports);
 let stored=fixture(),commits=0;const received=[],accepted=new Map();
 const runtime={version:1,draft:null,integration:null,runtimeState:'not_applied',blockingChecks:[],preservedPixelIds:[],overlaps:[],history:[],sharedFlows:[],dependencies:[],forms:[],pageCatalog:{available:true,pages:[]}};
 const server=createServer(async(req,res)=>{
  res.setHeader('Content-Type','application/json');
  if(req.method==='GET'){res.end(JSON.stringify(req.url.includes('/runtime/')?{runtime}:{loops:[stored]}));return;}
  let raw='';for await(const chunk of req)raw+=chunk;
  const payload=JSON.parse(raw);received.push(payload);
  if(!accepted.has(payload.requestId)){
   stored={...stored,version:stored.version+1,configs:{...stored.configs,[payload.nodeKey]:payload.values}};
   commits++;accepted.set(payload.requestId,{loop:stored});
  }
  if(received.length===1){res.writeHead(200);res.write('{');return;} // Headers arrive, body never completes.
  res.end(JSON.stringify(accepted.get(payload.requestId)));
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const origin=`http://127.0.0.1:${server.address().port}`;
 // Keep the deadline short enough to exercise the real timeout path, while
 // leaving enough headroom for a contended container host to accept and read
 // the request body before the client aborts it. The assertion below is about
 // replay/idempotency, not whether the event loop can schedule within 500 ms.
 const api=createApiClient({apiUrl:'/test-api/',csrfToken:'synthetic-test-only',timeoutMs:2000},(url,init)=>fetch(origin+url,init));
 globalThis.location=window.location;window.scrollTo=()=>{};
 window.history.replaceState(null,'','#/flow/guide-one?view=tasks&node=website');
 const root=createRoot(document.getElementById('root'));
 const eventually=async(predicate)=>{const until=Date.now()+8000;while(!predicate()&&Date.now()<until)await act(async()=>{await new Promise(resolve=>setTimeout(resolve,20));});assert.ok(predicate(),'expected UI state before safety deadline');};
 try{
  await act(async()=>root.render(React.createElement(appModule.exports.App,{crm:true,initialData:{loops:[stored]},api})));
  const input=document.querySelector('#guide-panel-website input');
  await act(async()=>{Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set.call(input,'https://example.test/timeout-retained');input.dispatchEvent(new window.Event('input',{bubbles:true}));});
  const save=()=>[...document.querySelectorAll('#guide-panel-website button')].find(b=>b.textContent==='保存本步');
  // Observe the synchronous pending render before yielding to the real HTTP
  // deadline. Async act can drain the timeout too on a busy build host.
  act(()=>save().click());
  assert.equal(document.querySelector('.app-shell').getAttribute('aria-busy'),'true');
  await eventually(()=>Boolean(document.querySelector('.global-error')?.textContent.includes('请求超时')));
  assert.notEqual(document.querySelector('.app-shell').getAttribute('aria-busy'),'true');
  assert.equal(input.value,'https://example.test/timeout-retained');
  assert.match(document.querySelector('#guide-toggle-website .guide-step-status').textContent,/未保存/);
  assert.equal(save().disabled,false);assert.equal(received.length,1,'no automatic replay');
  await click(save());
  await eventually(()=>document.querySelector('#guide-toggle-website .guide-step-status')?.textContent.includes('已保存'));
  assert.equal(received.length,2);assert.equal(commits,1);
  assert.equal(received[0].requestId,received[1].requestId);
  assert.equal(received[0].expectedVersion,received[1].expectedVersion);
  assert.equal(document.querySelector('.global-error'),null);
  assert.equal((await api.load()).loops[0].configs.website.url,'https://example.test/timeout-retained');
 }finally{
  await act(async()=>root.unmount());server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
 }
});

test('bound dataset summary identifies the saved binding rather than a draft selection',async()=>{
 const loop=fixture();
 loop.configs.dataset={source:'existing',externalId:'900001',alias:'正式备注'};
 loop.drafts.dataset={source:'created',externalId:'900002',alias:'暂存备注'};
 loop.sources=[{id:'900001',name:'已绑定的真实来源',origin:'existing'},{id:'900002',name:'草稿里的另一来源',origin:'existing'}];
 const ui=await mount({loop,selectedKey:'dataset'});
 try{
  const panel=document.getElementById('guide-panel-dataset');
  const summary=panel.querySelector('[aria-label="已绑定数据集"]');
  assert.ok(summary,'saved binding needs an explicit summary beside the locked fields');
  assert.match(summary.textContent,/已绑定的真实来源/);assert.match(summary.textContent,/900001/);
  assert.doesNotMatch(summary.textContent,/900002|草稿里的另一来源/);
  assert.ok([...panel.querySelectorAll('input[type="radio"]')].every(input=>input.disabled));
  assert.equal(panel.querySelector('input[inputmode="numeric"]').readOnly,true);
  assert.equal(panel.querySelector('input[inputmode="numeric"]').value,'900001');
  assert.equal(panel.querySelector('input[type="radio"]:checked').value,'existing');
  assert.equal(panel.querySelector('textarea').disabled,false);
 }finally{await ui.close();}
});

for(const direct of [false,true])test(`binding learned after editing requires explicit identity recovery, preserves notes and saves authoritative identity (${direct?'plan':'guide'})`,async()=>{
 const loop=fixture();loop.drafts.dataset={source:'created',externalId:'900002',alias:'保留本地备注'};
 const writes=[];const ui=await mount({loop,direct,selectedKey:'dataset',update:async(...args)=>{writes.push(args);return true;}});
 try{
  ui.props.loop={...loop,version:2,configs:{dataset:{source:'existing',externalId:'900001',alias:'另一标签备注'}}};
  await ui.render();
  const panel=document.getElementById('guide-panel-dataset'),form=panel.querySelector('form');
  assert.equal(panel.querySelector('input[inputmode="numeric"]').value,'900001','locked identity is the saved binding, not stale input');
  const conflict=panel.querySelector('[role="alert"]');assert.ok(conflict);assert.match(conflict.textContent,/900002/);assert.match(conflict.textContent,/900001/);
  await act(async()=>form.dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true})));
  assert.equal(writes.length,0,'identity recovery requires an explicit acknowledgement before any write');
  const adopt=[...panel.querySelectorAll('button')].find(b=>b.textContent==='采用已绑定数据集，保留备注');
  assert.ok(adopt);await click(adopt);assert.equal(writes.length,0,'recovery updates local input only');
  assert.match(panel.querySelector('.guide-save-feedback').textContent,/未保存/);
  await act(async()=>form.dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true})));
  assert.equal(writes.length,1);
  assert.deepEqual(writes[0][2],{action:'save_config',nodeKey:'dataset',values:{source:'existing',externalId:'900001',alias:'保留本地备注'}});
 }finally{await ui.close();}
});

test('bound dataset missing from the source catalog still shows its saved identifier',async()=>{
 const loop=fixture();loop.configs.dataset={source:'created',externalId:'900003',alias:'不是数据集名称'};
 const ui=await mount({loop,selectedKey:'dataset'});
 try{
  const summary=document.querySelector('[aria-label="已绑定数据集"]');
  assert.ok(summary);assert.match(summary.textContent,/900003/);
  assert.doesNotMatch(summary.textContent,/不是数据集名称/);
 }finally{await ui.close();}
});

test('an unbound dataset draft is not presented as an existing binding',async()=>{
 const loop=fixture();loop.drafts.dataset={source:'created',externalId:'900004',alias:''};
 const ui=await mount({loop,selectedKey:'dataset'});
 try{assert.equal(document.querySelector('[aria-label="已绑定数据集"]'),null);}
 finally{await ui.close();}
});

test('Pixel edits, pending saves, failures and success have consistent heading and navigation state',async()=>{
 const loop=fixture();loop.configs.pixel={mode:'managed'};
 const runtime={version:2,draft:{landingPaths:['/','/contact/'],leadPaths:['/contact/'],formId:1,consentRequired:true},integration:{id:1},runtimeState:'not_applied',configState:'saved',blockingChecks:[],preservedPixelIds:[],overlaps:[],history:[],sharedFlows:[],dependencies:[],forms:[{id:1,name:'项目询盘',code:'project-inquiry'}],pageCatalog:{available:true,pages:[{path:'/',title:'首页',locale:'en',formCodes:[]},{path:'/contact/',title:'联系页',locale:'en',formCodes:['project-inquiry']}]}};
 let rejectSave,resolveSave;const writes=[];
 const ui=await mount({loop,selectedKey:'pixel',api:{runtimeLoad:async()=>({runtime})},update:async(...args)=>{writes.push(args[2]);return new Promise((resolve,reject)=>{resolveSave=resolve;rejectSave=reject;});}});
 const heading=()=>document.querySelector('#guide-toggle-pixel .guide-step-status').textContent;
 const nav=()=>document.querySelector('[data-step="pixel"] [role="img"]').getAttribute('aria-label');
 const compact=()=>document.querySelector('select[aria-label="选择引导步骤"] option[value="pixel"]').textContent;
 try{
  await click(document.querySelector('button[aria-label="从记录浏览的页面移除 /"]'));
  assert.match(heading(),/未保存/);assert.match(nav(),/未保存/);assert.match(compact(),/未保存/);
  assert.match(document.querySelector('.guide-dirty-summary').textContent,/1 个步骤/);
  assert.equal(ui.props.guard.current.dirty,true);assert.equal(writes.length,0);
  const form=document.querySelector('.pixel-step-form');
  await act(async()=>form.dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true})));
  assert.match(heading(),/正在保存/);assert.match(nav(),/正在保存/);assert.match(compact(),/正在保存/);
  await act(async()=>rejectSave(Error('controlled network failure')));
  assert.match(heading(),/未保存/);assert.match(nav(),/未保存/);
  assert.equal(document.querySelector('button[aria-label="从记录浏览的页面移除 /"]'),null,'failed save preserves edited scope');
  await act(async()=>form.dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true})));
  await act(async()=>resolveSave({savedRuntimeVersion:2}));
  assert.match(heading(),/已保存/);assert.match(nav(),/已保存/);assert.doesNotMatch(compact(),/未保存|正在保存/);
  assert.equal(document.querySelector('.guide-dirty-summary'),null);
  assert.equal(Boolean(ui.props.guard.current.dirty),false);
  assert.deepEqual(writes[1].scope.landingPaths,['/contact/']);
 }finally{await ui.close();}
});

test('ad target follows its shared URL and retains save and return behavior',async()=>{
 const loop=fixture();loop.configs.website={url:'https://example.test/contact/?source=ad'};
 loop.configs.ad={adName:'本地广告',objective:'website'};
 const writes=[];const ui=await mount({loop,selectedKey:'ad',update:async(...args)=>{writes.push(args);return true;}});
 try{
  const panel=document.getElementById('guide-panel-ad');
  const fields=[...panel.querySelectorAll('.guide-fields input,.guide-fields select')];
  assert.deepEqual(fields.map(f=>f.value),['本地广告','https://example.test/contact/?source=ad','website','Lead'],
   'reading and keyboard order must follow names, shared URL, conversion location and fixed event');
  const url=fields[1],event=fields[3];assert.equal(url.readOnly,true);assert.equal(event.readOnly,true);
  await act(async()=>{fields[2].value='';fields[2].dispatchEvent(new window.Event('change',{bubbles:true}));});
  assert.equal(fields[0].value,'本地广告');assert.equal(url.value,loop.configs.website.url);
  await act(async()=>{fields[2].value='website';fields[2].dispatchEvent(new window.Event('change',{bubbles:true}));});
  await click(panel.querySelector('.guide-ad-destination button'));
  assert.ok(visible(document.getElementById('guide-panel-website')));assert.equal(writes.length,0);
  await click(document.querySelector('[data-step="ad"]'));
  await act(async()=>panel.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true})));
  assert.equal(writes.length,1);
  assert.deepEqual(writes[0][2],{action:'save_config',nodeKey:'ad',values:{adName:'本地广告',objective:'website'}});
  assert.equal(writes[0][0].configs.website.url,loop.configs.website.url);
 }finally{await ui.close();}
});

test('observation drafts join the guide leave guard without being implicitly registered',async()=>{
 const evidence={revisionKey:'revision-test',inquiries:1,serverAccepted:1,pending:0,failed:0,skipped:0,recentEvents:[{eventId:'synthetic-event-one',status:'sent'}],observations:[]};
 let rejectSave,commands=0;
 const ui=await mount({selectedKey:'closed-loop-test',api:{runtimeLoad:async()=>({evidence,canOperate:true}),runtimeCommand:()=>{commands++;return new Promise((_,reject)=>rejectSave=reject);}}});
 try{
  const select=document.querySelector('[aria-label="核对的事件编号"]');
  await act(async()=>{select.value='synthetic-event-one';select.dispatchEvent(new window.Event('change',{bubbles:true}));});
  await click(document.querySelector('[data-observation="meta_browser"]'));
  assert.equal(Boolean(ui.props.guard.current.dirty),false,'browsing an event or instructions is not an edit');
  await click(document.querySelector('[name="observationDecision"][value="observed"]'));
  assert.equal(ui.props.guard.current.dirty,true,'choosing an observation must be protected before confirmation');
  assert.equal(ui.props.guard.current.kind,'observation');
  assert.equal(await ui.props.guard.current.save(),false);assert.equal(commands,0,'save-and-leave cannot certify a positive observation');
  await click(document.querySelector('[name="eventMatched"]'));
  await click(document.querySelector('[data-step="ad"]'));
  await click(document.querySelector('[data-step="closed-loop-test"]'));
  assert.equal(document.querySelector('[name="eventMatched"]').checked,true,'returning to a mounted step retains unsaved confirmation');
  await act(async()=>document.querySelector('form.evidence-register').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true})));
  assert.equal(commands,1);assert.equal(ui.props.guard.current.saving,true);
  await act(async()=>rejectSave(Error('模拟失败')));
  assert.equal(ui.props.guard.current.saving,false);assert.equal(ui.props.guard.current.dirty,true);
  assert.equal(document.querySelector('[name="eventMatched"]').checked,true);
  await click(document.querySelector('[name="observationDecision"][value="pending"]'));
  assert.equal(Boolean(ui.props.guard.current.dirty),false,'uncertainty is not a pending positive observation');
 }finally{await ui.close();}
});

for(const [key,saved,draft] of [
 ['website',{url:'https://example.test/live'},{url:'https://example.test/draft'}],
 ['ad',{adName:'正式广告',objective:'website'},{adName:'草稿广告',objective:'website'}],
 ['server',{event:'Lead',dedup:true},{event:'Contact',dedup:false}],
])test(`${key} reopened draft is never labelled as the displayed saved configuration`,async()=>{
 const loop=fixture();loop.configs[key]=saved;loop.drafts[key]=draft;
 const ui=await mount({loop,selectedKey:key});
 try{
  const panel=document.getElementById(`guide-panel-${key}`);
  const field=panel.querySelector('.guide-fields input:not([type="checkbox"]),.guide-fields select');
  if(key==='website')assert.equal(field.value,'https://example.test/draft');
  const feedback=panel.querySelector('.guide-save-feedback');
  assert.match(feedback.textContent,/草稿/,'displayed draft must be distinguished from the saved configuration');
  assert.match(document.querySelector(`#guide-toggle-${key} .guide-step-status`).textContent,/草稿/);
  assert.match(document.querySelector(`[data-step="${key}"] [role="img"]`).getAttribute('aria-label'),/草稿/);
  assert.match(document.querySelector(`select[aria-label="选择引导步骤"] option[value="${key}"]`).textContent,/草稿/);
  assert.equal(Boolean(ui.props.guard.current.dirty),false,'persisted draft is not an unsaved edit');
  if(key==='server')assert.ok(visible(panel.querySelector('[aria-label="编辑回传约定"]')),'pending contract draft is visible rather than hidden behind the saved summary');
  ui.props.loop={...loop,configs:{...loop.configs,[key]:draft},drafts:{}};
  await ui.render();
  assert.doesNotMatch(feedback.textContent,/草稿/);
  assert.match(feedback.textContent,/已保存/);
  assert.doesNotMatch(document.querySelector(`[data-step="${key}"] [role="img"]`).getAttribute('aria-label'),/草稿/);
 }finally{await ui.close();}
});

for(const [key,target,label] of [['dataset','meta-adset','只查看转化事件'],['ad','closed-loop-test','只查看测试指引']]){
 test(`${key} keeps preview-only navigation beside saving without discarding edits or writing`,async()=>{
  const writes=[];const ui=await mount({selectedKey:key,update:async(...args)=>{writes.push(args);return true;}});
  try{
   const panel=document.getElementById(`guide-panel-${key}`);
   const input=panel.querySelector('.guide-fields input:not([type="radio"])');
   await act(async()=>{Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set.call(input,'keep-unsaved');input.dispatchEvent(new window.Event('input',{bubbles:true}));});
   const preview=[...panel.querySelectorAll('.guide-step-actions button')].find(b=>b.textContent.includes(label));
   assert.ok(preview,'preview-only navigation is discoverable in the save footer');
   await click(preview);
   assert.ok(visible(document.getElementById(`guide-panel-${target}`)));
   assert.equal(writes.length,0,'reading the next instructions never saves configuration');
   await click(document.querySelector(`[data-step="${key}"]`));
   assert.equal(input.value,'keep-unsaved');assert.equal(ui.props.guard.current.dirty,true);
   assert.equal(panel.querySelectorAll('.guide-dataset-next').length,0,'no second competing next-step bar');
  }finally{await ui.close();}
 });
}

test('conversion footer retains test navigation and warning without a second action region',async()=>{
 const ui=await mount({selectedKey:'meta-adset',update:()=>{throw Error('reading cannot save');}});
 try{
  const panel=document.getElementById('guide-panel-meta-adset');
  const footer=panel.querySelector('.guide-step-actions');
  const preview=[...footer.querySelectorAll('button')].find(b=>b.textContent.includes('只查看测试指引'));
  assert.ok(preview,'test preview remains discoverable alongside the continuation');
  assert.match(footer.textContent,/没有读取 Meta 的实时事件接收状态/);
  assert.equal(panel.querySelector('.meta-start-stop'),null);
  await click(preview);assert.ok(visible(document.getElementById('guide-panel-closed-loop-test')));
 }finally{await ui.close();}
});

test('reading options retain the record destination without a second task footer',async()=>{
 const destinations=[];const ui=await mount({navigate:path=>destinations.push(path)});
 try{
  const options=document.querySelector('details.guide-reading-options');
  const records=[...options.querySelectorAll('button')].find(b=>b.textContent.includes('核对与实操记录'));
  assert.ok(records,'supplementary instructions retain the record action');
  assert.equal(document.querySelector('.guide-task-workbench>.guidance-footer'),null);
  await click(options.querySelector('summary'));
  assert.equal(options.open,true);
  await click(records);assert.deepEqual(destinations,['/flow/guide-one?view=verification']);
  assert.ok(document.querySelector('nav[aria-label="引导步骤"]'),'core step navigation remains available');
 }finally{await ui.close();}
});

test('compact task selector navigates every step and references without discarding edits',async()=>{
 const changed=[],destinations=[];const ui=await mount({onStepChange:key=>changed.push(key),navigate:path=>destinations.push(path)});
 try{
  const select=document.querySelector('select[aria-label="选择引导步骤"]');
  assert.ok(select,'narrow screens need a complete compact step control');
  assert.equal(select.value,'website');
  const input=document.querySelector('#guide-panel-website input');
  await act(async()=>{Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set.call(input,'https://example.test/keep-edit');input.dispatchEvent(new window.Event('input',{bubbles:true}));});
  assert.match([...select.options].find(o=>o.value==='website').textContent,/未保存/);
  for(const key of ['pixel','closed-loop-test','illustrations','website','records']){
   await act(async()=>{select.value=key;select.dispatchEvent(new window.Event('change',{bubbles:true}));});
   if(key==='records')assert.equal(destinations.at(-1),'/flow/guide-one?view=verification');
   else assert.equal(changed.at(-1),key);
  }
  assert.equal(input.value,'https://example.test/keep-edit');
  assert.equal(ui.props.guard.current.dirty,true);
  assert.equal(document.querySelectorAll('nav[aria-label="引导步骤"] [data-step]').length,10,'desktop navigation remains complete');
 }finally{await ui.close();}
});

test('image library filters sixteen original pictures and returns without losing task input',async()=>{
 const changed=[];const ui=await mount({onStepChange:key=>changed.push(key)});
 const button=text=>[...document.querySelectorAll('button')].find(node=>node.textContent===text);
 try{
  const input=document.querySelector('#guide-panel-website input');
  await act(async()=>{Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set.call(input,'https://example.test/unsaved');input.dispatchEvent(new window.Event('input',{bubbles:true}));});
  assert.ok(button('全部操作图示'),'library needs a visible navigation entry');await click(button('全部操作图示'));
  const library=document.querySelector('[aria-label="操作图示库"]');assert.ok(visible(library));assert.equal(library.querySelectorAll('.guide-image-card').length,16);assert.equal(changed.at(-1),'illustrations');
  assert.equal(document.activeElement.textContent,'操作图示');
  await click(button('平台设置 8'));assert.equal(library.querySelectorAll('.guide-image-card').length,8);
  await click(button('接入与测试 8'));assert.equal(library.querySelectorAll('.guide-image-card').length,8);
  await click(library.querySelector('.guide-image-card button'));
  assert.ok(document.querySelector('dialog[open] img'));assert.match(document.querySelector('dialog[open]').textContent,/不是当前实例的实时证据/);
  await click(document.querySelector('button[aria-label="关闭弹窗"]'));
  assert.equal(document.querySelector('dialog[open]'),null);
  await click(button('返回配置任务'));assert.ok(visible(document.querySelector('#guide-panel-website')));assert.equal(input.value,'https://example.test/unsaved');assert.equal(changed.at(-1),'website');
 }finally{await ui.close();}
});

test('step navigation leaves clearance below the fixed host header',async()=>{
 const ui=await mount();const style=document.createElement('style');
 const root=document.getElementById('root');root.className='flow-crm-root';
 style.textContent=readFileSync(new URL('../src/guide-redesign.css',import.meta.url),'utf8');document.head.append(style);
 try{assert.ok(parseFloat(window.getComputedStyle(document.getElementById('guide-toggle-crm')).scrollMarginTop)>=72,'scroll target needs clearance for the 60px fixed header');}
 finally{style.remove();root.className='';await ui.close();}
});

test('form and CRM use the same saved association with return-to-scope and truthful loading state',async()=>{
 let resolve;
 const ui=await mount({selectedKey:'crm',api:{runtimeLoad:()=>new Promise(r=>resolve=r)}});try{
  assert.match(document.querySelector('#guide-panel-crm').textContent,/正在读取04已保存的表单关联/);
  await act(async()=>resolve({runtime:{version:2,runtimeState:'not_applied',blockingChecks:[],preservedPixelIds:[],overlaps:[],draft:{landingPaths:['/'],leadPaths:['/contact/','/ar/contact/'],formId:7},forms:[{id:7,name:'项目询盘',code:'project-inquiry'}],pageCatalog:{available:true,pages:[]}}}));
  for(const key of ['form','crm']){
   const panel=document.querySelector(`#guide-panel-${key}`);
   assert.match(panel.textContent,/项目询盘 · project-inquiry/);assert.match(panel.textContent,/\/ar\/contact\//);
   assert.match(panel.textContent,/表单成功入库之后/);
  }
  const crm=document.querySelector('#guide-panel-crm');
  assert.equal(crm.querySelectorAll('select').length,2,'CRM rules remain the only editable selections');
  assert.match(crm.textContent,/邮件通知失败不等于询盘提交失败/);
  await click([...crm.querySelectorAll('button')].find(b=>b.textContent.includes('返回04修改页面与表单')));
  assert.ok(visible(document.querySelector('#guide-panel-pixel')));
  assert.equal(document.querySelector('#pixel-landingPaths').parentElement.open,true);
 }finally{await ui.close();}
});

test('guide task navigation changes the active panel without discarding unsaved input',async()=>{
  const ui=await mount();try{
    const nav=document.querySelector('nav[aria-label="引导步骤"]');
    assert.ok(nav,'configuration needs an always-available task navigation');
    assert.equal(nav.querySelectorAll('[data-step]').length,10);
    const input=document.querySelector('#guide-panel-website input');
    await act(async()=>{
      Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set.call(input,'https://example.test/contact/');
      input.dispatchEvent(new window.Event('input',{bubbles:true}));
    });
    assert.equal(ui.props.guard.current.dirty,true);
    await click(nav.querySelector('[data-step="crm"]'));
    assert.ok(visible(document.getElementById('guide-panel-crm')));
    assert.ok(!visible(document.getElementById('guide-panel-website')));
    assert.equal(nav.querySelector('[aria-current="step"]').dataset.step,'crm');
    await click(nav.querySelector('[data-step="website"]'));
    assert.equal(input.value,'https://example.test/contact/');
    assert.equal(ui.props.guard.current.dirty,true);
  }finally{await ui.close();}
});

test('association read failure offers retry and never labels the saved form missing',async()=>{
 let reads=0;
 const ui=await mount({selectedKey:'crm',api:{runtimeLoad:async()=>{reads++;throw Error('读取失败（模拟网络故障）');}}});try{
  const association=document.querySelector('#guide-panel-crm .guide-form-association');
  assert.match(association.textContent,/表单关联读取失败/);
  assert.doesNotMatch(association.textContent,/请先到04选择并保存表单/);
  const before=reads;await click(association.querySelector('button'));
  assert.equal(reads,before+1,'retry invokes the existing runtime reader');
 }finally{await ui.close();}
});

test('task navigation does not replace the direct plan configuration interface',async()=>{
  const ui=await mount({direct:true});try{
    assert.equal(document.querySelector('nav[aria-label="引导步骤"]'),null);
    assert.ok(document.querySelector('nav[aria-label="配置类别"]'));
  }finally{await ui.close();}
});

test('step changes publish a resumable location without saving or remounting inputs',async()=>{
  const selected=[];
  const ui=await mount({onStepChange:key=>selected.push(key)});try{
    assert.deepEqual(selected,[],'initial URL selection must not write the route back');
    await click(document.querySelector('[data-step="crm"]'));
    assert.deepEqual(selected,['crm']);
    await click(document.querySelector('[data-step="website"]'));
    assert.deepEqual(selected,['crm','website']);
  }finally{await ui.close();}
});

test('reading instructions remain available without displacing the current task',async()=>{
  const ui=await mount();try{
    const help=document.querySelector('details.guide-reading-options');
    assert.ok(help,'secondary reading controls belong in a disclosure');
    assert.equal(help.open,false);
    await click(help.querySelector('summary'));
    assert.equal(help.open,true);
    await click([...help.querySelectorAll('button')].find(b=>b.textContent.includes('全部展开')));
    assert.ok(visible(document.getElementById('guide-panel-website')));
    assert.ok(visible(document.getElementById('guide-panel-crm')));
    await click(document.querySelector('[data-step="crm"]'));
    assert.ok(!visible(document.getElementById('guide-panel-website')));
    assert.ok(visible(document.getElementById('guide-panel-crm')));
  }finally{await ui.close();}
});

test('preparation presents the reference beside the instructions and continues without a fake save',async()=>{
  const ui=await mount({selectedKey:'meta-start',update:()=>{throw Error('Reading a tutorial must not save config');}});try{
    const panel=document.getElementById('guide-panel-meta-start');
    const figure=panel.querySelector('figure.guide-reference-figure');
    assert.ok(figure,'the preparation image must be visible without opening a popover');
    assert.ok(visible(figure.querySelector('img')));
    assert.ok(figure.querySelector('a[href]'),'full size reference remains available');
    const next=[...panel.querySelectorAll('button')].find(b=>b.textContent.includes('继续：选择数据集'));
    assert.ok(next);
    await click(next);
    assert.ok(visible(document.getElementById('guide-panel-dataset')));
    assert.equal(document.querySelector('[aria-current="step"]').dataset.step,'dataset');
  }finally{await ui.close();}
});

for(const surface of ['guide','plans'])test(`${surface} observation navigation requires explicit save and cannot leave an in-flight or failed submission`,async()=>{
 const appBuild=await build({entryPoints:[fileURLToPath(new URL('../src/App.jsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',external:['react','react-dom'],loader:{'.css':'empty','.png':'dataurl','.svg':'dataurl'},logLevel:'silent'});
 const appModule={exports:{}};new Function('require','module','exports',appBuild.outputFiles[0].text)(createRequire(import.meta.url),appModule,appModule.exports);
 globalThis.location=window.location;window.scrollTo=()=>{};
 const route=`#/flow/guide-one?view=${surface==='plans'?'verification':'tasks&node=closed-loop-test'}`;
 window.history.replaceState(null,'',route);
 const evidence={revisionKey:'revision-test',inquiries:1,serverAccepted:1,pending:0,failed:0,skipped:0,recentEvents:[{eventId:'synthetic-event-one',status:'sent'}],observations:[]};
 let resolveSave,rejectSave,commands=0;
 const api={runtimeLoad:async()=>({evidence,canOperate:true}),runtimeCommand:()=>{commands++;return new Promise((resolve,reject)=>{resolveSave=resolve;rejectSave=reject;});}};
 const root=createRoot(document.getElementById('root'));
 const button=text=>[...document.querySelectorAll('button')].find(b=>b.textContent===text);
 const leave=()=>click(button(surface==='plans'?'方案列表':'切换闭环'));
 try{
  await act(async()=>root.render(React.createElement(appModule.exports.App,{crm:true,surface,initialData:{loops:[fixture()]},api})));
  const select=document.querySelector('[aria-label="核对的事件编号"]');
  await act(async()=>{select.value='synthetic-event-one';select.dispatchEvent(new window.Event('change',{bubbles:true}));});
  if(surface==='plans'){
   const source=document.querySelector('[aria-label="实际观察结果"]');
   await act(async()=>{source.value='meta_browser';source.dispatchEvent(new window.Event('change',{bubbles:true}));});
  }else{
   await click(document.querySelector('[data-observation="meta_browser"]'));
   await click(document.querySelector('[name="observationDecision"][value="observed"]'));
  }
  await click(document.querySelector('[name="eventMatched"]'));
  if(surface==='plans'){
   const note=document.querySelector('.crm-evidence-form textarea');
   await act(async()=>{Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set.call(note,'仅本地模拟，仍需继续核对');note.dispatchEvent(new window.Event('input',{bubbles:true}));});
  }
  await leave();
  assert.equal(Boolean(document.querySelector('dialog[open]')),true,'unsaved observation must open leave protection');
  assert.equal(Boolean(button('保存草稿并离开')),false,'leaving cannot certify an observation');
  assert.equal(Boolean(button('保存记录并离开')),false);
  assert.equal(commands,0);
  await click(button('继续编辑'));
  await act(async()=>document.querySelector('form.evidence-register').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true})));
  await leave();
  assert.equal(location.hash,route);assert.equal(Boolean(document.querySelector('dialog[open]')),false,'in-flight writes cannot be discarded through a modal');
  assert.match(document.querySelector('.global-error').textContent,/保存/);
  const unload=new window.Event('beforeunload',{cancelable:true});window.dispatchEvent(unload);
  assert.equal(unload.defaultPrevented,true,'browser reload also protects an in-flight observation');
  await act(async()=>rejectSave(Error('模拟观察保存失败')));
  assert.equal(document.querySelector('[name="eventMatched"]').checked,true);
  await leave();assert.equal(Boolean(document.querySelector('dialog[open]')),true);
  await click(button('继续编辑'));
  await act(async()=>document.querySelector('form.evidence-register').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true})));
  assert.equal(commands,2);
  await act(async()=>resolveSave({evidence:{...evidence,observations:[{eventId:'synthetic-event-one',source:'meta_browser',currentCredential:true}]},canOperate:true}));
  await leave();
  if(surface==='plans'){
   assert.equal(Boolean(document.querySelector('dialog[open]')),true,'saving observation must not clear the separate unsaved record');
   assert.ok(button('保存记录并离开'));
   await click(button('继续编辑'));
   const note=document.querySelector('.crm-evidence-form textarea');assert.equal(note.value,'仅本地模拟，仍需继续核对');
   await act(async()=>{Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set.call(note,'');note.dispatchEvent(new window.Event('input',{bubbles:true}));});
   await leave();
  }
  assert.equal(location.hash,'#/loops');assert.equal(Boolean(document.querySelector('dialog[open]')),false,'successful explicit save releases its own guard');
 }finally{await act(async()=>root.unmount());}
});

test('real app keeps dirty panels mounted while updating the step URL and still guards leaving',async()=>{
  const appBuild=await build({entryPoints:[fileURLToPath(new URL('../src/App.jsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',external:['react','react-dom'],loader:{'.css':'empty','.png':'dataurl','.svg':'dataurl'},logLevel:'silent'});
  const appModule={exports:{}};
  new Function('require','module','exports',appBuild.outputFiles[0].text)(createRequire(import.meta.url),appModule,appModule.exports);
  globalThis.location=window.location;window.scrollTo=()=>{};
  window.history.replaceState(null,'','#/flow/guide-one?view=tasks&node=website');
  const root=createRoot(document.getElementById('root'));
  try{
    await act(async()=>root.render(React.createElement(appModule.exports.App,{crm:true,initialData:{loops:[fixture()]},api:{}})));
    const input=document.querySelector('#guide-panel-website input');
    await act(async()=>{
      Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set.call(input,'https://example.test/kept/');
      input.dispatchEvent(new window.Event('input',{bubbles:true}));
    });
    await click(document.querySelector('[data-step="crm"]'));
    assert.match(location.hash,/view=tasks&node=crm$/);
    assert.ok(visible(document.getElementById('guide-panel-crm')));
    assert.ok(document.querySelector('#guide-panel-website input')===input,'step navigation cannot remount inputs');
    assert.equal(input.value,'https://example.test/kept/');
    assert.ok(!document.querySelector('dialog[open]'));
    await click([...document.querySelectorAll('button')].find(b=>b.textContent==='切换闭环'));
    assert.match(document.body.textContent,/保存草稿并离开/,'leaving the instance remains protected');
  }finally{await act(async()=>root.unmount());}
});

test('saving two dirty drafts before leaving consumes each returned transaction version',async()=>{
 const appBuild=await build({entryPoints:[fileURLToPath(new URL('../src/App.jsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',external:['react','react-dom'],loader:{'.css':'empty','.png':'dataurl','.svg':'dataurl'},logLevel:'silent'});
 const appModule={exports:{}};new Function('require','module','exports',appBuild.outputFiles[0].text)(createRequire(import.meta.url),appModule,appModule.exports);
 globalThis.location=window.location;window.scrollTo=()=>{};
 window.history.replaceState(null,'','#/flow/guide-one?view=tasks&node=website');
 let stored=fixture();const received=[];
 // Transport double enforces the same expected-version contract as CRM.
 const api={load:async()=>({loops:[stored]}),command:async(action,target,payload)=>{
  received.push({node:payload.nodeKey,version:target.version});
  if(target.version!==stored.version)throw Object.assign(Error('version conflict'),{status:409,current:stored});
  assert.equal(action,'save_draft');
  stored={...stored,version:stored.version+1,drafts:{...stored.drafts,[payload.nodeKey]:payload.values}};
  return {loop:stored};
 }};
 const root=createRoot(document.getElementById('root'));
 try{
  await act(async()=>root.render(React.createElement(appModule.exports.App,{crm:true,initialData:{loops:[stored]},api})));
  for(const [key,value] of [['website','https://example.test/sequence'],['ad','连续保存广告']]){
   await click(document.querySelector(`[data-step="${key}"]`));
   const input=document.querySelector(`#guide-panel-${key} .guide-fields input:not([readonly])`);
   await act(async()=>{Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set.call(input,value);input.dispatchEvent(new window.Event('input',{bubbles:true}));});
  }
  await click([...document.querySelectorAll('button')].find(b=>b.textContent==='切换闭环'));
  await click([...document.querySelectorAll('dialog button')].find(b=>b.textContent.includes('保存草稿并离开')));
  assert.deepEqual(received,[{node:'website',version:1},{node:'ad',version:2}]);
  assert.equal(stored.drafts.website.url,'https://example.test/sequence');
  assert.ok(Object.values(stored.drafts.ad).includes('连续保存广告'));
  assert.equal(location.hash,'#/loops');assert.equal(document.querySelector('dialog[open]'),null);
 }finally{await act(async()=>root.unmount());}
});

test('leaving saves ordinary and Pixel drafts across an asynchronous busy render',async()=>{
 const appBuild=await build({entryPoints:[fileURLToPath(new URL('../src/App.jsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',external:['react','react-dom'],loader:{'.css':'empty','.png':'dataurl','.svg':'dataurl'},logLevel:'silent'});
 const appModule={exports:{}};new Function('require','module','exports',appBuild.outputFiles[0].text)(createRequire(import.meta.url),appModule,appModule.exports);
 globalThis.location=window.location;window.scrollTo=()=>{};
 window.history.replaceState(null,'','#/flow/guide-one?view=tasks&node=website');
 let stored=fixture(),releaseWebsite;const received=[];
 const runtime={version:2,draft:{landingPaths:['/','/contact/'],leadPaths:['/contact/'],formId:1,consentRequired:true},integration:{id:1},runtimeState:'not_applied',configState:'saved',blockingChecks:[],preservedPixelIds:[],overlaps:[],history:[],sharedFlows:[],dependencies:[],forms:[{id:1,name:'项目询盘',code:'project-inquiry'}],pageCatalog:{available:true,pages:[{path:'/',title:'首页',locale:'en',formCodes:[]},{path:'/contact/',title:'联系页面',locale:'en',formCodes:['project-inquiry']}]}};
 // Only the transport is controlled: actual App, Guidance, PixelStep and locks run.
 const api={load:async()=>({loops:[stored]}),runtimeLoad:async()=>({runtime}),command:async(action,target,payload)=>{
  received.push({action,version:target.version});
  if(target.version!==stored.version)throw Object.assign(Error('version conflict'),{status:409,current:stored});
  if(action==='save_draft'){
   await new Promise(resolve=>{releaseWebsite=resolve;});
   stored={...stored,version:stored.version+1,drafts:{...stored.drafts,[payload.nodeKey]:payload.values}};
  }else{
   assert.equal(action,'save_pixel_step');assert.equal(payload.intent,'draft');assert.equal(payload.expectedRuntimeVersion,2);
   stored={...stored,version:stored.version+1,drafts:{...stored.drafts,pixel:{scope:payload.scope}},savedRuntimeVersion:2};
  }
  return {loop:stored};
 }};
 const root=createRoot(document.getElementById('root'));
 try{
  await act(async()=>root.render(React.createElement(appModule.exports.App,{crm:true,initialData:{loops:[stored]},api})));
  const input=document.querySelector('#guide-panel-website input');
  await act(async()=>{Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set.call(input,'https://example.test/combined');input.dispatchEvent(new window.Event('input',{bubbles:true}));});
  await click(document.querySelector('[data-step="pixel"]'));
  await click(document.querySelector('button[aria-label="从记录浏览的页面移除 /"]'));
  await click([...document.querySelectorAll('button')].find(b=>b.textContent==='切换闭环'));
  await click([...document.querySelectorAll('dialog button')].find(b=>b.textContent.includes('保存草稿并离开')));
  assert.equal(document.querySelector('.app-shell').getAttribute('aria-busy'),'true');
  assert.equal(document.querySelector('#pixel-formId').disabled,true);
  await act(async()=>releaseWebsite());
  assert.deepEqual(received,[{action:'save_draft',version:1},{action:'save_pixel_step',version:2}]);
  assert.equal(stored.drafts.website.url,'https://example.test/combined');
  assert.deepEqual(stored.drafts.pixel.scope.landingPaths,['/contact/']);
  assert.deepEqual(stored.drafts.pixel.scope.leadPaths,['/contact/']);
  assert.equal(location.hash,'#/loops');assert.equal(Boolean(document.querySelector('dialog[open]')),false);
 }finally{await act(async()=>root.unmount());}
});

test('aggregate draft save retains the Pixel submission selected before the ordinary busy render',async()=>{
 let release;const actions=[];
 const runtime={version:2,draft:{landingPaths:['/','/contact/'],leadPaths:['/contact/'],formId:1,consentRequired:true},integration:{id:1},runtimeState:'not_applied',configState:'saved',blockingChecks:[],preservedPixelIds:[],overlaps:[],history:[],sharedFlows:[],dependencies:[],forms:[{id:1,name:'项目询盘',code:'project-inquiry'}],pageCatalog:{available:true,pages:[]}};
 const ui=await mount({api:{runtimeLoad:async()=>({runtime})},update:async(_loop,_message,change)=>{
  actions.push(change);if(change.action==='save_draft')await new Promise(resolve=>{release=resolve;});
  return {savedRuntimeVersion:2};
 }});
 try{
  const input=document.querySelector('#guide-panel-website input');
  await act(async()=>{Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set.call(input,'https://example.test/combined');input.dispatchEvent(new window.Event('input',{bubbles:true}));});
  await click(document.querySelector('[data-step="pixel"]'));
  await click(document.querySelector('button[aria-label="从记录浏览的页面移除 /"]'));
  let pending;await act(async()=>{pending=ui.props.guard.current.save();});
  // Parent's in-flight render is visible until its response-state render commits.
  ui.props.busy=true;await ui.render();
  assert.equal(document.querySelector('#pixel-formId').disabled,true);
  let saved;await act(async()=>{release();saved=await pending;});
  assert.equal(saved,true,'the same submission must save Pixel too, not silently return false');
  assert.deepEqual(actions.map(a=>a.action),['save_draft','save_pixel_step']);
  assert.deepEqual(actions[1].scope.landingPaths,['/contact/']);
 }finally{await ui.close();}
});

test('dataset and destination references are inline next to the actual saved fields',async()=>{
  const loop=fixture();loop.configs.dataset={source:'created',externalId:'123456789',alias:'原备注'};loop.configs.website={url:'https://example.test/contact/?campaign=one'};
  const ui=await mount({loop,selectedKey:'dataset'});try{
    const panel=document.getElementById('guide-panel-dataset');
    const figures=panel.querySelectorAll('.guide-instructions figure.guide-reference-figure');
    assert.equal(figures.length,2,'both creation references must be visible without opening help');
    for(const figure of figures){assert.ok(!figure.closest('details'));assert.ok(figure.querySelector('a[href]'));}
    assert.equal(panel.querySelector('input[inputmode="numeric"]').readOnly,true);
    const sources=[...panel.querySelectorAll('input[type="radio"]')];
    assert.equal(sources.length,2);
    assert.ok(sources.every(input=>input.disabled),'visual refactor must preserve binding lock');
    await click(document.querySelector('[data-step="website"]'));
    assert.ok(document.querySelector('#guide-panel-website .guide-instructions figure img'));
    await click(document.querySelector('[data-step="ad"]'));
    const ad=document.getElementById('guide-panel-ad');
    assert.ok(ad.querySelector('.guide-instructions figure img'));
    assert.match(ad.textContent,/不要求相同/);
    assert.match(ad.textContent,/API/);
    assert.equal(ad.querySelector('.guide-ad-destination input').value,'https://example.test/contact/?campaign=one');
    assert.equal(ad.querySelector('.guide-ad-destination input').readOnly,true);
    await click(ad.querySelector('.guide-ad-destination button'));
    assert.ok(visible(document.getElementById('guide-panel-website')),'shared landing address is edited at its owning step');
  }finally{await ui.close();}
});

test('dataset source cards clear obsolete selection and keep edits while reading help',async()=>{
  const loop=fixture();loop.drafts.dataset={source:'created',externalId:'123456789',alias:'保持备注'};
  const ui=await mount({loop,selectedKey:'dataset'});try{
    const panel=document.getElementById('guide-panel-dataset');
    const existing=panel.querySelector('input[type="radio"][value="existing"]');
    assert.ok(existing);await click(existing);
    assert.equal(panel.querySelector('.guide-fields select').value,'','changing source resets the stale identity');
    await click(panel.querySelector('input[type="radio"][value="created"]'));
    assert.equal(panel.querySelector('input[inputmode="numeric"]').value,'');
    assert.equal(panel.querySelector('textarea').value,'保持备注');
    await click(panel.querySelector('details.guide-path summary'));
    assert.equal(panel.querySelector('textarea').value,'保持备注');
    assert.equal(ui.props.guard.current.dirty,true);
  }finally{await ui.close();}
});

test('conversion task shows route and Lead references and continues without saving',async()=>{
  const ui=await mount({selectedKey:'meta-adset',update:()=>{throw Error('instruction cannot save config');}});try{
    const panel=document.getElementById('guide-panel-meta-adset');
    const figures=panel.querySelectorAll('.guide-conversion-layout figure');
    assert.equal(figures.length,2);
    for(const figure of figures)assert.ok(!figure.closest('details'),'core reference must not be hidden');
    const detail=panel.querySelector('details.guide-conversion-detail');
    assert.ok(detail,'original attribution and value rules remain accessible');
    await click(detail.querySelector('summary'));assert.equal(detail.open,true);
    assert.match(detail.textContent,/归因设置/);assert.match(detail.textContent,/价值规则/);
    await click([...panel.querySelectorAll('button')].find(b=>b.textContent.includes('继续：登记落地页')));
    assert.ok(visible(document.getElementById('guide-panel-website')));
  }finally{await ui.close();}
});
