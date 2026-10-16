import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';
const source = readFileSync(new URL('../apps/website/assets/meta-pixel.js', import.meta.url), 'utf8');
const settle = () => new Promise(resolve => setImmediate(resolve));
function fixture(consent='granted', language='en') {
  const calls=[], listeners={}, nodes=[], storage=new Map([['nc_privacy_consent_v1',consent]]);
  const element=()=>({setAttribute(){},appendChild(){},addEventListener(){},remove(){},querySelector:element});
  const document={readyState:'complete', title:'Test',cookie:'', referrer:'',documentElement:{lang:language},
    body:{dataset:{},appendChild(node){nodes.push(node);}},head:{appendChild(){}},createElement:element,
    getElementById:()=>null,querySelector:()=>null,querySelectorAll:()=>[],getElementsByTagName:()=>[{parentNode:{insertBefore(){}}}],
    addEventListener:(name,fn)=>listeners[name]=fn,dispatchEvent:e=>listeners[e.type]?.(e)};
  let payload={ok:true,schemaVersion:2,path:'/contact/',maxAgeSeconds:60,measurementContext:'signed',
    metaPixels:[{pixelId:'111',legacy:true,events:['PageView','Lead','Contact','FormStart']},{pixelId:'222',events:['PageView','Lead']}]};
  const window={location:{pathname:'/contact',search:'',href:'https://example.test/contact'},
    history:{},navigator:{userAgent:'Test'},addEventListener(){},setTimeout,clearTimeout,
    localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
    sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},
    fetch:async()=>({ok:true,json:async()=>payload}),fbq:(...args)=>calls.push(args)};
  vm.runInNewContext(source,{window,document,URLSearchParams,CustomEvent:class{constructor(type,{detail}){this.type=type;this.detail=detail;}},AbortController,setTimeout,clearTimeout});
  return {window,document,calls,listeners,storage,nodes,setPayload:p=>payload=p};
}
test('uses per-pixel routing and never broadcasts',async()=>{
  const f=fixture(); await settle(); await settle();
  assert.deepEqual(f.calls.filter(x=>x[0]==='init').map(x=>x[1]),['111','222']);
  assert.equal(f.calls.filter(x=>x[0]==='trackSingle'&&x[2]==='PageView').length,2);
  assert.equal(f.calls.some(x=>['track','trackCustom'].includes(x[0])),false);
});
test('only canonical server receipt sends Lead, once per target/event',async()=>{
  const f=fixture(); await settle();
  const detail={measurement:{shouldTrack:true,eventId:'canonical',pixelIds:['222','999']}};
  f.listeners['nc:lead-accepted']({detail}); f.listeners['nc:lead-accepted']({detail});
  await settle();
  const leads=f.calls.filter(x=>x[2]==='Lead');
  assert.equal(leads.length,1); assert.equal(leads[0][1],'222');assert.equal(leads[0][4].eventID,'canonical');
  f.window.ncMetaTrack('Lead',{}); await settle(); assert.equal(f.calls.filter(x=>x[2]==='Lead').length,1);
});
test('denied consent prevents initialization, context and Lead',async()=>{
  const f=fixture('denied'); await settle();
  assert.equal(await f.window.ncMeasurementContext(),'');
  assert.equal(f.calls.length,0);
});
test('a failed refresh has no hardcoded Pixel fallback',async()=>{
  const f=fixture(); await settle();
  f.window.location.pathname='/other'; f.window.fetch=async()=>{throw Error('offline');};
  const before=f.calls.length; assert.equal(await f.window.ncMeasurementContext(),'');
  f.window.ncMetaTrack('Contact',{}); await settle(); assert.equal(f.calls.length,before);
});


test('Chinese privacy controls remain available without enabling tracking',async()=>{
  const f=fixture('denied','zh'); await settle();
  f.window.ncPrivacyChoices();
  assert.match(f.nodes.at(-1).innerHTML,/隐私选项/);
  assert.match(f.nodes.at(-1).innerHTML,/lang=zh/);
  assert.equal(f.calls.length,0);
});

test('configured inquiry context is preserved instead of a fixed industry category',async()=>{
  const f=fixture(); await settle();
  f.listeners['nc:lead-accepted']({detail:{formCode:'service-consultation',category:'service_consultation',
    measurement:{shouldTrack:true,eventId:'service-attempt',pixelIds:['222']}}});
  await settle();
  const lead=f.calls.find(x=>x[2]==='Lead');
  assert.equal(lead[3].form_code,'service-consultation');
  assert.equal(lead[3].content_category,'service_consultation');
});
