import assert from 'node:assert/strict';
import test from 'node:test';
import {orbitFromCamera, cameraFromOrbit, snapshotSplatCamera} from '../apps/crm/console/static/console/three-d-camera.js';

test('saved Cartesian camera maps to a meter-based orbit around its actual target',()=>{
  const orbit=orbitFromCamera([1,2,7],[1,2,3]);
  assert.equal(orbit.theta,0); assert.equal(orbit.phi,Math.PI/2); assert.equal(orbit.radius,4);
});
test('camera capture returns world position not a spherical angle as Cartesian fields',()=>{
  const camera=cameraFromOrbit({theta:Math.PI/2,phi:Math.PI/2,radius:4},{x:1,y:2,z:3},60);
  assert.equal(camera.position.x,5); assert.ok(Math.abs(camera.position.y-2)<1e-12);
  assert.ok(Math.abs(camera.position.z-3)<1e-12); assert.equal(camera.fov,60);
  assert.deepEqual(camera.target,{x:1,y:2,z:3});
});
test('zero-radius or invalid drafts do not create an invalid rendering camera',()=>{
  assert.equal(orbitFromCamera([0,0,0],[0,0,0]).radius,4);
  assert.equal(orbitFromCamera([NaN,Infinity,0],[0,0,0]).radius,4);
});
test('splat capture reads the current panned pivot rather than reconstructing a saved distance',()=>{
  const current={position:{x:2,y:3,z:4,clone(){return {x:this.x,y:this.y,z:this.z};}},fov:52,
    calcFocusPoint(out){Object.assign(out,{x:-1,y:2,z:0});return out;}};
  const camera=snapshotSplatCamera(current);
  assert.deepEqual(camera,{position:{x:2,y:3,z:4},target:{x:-1,y:2,z:0},fov:52});
  current.position.x=8;assert.equal(camera.position.x,2);
});
