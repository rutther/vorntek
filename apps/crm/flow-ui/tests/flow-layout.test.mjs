import test from 'node:test';
import assert from 'node:assert/strict';
import {flowLayout,flowConnections} from '../src/flowLayout.mjs';
test('guide graph aligns both return paths while preserving saved positions',()=>{
 const compact=flowLayout({positions:{},filterEnabled:false},true);
 assert.equal(compact.positions.pixel.y,compact.positions.server.y);
 assert.equal(compact.positions.dataset.y,compact.positions.server.y);
 assert.equal(compact.laneHeight,500);
 const saved={pixel:{x:70,y:550}};
 const restored=flowLayout({positions:saved,filterEnabled:false},true);
 assert.deepEqual(restored.positions.pixel,saved.pixel);
 assert.ok(restored.laneHeight>=saved.pixel.y+120);
 assert.deepEqual(saved,{pixel:{x:70,y:550}});
});
test('legacy graph and optional filter preserve the original separated layout',()=>{
 assert.equal(flowLayout({positions:{}},false).positions.pixel.y,422);
 assert.equal(flowLayout({positions:{},filterEnabled:true},true).laneHeight,665);
});
test('compact browser return runs directly left while server return runs below it',()=>{
 const edges=flowConnections({filterEnabled:false},true);
 assert.equal(edges.length,7);
 assert.equal(edges.find(e=>e.source==='pixel').from,'left');
 assert.equal(edges.find(e=>e.source==='server').from,'bottom');
 assert.equal(edges.find(e=>e.source==='server').to,'bottom');
 assert.equal(flowConnections({filterEnabled:false},false).find(e=>e.source==='pixel').from,'bottom');
});

test('compact website browser branch bypasses the form on its left without changing legacy edges',()=>{
 const loop={positions:{form:{x:10,y:224}},filterEnabled:false};
 const edges=flowConnections(loop,true);
 const browser=edges.find(e=>e.source==='website'&&e.target==='pixel');
 assert.equal(browser.from,'browser-bottom');assert.equal(browser.to,'top');
 assert.equal(browser.data.browserBranchLeft,258,'lane offset 270 plus leftmost node 10 minus clearance 22');
 assert.equal(edges.find(e=>e.source==='form').from,'right');
 assert.equal(edges.find(e=>e.source==='form').to,'left');
 for(const [legacy,guide] of [[loop,false],[{...loop,filterEnabled:true},true]]){
  const edge=flowConnections(legacy,guide).find(e=>e.source==='website'&&e.target==='pixel');
  assert.equal(edge.from,'right');assert.equal(edge.to,'right');assert.equal(edge.data,undefined);
 }
 assert.deepEqual(loop.positions,{form:{x:10,y:224}},'routing does not rewrite saved positions');
});
