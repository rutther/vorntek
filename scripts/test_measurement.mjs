import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../apps/website/assets/meta-pixel.js', import.meta.url), 'utf8');
const config = (pixelId='123456789') => ({ok:true,schemaVersion:2,path:'/contact/',
  metaPixels:[{pixelId,legacy:true,events:['PageView','Lead']}],measurementContext:'synthetic-signed'});

async function simulate(payload, consent = 'granted', fails = false) {
  const events = [];
  const resources = [];
  const dialogs = [];
  const handlers = new Map();
  const storage = new Map([['nc_privacy_consent_v1', consent]]);
  const store = { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) };
  const document = {
    readyState: 'complete', title: 'Synthetic inquiry test', cookie: '', referrer: '',
    documentElement: { lang: 'en' }, body: { dataset: {}, appendChild(node) { if(node.id==='nc-consent')dialogs.push(node); } },
    head: { appendChild(node) { if (node.src) resources.push(node.src); } },
    getElementById() { return null; }, querySelector() { return null; }, querySelectorAll() { return []; },
    createElement() { return { setAttribute() {}, querySelector() { return { addEventListener() {} }; }, remove() {} }; },
    getElementsByTagName() { return [{ parentNode: { insertBefore(node) { resources.push(node.src); } } }]; },
    addEventListener(type, handler) { handlers.set(type, handler); },
  };
  const window = {
    location: new URL('http://localhost:8088/contact/?lang=en'), history: {},
    localStorage: store, sessionStorage: store, addEventListener() {}, setTimeout, clearTimeout,
    fbq: (...args) => events.push(args),
    fetch: async url => {
      assert.equal(url, '/api/marketing/measurement-config/?path=%2Fcontact%2F');
      if (fails) throw new Error('Synthetic unavailable config');
      return { ok: true, json: async () => payload };
    },
  };
  vm.runInNewContext(source, { window, document, URLSearchParams, URL, console, setTimeout });
  await new Promise(setImmediate);
  await new Promise(setImmediate);
  return { events, resources, handlers, dialogs };
}

test('disabled measurement remains silent even with remembered consent', async () => {
  const result = await simulate({ ok: true, meta_pixel_enabled: false, google_tag_enabled: false });
  result.handlers.get('nc:lead-accepted')({ detail: { eventId: 'synthetic-lead-1' } });
  assert.deepEqual(result.events, []);
  assert.deepEqual(result.resources, []);
});

test('unavailable config fails closed', async () => {
  const result = await simulate(null, 'granted', true);
  assert.deepEqual(result.events, []);
  assert.deepEqual(result.resources, []);
});

test('disabled or unavailable measurement does not show misleading opt-in UI', async () => {
  for(const unavailable of [false,true]){
    const result=await simulate({...config(),metaPixels:[],google_tag_enabled:false},'',unavailable);
    assert.equal(result.dialogs.length,0);assert.deepEqual(result.resources,[]);
  }
});

test('configured measurement prompts undecided visitors without loading trackers', async () => {
  const result=await simulate(config(),'');
  assert.equal(result.dialogs.length,1);assert.deepEqual(result.resources,[]);assert.deepEqual(result.events,[]);
});

test('enabled pixel still requires consent', async () => {
  const result = await simulate(config(), 'denied');
  assert.deepEqual(result.events, []);
  assert.deepEqual(result.resources, []);
});

test('invalid configured pixel is rejected', async () => {
  const result = await simulate(config('<invalid>'));
  assert.deepEqual(result.events, []);
  assert.deepEqual(result.resources, []);
});

test('explicit synthetic configuration preserves accepted Lead eventID', async () => {
  const result = await simulate(config());
  assert.ok(result.events.some(args => args[0] === 'init' && args[1] === '123456789'));
  assert.equal(result.events.filter(args => args[2] === 'Lead').length, 0);
  result.handlers.get('nc:lead-accepted')({ detail: { measurement:{shouldTrack:true,
    eventId:'synthetic-lead-1',pixelIds:['123456789']} } });
  await new Promise(setImmediate);
  const lead = result.events.find(args => args[2] === 'Lead');
  assert.equal(lead[1], '123456789');
  assert.equal(lead[4].eventID, 'synthetic-lead-1');
});
