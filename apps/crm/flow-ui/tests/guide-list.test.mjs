import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import React,{act} from 'react';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<div id="root"></div>',{url:'https://example.test/'});
Object.assign(globalThis,{window:dom.window,document:dom.window.document,IS_REACT_ACT_ENVIRONMENT:true});
const {createRoot}=await import('react-dom/client');
const compiled=await build({entryPoints:[fileURLToPath(new URL('../src/GuideList.jsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',external:['react'],logLevel:'silent'});
const module={exports:{}};
new Function('require','module','exports',compiled.outputFiles[0].text)(createRequire(import.meta.url),module,module.exports);
const {GuideList}=module.exports;
const fixture=(id,extra={})=>({id,name:`闭环${id}`,code:id,provider:'meta',preset:'website',environment:'test',configs:{},drafts:{},updatedAt:'2026-10-10T01:00:00Z',...extra});
test('archived instance names and record actions open their own history even when the guide is closed',async()=>{
 const calls=[];const root=createRoot(document.getElementById('root'));
 try{
  await act(async()=>root.render(React.createElement(GuideList,{loops:[fixture('D',{archived:true}),fixture('E',{archived:true,guideClosed:true})],navigate:path=>calls.push(path),onAction:()=>{}})));
  await act(async()=>document.querySelector('[data-state="archived"]').click());
  for(const id of ['D','E']){
   const row=document.querySelector(`[data-flow="${id}"]`);
   await act(async()=>row.querySelector('[data-continue]').click());
   await act(async()=>row.querySelector('.guide-list-name').click());
  }
  assert.deepEqual(calls,['/flow/D?view=verification','/flow/D?view=verification','/flow/E?view=verification','/flow/E?view=verification']);
 }finally{await act(async()=>root.unmount());}
});
test('same-type instances have separate next-step links and closed/archived tabs',async()=>{
 const calls=[];const root=createRoot(document.getElementById('root'));
 try{
  await act(async()=>root.render(React.createElement(GuideList,{loops:[fixture('A'),fixture('B',{configs:{dataset:{externalId:'90001'}}}),fixture('C',{guideClosed:true}),fixture('D',{archived:true})],navigate:x=>calls.push(x),onAction:()=>{}})));
  assert.equal(document.querySelectorAll('tbody tr').length,2);
  await act(async()=>document.querySelector('[data-flow="B"] [data-continue]').click());
  assert.deepEqual(calls,['/flow/B?view=tasks&node=website']);
  await act(async()=>document.querySelector('[data-state="closed"]').click());
  assert.equal(document.querySelector('tbody tr').dataset.flow,'C');
  await act(async()=>document.querySelector('[data-state="archived"]').click());
  assert.equal(document.querySelector('tbody tr').dataset.flow,'D');
 }finally{await act(async()=>root.unmount());}
});
test('load failures hide stale runtime status while preserving instance links and retry',async()=>{
 let retried=0;const root=createRoot(document.getElementById('root'));
 try{
  await act(async()=>root.render(React.createElement(GuideList,{loops:[fixture('A',{runtime:{state:'applied'}})],navigate:()=>{},onAction:()=>{},collectionState:{error:'读取失败'},onRetry:()=>retried++})));
  assert.doesNotMatch(document.body.textContent,/运行版本已应用/);
  assert.match(document.body.textContent,/状态待读取/);
  await act(async()=>[...document.querySelectorAll('button')].find(b=>b.textContent==='重新读取').click());
  assert.equal(retried,1);
 }finally{await act(async()=>root.unmount());}
});

test('an existing saved instance resumes its pending draft rather than skipping to testing',async()=>{
 const calls=[];const root=createRoot(document.getElementById('root'));
 const configs=Object.fromEntries(['dataset','website','pixel','form','crm','server','ad'].map(key=>[key,{}]));
 try{
  await act(async()=>root.render(React.createElement(GuideList,{loops:[fixture('A',{configs,drafts:{website:{url:'https://example.test/draft'}}})],navigate:path=>calls.push(path),onAction:()=>{}})));
  const row=document.querySelector('[data-flow="A"]');
  assert.match(row.textContent,/草稿已暂存/);
  await act(async()=>row.querySelector('[data-continue]').click());
  assert.deepEqual(calls,['/flow/A?view=tasks&node=website']);
  assert.match(row.querySelector('[data-continue]').textContent,/配置/);
 }finally{await act(async()=>root.unmount());}
});
