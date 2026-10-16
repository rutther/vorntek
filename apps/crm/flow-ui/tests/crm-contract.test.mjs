import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import postcss from "postcss";
import { crmCssScope } from "../scripts/crm-css-scope.mjs";
import { createLoop, nodeState, summary, simulateCheck, tasks } from "../src/model.mjs";

test("CRM records cannot become simulated or verified through prototype state", () => {
  const loop = { ...createLoop({ name: "真实工作区测试", environment: "test" }), version: 1,
    check: { result: "pass", revision: 1 }, history: [], taskKeys: ["dataset", "website"] };
  assert.equal(summary(loop).state, "draft");
  assert.throws(() => simulateCheck(loop, "pass"), /不提供模拟/);
  assert.deepEqual(tasks(loop), ["dataset", "website"]);
  loop.history = [{ kind: "manual", nodeKey: "website", result: "issue", revision: 1 }];
  assert.equal(summary(loop).state, "attention");
  assert.equal(summary(loop).next, "website");
  loop.history.unshift({ kind: "manual", nodeKey: "website", result: "observed", revision: 1 });
  assert.equal(nodeState(loop, "website"), "pending");
  assert.equal(summary(loop).state, "draft");
  loop.revision = 2;
  assert.equal(summary(loop).state, "draft");
});

test("CRM CSS scopes resets and vendor selectors without breaking keyframes", async () => {
  const css = ":root{--x:1}body{margin:0}button, input:hover{color:red}@media(max-width:760px){.item{display:block}}@keyframes fade{from{opacity:0}to{opacity:1}}";
  const result = await postcss([crmCssScope()]).process(css, { from: undefined });
  assert.match(result.css, /\.flow-crm-root\{--x/);
  assert.match(result.css, /\.flow-crm-root button/);
  assert.match(result.css, /\.flow-crm-root input:hover/);
  assert.match(result.css, /@keyframes fade\{from/);
  const styles = await readFile(new URL("../src/styles.css", import.meta.url), "utf8");
  const scoped = await postcss([crmCssScope()]).process(styles, { from: undefined });
  scoped.root.walkRules((rule) => {
    if (rule.parent.type === "atrule" && /keyframes$/.test(rule.parent.name)) return;
    assert.ok(rule.selectors.every((selector) => selector.startsWith(".flow-crm-root")), rule.selector);
  });
});

test("CRM static build resolves inside the intended existing console static directory", async () => {
  const config = (await import("../vite.crm.config.mjs")).default;
  assert.ok(config.build.outDir.replaceAll("\\", "/").endsWith("/apps/crm/console/static/console/flow-workspace/"));
  assert.equal(config.build.rollupOptions.output.entryFileNames, "app.js");
  assert.equal(config.build.emptyOutDir, true);
});
