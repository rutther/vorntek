import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
import {JSDOM} from 'jsdom';
import React,{act} from 'react';

const dom=new JSDOM('<div id="root"></div>',{url:'https://example.test/#/flow/A?view=tasks&node=server'});
Object.assign(globalThis,{window:dom.window,document:dom.window.document,location:dom.window.location,HTMLElement:dom.window.HTMLElement,IS_REACT_ACT_ENVIRONMENT:true});
globalThis.requestAnimationFrame=fn=>setTimeout(fn,0);
dom.window.scrollTo=()=>{};
dom.window.HTMLElement.prototype.scrollIntoView=function(){};
const {createRoot}=await import('react-dom/client');
const built=await build({entryPoints:['src/App.jsx'],bundle:true,write:false,platform:'node',format:'cjs',external:['react','react-dom'],loader:{'.css':'empty','.png':'dataurl','.svg':'dataurl'},logLevel:'silent'});
const output={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(createRequire(import.meta.url),output,output.exports);
const scope={landingPaths:['/'],leadPaths:['/contact/'],formId:1,consentRequired:true};
const fixture=id=>({id,name:`本地实例${id}`,code:id,provider:'meta',preset:'website',environment:'test',version:1,revision:1,configs:{dataset:{externalId:'900001'},server:{event:'Lead',dedup:true}},drafts:{},positions:{},sources:[],history:[],taskKeys:['dataset','website','pixel','form','crm','server','ad'],runtime:{state:'not_applied',activeRevision:null,integrationId:1}});
const runtime=(status,version)=>({version,flowVersion:1,configHash:'synthetic',runtimeState:status,configState:'saved',canOperate:true,blockingChecks:[],forms:[{id:1,name:'合成表单'}],draft:scope,integration:{id:1,pixelId:'900001',environment:'test',enabled:true,editUrl:'/local/'},activeRevision:status==='not_applied'?null:{key:'synthetic-revision',scope},history:[],sharedFlows:[],preservedPixelIds:[],overlaps:[],dependencies:[]});
const button=name=>[...document.querySelectorAll('button')].find(el=>el.textContent===name);
const badge=()=>document.querySelector('.guide-runtime-badge').textContent;

test('accepted runtime apply pause and resume update the instance heading without a reload',async()=>{
 let state=runtime('not_applied',1);const calls=[];
 const api={runtimeLoad:async()=>({runtime:state}),runtimeCommand:async(id,payload)=>{
  calls.push({id,action:payload.action,expectedVersion:payload.expectedVersion});
  state=runtime(payload.action==='pause'?'paused':'applied',state.version+1);return {runtime:state};
 }};
 const root=createRoot(document.getElementById('root'));
 try{
  await act(async()=>root.render(React.createElement(output.exports.App,{crm:true,initialData:{loops:[fixture('A'),fixture('B')]},api})));
  assert.equal(badge(),'尚未应用');
  for(const [action,confirm,want] of [['应用方案','确认应用','运行版本已应用'],['暂停方案','确认暂停','方案已暂停'],['恢复方案','确认应用','运行版本已应用']]){
   const before=badge();await act(async()=>button(action).click());assert.equal(badge(),before,'opening a confirmation must not invent a new state');
   await act(async()=>button(confirm).click());assert.equal(badge(),want);
  }
  assert.deepEqual(calls,[{id:'A',action:'apply',expectedVersion:1},{id:'A',action:'pause',expectedVersion:2},{id:'A',action:'resume',expectedVersion:3}]);
  assert.match(document.querySelector('.guide-instance-meta').textContent,/配置版本 1/,'runtime changes must not fabricate a configuration revision');
 }finally{await act(async()=>root.unmount());}
});

test('fresh runtime reads replace stale summary but read errors never invent applied status',async()=>{
 let fail=false;const root=createRoot(document.getElementById('root'));
 const api={runtimeLoad:async()=>{if(fail)throw Error('本地读取失败');return {runtime:runtime('paused',5)};}};
 try{
  await act(async()=>root.render(React.createElement(output.exports.App,{crm:true,initialData:{loops:[fixture('A')]},api})));
  assert.equal(badge(),'方案已暂停');fail=true;
  await act(async()=>button('刷新检查').click());
  assert.equal(badge(),'方案已暂停');assert.match(document.querySelector('[role="alert"]').textContent,/本地读取失败/);
 }finally{await act(async()=>root.unmount());}
});

test('rejected runtime command retains last confirmed status and local configuration input',async()=>{
 const root=createRoot(document.getElementById('root'));
 const api={runtimeLoad:async()=>({runtime:runtime('applied',2)}),runtimeCommand:async()=>{throw Error('版本冲突，请刷新检查');}};
 try{
  await act(async()=>root.render(React.createElement(output.exports.App,{crm:true,initialData:{loops:[fixture('A')]},api})));
  const checkbox=document.querySelector('#guide-panel-server input[type="checkbox"]');
  await act(async()=>checkbox.click());assert.equal(checkbox.checked,false);
  await act(async()=>button('暂停方案').click());await act(async()=>button('确认暂停').click());
  assert.equal(badge(),'运行版本已应用');assert.equal(checkbox.checked,false);
  assert.match(document.querySelector('.guide-runtime-workbench [role="alert"]').textContent,/版本冲突/);
  assert.equal(button('确认暂停').disabled,true,'a failed command requires re-reading before retry');
 }finally{await act(async()=>root.unmount());}
});
