// ES module: coordinates shared by saved profiles and orbit controls.
export function orbitFromCamera(position,target){
  const delta=position.map((value,index)=>Number(value)-Number(target[index]));
  const radius=Math.hypot(...delta);
  if(!Number.isFinite(radius)||radius<1e-6)return {theta:0,phi:Math.PI/2,radius:4};
  return {theta:Math.atan2(delta[0],delta[2]),phi:Math.acos(Math.max(-1,Math.min(1,delta[1]/radius))),radius};
}

export function cameraFromOrbit(orbit,target,fov){
  const {theta,phi,radius}=orbit;
  return {position:{x:target.x+radius*Math.sin(phi)*Math.sin(theta),
    y:target.y+radius*Math.cos(phi),z:target.z+radius*Math.sin(phi)*Math.cos(theta)},
    target:{x:target.x,y:target.y,z:target.z},fov};
}
