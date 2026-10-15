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
const built=await build({entryPoints:[fileURLToPath(new URL('../src/GuideCreate.jsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',external:['react'],logLevel:'silent'});
const out={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(createRequire(import.meta.url),out,out.exports);
async function fill(selector,value){await act(async()=>{const el=document.querySelector(selector);Object.getOwnPropertyDescriptor(el.tagName==='TEXTAREA'?window.HTMLTextAreaElement.prototype:el.tagName==='SELECT'?window.HTMLSelectElement.prototype:window.HTMLInputElement.prototype,'value').set.call(el,value);el.dispatchEvent(new window.Event(el.tagName==='SELECT'?'change':'input',{bubbles:true}));});}
test('creation uses real command payload, rejects concurrent submit, and retains failed input',async()=>{
 const root=createRoot(document.getElementById('root')),guard={current:null};let finish;const calls=[];
 try{
  await act(async()=>root.render(React.createElement(out.exports.GuideCreate,{guard,onCancel:()=>{},onCreate:payload=>{calls.push(payload);return new Promise(resolve=>finish=resolve);}})));
  assert.equal(document.querySelectorAll('.guide-type-card').length,4);
  await fill('input[name="name"]','第二个独立实例');await fill('select[name="environment"]','test');await fill('textarea[name="note"]','范围仅用于本地测试');
  assert.equal(guard.current.dirty,true);
  await act(async()=>{document.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));document.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));});
  assert.deepEqual(calls,[{name:'第二个独立实例',environment:'test',note:'范围仅用于本地测试'}]);
  await act(async()=>finish(false));
  assert.equal(document.querySelector('input[name="name"]').value,'第二个独立实例');
  assert.equal(document.querySelector('textarea[name="note"]').value,'范围仅用于本地测试');
  assert.match(document.body.textContent,/未创建/);
 }finally{await act(async()=>root.unmount());}
});
