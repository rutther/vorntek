import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {act} from 'react';
import {JSDOM} from 'jsdom';

const compiled=await build({entryPoints:[fileURLToPath(new URL('../src/App.jsx',import.meta.url))],bundle:true,write:false,
 platform:'node',format:'cjs',external:['react','react-dom','react-dom/server'],loader:{'.css':'empty','.png':'dataurl','.svg':'dataurl'},logLevel:'silent'});
const output={exports:{}};
new Function('require','module','exports',compiled.outputFiles[0].text)(createRequire(import.meta.url),output,output.exports);
const loop={id:'plan-example',name:'长期方案演练',code:'PLAN-TEST',environment:'test',provider:'meta',preset:'website',configs:{},drafts:{},
 filterEnabled:false,positions:{},revision:1,version:1,check:null,history:[],archived:false,guideClosed:true,updatedAt:'2026-10-08T00:00:00Z',sources:[],
 runtime:{state:'applied',activeRevision:'synthetic-version'}};
function render(hash,surface='plans',loops=[loop]) { globalThis.location={hash};return renderToStaticMarkup(React.createElement(output.exports.App,{crm:true,surface,initialData:{loops,legacyLoops:[]},api:{}})); }

test('plans expose persistent records and separate applied, guide and evidence states',()=>{
 const html=render('#/loops');
 assert.match(html,/获客方案/);assert.match(html,/新建方案/);assert.match(html,/运行版本已应用/);assert.match(html,/引导已关闭/);
 assert.doesNotMatch(html,/配置进度|下一项待办|返回第 ?07|<h1>流程管理/);
});
test('direct configuration reuses real forms without numbered guide instructions',()=>{
 const html=render('#/flow/plan-example?view=config&node=dataset');
 assert.match(html,/方案配置/);assert.match(html,/数据集 ID/);assert.match(html,/网站与表单/);assert.match(html,/保存配置/);
 assert.doesNotMatch(html,/典型操作指引|到哪里操作|保存本步|返回04|查看第 01 步/);
});
test('closed guide remains available to reopen without implying stopped business',()=>{
 const html=render('#/flow/plan-example?view=tasks','guide');
 assert.match(html,/此引导已关闭/);assert.match(html,/重新打开引导/);assert.match(html,/方案及运行版本不受影响/);
 assert.doesNotMatch(html,/典型操作指引/);
});
test('direct running screen has no guide-origin integration or mandatory step numbers',()=>{
 const html=render('#/flow/plan-example?view=runtime');
 assert.match(html,/运行与版本/);assert.doesNotMatch(html,/先到 04|上方操作登记|07核对/);
});

test('returning to plan list refreshes runtime status instead of showing a stale applied badge',async()=>{
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/#/flow/plan-example?view=config'});
 Object.assign(globalThis,{window:dom.window,document:dom.window.document,location:dom.window.location,IS_REACT_ACT_ENVIRONMENT:true});
 globalThis.requestAnimationFrame=fn=>setTimeout(fn,0);dom.window.scrollTo=()=>{};
 const {createRoot}=await import('react-dom/client');const root=createRoot(document.getElementById('root'));
 let complete;const api={load:()=>new Promise(resolve=>{complete=resolve;})};
 await act(async()=>{root.render(React.createElement(output.exports.App,{crm:true,surface:'plans',initialData:{loops:[loop],legacyLoops:[]},api}));});
 await act(async()=>{Array.from(document.querySelectorAll('button')).find(b=>b.textContent==='方案列表').click();});
 assert.match(document.body.textContent,/正在刷新运行状态/);
 assert.doesNotMatch(document.body.textContent,/运行版本已应用/);
 await act(async()=>{complete({loops:[{...loop,runtime:{state:'paused'}}]});});
 assert.match(document.body.textContent,/方案已暂停/);
 await act(async()=>root.unmount());dom.window.close();
});

test('list refresh failure hides stale runtime and retry restores authoritative state',async()=>{
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/#/loops'});
 Object.assign(globalThis,{window:dom.window,document:dom.window.document,location:dom.window.location,IS_REACT_ACT_ENVIRONMENT:true});
 globalThis.requestAnimationFrame=fn=>setTimeout(fn,0);dom.window.scrollTo=()=>{};
 const {createRoot}=await import('react-dom/client');const root=createRoot(document.getElementById('root'));
 let calls=0;const api={load:async()=>{if(++calls===1)throw new Error('offline');return {loops:[{...loop,runtime:{state:'paused'}}]};}};
 try {
  await act(async()=>root.render(React.createElement(output.exports.App,{crm:true,surface:'plans',initialData:{loops:[loop]},api})));
  assert.match(document.querySelector('[role="alert"]').textContent,/读取失败/);
  assert.doesNotMatch(document.body.textContent,/运行版本已应用/);
  await act(async()=>Array.from(document.querySelectorAll('button')).find(b=>b.textContent==='重新读取').click());
  assert.match(document.body.textContent,/方案已暂停/);assert.equal(calls,2);
 } finally {await act(async()=>root.unmount());dom.window.close();}
});

test('leaving the list cancels a late collection result and preserves list filters',async()=>{
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/#/loops'});
 Object.assign(globalThis,{window:dom.window,document:dom.window.document,location:dom.window.location,IS_REACT_ACT_ENVIRONMENT:true});
 globalThis.requestAnimationFrame=fn=>setTimeout(fn,0);dom.window.scrollTo=()=>{};
 const {createRoot}=await import('react-dom/client');const root=createRoot(document.getElementById('root'));
 let complete;const api={load:()=>new Promise(resolve=>{complete=resolve;})};
 try {
  await act(async()=>root.render(React.createElement(output.exports.App,{crm:true,surface:'plans',initialData:{loops:[loop]},api})));
  const environment=document.querySelector('select[aria-label="方案环境"]');
  await act(async()=>{environment.value='test';environment.dispatchEvent(new dom.window.Event('change',{bubbles:true}));});
  await act(async()=>{location.hash='/flow/plan-example?view=config';window.dispatchEvent(new dom.window.HashChangeEvent('hashchange'));});
  await act(async()=>complete({loops:[{...loop,name:'过期请求不应覆盖此名称'}]}));
  assert.equal(document.querySelector('.plan-heading h1').textContent,loop.name);
  await act(async()=>Array.from(document.querySelectorAll('button')).find(b=>b.textContent==='方案列表').click());
  assert.equal(document.querySelector('select[aria-label="方案环境"]').value,'test');
  await act(async()=>complete({loops:[loop]}));
 } finally {await act(async()=>root.unmount());dom.window.close();}
});
