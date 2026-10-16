// Exercise the shipped definition-driven controller against a real DOM.
// Transport responses are synthetic; production persistence is tested separately.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
import {webcrypto} from 'node:crypto';
const require=createRequire(new URL('../apps/crm/flow-ui/package.json',import.meta.url));
const {JSDOM}=require('jsdom');
const source=readFileSync(new URL('../apps/website/inquiry-form.js',import.meta.url),'utf8');
const turn=()=>new Promise(resolve=>setImmediate(resolve));

async function setup(){
 const html=readFileSync(new URL('../apps/website/contact/index.html',import.meta.url),'utf8');
 const dom=new JSDOM(html,{url:'https://example.invalid/contact/',runScripts:'outside-only'});
 const {window}=dom;Object.defineProperty(window,'crypto',{value:webcrypto});
 const requests=[],replies=[];
 window.fetch=async(url,options={})=>{
  if(url.endsWith('/definition/'))return {ok:true,status:200,json:async()=>({ok:true,form:{code:'project-inquiry',fields:[
   {name:'full_name',label:'姓名',type:'text',required:true},
   {name:'email',label:'邮箱',type:'email',required:false},
   {name:'phone',label:'电话',type:'tel',required:false},
   {name:'message',label:'咨询内容',type:'textarea',required:true}
  ]}})};
  requests.push(JSON.parse(options.body));const reply=replies.shift();if(reply instanceof Error)throw reply;
  if(typeof reply==='function')return reply();
  return reply||{ok:true,status:200,json:async()=>({ok:true,duplicate:false,submission_id:31})};
 };
 window.ncPrivacyConsentGranted=()=>false;
 window.ncAdAttribution=()=>{throw Error('No consent: attribution must not be read');};
 window.eval(source);await turn();
 const form=window.document.querySelector('form');
 const input=(name,value)=>{form.querySelector(`[name="${name}"]`).value=value;};
 const fill=()=>{input('full_name','Synthetic Engineer');input('email','engineer@example.invalid');input('message','Inspect a synthetic component');form.querySelector('[name=consent]').checked=true;};
 fill();
 return {dom,window,form,input,fill,requests,replies,status:form.querySelector('[data-form-status]'),
  async submit(){form.dispatchEvent(new window.Event('submit',{cancelable:true,bubbles:true}));await turn();}};
}

test('configured fields submit with consent boundaries and no fixed industry requirement',async()=>{
 const f=await setup();await f.submit();assert.equal(f.requests.length,1);
 const payload=f.requests[0];assert.equal(payload.email,'engineer@example.invalid');
 assert.deepEqual(payload.extra_fields,{});assert.equal(payload.consent.marketing,false);assert.deepEqual(payload.utm,{});
 assert.equal(f.status.dataset.kind,'success');assert.equal(f.form.querySelector('[name=email]').value,'');f.dom.window.close();
});

test('unknown submission outcome keeps input and the same event identity for retry',async()=>{
 const f=await setup();f.replies.push(Error('connection ended'));await f.submit();
 const first=f.requests[0].client_event_id;assert.equal(f.status.dataset.kind,'error');
 assert.equal(f.form.querySelector('[name=email]').value,'engineer@example.invalid');
 await f.submit();assert.equal(f.requests[1].client_event_id,first);assert.equal(f.status.dataset.kind,'success');f.dom.window.close();
});

test('a new invalid attempt cannot retain the prior success state',async()=>{
 const f=await setup();await f.submit();assert.equal(f.status.dataset.kind,'success');
 await f.submit();assert.equal(f.status.dataset.kind,'error');assert.equal(f.requests.length,1);f.dom.window.close();
});

test('server validation preserves entries and exposes a correctable result',async()=>{
 const f=await setup();f.replies.push({ok:false,status:400,json:async()=>({ok:false,code:'invalid_phone',error:'Check phone format'})});
 await f.submit();assert.equal(f.status.dataset.code,'invalid_phone');
 assert.equal(f.form.querySelector('[name=message]').value,'Inspect a synthetic component');
 assert.match(f.status.textContent,/Check phone format/);f.dom.window.close();
});

test('busy submission suppresses double-send and returns control after a rate limit',async()=>{
 const f=await setup();let release;f.replies.push(()=>new Promise(resolve=>{release=resolve;}));
 await f.submit();await f.submit();assert.equal(f.requests.length,1);assert.equal(f.form.getAttribute('aria-busy'),'true');
 release({ok:false,status:429,json:async()=>({ok:false})});await turn();
 assert.equal(f.form.querySelector('[type=submit]').disabled,false);assert.equal(f.status.dataset.kind,'error');
 assert.equal(f.form.querySelector('[name=email]').value,'engineer@example.invalid');f.dom.window.close();
});
