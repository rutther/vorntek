import test from "node:test";
import assert from "node:assert/strict";
import * as m from "../src/model.mjs";

test('node positioning stays inside its domain and rejects archived writes',()=>{
 const loop=m.createLoop({name:'移动测试',environment:'test'});
 const moved=m.moveNode(loop,'website',{x:900,y:-40});
 assert.deepEqual(moved.positions.website,{x:77,y:65});
 assert.equal(moved.revision,loop.revision);
 assert.throws(()=>m.moveNode({...loop,archived:true},'website',{x:25,y:92}),/恢复/);
 assert.throws(()=>m.moveNode(loop,'filter',{x:20,y:90}),/节点/);
});

test("create rejects whitespace name and normalizes a valid one", () => {
  assert.throws(
    () => m.createLoop({ name: "  ", environment: "production" }),
    /名称/,
  );
  const a = m.createLoop({ name: "  官网   询盘 ", environment: "test" });
  const b = m.createLoop({ name: "官网询盘", environment: "test" });
  assert.equal(a.name, "官网 询盘");
  assert.notEqual(a.id, b.id);
  assert.equal(m.summary(a).state, "draft");
  assert.equal(a.environment, "test");
});
test("rename keeps immutable identity and rejects overly long names", () => {
  const a = m.createLoop({ name: "测试流程", environment: "test" });
  const b = m.renameLoop(a, "新的中文流程");
  assert.equal(b.id, a.id);
  assert.equal(b.code, a.code);
  assert.throws(() => m.renameLoop(a, "长".repeat(201)), /200/);
});
test("dataset requires explicit source and numeric id", () => {
  let a = m.createLoop({ name: "测试流程", environment: "test" });
  assert.throws(
    () => m.saveConfig(a, "dataset", { source: "existing", externalId: "abc" }),
    /数字/,
  );
  assert.throws(
    () => m.saveConfig(a, "dataset", { externalId: "12345678" }),
    /来源/,
  );
  a = m.saveConfig(a, "dataset", {
    source: "existing",
    externalId: "12345678",
  });
  assert.equal(m.summary(a).done, 1);
  assert.equal(m.summary(a).next, "website");
  assert.equal(m.nodeState(a, "dataset"), "saved");
});
test("http URL validation rejects executable or incomplete websites", () => {
  const a = m.createLoop({ name: "测试流程", environment: "test" });
  assert.throws(
    () => m.saveConfig(a, "website", { url: "javascript:alert(1)" }),
    /网址/,
  );
  assert.throws(() => m.saveConfig(a, "website", { url: "not-a-url" }), /网址/);
});
test("saving changes invalidates prior simulated verification", () => {
  let a = m.seedLoops()[1];
  a = m.simulateCheck(a, "pass");
  assert.equal(m.summary(a).state, "simulated");
  a = m.saveConfig(a, "dataset", {
    source: "existing",
    externalId: "87654321",
  });
  assert.equal(a.check, null);
  assert.notEqual(m.summary(a).state, "simulated");
});
test("simulation cannot pass incomplete configuration", () => {
  const a = m.createLoop({ name: "未配置流程", environment: "test" });
  assert.throws(() => m.simulateCheck(a, "pass"), /配置/);
});
test("duplicate never copies asset ids, evidence, or object identity", () => {
  const a = m.simulateCheck(m.seedLoops()[1], "pass");
  const b = m.duplicateLoop(a);
  assert.notEqual(b.id, a.id);
  assert.notEqual(b.code, a.code);
  assert.equal(b.check, null);
  assert.equal(m.summary(b).done, 0);
  assert.equal(b.configs.dataset, undefined);
});
test("optional filter changes both task count and links", () => {
  const a = m.createLoop({ name: "筛选流程", environment: "test" });
  const b = m.toggleFilter(a, true);
  assert.equal(m.tasks(b).length, m.tasks(a).length + 1);
  assert.ok(
    m.connections(b).some((e) => e.source === "crm" && e.target === "filter"),
  );
  assert.ok(
    !m.connections(b).some((e) => e.source === "crm" && e.target === "server"),
  );
  const c = m.toggleFilter(b, false);
  assert.ok(
    m.connections(c).some((e) => e.source === "crm" && e.target === "server"),
  );
  assert.ok(!m.tasks(c).includes("filter"));
});
test("archive and restore preserve configuration, not external side effects", () => {
  let a = m.seedLoops()[0];
  const config = JSON.stringify(a.configs);
  a = m.archiveLoop(a, true);
  assert.equal(m.summary(a).state, "archived");
  assert.equal(JSON.stringify(a.configs), config);
  a = m.archiveLoop(a, false);
  assert.equal(m.summary(a).state, "configuring");
});
test("list filters combine name, environment and state and preserve all source records", () => {
  const loops = m.seedLoops();
  assert.equal(
    m.filterLoops(loops, {
      search: "官网询盘",
      state: "all",
      environment: "production",
    }).length,
    1,
  );
  assert.equal(
    m.filterLoops(loops, { search: "不存在", state: "all" }).length,
    0,
  );
  assert.equal(loops.length, 4);
});
test("storage round trips and does not hide corrupt data or blocked writes", () => {
  const map = new Map();
  const storage = {
    getItem: (k) => map.get(k) ?? null,
    setItem: (k, v) => map.set(k, v),
  };
  const a = m.seedLoops();
  m.writeStore(storage, a);
  assert.deepEqual(m.readStore(storage), a);
  storage.setItem(m.STORAGE_KEY, "{bad");
  assert.throws(() => m.readStore(storage), /损坏/);
  assert.throws(
    () =>
      m.writeStore(
        {
          setItem() {
            throw Error("quota");
          },
        },
        a,
      ),
    /保存/,
  );
});

test("well-formed JSON with incomplete records is rejected without overwriting", () => {
  for (const mutate of [
    (l) => delete l.positions,
    (l) => (l.configs.dataset = {}),
    (l) => (l.environment = "unknown"),
    (l) => (l.history = [{}]),
  ]) {
    const loop = m.seedLoops()[1];
    mutate(loop);
    const raw = JSON.stringify({ version: 1, loops: [loop] });
    assert.throws(() => m.readStore({ getItem: () => raw }), /损坏/);
  }
});
test("same-route navigation keeps editing in place without a discard prompt", () => {
  assert.equal(
    m.navigationIntent(
      "/flow/a/config/dataset",
      "/flow/a/config/dataset",
      true,
    ),
    "stay",
  );
  assert.equal(
    m.navigationIntent(
      "/flow/a/config/dataset",
      "/flow/a/config/website",
      true,
    ),
    "confirm",
  );
  assert.equal(
    m.navigationIntent("/flow/a/config/dataset", "/loops", false),
    "navigate",
  );
});
