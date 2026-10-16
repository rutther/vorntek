import './vendor/model-viewer/4.4.0/model-viewer.min.js';
import {orbitFromCamera,cameraFromOrbit} from './three-d-camera.js';

const model=document.getElementById('model');
const status=document.getElementById('model-status');
const config=JSON.parse(document.getElementById('model-config').textContent);
const query=new URL(location.href).searchParams;
const numeric=(key,fallback)=>{
  const raw=query.get(key), value=raw===null?Number(fallback):Number(raw);
  return Number.isFinite(value)?value:Number(fallback);
};
const position=['px','py','pz'].map((key,index)=>numeric(key,config.position[index]));
const target=['tx','ty','tz'].map((key,index)=>numeric(key,config.target[index]));
const orbit=orbitFromCamera(position,target);
const fov=Math.max(10,Math.min(120,numeric('fov',config.fov)));
const background=query.get('bg')||config.background;
if(/^#[0-9a-f]{6}$/i.test(background))model.style.backgroundColor=background;
const initialOrbit=`${orbit.theta}rad ${orbit.phi}rad ${orbit.radius}m`;
const initialTarget=target.map(value=>`${value}m`).join(' ');
const reset=()=>{
  model.cameraOrbit=initialOrbit; model.cameraTarget=initialTarget; model.fieldOfView=`${fov}deg`;
  model.jumpCameraToGoal();
};
model.setAttribute('min-camera-orbit','auto auto 0m');
model.setAttribute('max-camera-orbit','auto auto Infinity');
model.setAttribute('min-field-of-view','10deg');
model.setAttribute('max-field-of-view','120deg');
model.setAttribute('camera-orbit',initialOrbit);
model.setAttribute('camera-target',initialTarget);
model.setAttribute('field-of-view',`${fov}deg`);
const interactive=query.has('locked')?false:config.interaction;
model.toggleAttribute('camera-controls',interactive);
const rotating=query.has('noanim')?false:(query.has('spin')||config.autoRotate);
model.toggleAttribute('auto-rotate',rotating);
model.setAttribute('auto-rotate-delay','0');
model.setAttribute('rotation-per-second',`${360/Math.max(5,Math.min(120,numeric('spin',config.duration)))}deg`);
document.getElementById('model-controls').hidden=query.has('noui')||!config.showUi;
document.getElementById('reset-view').addEventListener('click',reset);
let loaded=false;
model.addEventListener('load',()=>{loaded=true; status.textContent='编辑预览 · 未公开发布';});
model.addEventListener('error',()=>{
  loaded=false; status.textContent='模型加载失败。原文件未更改，请返回资产库核对格式；不要重复发布。';
});
// A private draft may answer only its own same-origin embedding editor.
window.addEventListener('message',event=>{
  if(!loaded||event.source!==window.parent||event.origin!==location.origin||event.data?.type!=='siteos:getCamera')return;
  const camera=cameraFromOrbit(model.getCameraOrbit(),model.getCameraTarget(),model.getFieldOfView());
  window.parent.postMessage({type:'siteos:camera',camera},location.origin);
});
// The authenticated endpoint inspects self-contained content, and its CSP
// refuses remote resources/decoders. No cloud runtime or AR entry is enabled.
model.src=config.src;
