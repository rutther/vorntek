import './vendor/model-viewer/4.4.0/model-viewer.min.js';
import {orbitFromCamera,cameraFromOrbit} from './three-d-camera.js';
import {draftConfig} from './three-d-preview-config.js';
import {createLoadStatus} from './three-d-load-state.js';

const model=document.getElementById('model');
const status=document.getElementById('model-status');
const config=draftConfig(JSON.parse(document.getElementById('model-config').textContent),location.href);
const {position,target}=config;
const orbit=orbitFromCamera(position,target);
const fov=config.fov;
const background=config.background;
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
const interactive=config.interaction;
model.toggleAttribute('camera-controls',interactive);
const rotating=config.autoRotate&&!matchMedia('(prefers-reduced-motion: reduce)').matches;
model.toggleAttribute('auto-rotate',rotating);
model.setAttribute('auto-rotate-delay','0');
model.setAttribute('rotation-per-second',`${360/config.duration}deg`);
document.getElementById('model-controls').hidden=!config.showUi;
document.getElementById('reset-view').addEventListener('click',reset);
let loaded=false;
const load=createLoadStatus(status,{privatePreview:true,reducedMotion:config.autoRotate&&!rotating});
window.addEventListener('pagehide',()=>load.dispose(),{once:true});
model.addEventListener('load',()=>{loaded=true;load.ready();});
model.addEventListener('error',()=>{
  loaded=false;load.failed();
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
