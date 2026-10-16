import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';

const pixel=readFileSync(new URL('../../../website/assets/meta-pixel.js',import.meta.url),'utf8');
const controller=readFileSync(new URL('../../../website/app.js',import.meta.url),'utf8');

test('saved and changed website language reaches privacy controls without granting consent',async()=>{
 const dom=new JSDOM('<html lang="en"><body><select id="language"><option value="en">EN</option><option value="zh">中文</option></select><footer><div class="footer-main"></div></footer></body></html>',
  {url:'https://example.invalid/products/advice/',runScripts:'outside-only'});
 try{
  const {window}=dom;let sends=0;
  window.localStorage.setItem('nc_privacy_consent_v1','denied');
  window.localStorage.setItem('vorntekLang','zh');
  window.fetch=async()=>({ok:true,json:async()=>({ok:true,schemaVersion:2,metaPixels:[]})});
  window.fbq=()=>sends++;
  window.eval(pixel);await new Promise(resolve=>window.setTimeout(resolve,0));
  window.eval(controller);
  const settings=window.document.querySelector('[data-privacy-settings]');
  assert.ok(settings);assert.equal(settings.textContent,'隐私选项');
  const select=window.document.querySelector('#language');select.value='en';
  select.dispatchEvent(new window.Event('change'));
  assert.equal(settings.textContent,'Privacy choices');
  assert.equal(window.localStorage.getItem('nc_privacy_consent_v1'),'denied');
  assert.equal(sends,0);
 }finally{dom.window.close();}
});
