// Execute the complete shared browser controller with synthetic DOM/transports.
// No requests leave this process. Real browser + PostgreSQL acceptance is separate.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import {webcrypto} from 'node:crypto';

const source=readFileSync(new URL('../apps/website/app.js',import.meta.url),'utf8');
function fixture(){
  const handlers={}, events=[], requests=[], replies=[];
  const status={dataset:{},textContent:''}, help={textContent:''}, button={disabled:false};
  const values={full_name:'Synthetic tester',company:'Synthetic buyer',country:'China',
    email:'test@example.invalid',phone:'',message:'Synthetic control request',website:'',
    business_line:'circulatingWaterControl',application_context:'Cooling loop',consent:'on'};
  const elements=Object.fromEntries(Object.entries(values).map(([key,value])=>[key,{value,focus(){}}]));
  elements.business_line.selectedOptions=[{dataset:{en:'Circulating water control systems'}}];
  elements.business_line.addEventListener=()=>{};
  const form={elements,valid:true,resets:0,
    addEventListener(name,fn){handlers[name]=fn;},
    checkValidity(){return this.valid;},reportValidity(){},
    querySelector(selector){assert.equal(selector,'[type=submit]');return button;},
    reset(){this.resets++;Object.values(elements).forEach(e=>e.value='');}
  };
  const document={documentElement:{},referrer:'',
    querySelector(selector){return {'#project-form':form,'#form-status':status,'#business-help':help}[selector]||null;},
    querySelectorAll(){return [];},dispatchEvent(event){events.push(event);}
  };
  const window={};
  const context={document,window,localStorage:{getItem(){return null;}},location:{search:'',href:'http://localhost:8088/contact/',pathname:'/contact/'},
    URL,URLSearchParams,Uint8Array,crypto:webcrypto,AbortController,setTimeout,clearTimeout,
    CustomEvent:class{constructor(type,options){Object.assign(this,{type,...options});}},
    FormData:class{constructor(){this.data=Object.entries(elements).map(([k,v])=>[k,v.value]);}entries(){return this.data.values();}},
    async fetch(url,options){requests.push({url,payload:JSON.parse(options.body),options});const reply=replies.shift();if(reply instanceof Error)throw reply;if(typeof reply==='function')return await reply();return reply||{ok:true,status:200,json:async()=>({ok:true,duplicate:false,submission_id:1})};}
  };
  vm.runInNewContext(source,context);
  return {form,elements,status,button,events,requests,replies,window,
    input(trusted=true){handlers.input({isTrusted:trusted,target:{name:'company'}});},
    change(){handlers.change({isTrusted:true,target:{name:'business_line'}});},
    submit(){return handlers.submit({preventDefault(){}});},
    refill(){for(const [name,value] of Object.entries(values))elements[name].value=value;}
  };
}

test('analytics download link contains the selected 24/168-point chart dataset',()=>{
  const elements={
    '#data-chart':{innerHTML:''}, '#data-metrics':{innerHTML:''},
    '#data-description':{textContent:''}, '#data-export':{href:'',download:''},
    '#data-window':{value:'24',addEventListener(name,handler){this.change=handler;}},
  };
  const context={document:{documentElement:{},querySelector:s=>elements[s]||null,querySelectorAll:()=>[]},
    window:{},localStorage:{getItem:()=>null,setItem(){}},location:{search:'',href:'http://localhost/'},
    URL,URLSearchParams,encodeURIComponent};
  vm.runInNewContext(source,context);
  for(const hours of [24,168]){
    elements['#data-window'].value=String(hours);elements['#data-window'].change();
    const link=elements['#data-export'];
    assert.equal(link.download,`vorntekSyntheticData${hours}h.csv`);
    assert.ok(link.href.startsWith('data:text/csv;charset=utf-8,'));
    const lines=decodeURIComponent(link.href.slice(link.href.indexOf(',')+1)).trim().split('\n');
    assert.equal(lines[0],'synthetic_hour,synthetic_temperature_c,synthetic_load_percent');
    assert.equal(lines.length,hours+1);
    const points=[];
    for(let i=0;i<hours;i++){
      const [hour,temperature,load]=lines[i+1].split(',').map(Number);
      assert.equal(hour,i);
      assert.equal(temperature,Number((26+Math.sin(i*.34)*2+.5*Math.cos(i*.13)).toFixed(2)));
      assert.equal(load,Number((58+Math.cos(i*.24)*11).toFixed(1)));
      points.push(`${(i/(hours-1)*780+10).toFixed(1)},${(190-(temperature-22)*22).toFixed(1)}`);
    }
    assert.ok(elements['#data-chart'].innerHTML.includes(points.join(' ')));
    assert.ok(elements['#data-metrics'].innerHTML.includes(`<b>${hours}</b>`));
  }
});

test('shared form: all seven business lines submit industrial fields, never bottle capacity',async()=>{
  for(const line of ['circulatingWaterControl','liquidControl','waterQualitySensors','gasSensors','propaneMicroHeaters','smallTurbojetEngines','industrialDataAnalysis']){
    const f=fixture();f.elements.business_line.value=line;await f.submit();
    const {payload,url}=f.requests[0];assert.equal(url,'/admin/api/leads/forms/project-inquiry/submit/');
    assert.equal(payload.extra_fields.business_line,line);assert.equal(payload.extra_fields.application_context,'Cooling loop');
    assert.equal('capacity' in payload.extra_fields,false);assert.equal(f.status.dataset.kind,'success');
    assert.equal(payload.consent.marketing,false);assert.deepEqual(payload.utm,{});
    assert.equal(f.events[0].detail.eventId,payload.client_event_id);assert.equal(f.form.resets,1);
  }
});
test('validation refuses invalid, missing contact and whitespace; never calls backend',async()=>{
  for(const kind of ['invalid','contact','whitespace']){
    const f=fixture();if(kind==='invalid')f.form.valid=false;
    if(kind==='contact'){f.elements.email.value='';f.elements.phone.value='';}
    if(kind==='whitespace')f.elements.message.value=' ';
    await f.submit();assert.equal(f.requests.length,0);assert.equal(f.events.length,0);assert.equal(f.status.dataset.kind,'error');
  }
});
test('failed request preserves inputs, retry event id and no Lead until confirmed',async()=>{
  const f=fixture();f.replies.push(new Error('network failure'));await f.submit();
  assert.equal(f.form.resets,0);assert.equal(f.elements.company.value,'Synthetic buyer');assert.equal(f.events.length,0);
  assert.equal(f.status.dataset.kind,'error');assert.equal(f.button.disabled,false);
  await f.submit();assert.equal(f.requests[0].payload.client_event_id,f.requests[1].payload.client_event_id);
  assert.equal(f.events.length,1);assert.equal(f.form.resets,1);
});
test('server duplicate produces success without a second browser Lead',async()=>{
  const f=fixture();f.replies.push({ok:true,status:200,json:async()=>({ok:true,duplicate:true})});await f.submit();
  assert.equal(f.status.dataset.kind,'success');assert.equal(f.events.length,0);assert.equal(f.form.resets,1);
});
test('400,429,server ok:false and malformed responses do not reset or emit Lead',async()=>{
  for(const reply of [{ok:false,status:400,json:async()=>({ok:false})},{ok:false,status:429,json:async()=>({ok:false})},
    {ok:true,status:200,json:async()=>({ok:false})},{ok:true,status:200,json:async()=>{throw new Error('invalid JSON');}}]){
    const f=fixture();f.replies.push(reply);await f.submit();assert.equal(f.form.resets,0);assert.equal(f.events.length,0);
    assert.equal(f.status.dataset.kind,'error');assert.equal(f.button.disabled,false);
  }
});
test('concurrent clicks cannot send two requests',async()=>{
  const f=fixture();let resolve;f.replies.push(()=>new Promise(r=>resolve=r));const first=f.submit();
  assert.equal(f.button.disabled,true);await f.submit();assert.equal(f.requests.length,1);
  resolve({ok:true,status:200,json:async()=>({ok:true})});await first;assert.equal(f.button.disabled,false);
});
test('trusted input/change emits one FormStart per successful form cycle',async()=>{
  const f=fixture();f.input(false);assert.equal(f.events.length,0);f.input();f.change();
  assert.equal(f.events.filter(e=>e.type==='nc:form-started').length,1);
  await f.submit();f.refill();f.change();assert.equal(f.events.filter(e=>e.type==='nc:form-started').length,2);
});
test('changed request after failure gets a new id; marketing identifiers require consent',async()=>{
  const f=fixture();f.window.ncAdAttribution=()=>({gclid:'synthetic-click',utm_source:'synthetic'});
  f.window.ncMetaIdentifiers=()=>({fbp:'synthetic-fbp'});f.window.ncPrivacyConsentGranted=()=>false;
  f.replies.push(new Error('failed'));await f.submit();assert.equal(f.requests[0].payload.gclid,'');
  f.elements.message.value='Changed synthetic request';f.window.ncPrivacyConsentGranted=()=>true;
  await f.submit();assert.notEqual(f.requests[0].payload.client_event_id,f.requests[1].payload.client_event_id);
  assert.equal(f.requests[1].payload.gclid,'synthetic-click');assert.equal(f.requests[1].payload.fbp,'synthetic-fbp');
});
