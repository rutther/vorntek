import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
import {JSDOM} from 'jsdom';
import React,{act} from 'react';
const dom=new JSDOM('<div id="root"></div>',{url:'https://example.test/'});
globalThis.window=dom.window;globalThis.document=dom.window.document;
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const {createRoot}=await import('react-dom/client');
const compiled=await build({entryPoints:[fileURLToPath(new URL('../src/RuntimePanel.jsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',external:['react'],loader:{'.css':'empty','.png':'dataurl','.svg':'dataurl'},logLevel:'silent'});
const module={exports:{}};new Function('require','module','exports',compiled.outputFiles[0].text)(createRequire(import.meta.url),module,module.exports);
const {useRuntime}=module.exports;
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const state=(version,path='/')=>({runtime:{version,draft:{landingPaths:[path],leadPaths:['/contact/'],formId:1}}});
async function mountControls(action){
 const scope={landingPaths:['/'],leadPaths:['/contact/'],formId:1,consentRequired:true};
 let snapshot={version:8,flowVersion:4,configHash:'unchanged-hash',runtimeState:action==='resume'?'paused':'applied',configState:action==='apply'?'unapplied_changes':'saved',canOperate:true,blockingChecks:[],forms:[],preservedPixelIds:[],overlaps:[],draft:scope,integration:{pixelId:'900000000000001',environment:'test',editUrl:'/local/'},activeRevision:{key:'current',scope},history:[{key:'previous',createdAt:'synthetic',scope}]};
 let dirty=false;const calls=[],root=createRoot(document.getElementById('root'));
 const button=text=>[...document.querySelectorAll('button')].find(node=>node.textContent===text);
 const render=()=>act(async()=>root.render(React.createElement(module.exports.RuntimePanel,{workbench:true,mode:'server',loop:{id:'A',configs:{}},runtime:{state:snapshot,dirty,command:async(...args)=>{calls.push(args);return true;}}})));
 const open=async()=>{
  if(action==='rollback'){const select=document.querySelector('select');await act(async()=>{select.value='previous';select.dispatchEvent(new window.Event('change',{bubbles:true}));});}
  await act(async()=>button({apply:'应用方案',resume:'恢复方案',pause:'暂停方案',rollback:'预备恢复这个范围'}[action]).click());
  assert.ok(document.querySelector('[aria-label="确认运行变更"]'));
 };
 await render();await open();
 return {calls,button,open,async update(patch,nextDirty=dirty){snapshot={...snapshot,...patch};dirty=nextDirty;await render();},close:()=>act(async()=>root.unmount())};
}

for(const action of ['apply','resume','pause','rollback'])test(`${action} confirmation is invalidated when permission changes without a new version`,async()=>{
 const ui=await mountControls(action);
 try{
  await ui.update({canOperate:false});
  const staleButton=document.querySelector('.runtime-confirm button');
  if(staleButton)await act(async()=>staleButton.click());
  assert.equal(ui.calls.length,0,'revoked permission must not dispatch a runtime command');
  assert.equal(Boolean(document.querySelector('[aria-label="确认运行变更"]')),false);
  await ui.update({canOperate:true});
  assert.equal(Boolean(document.querySelector('[aria-label="确认运行变更"]')),false,'restored permission must require a fresh confirmation');
 }finally{await ui.close();}
});

for(const action of ['apply','resume'])test(`${action} confirmation closes when fresh checks block the unchanged draft`,async()=>{
 const ui=await mountControls(action);
 try{
  await ui.update({blockingChecks:[{code:'integration_disabled',message:'共享接入已停用'}]});
  const staleButton=document.querySelector('.runtime-confirm button');
  if(staleButton)await act(async()=>staleButton.click());
  assert.equal(ui.calls.length,0);
  assert.equal(Boolean(document.querySelector('[aria-label="确认运行变更"]')),false);
 }finally{await ui.close();}
});

test('resume confirmation cannot survive a runtime state change with equal version fields',async()=>{
 const ui=await mountControls('resume');
 try{
  await ui.update({runtimeState:'applied'});
  assert.equal(Boolean(document.querySelector('[aria-label="确认运行变更"]')),false);
  assert.equal(ui.calls.length,0);
 }finally{await ui.close();}
});

test('historical confirmation closes if the selected revision is no longer available',async()=>{
 const ui=await mountControls('rollback');
 try{
  await ui.update({history:[]});
  assert.equal(Boolean(document.querySelector('[aria-label="确认运行变更"]')),false);
  assert.equal(ui.calls.length,0);
 }finally{await ui.close();}
});

test('pause remains available when application prerequisites fail, without applying the draft',async()=>{
 const ui=await mountControls('pause');
 try{
  await ui.update({blockingChecks:[{code:'missing_form',message:'表单需核对'}]});
  assert.equal(ui.button('确认暂停').disabled,false);
  await act(async()=>ui.button('确认暂停').click());
  assert.deepEqual(ui.calls,[['pause',{}]]);
 }finally{await ui.close();}
});

async function mount(api,onSnapshot){
 let runtime;const props={loop:{id:'A',version:1},enabled:true};
 function Harness(){runtime=useRuntime(props.loop,api,props.enabled,onSnapshot);return React.createElement('span',null,runtime.state?.version||'loading');}
 const root=createRoot(document.getElementById('root'));
 const render=()=>act(async()=>root.render(React.createElement(Harness)));
 await render();return {get runtime(){return runtime;},props,render,close:()=>act(async()=>root.unmount())};
}
test('latest runtime read wins and a late older failure cannot erase it',async()=>{
 const first=deferred(),second=deferred();let n=0;
 const ui=await mount({runtimeLoad:()=>++n===1?first.promise:second.promise});
 try{let request;await act(async()=>{request=ui.runtime.read();});await act(async()=>second.resolve(state(3)));
  assert.equal(ui.runtime.state.version,3);await act(async()=>first.reject(Error('old failure')));
  assert.equal(ui.runtime.error,'');assert.equal(ui.runtime.state.version,3);await request;
 }finally{await ui.close();}
});
test('command response cannot be rolled back by an earlier background read',async()=>{
 const slow=deferred();let n=0;
 const ui=await mount({runtimeLoad:()=>++n===1?Promise.resolve(state(1)):slow.promise,runtimeCommand:async()=>state(3,'/new/')});
 try{await act(async()=>{ui.runtime.read();});await act(async()=>{assert.equal(await ui.runtime.command('prepare_integration'),true);});
  await act(async()=>slow.resolve(state(2,'/old/')));assert.equal(ui.runtime.state.version,3);assert.equal(ui.runtime.state.draft.landingPaths[0],'/new/');
 }finally{await ui.close();}
});
test('switching instance ignores old reads and old command success feedback',async()=>{
 const old=deferred(),summaries=[];
 const ui=await mount({runtimeLoad:async id=>state(id==='A'?1:9),runtimeCommand:()=>old.promise},(id,s)=>summaries.push([id,s.version]));
 try{let command;await act(async()=>{command=ui.runtime.command('apply');});
  ui.props.loop={id:'B',version:1};await ui.render();assert.equal(ui.runtime.state.version,9);
  await act(async()=>old.resolve(state(2)));assert.equal(await command,false);assert.equal(ui.runtime.state.version,9);assert.equal(ui.runtime.notice,'');assert.equal(ui.runtime.busy,false);
  assert.deepEqual(summaries,[['A',1],['B',9]],'late commands must not publish a stale parent/header summary');
 }finally{await ui.close();}
});
test('refresh preserves unsaved scope edits and updates the authoritative comparison',async()=>{
 let n=0;const ui=await mount({runtimeLoad:async()=>state(++n,n===1?'/':'/server/')});
 try{await act(async()=>ui.runtime.setValues({...ui.runtime.values,landing:'/local/'}));await act(async()=>ui.runtime.read());
  assert.equal(ui.runtime.values.landing,'/local/');assert.equal(ui.runtime.state.version,2);assert.equal(ui.runtime.dirty,true);
 }finally{await ui.close();}
});

test('running confirmations and historical selection cannot carry across instances with equal versions',async()=>{
 const scope={landingPaths:['/'],leadPaths:['/contact/'],formId:1,consentRequired:true};
 const makeState=id=>({version:2,flowVersion:3,configHash:'same',runtimeState:'applied',configState:'saved',canOperate:true,blockingChecks:[],forms:[],preservedPixelIds:[],overlaps:[],draft:scope,integration:{pixelId:id,environment:'test',editUrl:'/local/'},activeRevision:{key:`revision-${id}`,scope},history:[{key:`revision-${id}`,createdAt:'synthetic',scope}]});
 const root=createRoot(document.getElementById('root'));const calls=[];
 const render=id=>act(async()=>root.render(React.createElement(module.exports.RuntimePanel,{workbench:true,mode:'server',loop:{id,configs:{}},runtime:{state:makeState(id),command:async(...args)=>{calls.push(args);return true;}}})));
 const button=text=>[...document.querySelectorAll('button')].find(node=>node.textContent===text);
 try{
  await render('A');const select=document.querySelector('select');
  await act(async()=>{select.value='revision-A';select.dispatchEvent(new window.Event('change',{bubbles:true}));});
  await act(async()=>button('预备恢复这个范围').click());assert.ok(document.querySelector('[aria-label="确认运行变更"]'));assert.equal(calls.length,0);
  await render('B');assert.equal(Boolean(document.querySelector('[aria-label="确认运行变更"]')),false);assert.equal(document.querySelector('select').value,'');assert.equal(Boolean(button('预备恢复这个范围')),false);assert.equal(calls.length,0);
 }finally{await act(async()=>root.unmount());}
});

test('historical recovery confirmation shows the selected scope, cancels without writing and submits that revision',async()=>{
 const current={landingPaths:['/','/new/'],leadPaths:['/contact/'],formId:1,consentRequired:true};
 const previous={...current,landingPaths:['/']};
 const s={version:8,flowVersion:4,configHash:'draft-hash',runtimeState:'applied',configState:'saved',canOperate:true,blockingChecks:[],forms:[],preservedPixelIds:[],overlaps:[],draft:current,integration:{pixelId:'900000000000001',environment:'test',editUrl:'/local/'},activeRevision:{key:'current-version',scope:current},history:[{key:'previous-version',createdAt:'synthetic-old',scope:previous},{key:'current-version',createdAt:'synthetic-current',scope:current}]};
 const root=createRoot(document.getElementById('root')),calls=[];
 const button=text=>[...document.querySelectorAll('button')].find(node=>node.textContent===text);
 try{
  await act(async()=>root.render(React.createElement(module.exports.RuntimePanel,{workbench:true,mode:'server',loop:{id:'A',configs:{}},runtime:{state:s,command:async(...args)=>{calls.push(args);return true;}}})));
  const select=document.querySelector('select');
  await act(async()=>{select.value='previous-version';select.dispatchEvent(new window.Event('change',{bubbles:true}));});
  await act(async()=>button('预备恢复这个范围').click());
  const confirmation=document.querySelector('[aria-label="确认运行变更"]');
  assert.ok(confirmation.querySelector('table'),'final confirmation must show the historical scope, not only the draft above');
  assert.match(confirmation.textContent,/previous-version/);
  const cells=[...confirmation.querySelector('tbody tr').querySelectorAll('td')].map(cell=>cell.textContent);
  assert.deepEqual(cells,['/、/new/','/']);
  assert.equal(calls.length,0);
  await act(async()=>button('取消').click());assert.equal(document.querySelector('[aria-label="确认运行变更"]'),null);assert.equal(calls.length,0);
  await act(async()=>button('预备恢复这个范围').click());
  await act(async()=>button('确认恢复历史范围').click());
  assert.deepEqual(calls,[['rollback',{configHash:'draft-hash',expectedFlowVersion:4,targetRevision:'previous-version'}]]);
  assert.equal(document.querySelector('[aria-label="确认运行变更"]'),null);
 }finally{await act(async()=>root.unmount());}
});
