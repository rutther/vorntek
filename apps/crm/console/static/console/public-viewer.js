import {orbitFromCamera} from './three-d-camera.js';

const config=JSON.parse(document.getElementById('model-config').textContent);
const status=document.getElementById('model-status');
const controls=document.getElementById('model-controls');
controls.hidden=!config.showUi;
const rotating=config.autoRotate&&!matchMedia('(prefers-reduced-motion: reduce)').matches;
const failed=()=>{status.textContent='Unable to load 3D. Reload this page or contact the site owner. / 3D加载失败，请刷新或联系网站管理员。';};
const ready=()=>{status.textContent='3D';};

try{
  if(config.kind==='glb'){
    await import('./vendor/model-viewer/4.4.0/model-viewer.min.js');
    const model=document.getElementById('model');
    model.style.backgroundColor=config.background;
    model.setAttribute('min-camera-orbit','auto auto 0m');
    model.setAttribute('max-camera-orbit','auto auto Infinity');
    model.setAttribute('min-field-of-view','10deg');
    model.setAttribute('max-field-of-view','120deg');
    const orbit=orbitFromCamera(config.position,config.target);
    const reset=()=>{
      model.cameraOrbit=`${orbit.theta}rad ${orbit.phi}rad ${orbit.radius}m`;
      model.cameraTarget=config.target.map(value=>`${value}m`).join(' ');
      model.fieldOfView=`${config.fov}deg`;model.jumpCameraToGoal();
    };
    model.setAttribute('camera-orbit',`${orbit.theta}rad ${orbit.phi}rad ${orbit.radius}m`);
    model.setAttribute('camera-target',config.target.map(value=>`${value}m`).join(' '));
    model.setAttribute('field-of-view',`${config.fov}deg`);
    model.toggleAttribute('camera-controls',config.interaction);
    model.toggleAttribute('auto-rotate',rotating);
    model.setAttribute('auto-rotate-delay','0');
    model.setAttribute('rotation-per-second',`${360/Math.max(5,config.duration)}deg`);
    model.addEventListener('load',ready);model.addEventListener('error',failed);
    document.getElementById('reset-view').addEventListener('click',reset);
    model.src=config.src;
  }else{
    const {createViewer}=await import('./vendor/supersplat/1.37.0/index.js');
    const rgb=[1,3,5].map(index=>parseInt(config.background.slice(index,index+2),16)/255);
    const tracks=[];
    if(rotating){
      const times=[],position=[],target=[],fov=[],duration=Math.max(5,config.duration);
      const dx=config.position[0]-config.target[0],dz=config.position[2]-config.target[2];
      for(let i=0;i<=32;i++){
        const angle=i/32*2*Math.PI;times.push(i/32*duration);
        position.push(config.target[0]+dx*Math.cos(angle)+dz*Math.sin(angle),config.position[1],config.target[2]-dx*Math.sin(angle)+dz*Math.cos(angle));
        target.push(...config.target);fov.push(config.fov);
      }
      tracks.push({name:'orbit',duration,frameRate:30,loopMode:'repeat',interpolation:'spline',smoothness:1,keyframes:{times,values:{position,target,fov}}});
    }
    const settings={version:2,tonemapping:'linear',highPrecisionRendering:false,background:{color:rgb},
      postEffectSettings:{sharpness:{enabled:false,amount:0},bloom:{enabled:false,intensity:0.1,blurLevel:2},
        grading:{enabled:false,brightness:1,contrast:1,saturation:1,tint:[1,1,1]},
        vignette:{enabled:false,intensity:0.5,inner:0.3,outer:0.75,curvature:1},fringing:{enabled:false,intensity:0.5}},
      cameras:[{initial:{position:config.position,target:config.target,fov:config.fov}}],
      animTracks:tracks,annotations:[],startMode:rotating?'animTrack':'default'};
    const viewer=await createViewer({container:document.getElementById('splat-viewer'),settings,
      contentUrl:config.src,renderer:'webgl',ui:config.showUi,exposeGlobals:false,noanim:!rotating});
    viewer.state.inputEnabled=config.interaction;
    if(viewer.state.loaded)ready();else viewer.events.on('loaded:changed',value=>{if(value)ready();});
    viewer.events.on('error:changed',value=>{if(value)failed();});
    document.getElementById('reset-view').addEventListener('click',()=>{if(viewer.state.loaded)viewer.resetCamera();});
    window.addEventListener('pagehide',()=>viewer.destroy(),{once:true});
  }
}catch{failed();}
