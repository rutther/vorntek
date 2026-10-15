import React,{useEffect,useRef,useState} from 'react';
import {PathPicker} from './PathPicker.jsx';
import {validatePixelValues,mergePaths} from './pixel-model.mjs';
import './pixel-step.css';

const empty=()=>({landingPaths:[],leadPaths:[],formId:'',consentRequired:true});
const scopeFrom=(loop,state)=>{const source=loop.drafts.pixel?.scope??state?.draft??{};return {...empty(),...source,formId:String(source.formId??'')};};
export function PixelStep({loop,runtime,update,onContinue,controller,onStatusChange,busy,direct=false,workbench=false}){
 const s=runtime.state;
 const [scope,setScope]=useState(()=>scopeFrom(loop,s)),[baseline,setBaseline]=useState(null),[version,setVersion]=useState(null);
 const [errors,setErrors]=useState({}),[notice,setNotice]=useState(''),[saving,setSaving]=useState(false);
 const [manual,setManual]=useState({landingPaths:'',leadPaths:''});
 const formRef=useRef(null),lock=useRef(false),edited=useRef(false);
 const dirty=baseline!==null&&(JSON.stringify(scope)!==baseline||Object.values(manual).some(v=>v.trim())); edited.current=dirty;
 useEffect(()=>{onStatusChange?.({dirty,saving});},[dirty,saving,onStatusChange]);
 const disabled=busy||saving||runtime.busy||loop.archived||!s;
 const catalog=s?.pageCatalog;
 useEffect(()=>{if(s&&!edited.current&&!lock.current){const next=scopeFrom(loop,s);setScope(next);setBaseline(JSON.stringify(next));setVersion(s.version);}},[s?.version,loop.version]);
 const focusError=key=>requestAnimationFrame(()=>{const element=formRef.current?.querySelector(`#pixel-${key}`);if(element?.tagName==='SUMMARY')element.parentElement.open=true;element?.focus();});
 const save=async(draft=false)=>{
  if(lock.current||disabled||version===null)return false;
  const submitted=structuredClone(scope);
  for(const key of ['landingPaths','leadPaths']){try{submitted[key]=mergePaths(scope[key],manual[key]);}catch(error){setErrors({[key]:error.message});focusError(key);return false;}}
  const found=draft?{}:validatePixelValues(submitted,catalog,s.forms);
  if(Object.keys(found).length){setErrors(found);focusError(Object.keys(found)[0]);return false;}
  lock.current=true;setSaving(true);setErrors({});setNotice('');
  try{
   const saved=await update(loop,'',{action:'save_pixel_step',intent:draft?'draft':'save',expectedRuntimeVersion:version,
    values:{mode:loop.configs.pixel?.mode||'managed',consent:true},scope:submitted});
   if(!saved){setErrors({save:'保存未成功，输入已保留。请查看页面上方的具体提示后重试。'});await runtime.read();return false;}
   setScope(submitted);setManual({landingPaths:'',leadPaths:''});setBaseline(JSON.stringify(submitted));edited.current=false;
   setNotice(draft?'草稿已暂存，尚未应用':s.activeRevision?'配置已保存，当前运行版本未改变':direct?'配置已保存，尚未启用':'本步配置已保存，尚未启用');
   // The runtime version advances only for a complete save, never for editing drafts.
   setVersion(saved.savedRuntimeVersion??version+(draft?0:1));
   await runtime.read();
   return true;
  }catch(error){setErrors({save:'保存未成功，输入已保留。请重试。'});return false;}finally{lock.current=false;setSaving(false);}
 };
 if(controller)controller.current={get dirty(){return dirty||saving;},save:()=>save(true)};
 return <form className="config-form pixel-step-form" noValidate ref={formRef} onSubmit={e=>{e.preventDefault();save();}} aria-label="配置网站 Pixel 接入">
  <div className="pixel-step-content">
   <div className="pixel-context"><div><span>当前数据集</span><strong>{loop.configs.dataset?.externalId||'尚未登记'}</strong></div><span className={`pixel-runtime-status is-${s?.runtimeState||'loading'}`}>{!s?'正在读取状态':s.runtimeState==='applied'?'Pixel 运行中':s.runtimeState==='paused'?'Pixel 已暂停':s.runtimeState==='inactive'?'Pixel 已停用':'Pixel 尚未启用'}</span></div>
   {runtime.error&&<p className="pixel-field-error" role="alert">{runtime.error}<button type="button" className="text-button" onClick={runtime.read}>重试读取</button></p>}
   {!s?<p role="status">正在读取网站接入设置…</p>:<>
    {!s.integration&&<div className="pixel-prepare"><p>尚未准备实际接入。管理员准备后才能保存完整配置；现在可以暂存草稿。</p><button type="button" className="button" disabled={disabled||!s.canOperate} onClick={()=>runtime.command('prepare_integration')}>准备接入（暂不启用）</button></div>}
    {!catalog?.available&&<p id="pixel-catalog" tabIndex={-1} className="pixel-field-error" role="alert">目录暂不可用，已选内容保留。<button className="text-button" type="button" disabled={disabled} onClick={runtime.read}>刷新页面目录</button></p>}
    {version!==null&&s.version!==version&&<section className="pixel-conflict" role="alert"><strong>接入版本已更新，当前输入仍保留</strong><p>请先核对差异；如上方也提示流程版本冲突，先点击“加载最新版本”。确认只更新本页保存基准，不会立即覆盖服务器。</p><details><summary>查看当前输入与服务器最新范围</summary><h4>当前输入（版本 {version}）</h4><pre>{JSON.stringify(scope,null,2)}</pre><h4>服务器最新（版本 {s.version}）</h4><pre>{JSON.stringify(s.draft,null,2)}</pre></details><button type="button" className="button" disabled={disabled} onClick={()=>{setVersion(s.version);setErrors({});setNotice('已核对新版本，请检查当前输入后重新保存');}}>已核对，保留当前输入并使用最新版本</button></section>}
    <div className={workbench?'guide-pixel-layout':undefined}><div>
    <PathPicker id="pixel-landingPaths" initiallyOpen={workbench} eventName={workbench?'浏览事件 PageView':undefined} manual={manual.landingPaths} onManualChange={v=>setManual({...manual,landingPaths:v})} label="记录浏览的页面" hint="访问选中页面时记录；不代表提交询盘。首页和联系页可以同时勾选。" pages={catalog?.pages||[]} available={catalog?.available} selected={scope.landingPaths} onChange={v=>{setScope({...scope,landingPaths:v});setErrors({...errors,landingPaths:''});}} disabled={disabled} error={errors.landingPaths}/>
    <PathPicker id="pixel-leadPaths" eventName={workbench?'成功询盘 Lead':undefined} manual={manual.leadPaths} onManualChange={v=>setManual({...manual,leadPaths:v})} label="记录成功询盘的页面" hint="独立选择允许成功表单提交的页面，只有提交成功才记录 Lead。" pages={catalog?.pages||[]} available={catalog?.available} selected={scope.leadPaths} onChange={v=>{setScope({...scope,leadPaths:v});setErrors({...errors,leadPaths:''});}} disabled={disabled} error={errors.leadPaths}/>
    <section className="pixel-field"><header><label htmlFor="pixel-formId"><strong>网站询盘表单</strong><span className="required"> ＊必填</span></label><span>单选</span></header><p>{direct?'此处维护方案的唯一表单关联，引导中沿用同一选择。':'此处选择会带入05核对，不需要再次选择。'}</p>
     <select id="pixel-formId" aria-invalid={Boolean(errors.formId)} disabled={disabled} value={scope.formId} onChange={e=>{setScope({...scope,formId:e.target.value});setErrors({...errors,formId:''});}}><option value="">请选择本站表单</option>{s.forms.map(f=><option key={f.id} value={f.id}>{f.name} · {f.code}</option>)}{scope.formId&&!s.forms.some(f=>String(f.id)===scope.formId)&&<option value={scope.formId}>已选表单不可用，请重新选择</option>}</select>
     {errors.formId&&<p className="pixel-field-error" role="alert">{errors.formId}</p>}
    </section>
    </div>
    {workbench&&<aside className="guide-pixel-explanation">
     <section className="guide-scope-summary" aria-label="本次追踪范围摘要"><h4>这次会记录什么</h4><p>当前编辑范围，不代表已保存或生效。</p>
      <dl><div><dt>数据集</dt><dd>{loop.configs.dataset?.externalId||'尚未登记'}</dd></div>
       <div data-scope="browse"><dt>浏览范围</dt><dd>{scope.landingPaths.length} 个页面</dd></div>
       <div data-scope="lead"><dt>成功询盘范围</dt><dd>{scope.leadPaths.length} 个页面 · {s.forms.some(f=>String(f.id)===scope.formId)?1:0} 个表单</dd></div>
       <div><dt>旧 Pixel</dt><dd>保留，不自动停用</dd></div></dl>
      {Object.values(manual).some(v=>v.trim())&&<p role="status">手填路径尚未计入摘要；点击“加入已选页面”或保存时合并核验。</p>}
      <p>遵循营销追踪同意；拒绝追踪仍可提交询盘。所选页面的自然访问也会记录。</p>
     </section>
     <p className="guide-scope-save-note">保存只更新配置，不改变当前运行版本。完成接收与回传设置后，到07比较差异，再决定是否应用。</p>
     <section className="guide-event-meaning"><h4>容易混淆的地方</h4><p>PageView：有人浏览了页面。</p><p>Lead：询盘表单已成功入库。</p><p>表单按钮点击：不等于提交成功。</p></section>
    </aside>}
    </div>
    {!workbench&&<p className="pixel-boundary">仅在访客同意营销追踪后发送，询盘成功入库后才记录 Lead。所选页面的自然访问也会记录；旧 Pixel 保留。</p>}
    {s.activeRevision&&<details><summary>查看当前生效范围（保存不会替换它）</summary><pre>{JSON.stringify(s.activeRevision.scope,null,2)}</pre></details>}
   </>}
   {(errors.save||errors.catalog)&&<p className="pixel-field-error" role="alert">{errors.save||errors.catalog}</p>}
  </div>
  <footer className="guide-step-actions pixel-step-actions"><div role="status"><strong>{saving?'正在保存…':dirty?'有未保存修改':notice|| (loop.drafts.pixel?.scope?'已有编辑草稿，尚未完成':s?.activeRevision&&s?.configState==='saved'?'当前范围已应用':s?.draft?.formId?'范围已保存，尚未应用':'尚未保存')}</strong><small>{direct?'保存不改变生效版本；到“运行与版本”查看差异并应用。':'保存不等于启用；07核对并应用，09由你实测。'}</small></div><div><button type="button" className="button" disabled={disabled} onClick={()=>save(true)}>暂存草稿</button><button type="submit" className={`button ${direct?'primary':''}`} disabled={disabled}>{direct?'保存范围':'保存本步'}</button>{!direct&&<button type="button" className="button primary" disabled={disabled} onClick={async()=>{if(await save())onContinue();}}>保存并继续 →</button>}</div></footer>
 </form>;
}


