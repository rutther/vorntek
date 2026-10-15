import test from "node:test";
import assert from "node:assert/strict";
import { createApiClient, ApiError } from "../src/apiClient.mjs";

const response = (status, body) => ({ ok: status < 400, status, redirected: false,
  headers: { get: () => "application/json" }, json: async () => body });
const config = { apiUrl: "/admin/api/marketing/flows/", csrfToken: "test-csrf" };

const bounded = promise => {
 let timer;
 return Promise.race([promise,new Promise(resolve=>{timer=setTimeout(()=>resolve('still pending after deadline'),150);})]).finally(()=>clearTimeout(timer));
};

for(const kind of ['command','runtimeCommand','load','runtimeLoad'])test(`${kind} exits a hung transport and late results cannot replace a retry`,async()=>{
 const attempts=[];let release;
 const api=createApiClient({...config,timeoutMs:15},async(url,init)=>{
  attempts.push({url,init});
  if(attempts.length===1)return new Promise(resolve=>{release=resolve;});
  return response(200,{loop:{id:'a',version:3},marker:'retry'});
 });
 const call=()=>kind==='command'?api.command('save_draft',{id:'a',version:2},{nodeKey:'website',values:{url:'https://example.test/'}}):kind==='runtimeCommand'?api.runtimeCommand('a',{action:'pause',expectedVersion:2}):kind==='runtimeLoad'?api.runtimeLoad('a'):api.load();
 const pending=call();
 try{
  if(kind.includes('ommand'))assert.equal(call(),pending,'double click shares the pending operation');
  await assert.rejects(bounded(pending),e=>e instanceof ApiError&&e.code==='timeout'&&/结果尚未确认/.test(e.message));
  assert.equal(attempts[0].init.signal.aborted,true);
  const retried=await call();assert.equal(retried.marker,'retry');assert.equal(attempts.length,2,'no automatic replay');
  if(kind.includes('ommand'))assert.equal(JSON.parse(attempts[0].init.body).requestId,JSON.parse(attempts[1].init.body).requestId);
  release(response(200,{loop:{id:'a',version:1},marker:'late'}));
  await assert.rejects(pending,e=>e.code==='timeout','settled timeout cannot turn into late success');
  assert.equal(retried.marker,'retry');
 }finally{release?.(response(200,{loop:{id:'a',version:1}}));}
});

test('deadline also covers response body reads and preserves the command identity for retry',async()=>{
 const bodies=[];let finishBody;
 const api=createApiClient({...config,timeoutMs:15},async(_url,init)=>{
  bodies.push(JSON.parse(init.body));
  return bodies.length===1?{...response(200,{}),json:()=>new Promise(resolve=>{finishBody=resolve;})}:response(200,{loop:{id:'a',version:3}});
 });
 const call=()=>api.command('save_config',{id:'a',version:2},{nodeKey:'website',values:{url:'https://example.test/contact/'}});
 try{
  await assert.rejects(bounded(call()),e=>e.code==='timeout');
  assert.equal((await call()).loop.version,3);
  assert.deepEqual(bodies[0],bodies[1],'unknown outcome retries the exact idempotent command');
 }finally{finishBody?.({loop:{id:'a',version:3}});}
});

test("load uses same-origin credentials and never seeds an empty collection", async () => {
  const requests = [];
  const api = createApiClient(config, async (...args) => {
    requests.push(args); return response(200, { loops: [], definitions: {}, legacyLoops: [] });
  });
  assert.deepEqual((await api.load()).loops, []);
  assert.equal(requests[0][1].credentials, "same-origin");
  assert.equal(requests[0][1].method, "GET");
});

test("network retry reuses requestId and sends CSRF plus expected version", async () => {
  const bodies = [];
  const api = createApiClient(config, async (_url, init) => {
    assert.equal(init.headers["X-CSRFToken"], "test-csrf");
    bodies.push(JSON.parse(init.body));
    if (bodies.length === 1) throw Error("network down");
    return response(200, { loop: { id: "a", version: 3 } });
  });
  const loop = { id: "a", version: 2 };
  await assert.rejects(api.command("save_draft", loop, { nodeKey: "website", values: { url: "https://example.com" } }), /网络/);
  const saved = await api.command("save_draft", loop, { nodeKey: "website", values: { url: "https://example.com" } });
  assert.equal(saved.loop.version, 3);
  assert.equal(bodies[0].requestId, bodies[1].requestId);
  assert.equal(bodies[1].expectedVersion, 2);
});

test("409 exposes latest record without silently retrying or replacing user values", async () => {
  let calls = 0;
  const api = createApiClient(config, async () => {
    calls++; return response(409, { error: "其他页面已更新", code: "version_conflict", current: { id: "a", version: 5 } });
  });
  await assert.rejects(api.command("rename", { id: "a", version: 4 }, { name: "保留输入" }), (e) => {
    assert.ok(e instanceof ApiError); assert.equal(e.status, 409);
    assert.equal(e.current.version, 5); return true;
  });
  assert.equal(calls, 1);
});

test("double click shares an in-flight create; subsequent successful action gets a new key", async () => {
  const bodies = []; let release;
  const api = createApiClient(config, async (_url, init) => {
    bodies.push(JSON.parse(init.body));
    if (bodies.length === 1) await new Promise((resolve) => { release = resolve; });
    return response(200, { loop: { id: String(bodies.length), version: 1 } });
  });
  const args = { name: "演练流程", environment: "test" };
  const a = api.command("create", null, args), b = api.command("create", null, args);
  release();
  assert.deepEqual(await a, await b);
  assert.equal(bodies.length, 1);
  await api.command("create", null, args);
  assert.notEqual(bodies[0].requestId, bodies[1].requestId);
});

test("400 retains reusable request and reports error; edited payload gets a distinct request", async () => {
  const bodies = [];
  const api = createApiClient(config, async (_url, init) => {
    bodies.push(JSON.parse(init.body));
    return response(400, { error: "请输入数字 ID", code: "invalid" });
  });
  for (const externalId of ["abc", "abc", "12345678"]) {
    await assert.rejects(api.command("save_config", { id: "a", version: 1 }, { nodeKey: "dataset", values: { externalId } }), /数字/);
  }
  assert.equal(bodies[0].requestId, bodies[1].requestId);
  assert.notEqual(bodies[1].requestId, bodies[2].requestId);
});

test("login redirect is reported as session expiry, not JSON parse or success", async () => {
  const api = createApiClient(config, async () => ({ ok: true, status: 200, redirected: true,
    headers: { get: () => "text/html" }, json: async () => { throw Error("bad json"); } }));
  await assert.rejects(api.load(), /登录/);
});

test("503 never produces a success record and retry keeps the original request", async () => {
  const bodies = [];
  const api = createApiClient(config, async (_url, init) => {
    bodies.push(JSON.parse(init.body));
    return response(503, { error: "CRM 保存暂时不可用", code: "storage_unavailable" });
  });
  for (let i = 0; i < 2; i++) await assert.rejects(api.command("create", null, { name: "演练测试", environment: "test" }), (e) => e.status === 503);
  assert.equal(bodies[0].requestId, bodies[1].requestId);
});

test("re-login rotates cookie token while retry preserves requestId and entered values", async () => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, "document");
  let cookie = "sessionid=opaque; csrftoken=BeforeLogin123";
  Object.defineProperty(globalThis, "document", { configurable: true, value: { get cookie() { return cookie; } } });
  try {
    const attempts = [];
    const values = Object.freeze({ url: "https://example.com/contact/?lang=en" });
    const input = Object.freeze({ nodeKey: "website", values });
    const api = createApiClient(config, async (_url, init) => {
      attempts.push({ token: init.headers["X-CSRFToken"], body: JSON.parse(init.body) });
      if (attempts.length === 1) return { ok: false, status: 403, redirected: false,
        headers: { get: () => "text/html" } };
      return response(200, { loop: { id: "a", version: 3 } });
    });
    await assert.rejects(api.command("save_config", { id: "a", version: 2 }, input), /登录/);
    cookie = "sessionid=renewed; notcsrftoken=WrongValue; csrftoken=After%4Cogin456";
    const saved = await api.command("save_config", { id: "a", version: 2 }, input);
    assert.equal(saved.loop.version, 3);
    assert.equal(attempts[0].token, "BeforeLogin123");
    assert.equal(attempts[1].token, "AfterLogin456");
    assert.equal(attempts[0].body.requestId, attempts[1].body.requestId);
    assert.deepEqual(attempts[0].body, attempts[1].body);
    assert.deepEqual(input, { nodeKey: "website", values: { url: "https://example.com/contact/?lang=en" } });
  } finally {
    if (previous) Object.defineProperty(globalThis, "document", previous);
    else delete globalThis.document;
  }
});

test("missing, unreadable or malformed CSRF cookies safely fall back to bootstrap", async () => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, "document");
  try {
    for (const document of [undefined, { cookie: "sessionid=opaque" }, { cookie: "csrftoken=" },
      { cookie: "csrftoken=%broken" }, { cookie: "csrftoken=bad%0D%0Aheader" },
      { get cookie() { throw Error("cookie access blocked"); } }]) {
      Object.defineProperty(globalThis, "document", { configurable: true, value: document });
      const api = createApiClient(config, async (_url, init) => {
        assert.equal(init.headers["X-CSRFToken"], config.csrfToken);
        return response(200, { loop: { id: "a", version: 2 } });
      });
      await api.command("rename", { id: "a", version: 1 }, { name: "保留名称" });
    }
  } finally {
    if (previous) Object.defineProperty(globalThis, "document", previous);
    else delete globalThis.document;
  }
});
