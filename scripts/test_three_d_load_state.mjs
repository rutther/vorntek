import assert from 'node:assert/strict';
import test from 'node:test';
import {createLoadStatus} from '../apps/crm/console/static/console/three-d-load-state.js';

test('a slow model gives actionable feedback but can still finish loading',async()=>{
  const status={textContent:'Loading'};
  const load=createLoadStatus(status,{delayMs:5,privatePreview:true});
  await new Promise(resolve=>setTimeout(resolve,20));
  assert.match(status.textContent,/较久/);
  load.ready();
  assert.match(status.textContent,/未公开发布/);
  load.dispose();
});
test('a rejected asset is not labelled ready by a later callback',()=>{
  const status={textContent:'Loading'};
  const load=createLoadStatus(status,{delayMs:50});
  load.failed();
  assert.match(status.textContent,/加载失败/);
  load.ready();
  assert.match(status.textContent,/加载失败/);
  load.dispose();
});
test('reduced motion explains why a configured automatic rotation is paused',()=>{
  const status={textContent:'Loading'};
  const load=createLoadStatus(status,{reducedMotion:true,privatePreview:true});
  load.ready();
  assert.match(status.textContent,/减少动态/);assert.match(status.textContent,/暂停/);
  load.dispose();
});
test('ready and disposed models do not acquire a stale timeout warning',async()=>{
  for(const method of ['ready','dispose']){
    const status={textContent:'Loading'};
    const load=createLoadStatus(status,{delayMs:5});
    load[method]();const before=status.textContent;
    await new Promise(resolve=>setTimeout(resolve,20));
    assert.equal(status.textContent,before);
    load.dispose();
  }
});
