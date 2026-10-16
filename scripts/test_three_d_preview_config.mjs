import assert from 'node:assert/strict';
import test from 'node:test';
import {draftConfig,setSplatInteraction} from '../apps/crm/console/static/console/three-d-preview-config.js';

const saved={position:[0,0,4],target:[0,0,0],fov:60,background:'#ffffff',
  showUi:false,interaction:false,autoRotate:false,duration:20,src:'/admin/assets/1/file/'};
test('an unsaved editor can enable as well as disable every preview switch',()=>{
  const enabled=draftConfig(saved,'http://localhost/?ui=1&interaction=1&rotate=1');
  assert.equal(enabled.showUi,true);assert.equal(enabled.interaction,true);assert.equal(enabled.autoRotate,true);
  const disabled=draftConfig(enabled,'http://localhost/?ui=0&interaction=0&rotate=0');
  assert.equal(disabled.showUi,false);assert.equal(disabled.interaction,false);assert.equal(disabled.autoRotate,false);
  assert.equal(saved.showUi,false);
});
test('draft query never changes the scoped asset and rejects invalid visual values',()=>{
  const value=draftConfig(saved,'http://localhost/?px=NaN&fov=999&bg=url(evil)&src=https://example.invalid/model.ply&ui=invalid');
  assert.deepEqual(value.position,[0,0,4]);assert.equal(value.fov,120);assert.equal(value.background,'#ffffff');
  assert.equal(value.src,'/admin/assets/1/file/');assert.equal(value.showUi,false);
});
test('locked splats disable pointer gestures as well as global keyboard input',()=>{
  const state={inputEnabled:true},canvas={style:{pointerEvents:''}};
  setSplatInteraction(state,canvas,false);
  assert.equal(state.inputEnabled,false);assert.equal(canvas.style.pointerEvents,'none');
  setSplatInteraction(state,canvas,true);
  assert.equal(state.inputEnabled,true);assert.equal(canvas.style.pointerEvents,'auto');
});
