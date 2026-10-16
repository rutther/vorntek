import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
const require=createRequire(new URL('../apps/crm/flow-ui/package.json',import.meta.url));
const {JSDOM}=require('jsdom');
const script=readFileSync(new URL('../apps/website/app.js',import.meta.url),'utf8');

function fixture(route='',query=''){
 const source=readFileSync(new URL('../apps/website/'+route+'index.html',import.meta.url),'utf8');
 const dom=new JSDOM(source,{url:'https://example.invalid/'+route+query,runScripts:'outside-only'});
 dom.window.fetch=async()=>({ok:true,json:async()=>({products:[{id:'inspect',name:'Inspect vision station',nameZh:'Inspect 视觉检测工作站'}]})});
 dom.window.eval(script);return dom;
}

test('family selection exposes only its matching equipment and supports keyboard navigation',()=>{
 const dom=fixture(),{document,KeyboardEvent}=dom.window;
 const tabs=[...document.querySelectorAll('[data-family-tab]')];
 tabs[1].click();
 assert.equal(document.querySelector('#panel-inspection').hidden,true);
 assert.equal(document.querySelector('#panel-assembly').hidden,false);
 assert.match(document.querySelector('#panel-assembly').textContent,/Assemble modular workstation/);
 tabs[1].dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));
 assert.equal(document.querySelector('#panel-integration').hidden,false);
 assert.equal(document.activeElement,tabs[2]);dom.window.close();
});

test('language change updates visible copy, metadata and product inquiry context links',()=>{
 const dom=fixture('products/inspect/'),{document,Event}=dom.window;
 const language=document.querySelector('#language');language.value='zh';language.dispatchEvent(new Event('change'));
 assert.match(document.querySelector('h1').textContent,/视觉检测/);
 assert.match(document.title,/视觉检测/);
 assert.match(document.querySelector('meta[name=description]').content,/检测单元/);
 assert.match(document.querySelector('model-viewer').getAttribute('alt'),/视觉检测/);
 assert.match(document.querySelector('[data-gallery-src]').getAttribute('aria-label'),/视图/);
 assert.match(document.querySelector('video').getAttribute('aria-label'),/结构演示/);
 const target=new URL(document.querySelector('[data-product-context]').href);
 assert.equal(target.searchParams.get('product'),'inspect');assert.equal(target.searchParams.get('lang'),'zh');
 assert.equal(document.querySelector('a[href*="/articles/"]').getAttribute('href'),'/zh/articles/');
 assert.equal(document.documentElement.lang,'zh-CN');dom.window.close();
});

test('gallery buttons change the actual product image and selected state',()=>{
 const dom=fixture('products/inspect/'),{document}=dom.window;
 const buttons=[...document.querySelectorAll('[data-gallery-src]')];buttons[1].click();
 assert.match(document.querySelector('.gallery-main').src,/inspect-side\.png$/);
 assert.equal(buttons[1].getAttribute('aria-pressed'),'true');assert.equal(buttons[0].getAttribute('aria-pressed'),'false');dom.window.close();
});

test('3D failure retry creates a fresh viewer rather than assigning an unchanged source',()=>{
 const dom=fixture('products/inspect/'),{document,Event}=dom.window;
 const model=document.querySelector('model-viewer'),button=document.querySelector('[data-load-model]');
 assert.equal(model.getAttribute('src'),null);button.click();
 assert.match(model.getAttribute('src'),/inspect\.glb$/);assert.equal(button.disabled,true);
 model.dispatchEvent(new Event('error'));assert.equal(button.disabled,false);
 assert.match(document.querySelector('[data-model-status]').textContent,/pictures and specifications/);
 button.click();
 const retry=document.querySelector('model-viewer');
 assert.notEqual(retry,model);assert.equal(model.isConnected,false);
 assert.match(retry.getAttribute('src'),/inspect\.glb\?view_retry=1$/);
 model.dispatchEvent(new Event('load'));assert.equal(button.hidden,false);
 retry.dispatchEvent(new Event('load'));assert.equal(button.hidden,true);dom.window.close();
});

test('product context waits for configured fields and does not replace entered content',async()=>{
 const dom=fixture('contact/','?product=inspect'),{document}=dom.window;
 await new Promise(resolve=>setImmediate(resolve));
 const input=document.createElement('textarea');input.name='message';input.value='Existing project request';
 document.querySelector('[data-inquiry-fields]').append(input);
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(input.value,'Existing project request');
 assert.match(document.querySelector('[data-inquiry-context]').textContent,/Inspect vision station/);dom.window.close();
});

test('an unrecognised product query cannot become trusted inquiry context',async()=>{
 const dom=fixture('contact/','?product=unrecognised'),{document}=dom.window;
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(document.querySelector('[data-inquiry-context]').hidden,true);dom.window.close();
});

test('privacy settings reuse the existing footer action across language changes',async()=>{
 const dom=fixture(),{document,Event}=dom.window;
 dom.window.eval(readFileSync(new URL('../apps/website/assets/meta-pixel.js',import.meta.url),'utf8'));
 document.dispatchEvent(new Event('DOMContentLoaded'));
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(document.querySelectorAll('[data-privacy-settings]').length,1);
 const language=document.querySelector('#language');language.value='zh';language.dispatchEvent(new Event('change'));
 assert.equal(document.querySelectorAll('[data-privacy-settings]').length,1);
 document.querySelector('[data-privacy-settings]').click();
 assert.equal(document.querySelector('[role=dialog]').getAttribute('aria-label'),'隐私选项');
 dom.window.close();
});
