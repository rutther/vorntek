import test from "node:test";
import assert from "node:assert/strict";
import { saveDirtySteps } from "../src/guidance.mjs";

test("leaving saves every dirty step in order, including collapsed steps", async () => {
  const calls = [];
  const result = await saveDirtySteps(["dataset", "website", "ad"], { dataset: true, website: false, ad: true }, async key => {
    calls.push(key);
    await Promise.resolve();
    return true;
  });
  assert.equal(result, true);
  assert.deepEqual(calls, ["dataset", "ad"]);
});

test("a failed draft write stops navigation and does not try later writes", async () => {
  const calls = [];
  const result = await saveDirtySteps(["dataset", "website", "ad"], { dataset: true, website: true, ad: true }, async key => {
    calls.push(key);
    return key !== "website";
  });
  assert.equal(result, false);
  assert.deepEqual(calls, ["dataset", "website"]);
});
