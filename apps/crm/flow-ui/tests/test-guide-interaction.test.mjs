import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
import {JSDOM} from 'jsdom';
import React,{act} from 'react';
const dom=new JSDOM('<div id="root"></div>',{url:'https://example.test/'});
globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.HTMLElement=dom.window.HTMLElement;globalThis.IS_REACT_ACT_ENVIRONMENT=true;
dom.window.HTMLElement.prototype.scrollIntoView=function(){};
const {createRoot}=await import('react-dom/client');
const compiled=await build({entryPoints:['src/ClosedLoopTestGuide.jsx'],bundle:true,write:false,platform:'node',format:'cjs',external:['react'],logLevel:'silent'});
const module={exports:{}};new Function('require','module','exports',compiled.outputFiles[0].text)(createRequire(import.meta.url),module,module.exports);
const button=text=>[...document.querySelectorAll('button')].find(b=>b.textContent.includes(text));
async function mount(extra={}){
 const props={loop:{id:'guide-a',provider:'meta',preset:'website',configs:{dataset:{externalId:'900000000000009'}},taskKeys:['dataset','website','pixel','form','crm','server','ad']},open:true,onToggle:()=>{},onReveal:()=>{},onRecord:()=>{},...extra};
 const root=createRoot(document.getElementById('root'));await act(async()=>root.render(React.createElement(module.exports.ClosedLoopTestGuide,props)));
 return {close:()=>act(async()=>root.unmount())};
}
test('test entry exposes website and result pictures before choosing an event, with original image access',async()=>{
 const ui=await mount();
 try{
  for(const file of ['meta-open-events-manager-20261009.svg','meta-test-website-events-20261009.svg','meta-test-pageview-received-20261009.svg']){
   const img=document.querySelector(`.guide-test-preparation img[src$="${file}"]`);assert.ok(img,`inline reference missing: ${file}`);
   assert.equal(img.closest('[hidden],details:not([open])'),null,'essential references must not depend on opening help');
   assert.equal(img.closest('a').getAttribute('target'),'_blank');
  }
  assert.ok(document.querySelector('.guide-test-preparation').textContent.includes('900000000000009'));
  assert.equal(document.querySelector('form'),null,'reading a tutorial must not create a fake save action');
 }finally{await ui.close();}
});
test('continue focuses local evidence without recording or submitting, including no-runtime empty state',async()=>{
 const records=[];const ui=await mount({onRecord:key=>records.push(key),children:React.createElement('section',null,React.createElement('h3',null,'核对本次询盘'))});
 try{assert.ok(button('继续：核对本次询盘'));await act(async()=>button('继续：核对本次询盘').click());assert.equal(document.activeElement.className,'evidence-anchor');assert.deepEqual(records,[]);}
 finally{await ui.close();}
});

test('compact test workbench keeps prerequisites and no-write boundaries without a duplicate intro',async()=>{
 const routes=[],records=[];
 const ui=await mount({separateEvidence:true,onReveal:key=>routes.push(key),onRecord:key=>records.push(key)});
 try{
  const heading=document.querySelector('.guide-step-title');
  assert.match(heading.textContent,/先完成接入/);
  assert.match(heading.textContent,/不用先发布广告/);
  assert.equal(document.querySelector('.meta-start-intro'),null,'workbench header owns the preparation reminder');
  assert.ok(document.querySelector('.loop-test-prerequisites'),'missing configuration remains actionable');
  const boundary=document.querySelector('.guide-test-boundary');
  assert.match(boundary.textContent,/不会.*提交.*发送/);
  assert.match(boundary.textContent,/人工记录.*自动验收/);
  const recovery=document.querySelector('.guide-test-recovery');
  assert.match(recovery.textContent,/超时.*CRM/);
  assert.match(recovery.textContent,/一条询盘/);
  await act(async()=>recovery.querySelector('button').click());
  assert.deepEqual(routes,['form']);assert.deepEqual(records,[]);
  assert.equal(document.querySelectorAll('.guide-test-preparation img').length,3);
 }finally{await ui.close();}
});

test('test preparation uses the full saved destination, not a draft or example, and routes edits to its owner',async()=>{
 const destinations=[];
 const ui=await mount({loop:{id:'destination-a',provider:'meta',preset:'website',configs:{website:{url:'https://example.test/ar/contact/?campaign=water#inquiry'}},drafts:{website:{url:'https://example.test/unsaved/'}},taskKeys:[]},onReveal:key=>destinations.push(key)});
 try{
  const address=document.querySelector('[aria-label="本实例已保存的测试网址"]');
  assert.ok(address,'the test action needs this instance’s address, not a screenshot example');
  assert.equal(address.querySelector('code').textContent,'https://example.test/ar/contact/?campaign=water#inquiry');
  assert.doesNotMatch(address.textContent,/unsaved/);
  assert.equal(address.querySelector('a'),null,'reading the guide must not launch a tracked website');
  await act(async()=>address.querySelector('button').click());
  assert.deepEqual(destinations,['website']);
 }finally{await ui.close();}
});

test('an unsaved destination is explicitly missing instead of borrowing another instance or draft',async()=>{
 const ui=await mount({loop:{id:'destination-b',provider:'meta',preset:'website',configs:{},drafts:{website:{url:'https://example.test/draft-only/'}},taskKeys:[]}});
 try{
  const address=document.querySelector('[aria-label="本实例已保存的测试网址"]');
  assert.ok(address);
  assert.match(address.textContent,/尚未登记/);
  assert.equal(address.querySelector('code'),null);
  assert.doesNotMatch(address.textContent,/draft-only/);
 }finally{await ui.close();}
});
test('preparation links retain instance step and record destinations, while detailed test sequence stays available',async()=>{
 const steps=[],records=[];const ui=await mount({onReveal:key=>steps.push(key),onRecord:key=>records.push(key)});
 try{
  await act(async()=>button('核对数据集').click());assert.deepEqual(steps,['dataset']);
  assert.ok(document.querySelector('details.guide-test-details'),'full test instructions have an explicit disclosure');
  assert.equal(document.querySelectorAll('ol[aria-label="闭环测试操作顺序"]>li').length,8);
  await act(async()=>button('继续：核对本次询盘').click());assert.deepEqual(records,['server']);
 }finally{await ui.close();}
});
test('evidence focus has a return action and keeps selected evidence mounted',async()=>{
 const ui=await mount({children:React.createElement('input',{'aria-label':'保留选择',defaultValue:'chosen-event'})});
 try{
  const input=document.querySelector('[aria-label="保留选择"]');
  await act(async()=>button('继续：核对本次询盘').click());
  assert(document.querySelector('.is-evidence-focused'));
  assert(document.querySelector('.guide-test-preparation').closest('[hidden]'));
  const heading=document.querySelector('#guide-toggle-closed-loop-test');
  let focusedWhileHidden=false;
  const nativeFocus=heading.focus.bind(heading);
  heading.focus=()=>{focusedWhileHidden=Boolean(heading.closest('[hidden]'));nativeFocus();};
  await act(async()=>button('返回测试步骤').click());
  assert.equal(focusedWhileHidden,false,'focus must be restored after the heading is revealed');
  assert.equal(document.querySelector('.is-evidence-focused'),null);
  assert.equal(document.querySelector('[aria-label="保留选择"]'),input,'back must not remount the event session');
  assert.equal(input.value,'chosen-event');
  assert.equal(document.querySelector('.guide-test-preparation').closest('[hidden]'),null);
  assert.equal(document.activeElement.id,'guide-toggle-closed-loop-test');
 }finally{await ui.close();}
});

test('embedded evidence owns the return action without losing its selected event',async()=>{
 const ui=await mount({children:onReturn=>React.createElement('section',null,
  React.createElement('input',{'aria-label':'保留事件',defaultValue:'chosen-event'}),
  onReturn&&React.createElement('button',{type:'button',onClick:onReturn},'返回测试步骤'))});
 try{
  const input=document.querySelector('[aria-label="保留事件"]');
  assert.ok(input,'render the evidence session before focus');
  await act(async()=>button('继续：核对本次询盘').click());
  assert.equal([...document.querySelectorAll('button')].filter(b=>b.textContent==='返回测试步骤').length,1);
  await act(async()=>button('返回测试步骤').click());
  assert.equal(document.querySelector('.is-evidence-focused'),null);
  assert.equal(document.querySelector('[aria-label="保留事件"]'),input);
  assert.equal(input.value,'chosen-event');
  assert.equal(document.activeElement.id,'guide-toggle-closed-loop-test');
 }finally{await ui.close();}
});

test('step-by-step preparation defers evidence until continue and preserves the mounted session on return',async()=>{
 const ui=await mount({separateEvidence:true,children:React.createElement('select',{'aria-label':'核对的事件编号',defaultValue:'event-a'},React.createElement('option',{value:'event-a'},'event-a'))});
 try{
  const selector=document.querySelector('select');
  assert.ok(selector.closest('[hidden]'),'preparation must not append the complete next-stage workbench');
  let focusedHidden=false;
  const focus=selector.focus.bind(selector);
  selector.focus=(options)=>{focusedHidden ||= Boolean(selector.closest('[hidden]'));focus(options);};
  await act(async()=>button('继续：核对本次询盘').click());
  assert.equal(selector.closest('[hidden]'),null);
  assert.equal(focusedHidden,false,'focus only after the evidence stage is visible');
  assert.equal(document.activeElement,selector);
  await act(async()=>button('返回测试步骤').click());
  assert.ok(selector.closest('[hidden]'));
  await act(async()=>button('继续：核对本次询盘').click());
  assert.equal(document.querySelector('select'),selector,'do not remount or clear the event session');
  assert.equal(selector.value,'event-a');
 }finally{await ui.close();}
});
