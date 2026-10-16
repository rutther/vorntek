import assert from 'node:assert/strict';
import test from 'node:test';
import {orbitFromCamera, cameraFromOrbit} from '../apps/crm/console/static/console/three-d-camera.js';

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
