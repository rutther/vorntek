import React, {useEffect, useRef, useState} from 'react';
import {LOOP_TEST_KEY} from './guidance.mjs';
import {Icon} from './ui.jsx';
import {GuideRuntimeView} from './GuideRuntimeView.jsx';

const fields = draft => ({landing:(draft?.landingPaths || []).join('\n'), lead:(draft?.leadPaths || []).join('\n'), form:String(draft?.formId || '')});
export function useRuntime(loop, api, enabled, onSnapshot) {
  const [snapshot,setSnapshot]=useState(null), [values,setValues]=useState(fields()), [baseline,setBaseline]=useState(JSON.stringify(fields()));
  const [error,setError]=useState(''), [busy,setBusy]=useState(false), [notice,setNotice]=useState(''), [loading,setLoading]=useState(false);
  const lock=useRef(false), edits=useRef(false),sequence=useRef(0),alive=useRef(true),currentId=useRef(loop.id);
  currentId.current=loop.id;
  const state=enabled&&snapshot?.id===loop.id?snapshot.data:null;
  const dirty=JSON.stringify(values)!==baseline; edits.current=dirty;
  const current=(id,ticket)=>alive.current&&currentId.current===id&&sequence.current===ticket;
  useEffect(()=>{
    alive.current=true;lock.current=false;setSnapshot(null);setValues(fields());setBaseline(JSON.stringify(fields()));setError('');setNotice('');setBusy(false);setLoading(false);
    return()=>{alive.current=false;sequence.current++;};
  },[loop.id,enabled]);
  const read=async()=>{
    if (!enabled||!api?.runtimeLoad||lock.current) return false;
    const id=loop.id,ticket=++sequence.current;
    setError('');setLoading(true);
    try { const {runtime}=await api.runtimeLoad(id);if(!current(id,ticket))return false;
      setSnapshot({id,data:runtime});
      onSnapshot?.(id,runtime);
      if (!edits.current) { const next=fields(runtime.draft);setValues(next);setBaseline(JSON.stringify(next)); }
      return true;
    } catch(e) {if(current(id,ticket))setError(e.message);return false;}
    finally{if(current(id,ticket))setLoading(false);}
  };
  useEffect(()=>{ if(enabled) read(); },[enabled,loop.id,loop.version]);
  const command=async(action, extra={})=>{
    if(lock.current || !state || !enabled || loop.archived) return false;
    const id=loop.id,ticket=++sequence.current;
    lock.current=true;setBusy(true);setLoading(false);setError('');setNotice('');
    try {
      const {runtime}=await api.runtimeCommand(id,{action,expectedVersion:state.version,...extra});
      if(!current(id,ticket))return false;
      setSnapshot({id,data:runtime});
      onSnapshot?.(id,runtime);
      if(action==='save_draft'||action==='rollback') { const next=fields(runtime.draft);setValues(next);setBaseline(JSON.stringify(next)); }
      setNotice(action==='save_draft'?'范围已保存，尚未应用。':action==='prepare_integration'?'实际接入已准备，尚未启用。':action==='pause'?'本方案已暂停；其他方案与旧接入不受影响。':'运行版本已应用；事件接收与去重仍需测试。');
      return true;
    } catch(e) {if(current(id,ticket))setError(e.message);return false;}
    finally {if(current(id,ticket)){lock.current=false;setBusy(false);}}
  };
  const save=()=>command('save_draft',{draft:{landingPaths:values.landing.split('\n').map(x=>x.trim()).filter(Boolean),leadPaths:values.lead.split('\n').map(x=>x.trim()).filter(Boolean),formId:values.form,consentRequired:true}});
  return {state,values,setValues,error,busy,loading,notice,dirty,read,command,save};
}

export function RuntimePanel({mode, runtime:r, loop, direct=false,workbench=false,configuration}) {
  const [confirm,setConfirm]=useState(''), [target,setTarget]=useState('');
  const s=r.state, disabled=r.busy||r.loading||loop.archived;
  const unchanged=s?.runtimeState==='applied'&&s?.configState==='saved'&&!s.blockingChecks.length;
  const confirmationAllowed=Boolean(s?.canOperate&&(
    confirm==='pause'?s.runtimeState==='applied':
    !r.dirty&&(confirm==='rollback'?s.history.some(h=>h.key===target):
      !s.blockingChecks.length&&(confirm==='resume'?s.runtimeState==='paused':confirm==='apply'&&s.runtimeState!=='paused'))
  ));
  useEffect(()=>{setConfirm('');},[s?.version,s?.flowVersion,s?.configHash,s?.runtimeState,r.dirty]);
  useEffect(()=>{if(confirm&&!confirmationAllowed)setConfirm('');},[confirm,confirmationAllowed]);
  useEffect(()=>{setTarget('');setConfirm('');},[loop.id]);
  const history=s?.activeRevision&&<details><summary>当前版本与历史恢复</summary><p>当前版本：{s.activeRevision.key.slice(0,8)}</p><label className="field">恢复历史范围<select value={target} disabled={disabled} onChange={e=>{setTarget(e.target.value);setConfirm('');}}><option value="">选择本方案历史版本</option>{s.history.map(h=><option key={h.key} value={h.key}>{h.createdAt} · {h.key.slice(0,8)}</option>)}</select></label>{target&&<><ScopeComparison state={s} target={s.history.find(h=>h.key===target)?.scope}/><button className="button" disabled={disabled||r.dirty||!s.canOperate||Boolean(r.error)} onClick={()=>setConfirm('rollback')}>预备恢复这个范围</button></>}</details>;
  const controls=s&&<>
   <div className="runtime-actions"><button className="button" disabled={disabled} onClick={r.read}>刷新检查</button>
    {unchanged?<a className="button primary" href={`#/flow/${loop.id}?view=${direct?'verification':`tasks&node=${LOOP_TEST_KEY}`}`}>{direct?'查看核验记录':'继续事件测试'}</a>:<button className="button primary" disabled={disabled||r.dirty||!s.canOperate||s.blockingChecks.length>0||Boolean(r.error)} onClick={()=>setConfirm(s.runtimeState==='paused'?'resume':'apply')}>{s.runtimeState==='paused'?'恢复方案':'应用方案'}</button>}
    {s.runtimeState==='applied'&&<button className="button" disabled={disabled||!s.canOperate||Boolean(r.error)} onClick={()=>setConfirm('pause')}>暂停方案</button>}
   </div>
   {!s.canOperate&&<p>当前账号可保存范围；实际应用、暂停与恢复由系统管理员执行。</p>}
   {r.dirty&&<p role="status">{direct?'先保存“网站与表单”的范围，再应用。':'先在 04 保存接入范围，再应用。'}</p>}
   {confirm&&<div className="runtime-confirm" role="group" aria-label="确认运行变更">
    <strong>{confirm==='pause'?'确认暂停本方案？':confirm==='rollback'?'确认恢复选中的历史范围？':'确认将上述范围应用到网站与服务器？'}</strong>
    <p>数据集 {s.integration?.pixelId}；环境 {s.integration?.environment}。不会修改其他数据集。已发出的事件不能撤回。</p>
    {confirm==='rollback'&&<><p>目标历史版本：<code>{target}</code>。恢复后，当前生效范围与范围草稿将采用该版本；其他步骤的配置记录不变。</p><ScopeComparison state={s} target={s.history.find(h=>h.key===target)?.scope}/></>}
    <div className="runtime-actions"><button className="button primary" disabled={disabled||Boolean(r.error)||!confirmationAllowed} onClick={async()=>{if(!confirmationAllowed)return;if(await r.command(confirm,confirm==='pause'?{}:{configHash:s.configHash,expectedFlowVersion:s.flowVersion,...(confirm==='rollback'?{targetRevision:target}:{})}))setConfirm('');}}>确认{confirm==='pause'?'暂停':confirm==='rollback'?'恢复历史范围':'应用'}</button><button className="button" disabled={disabled} onClick={()=>setConfirm('')}>取消</button></div>
   </div>}
  </>;
  if(workbench)return <GuideRuntimeView runtime={r} loop={loop} comparison={s&&<ScopeComparison state={s}/>} dependencies={s&&<RuntimeDependencies dependencies={s.dependencies} canOperate={s.canOperate} compact/>} history={history} controls={controls} configuration={configuration}/>;
  return <section className="runtime-panel" aria-label={mode==='pixel'?'网站实际接入范围':'实际运行管理'}>
    <header><h4>{direct?'运行与版本':mode==='pixel'?'让本数据集接入网站':'应用网站与服务器接入'}</h4><span>{!s?'尚未读取':s.runtimeState==='applied'?'运行版本已应用':s.runtimeState==='paused'?'已暂停':s.runtimeState==='inactive'?'已停用，保留历史版本':'尚未应用'}</span></header>
    <p className="runtime-help">{direct?'已保存配置不会自动替换当前运行版本。核对目标和范围后再应用。':'这里控制真实运行配置，与上方操作登记分开。'}旧 Pixel 保留；不会创建 Meta 资产或发布广告。</p>
    {r.error&&<p role="alert" className="form-error">{r.error}</p>}
    {r.notice&&<p role="status">{r.notice}</p>}
    {!s?<button className="button" onClick={r.read}>读取实际接入</button>:<>
      <div className="runtime-id">{direct?'方案数据集':'本流程数据集'} <strong>{loop.configs.dataset?.externalId||(direct?'尚未关联':'请先在 01 登记')}</strong><span>{s.activeRevision?<>已应用版本 <code title={s.activeRevision.key}>{s.activeRevision.key.slice(0,8)}</code></>:'尚无已应用版本'}</span></div>
      {mode==='pixel'?<>
        {!s.integration&&<button className="button" disabled={disabled||!s.canOperate||!loop.configs.dataset?.externalId} onClick={()=>r.command('prepare_integration')}>准备实际接入（暂不启用）</button>}
        <form className="runtime-scope" onSubmit={e=>{e.preventDefault();r.save();}}>
          <label className="field">记录浏览的页面路径（每行一个）<textarea required disabled={disabled} placeholder={'/\n/contact/'} value={r.values.landing} onChange={e=>r.setValues({...r.values,landing:e.target.value})}/></label>
          <label className="field">允许提交询盘的页面路径（每行一个）<textarea required disabled={disabled} placeholder="/contact/" value={r.values.lead} onChange={e=>r.setValues({...r.values,lead:e.target.value})}/></label>
          <label className="field">实际网站表单<select aria-label="实际网站表单" required disabled={disabled} value={r.values.form} onChange={e=>r.setValues({...r.values,form:e.target.value})}><option value="">请选择本站表单</option>{s.forms.map(f=><option key={f.id} value={f.id}>{f.name} · {f.code}</option>)}</select></label>
          <p className="runtime-help">只接受本站精确路径，不填完整域名、查询参数或通配符。Lead 仅在询盘成功入库后发送；未同意营销时不发送。范围内的自然访问也会被记录。</p>
          <div className="runtime-actions"><button className="button" disabled={disabled||!r.dirty}>保存接入范围</button><span role="status">{r.dirty?'范围有未保存修改':'范围与已保存版本一致'}</span></div>
        </form>
      </>:<>
        {!s.integration?<p>{direct?<a href={`#/flow/${loop.id}?view=config&node=pixel`}>配置网站与表单，准备接入</a>:'先到 04 点击“准备实际接入”，再保存页面与表单范围。'}</p>:<p>服务器凭据：<a href={direct?s.integration.editUrl:`${s.integration.editUrl}?origin=guide&flow=${encodeURIComponent(loop.id)}`} target="_blank" rel="noopener">维护本数据集接入</a>（新标签页，仅在那里维护密钥）</p>}
        <div className="runtime-checks"><strong>应用前检查</strong>{s.blockingChecks.length?<ul>{s.blockingChecks.map(c=><li key={c.code}>{c.message}</li>)}</ul>:<p>内部条件满足；凭据存在不代表 Meta 已认可，应用后仍需实测。</p>}</div>
        <RuntimeDependencies dependencies={s.dependencies} canOperate={s.canOperate}/>
        <div className="runtime-checks"><strong>{unchanged?'当前范围 · 无待应用修改':`本次待应用范围${s.configState==='unapplied_changes'?'（有未应用修改）':''}`}</strong>
          <ScopeComparison state={s}/>
          <p>同一接入关联 {s.sharedFlows?.length||0} 个方案。{s.sharedFlows?.map(f=>f.name).join('、')}{s.integration&&!s.integration.enabled&&s.sharedFlows?.some(f=>f.state==='inactive')?'。应用将重新启用此共享接入，其他未暂停方案也可能恢复发送。':''}</p>
        </div>
        <p className="runtime-help">保留的旧 Pixel：{s.preservedPixelIds.join('、')||'无'}。{s.boundary}</p>
        {s.overlaps.length>0&&<p>范围重叠：{s.overlaps.map(x=>`${x.flowName}（${x.pixelId}）`).join('、')}。同一 Pixel 同一事件只发送一次。</p>}
        {history}{controls}
      </>}
    </>}
  </section>;
}

export function RuntimeDependencies({dependencies=[],canOperate,compact=false}) {
  if(!dependencies.length)return null;
  const needsAttention=dependencies.some(d=>d.issues.length||d.checks.some(c=>c.status==='attention'));
  const content=<>
    {!compact&&<p className="runtime-help">本地配置检查，不代表询盘入库或邮件送达。销售阶段事件由表单定义维护，不由方案中的人工约定启用。</p>}
    {dependencies.map(d=><section key={d.key} aria-label={d.label}><header><strong>{d.label} · {d.form?.name||'未选择'}</strong>{d.form&&<a href={d.form.editUrl} target="_blank" rel="noopener">维护表单<Icon name="external" size={14}/></a>}</header>
      {d.issues.map(issue=><p className="form-error" key={issue}>{issue}</p>)}
      <dl>{d.checks.map(c=><div key={c.label}><dt>{c.label}</dt><dd><span className={`dependency-state ${c.status}`}><Icon name={c.status==='ready'?'check':c.status==='attention'?'alert':'info'} size={15}/>{c.detail}</span></dd></div>)}</dl>
    </section>)}
    <p className="runtime-help">{compact&&<>本地检查不代表询盘入库或邮件送达。销售阶段事件由表单维护，不由人工约定启用。<br/></>}{canOperate?<a href="/admin/system/email/" target="_blank" rel="noopener">维护邮件服务</a>:'邮件服务由系统管理员维护。'} · 真实提交与投递结果请到核验记录确认。</p>
  </>;
  return compact?<div className="runtime-dependencies is-compact">{content}</div>:<details className="runtime-dependencies" open={needsAttention}>
    <summary>表单、CRM 与通知 <span>{needsAttention?'有配置需核对':'查看本地配置'}</span></summary>{content}
  </details>;
}

export {RuntimeEvidence} from './RuntimeEvidence.jsx';

export function ScopeComparison({state:s,target}) {
  const proposed=target||s.draft||{},current=s.activeRevision?.scope||{};
  const form=value=>s.forms.find(f=>String(f.id)===String(value))?.name||(value?`表单 ${value}（需核对可用性）`:'未选择');
  const text=(scope,key)=>key==='formId'?form(scope[key]):key==='consentRequired'?(scope[key]?'需要营销同意':'未配置'):(scope[key]||[]).join('、')||'未配置';
  return <table className="plan-scope-diff" aria-label="当前生效与待应用范围比较"><thead><tr><th>范围</th><th>当前生效</th><th>{target?'拟恢复版本':'待应用配置'}</th></tr></thead><tbody>{[['landingPaths','浏览页面'],['leadPaths','询盘页面'],['formId','询盘表单'],['consentRequired','访客同意']].map(([key,label])=><tr key={key}><th scope="row">{label}</th><td>{s.activeRevision?text(current,key):'尚未应用'}</td><td className={JSON.stringify(current[key])!==JSON.stringify(proposed[key])?'is-changed':''}>{text(proposed,key)}</td></tr>)}</tbody></table>;
}
