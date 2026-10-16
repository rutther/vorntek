// Preview-only visual overrides. The authenticated asset URL is never overridable.
export function draftConfig(saved,url){
  const config={...saved},query=new URL(url).searchParams;
  const number=(key,value)=>{
    const input=query.get(key);
    return input!==null&&input.trim()!==''&&Number.isFinite(Number(input))?Number(input):value;
  };
  const flag=(key,fallback)=>query.get(key)==='1'?true:query.get(key)==='0'?false:fallback;
  config.position=['px','py','pz'].map((key,index)=>number(key,saved.position[index]));
  config.target=['tx','ty','tz'].map((key,index)=>number(key,saved.target[index]));
  config.fov=Math.max(10,Math.min(120,number('fov',saved.fov)));
  config.duration=Math.max(5,Math.min(120,number('spin',saved.duration)));
  if(/^#[a-f0-9]{6}$/i.test(query.get('bg')||''))config.background=query.get('bg');
  config.autoRotate=flag('rotate',query.has('noanim')?false:query.has('spin')||saved.autoRotate);
  config.interaction=flag('interaction',query.has('locked')?false:saved.interaction);
  config.showUi=flag('ui',query.has('noui')?false:saved.showUi);
  return config;
}

export function setSplatInteraction(state,canvas,enabled){
  // Upstream inputEnabled gates keyboard/gamepad, not canvas pointer gestures.
  state.inputEnabled=Boolean(enabled);
  if(canvas)canvas.style.pointerEvents=enabled?'auto':'none';
}
