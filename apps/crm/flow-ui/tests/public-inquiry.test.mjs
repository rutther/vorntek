import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync, readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';

const path=new URL('../../../website/inquiry-form.js',import.meta.url);
const source=existsSync(path)?readFileSync(path,'utf8'):'';
const siteController=readFileSync(new URL('../../../website/app.js',import.meta.url),'utf8');
const css=readFileSync(new URL('../../../website/styles.css',import.meta.url),'utf8');
const defaults=[{name:'full_name',type:'text',label:'姓名',required:true},
 {name:'email',type:'email',label:'邮箱',required:false},
 {name:'phone',type:'tel',label:'电话',required:false},
 {name:'message',type:'textarea',label:'咨询内容',required:false}];
const html=code=>`<form data-configured-form data-form-code="${code}" novalidate>
 <div data-inquiry-fields></div><input name="website"><input name="consent" type="checkbox" required>
 <button class="button" type="submit" disabled>Send</button><button class="button" type="button" data-form-retry hidden>Retry</button>
 <p role="status" data-form-status></p></form>`;
const flush=()=>new Promise(resolve=>setTimeout(resolve,0));
async function fixture({fields=defaults,definitionError=false,second=false}={}){
 const dom=new JSDOM(`<html lang="en"><style>${css}</style>${html('general-inquiry')}${second?html('service-inquiry'):''}</html>`,
  {url:'https://example.test/contact/',runScripts:'outside-only'});
 const {window}=dom,requests=[],replies=[],events=[];
 let fail=definitionError;
 window.fetch=async(url,options={})=>{
  requests.push({url,options,payload:options.body?JSON.parse(options.body):null});
  if(!options.body){if(fail)throw Error('Synthetic unavailable');return {ok:true,json:async()=>({ok:true,form:{code:url.includes('service-inquiry')?'service-inquiry':'general-inquiry',fields}})};}
  const result=replies.shift();if(result instanceof Error)throw result;
  if(typeof result==='function')return result();
  return result||{ok:true,status:200,json:async()=>({ok:true,measurement:{shouldTrack:false}})};
 };
 window.document.addEventListener('nc:lead-accepted',event=>events.push(event.detail));
 window.eval(source);await flush();await flush();
 const form=window.document.querySelector('form');
 return {window,dom,form,requests,replies,events,
  get status(){return form.querySelector('[data-form-status]');},
  async submit(){form.dispatchEvent(new window.Event('submit',{cancelable:true}));await flush();await flush();},
  recover(){fail=false;},close(){window.close();}};
}

test('configured general form removes industry, company and country requirements',async()=>{
 const ui=await fixture();try{
  assert.ok(ui.form.elements.full_name,'configured fields must be rendered');
  assert.equal(ui.form.elements.full_name.required,true);
  assert.equal(ui.form.elements.company,undefined);assert.equal(ui.form.elements.country,undefined);
  assert.equal(ui.form.elements.business_line,undefined);assert.equal(ui.form.elements.capacity,undefined);
  ui.form.elements.full_name.value='Example visitor';ui.form.elements.email.value='visitor@example.test';
  ui.form.elements.consent.checked=true;await ui.submit();
  const writes=ui.requests.filter(r=>r.payload);assert.equal(writes.length,1);
  assert.equal(writes[0].url,'/admin/api/leads/forms/general-inquiry/submit/');
  assert.deepEqual(writes[0].payload.extra_fields,{});
  assert.equal(writes[0].payload.company,undefined);assert.equal(ui.status.dataset.kind,'success');
 }finally{ui.close();}
});

test('loaded form hides retry and gives the long request its own row',async()=>{
 const ui=await fixture();try{
  assert.equal(ui.window.getComputedStyle(ui.form.querySelector('[data-form-retry]')).display,'none');
  assert.ok(ui.form.elements.message.closest('label').classList.contains('inquiry-field-wide'));
 }finally{ui.close();}
});

test('language switch translates current status without resetting the request or losing errors',async()=>{
 const ui=await fixture();try{
  const select=ui.window.document.createElement('select');select.id='language';
  select.innerHTML='<option value="en">EN</option><option value="zh">中文</option>';
  ui.window.document.body.append(select);ui.window.eval(siteController);
  select.value='zh';select.dispatchEvent(new ui.window.Event('change'));
  assert.equal(ui.status.textContent,'邮箱或电话至少填写一项，* 为必填。');
  ui.form.elements.full_name.value='Example';ui.form.elements.email.value='visitor@example.test';
  ui.form.elements.consent.checked=true;ui.replies.push(Error('Synthetic failure'));await ui.submit();
  assert.equal(ui.status.dataset.kind,'error');
  select.value='en';select.dispatchEvent(new ui.window.Event('change'));
  assert.equal(ui.status.textContent,'Submission was not confirmed. Entries are preserved; retry the same request.');
  assert.equal(ui.status.dataset.kind,'error');assert.equal(ui.form.elements.email.value,'visitor@example.test');
  assert.equal(ui.requests.filter(r=>r.payload).length,1);
 }finally{ui.close();}
});

test('custom select/date/number and optional name follow the configured definition',async()=>{
 const ui=await fixture({fields:[{name:'email',type:'email',label:'Email',required:true},
  {name:'request_kind',type:'select',label:'Request type',required:true,options:[{value:'support',label:'Support'}]},
  {name:'quantity',type:'number',label:'Quantity',required:false},
  {name:'requested_date',type:'date',label:'Requested date',required:false}]});try{
  assert.ok(ui.form.elements.request_kind);assert.equal(ui.form.elements.full_name,undefined);
  ui.form.elements.email.value='visitor@example.test';ui.form.elements.request_kind.value='support';
  ui.form.elements.quantity.value='0';ui.form.elements.requested_date.value='2026-10-16';
  ui.form.elements.consent.checked=true;await ui.submit();
  const write=ui.requests.find(r=>r.payload);assert.ok(write);
  assert.deepEqual(write.payload.extra_fields,{request_kind:'support',quantity:'0',requested_date:'2026-10-16'});
 }finally{ui.close();}
});

test('definition failure is recoverable but cannot submit an invented fallback form',async()=>{
 const ui=await fixture({definitionError:true});try{
  assert.equal(ui.status.dataset.kind,'error');assert.equal(ui.form.querySelector('[type=submit]').disabled,true);
  await ui.submit();assert.equal(ui.requests.filter(r=>r.payload).length,0);
  const retry=ui.form.querySelector('[data-form-retry]');assert.equal(retry.hidden,false);
  ui.recover();retry.click();await flush();await flush();
  assert.ok(ui.form.elements.email);assert.equal(ui.form.querySelector('[type=submit]').disabled,false);
 }finally{ui.close();}
});

test('failed submission retains input and event identity; accepted retry uses the authoritative receipt',async()=>{
 const ui=await fixture();try{
  assert.ok(ui.form.elements.email);ui.form.elements.full_name.value='Example';ui.form.elements.email.value='visitor@example.test';
  ui.form.elements.consent.checked=true;ui.window.ncPrivacyConsentGranted=()=>true;
  ui.window.ncMeasurementContext=async()=>'signed-synthetic-context';
  ui.replies.push(Error('Synthetic interrupted response'));await ui.submit();
  assert.equal(ui.form.elements.email.value,'visitor@example.test');assert.equal(ui.status.dataset.kind,'error');
  assert.equal(ui.events.length,0);
  ui.replies.push({ok:true,json:async()=>({ok:true,duplicate:true,
   measurement:{shouldTrack:true,eventId:'canonical-retry-id',pixelIds:['222']}})});
  await ui.submit();const writes=ui.requests.filter(r=>r.payload);
  assert.equal(writes[0].payload.client_event_id,writes[1].payload.client_event_id);
  assert.equal(writes[1].payload.measurement_context,'signed-synthetic-context');
  assert.equal(ui.events.length,1);assert.equal(ui.events[0].eventId,'canonical-retry-id');
  assert.equal(ui.events[0].formCode,'general-inquiry');assert.equal(ui.form.elements.email.value,'');
 }finally{ui.close();}
});

test('multiple same-page forms retain independent schema, endpoint and pending state',async()=>{
 const ui=await fixture({second:true});try{
  const forms=ui.window.document.querySelectorAll('form');assert.equal(forms.length,2);
  for(const form of forms){assert.ok(form.elements.email);form.elements.full_name.value='Example';
   form.elements.email.value='visitor@example.test';form.elements.consent.checked=true;}
  let release;ui.replies.push(()=>new Promise(resolve=>release=resolve));
  await ui.submit();await ui.submit();
  assert.equal(ui.requests.filter(r=>r.payload).length,1);
  forms[1].dispatchEvent(new ui.window.Event('submit',{cancelable:true}));await flush();await flush();
  const writes=ui.requests.filter(r=>r.payload);assert.equal(writes.length,2);
  assert.equal(writes[1].url,'/admin/api/leads/forms/service-inquiry/submit/');
  release({ok:true,json:async()=>({ok:true})});await flush();await flush();
  assert.equal(ui.form.querySelector('[type=submit]').disabled,false);
 }finally{ui.close();}
});

test('required whitespace and missing enabled contact never reach capture',async()=>{
 for(const missing of ['name','contact','consent']){
  const ui=await fixture();try{
   ui.form.elements.full_name.value=missing==='name'?'   ':'Example';
   ui.form.elements.email.value=missing==='contact'?'':'visitor@example.test';
   ui.form.elements.consent.checked=missing!=='consent';await ui.submit();
   assert.equal(ui.requests.filter(r=>r.payload).length,0);assert.equal(ui.status.dataset.kind,'error');
  }finally{ui.close();}
 }
});

test('rejected, rate-limited and malformed responses preserve configured fields without a Lead',async()=>{
 for(const reply of [{ok:false,status:400,json:async()=>({ok:false})},
  {ok:false,status:429,json:async()=>({ok:false})},{ok:true,json:async()=>({ok:false})},
  {ok:true,json:async()=>{throw Error('Synthetic malformed JSON');}}]){
  const ui=await fixture();try{
   ui.form.elements.full_name.value='Example';ui.form.elements.email.value='visitor@example.test';
   ui.form.elements.consent.checked=true;ui.replies.push(reply);await ui.submit();
   assert.equal(ui.form.elements.email.value,'visitor@example.test');assert.equal(ui.status.dataset.kind,'error');
   assert.equal(ui.form.querySelector('[type=submit]').disabled,false);assert.equal(ui.events.length,0);
  }finally{ui.close();}
 }
});

test('changed fields create a new request identity and unconsented attribution stays absent',async()=>{
 const ui=await fixture();try{
  ui.form.elements.full_name.value='Example';ui.form.elements.email.value='visitor@example.test';
  ui.form.elements.consent.checked=true;
  ui.window.ncAdAttribution=()=>({gclid:'synthetic-click',utm_campaign:'synthetic-campaign'});
  ui.window.ncMetaIdentifiers=()=>({fbp:'synthetic-browser-id'});
  ui.window.ncPrivacyConsentGranted=()=>false;
  ui.replies.push(Error('Synthetic failure'));await ui.submit();
  const first=ui.requests.find(r=>r.payload).payload;
  assert.equal(first.fbp,undefined);assert.equal(first.gclid,undefined);assert.deepEqual(first.utm,{});
  ui.form.elements.message.value='Different request';ui.window.ncPrivacyConsentGranted=()=>true;await ui.submit();
  const second=ui.requests.filter(r=>r.payload)[1].payload;
  assert.notEqual(first.client_event_id,second.client_event_id);assert.equal(second.gclid,'synthetic-click');
  assert.equal(second.fbp,'synthetic-browser-id');assert.equal(second.utm.utm_campaign,'synthetic-campaign');
 }finally{ui.close();}
});

test('server validation explains the correction while preserving input and retry identity',async()=>{
 const ui=await fixture();try{
  ui.form.elements.full_name.value='Synthetic';ui.form.elements.phone.value='abc';ui.form.elements.consent.checked=true;
  const invalid={ok:false,status:400,json:async()=>({ok:false,code:'invalid_submission',error:'Phone number is invalid.'})};
  ui.replies.push(invalid);await ui.submit();
  assert.match(ui.status.textContent,/Phone number is invalid/);
  assert.doesNotMatch(ui.status.textContent,/retry the same request/);
  assert.equal(ui.status.dataset.code,'invalid_submission');assert.equal(ui.form.elements.phone.value,'abc');
  ui.replies.push(invalid);await ui.submit();
  let writes=ui.requests.filter(r=>r.payload);assert.equal(writes[0].payload.client_event_id,writes[1].payload.client_event_id);
  ui.form.elements.phone.value='+15550001234';await ui.submit();writes=ui.requests.filter(r=>r.payload);
  assert.notEqual(writes[1].payload.client_event_id,writes[2].payload.client_event_id);
  assert.equal(ui.status.dataset.kind,'success');assert.equal(ui.events.length,1);
 }finally{ui.close();}
});

test('validation detail is bounded literal text and server failures do not expose internal errors',async()=>{
 for(const status of [400,500]){
  const ui=await fixture();try{
   ui.form.elements.full_name.value='Synthetic';ui.form.elements.email.value='test@example.invalid';ui.form.elements.consent.checked=true;
   ui.replies.push({ok:false,status,json:async()=>({ok:false,code:'invalid_submission',error:'<img src=x onerror=alert(1)>PRIVATEDETAIL'+ 'x'.repeat(600)})});
   await ui.submit();assert.equal(ui.status.querySelector('img'),null);assert.ok(ui.status.textContent.length<500);
   if(status===400)assert.match(ui.status.textContent,/PRIVATEDETAIL/);else assert.doesNotMatch(ui.status.textContent,/PRIVATEDETAIL/);
   assert.equal(ui.events.length,0);assert.equal(ui.form.elements.email.value,'test@example.invalid');
  }finally{ui.close();}
 }
});
