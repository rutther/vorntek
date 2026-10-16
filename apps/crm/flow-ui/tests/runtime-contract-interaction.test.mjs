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
const compiled=await build({entryPoints:[fileURLToPath(new URL('../src/Configuration.jsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',external:['react'],loader:{'.css':'empty','.png':'dataurl','.svg':'dataurl'},logLevel:'silent'});
const module={exports:{}};new Function('require','module','exports',compiled.outputFiles[0].text)(createRequire(import.meta.url),module,module.exports);
const button=text=>[...document.querySelectorAll('button')].find(b=>b.textContent===text);
const click=async el=>{assert.ok(el,'requested action is discoverable');await act(async()=>el.click());};
const editor=()=>document.querySelector('[aria-label="编辑回传约定"]');
async function mount(overrides={}){
 const calls=[],props={loop:{id:'A',platform:'meta',route:'website',configs:{server:{event:'Lead',dedup:true}},drafts:{}},nodeKey:'server',values:{event:'Lead',dedup:true},crm:true,compactRuntime:true,dirty:false,onReveal:()=>{},...overrides};
 const root=createRoot(document.getElementById('root'));
 function Harness(){return React.createElement(module.exports.Configuration,{...props,onChange:value=>{props.values=value;props.dirty=true;root.render(React.createElement(Harness));},onSave:draft=>calls.push({draft,values:{...props.values}})});}
 // Keep the same component tree across controlled updates.
 const update=()=>act(async()=>root.render(React.createElement(Harness)));
 await update();return {props,calls,update,close:()=>act(async()=>root.unmount())};
}

test('saved runtime contract is readable before editing and saves edits only on explicit request',async()=>{
 const ui=await mount();try{
  assert.ok(editor(),'the editor has a labelled disclosure region');
  assert.equal(editor().hidden,true);
  const summary=document.querySelector('[aria-label="回传约定摘要"]');
  assert.match(summary.textContent,/Lead/);assert.match(summary.textContent,/相同事件 ID/);
  await click(button('修改回传约定'));
  assert.equal(editor().hidden,false);
  await click(editor().querySelector('input[type="checkbox"]'));
  assert.equal(ui.props.values.dedup,false);assert.equal(ui.calls.length,0);
  assert.match(summary.textContent,/相同事件 ID/,'summary remains authoritative saved state, not unsaved input');
  assert.equal(button('收起已保存约定').disabled,true,'do not hide unfinished edits');
  await click(button('保存回传约定'));
  assert.deepEqual(ui.calls,[{draft:false,values:{event:'Lead',dedup:false}}]);
  ui.props.loop={...ui.props.loop,configs:{server:{event:'Lead',dedup:false}}};ui.props.dirty=false;await ui.update();
  await click(button('收起已保存约定'));
  assert.equal(editor().hidden,true);
  assert.match(summary.textContent,/未记录同编号要求/);
 }finally{await ui.close();}
});

test('new, dirty, failed and saving contracts stay exposed without losing current values',async()=>{
 const ui=await mount();try{
  for(const state of [{dirty:true},{dirty:false,error:'保存失败，请重试'},{error:'',busy:true}]){
   Object.assign(ui.props,state);await ui.update();assert.ok(editor());assert.equal(editor().hidden,false);
   assert.equal(button('收起已保存约定').disabled,true);
   assert.equal(editor().querySelector('select').value,'Lead');
  }
  Object.assign(ui.props,{busy:false,loop:{...ui.props.loop,configs:{}}});await ui.update();
  assert.equal(editor().hidden,false);assert.equal(ui.calls.length,0);
 }finally{await ui.close();}
});

test('direct server configuration retains its original always-visible form',async()=>{
 const ui=await mount({compactRuntime:false,direct:true});try{
  assert.equal(editor(),null);assert.ok(document.querySelector('form select'));
  assert.equal(Boolean(button('修改回传约定')),false);
 }finally{await ui.close();}
});
