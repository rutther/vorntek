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
const compiled=await build({entryPoints:['src/CrmRecords.jsx'],bundle:true,write:false,platform:'node',format:'cjs',external:['react'],logLevel:'silent'});
const module={exports:{}};new Function('require','module','exports',compiled.outputFiles[0].text)(createRequire(import.meta.url),module,module.exports);
const click=node=>act(async()=>node.click());
const button=text=>[...document.querySelectorAll('button')].find(b=>b.textContent.includes(text));
async function fill(selector,value){await act(async()=>{const node=document.querySelector(selector);Object.getOwnPropertyDescriptor(node.tagName==='TEXTAREA'?window.HTMLTextAreaElement.prototype:window.HTMLInputElement.prototype,'value').set.call(node,value);node.dispatchEvent(new window.Event('input',{bubbles:true}));});}
async function mount(extra={}){
 const props={loop:{id:'record-a',provider:'meta',preset:'website',revision:3,configs:{},drafts:{},sources:[],taskKeys:['dataset','website','pixel','form','crm','server','ad'],history:[{at:'2026-10-10T07:00:00Z',revision:2,nodeKey:'server',kind:'evidence',result:'observed',note:'旧版合成观察，不证明当前配置。',reference:'合成图一'}]},guard:{current:null},busy:false,selectedKey:'server',update:async()=>true,navigate:()=>{},...extra};
 const root=createRoot(document.getElementById('root'));await act(async()=>root.render(React.createElement(module.exports.CrmRecords,props)));
 return {props,close:()=>act(async()=>root.unmount())};
}
test('records keep historical evidence beside edits and reveal closeout without discarding input',async()=>{
 const destinations=[];const ui=await mount({navigate:path=>destinations.push(path)});
 try{
  const history=document.querySelector('.guide-records-history');assert.ok(history,'reference 13 needs history beside the form');
  assert.match(history.textContent,/配置版本 2/);assert.match(history.textContent,/已过期/);
  assert.equal(document.querySelector('select').value,'server');
  await fill('textarea','本地合成说明，等待下一项核对。');
  assert.ok(button('查看收尾检查'));await click(button('查看收尾检查'));
  assert.equal(document.activeElement.id,'guide-records-closeout');assert.equal(document.querySelector('textarea').value,'本地合成说明，等待下一项核对。');
  assert.equal(ui.props.guard.current.dirty,true);assert.deepEqual(destinations,[]);
  await click(button('返回 09'));assert.equal(destinations.at(-1),'/flow/record-a?view=tasks&node=closed-loop-test');
 }finally{await ui.close();}
});
test('record save prevents same-tick duplicate requests and preserves failed input for retry',async()=>{
 const calls=[];let resolve;const ui=await mount({update:(loop,message,payload)=>{calls.push({id:loop.id,...payload});return new Promise(r=>resolve=r);}});
 try{
  await fill('textarea','本地合成操作，验证失败后输入保留。');await fill('input','合成截图，不含客户资料');
  await act(async()=>{const form=document.querySelector('form');form.dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));form.dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));});
  assert.equal(calls.length,1,'same-tick submissions must be coalesced');
  assert.equal(document.querySelector('textarea').disabled,true);
  await act(async()=>resolve(false));
  assert.equal(document.querySelector('textarea').value,'本地合成操作，验证失败后输入保留。');assert.equal(ui.props.guard.current.dirty,true);
  await click(button('保存本次实操记录'));assert.equal(calls.length,2);assert.equal(calls[1].nodeKey,'server');assert.equal(calls[1].result,'pending');assert.equal(calls[1].action,'evidence');
  await act(async()=>resolve(true));assert.equal(document.querySelector('textarea').value,'');assert.equal(document.querySelector('input').value,'');
 }finally{await ui.close();}
});
test('direct plan records retain the existing surface and CRM check action',async()=>{
 const calls=[];const ui=await mount({direct:true,update:async(loop,message,payload)=>{calls.push(payload);return true;}});
 try{assert.equal(document.querySelector('.guide-records-workbench'),null);await click(button('检查 CRM 配置'));assert.deepEqual(calls,[{action:'preflight'}]);}
 finally{await ui.close();}
});
test('guide CRM check stays with record entry but cannot submit or clear the evidence form',async()=>{
 const calls=[];const ui=await mount({update:async(loop,message,payload)=>{calls.push(payload);return true;}});
 try{
  const form=document.querySelector('form');const check=button('检查 CRM 配置');
  assert.ok(!check.closest('form'),'configuration check is not an evidence submit');
  assert.ok(!check.closest('.guide-records-history'),'history column must not stack the entry-side check below records');
  assert.ok(check.closest('.guide-records-entry')===form.parentElement,'entry and its auxiliary check share the left column');
  await fill('textarea','本地合成观察，CRM检查不应把此说明保存或清空。');
  await click(check);
  assert.deepEqual(calls,[{action:'preflight'}]);
  assert.equal(document.querySelector('textarea').value,'本地合成观察，CRM检查不应把此说明保存或清空。');
  assert.equal(ui.props.guard.current.dirty,true);
  await click(button('保存本次实操记录'));
  assert.equal(calls[1].action,'evidence');assert.equal(calls[1].result,'pending');
 }finally{await ui.close();}
});
