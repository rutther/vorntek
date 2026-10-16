import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
import {JSDOM} from 'jsdom';
import {readFileSync} from 'node:fs';
import React,{act} from 'react';
const dom=new JSDOM('<div id="root"></div>',{url:'https://example.test/'});
globalThis.window=dom.window;globalThis.document=dom.window.document;
globalThis.HTMLElement=dom.window.HTMLElement;
globalThis.requestAnimationFrame=fn=>fn();globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const {createRoot}=await import('react-dom/client');
const compiled=await build({entryPoints:[fileURLToPath(new URL('../src/PixelStep.jsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',external:['react'],loader:{'.css':'empty'},logLevel:'silent'});
const module={exports:{}};
new Function('require','module','exports',compiled.outputFiles[0].text)(createRequire(import.meta.url),module,module.exports);
const {PixelStep}=module.exports;
const base=()=>({version:2,draft:{landingPaths:['/','/contact/'],leadPaths:['/contact/'],formId:1,consentRequired:true},integration:{id:1},runtimeState:'not_applied',configState:'saved',forms:[{id:1,name:'项目询盘',code:'project-inquiry'}],pageCatalog:{available:true,pages:[{path:'/',title:'首页',locale:'en',formCodes:[]},{path:'/contact/',title:'联系页面',locale:'en',formCodes:['project-inquiry']}]}});
async function mount(overrides={}){
 const calls=[];let continued=0;
 const props={loop:{id:'flow',version:4,configs:{dataset:{externalId:'123456'},pixel:{mode:'managed'}},drafts:{}},runtime:{state:base(),read:async()=>{},busy:false},update:async(...args)=>{calls.push(args[2]);return true;},controller:{current:null},onContinue:()=>continued++,...overrides};
 const root=createRoot(document.getElementById('root'));
 await act(async()=>root.render(React.createElement(PixelStep,props)));
 return {props,calls,get continued(){return continued;},async render(){await act(async()=>root.render(React.createElement(PixelStep,props)));},async close(){await act(async()=>root.unmount());}};
}
const button=text=>[...document.querySelectorAll('button')].find(b=>b.textContent.includes(text));
const click=async element=>act(async()=>element.click());
test('guide selected-page removal preserves the other event scope and saves only on request',async()=>{
 const ui=await mount({workbench:true});try{
  const remove=document.querySelector('button[aria-label="从记录浏览的页面移除 /contact/"]');
  assert.ok(remove,'selected pages have a direct removal action outside the disclosure');
  assert.equal(remove.closest('summary'),null);
  await click(remove);
  assert.match(document.querySelector('[data-scope="browse"]').textContent,/1 个页面/);
  assert.match(document.querySelector('[data-scope="lead"]').textContent,/1 个页面/);
  assert.equal(ui.calls.length,0);
  assert.equal(document.activeElement.id,'pixel-landingPaths');
  await click(button('保存本步'));
  assert.deepEqual(ui.calls[0].scope.landingPaths,['/']);
  assert.deepEqual(ui.calls[0].scope.leadPaths,['/contact/']);
 }finally{await ui.close();}
});
test('guide selected-page removal is disabled while runtime controls are busy',async()=>{
 const ui=await mount({workbench:true,runtime:{state:base(),read:async()=>{},busy:true}});try{
  const remove=document.querySelector('button[aria-label="从记录浏览的页面移除 /contact/"]');
  assert.ok(remove);assert.equal(remove.disabled,true);await click(remove);
  assert.match(document.querySelector('[data-scope="browse"]').textContent,/2 个页面/);
  assert.equal(ui.calls.length,0);
 }finally{await ui.close();}
});
async function type(element,value){await act(async()=>{Object.getOwnPropertyDescriptor(element.tagName==='TEXTAREA'?window.HTMLTextAreaElement.prototype:window.HTMLInputElement.prototype,'value').set.call(element,value);element.dispatchEvent(new window.Event('input',{bubbles:true}));});}

test('guide scope summary follows independent unsaved selections and does not certify runtime',async()=>{
 const ui=await mount({workbench:true});try{
  const pickers=document.querySelectorAll('.pixel-picker');
  assert.equal(pickers[0].open,true,'guide browse directory is immediately discoverable');
  assert.equal(pickers[1].open,false,'success scope remains independently expandable');
  const summary=document.querySelector('[aria-label="本次追踪范围摘要"]');
  assert.ok(summary);assert.match(summary.textContent,/2 个页面/);assert.match(summary.textContent,/1 个页面 · 1 个表单/);
  await click(pickers[0].querySelector('input[type=checkbox]'));
  assert.match(summary.querySelector('[data-scope="browse"]').textContent,/1 个页面/);
  assert.match(summary.querySelector('[data-scope="lead"]').textContent,/1 个页面 · 1 个表单/);
  await click(pickers[1].querySelector('summary'));await click(pickers[1].querySelectorAll('input[type=checkbox]')[1]);
  assert.match(summary.querySelector('[data-scope="lead"]').textContent,/0 个页面 · 1 个表单/);
  assert.equal(ui.calls.length,0,'summary does not save or apply on selection');
  assert.match(summary.textContent,/当前编辑范围/);assert.match(summary.textContent,/旧 Pixel/);
  assert.match(document.body.textContent,/表单按钮点击.*不等于提交成功/);
  await click(button('暂存草稿'));
  assert.deepEqual(ui.calls[0].scope.landingPaths,['/contact/']);assert.deepEqual(ui.calls[0].scope.leadPaths,[]);
 }finally{await ui.close();}
});

test('both selectors default closed, search preserves hidden selections and Escape closes',async()=>{
 const ui=await mount();try{
 const lists=[...document.querySelectorAll('.pixel-picker')];assert.equal(lists.length,2);assert(lists.every(d=>!d.open));
 await click(lists[0].querySelector('summary'));assert(lists[0].open);
 await type(lists[0].querySelector('input[type=search]'),'contact');
 assert.equal(lists[0].querySelectorAll('input[type=checkbox]').length,1);
 await click(lists[0].querySelector('input[type=checkbox]'));assert(lists[0].open);
 await type(lists[0].querySelector('input[type=search]'),'');
 assert(lists[0].querySelector('input[type=checkbox]').checked);
 await act(async()=>lists[0].dispatchEvent(new window.KeyboardEvent('keydown',{key:'Escape',bubbles:true})));
 assert(!lists[0].open);assert.equal(document.activeElement,lists[0].querySelector('summary'));
 await click(button('保存本步'));assert.deepEqual(ui.calls[0].scope.landingPaths,['/']);assert.equal(ui.calls[0].action,'save_pixel_step');assert.equal(ui.calls[0].expectedRuntimeVersion,2);
 }finally{await ui.close();}
});
test('blank saved draft stays blank and full save focuses first required group',async()=>{
 const ui=await mount({loop:{id:'flow',version:4,configs:{},drafts:{pixel:{scope:{landingPaths:[],leadPaths:[],formId:'',consentRequired:true}}}}});try{
 assert.equal(document.querySelector('select').value,'');await click(button('保存并继续'));assert.equal(ui.calls.length,0);assert.equal(ui.continued,0);
 assert.equal(document.activeElement.id,'pixel-landingPaths');assert(document.activeElement.parentElement.open);
 await click(button('暂存草稿'));assert.equal(ui.calls[0].intent,'draft');assert.equal(ui.calls[0].scope.formId,'');
 }finally{await ui.close();}
});
test('pending manual input participates in navigation guard and save; network failure preserves it',async()=>{
 let payload;
 const ui=await mount({update:async(a,b,data)=>{payload=data;throw Error('offline');}});try{
 const input=document.querySelector('textarea');await type(input,'/contact');assert(ui.props.controller.current.dirty);
 await click(button('保存并继续'));assert.equal(ui.continued,0);assert.equal(input.value,'/contact');assert.deepEqual(payload.scope.landingPaths,['/','/contact/']);assert.match(document.body.textContent,/输入已保留/);
 }finally{await ui.close();}
});
test('dirty inputs survive runtime conflict and explicit version acknowledgement enables retry',async()=>{
 const ui=await mount();try{
 await click(document.querySelector('.pixel-picker input[type=checkbox]'));
 ui.props.runtime={...ui.props.runtime,state:{...base(),version:3}};await ui.render();
 assert.match(document.body.textContent,/接入版本已更新/);await click(button('已核对，保留当前输入'));
 await click(button('保存本步'));assert.equal(ui.calls[0].expectedRuntimeVersion,3);assert.deepEqual(ui.calls[0].scope.landingPaths,['/contact/']);
 }finally{await ui.close();}
});
test('double click submits once and advances only after confirmed success',async()=>{
 let resolve;let count=0;
 const ui=await mount({update:()=>{count++;return new Promise(r=>resolve=r);}});try{
 await act(async()=>{button('保存并继续').click();button('保存并继续').click();});assert.equal(count,1);assert.equal(ui.continued,0);
 await act(async()=>resolve(true));assert.equal(ui.continued,1);
 }finally{await ui.close();}
});

test('long published SEO titles do not replace recognizable selected page names and paths',async()=>{
 const state=base();state.pageCatalog.pages[0].title='Vorntek | Example services, project planning and professional support for organizations';state.pageCatalog.pages[1].title='Project Inquiry | Vorntek';
 const ui=await mount({runtime:{state,read:async()=>{}}});try{
 const summary=document.getElementById('pixel-landingPaths');
 assert.match(summary.textContent,/首页/);assert.match(summary.textContent,/联系页/);assert.match(summary.textContent,/\/contact\//);
 assert(!summary.textContent.includes('Example services'));
 await click(summary);assert.match(summary.parentElement.querySelector('.pixel-options').textContent,/Example services/);
 assert.equal(document.querySelectorAll('.pixel-step-content h3').length,0,'No competing step title inside the existing step heading');
 }finally{await ui.close();}
});

test('select and search have enough content height for a complete line of text',async()=>{
 const css=document.createElement('style');css.textContent=readFileSync(new URL('../src/hk7-baseline.css',import.meta.url),'utf8')+'\n'+readFileSync(new URL('../src/pixel-step.css',import.meta.url),'utf8');document.head.append(css);
 document.getElementById('root').className='flow-crm-root';
 const ui=await mount();try{
 for(const element of document.querySelectorAll('select,input[type=search]')){
  const s=window.getComputedStyle(element);
  // jsdom does not resolve the color variable in border shorthand. Conservatively
  // reserve 1px on each edge, matching the existing component border contract.
  const available=parseFloat(s.height)-parseFloat(s.paddingTop)-parseFloat(s.paddingBottom)-(parseFloat(s.borderTopWidth)||1)-(parseFloat(s.borderBottomWidth)||1);
  const line=parseFloat(s.lineHeight)||parseFloat(s.fontSize)*1.4;
  assert(available>=line,`${element.tagName}: available ${available}px below text line ${line}px`);
 }
 }finally{await ui.close();css.remove();}
});
