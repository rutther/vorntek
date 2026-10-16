// Slow is not failed: a large model can still finish after the warning appears.
export function createLoadStatus(status,{privatePreview=false,reducedMotion=false,delayMs=30000}={}){
  let failed=false;
  const timer=setTimeout(()=>{
    status.textContent='3D加载较久，请检查网络；可刷新重试，原文件和配置未更改。 / 3D is taking longer to load.';
  },delayMs);
  return {
    ready(){if(!failed){clearTimeout(timer);status.textContent=(privatePreview?'编辑预览 · 未公开发布':'3D')
      +(reducedMotion?' · 已按系统减少动态设置暂停自动旋转 / Reduced motion':'');}},
    failed(){failed=true;clearTimeout(timer);status.textContent='3D加载失败，请刷新或联系网站管理员。 / Unable to load 3D. Reload or contact the site owner.';},
    dispose(){clearTimeout(timer);}
  };
}
