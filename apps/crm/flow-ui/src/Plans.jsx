import React, {useEffect,useRef,useState} from 'react';
import {Icon, dateText} from './ui.jsx';
import {Guidance} from './Guidance.jsx';
import {CrmRecords} from './CrmRecords.jsx';
import {useRuntime, RuntimePanel, RuntimeEvidence} from './RuntimePanel.jsx';
import './plans.css';

export const runtimeLabel = state => ({applied:'运行版本已应用', paused:'方案已暂停', inactive:'已停用 · 保留版本',
 not_applied:'尚未应用', unavailable:'运行功能未启用'})[state] || '状态待读取';

export function PlanList({loops,navigate,onAction,legacyLoops=[],guide=false,filters,setFilters,collectionState={},onRetry}) {
 const [localFilters,setLocalFilters]=useState({search:'',environment:'all',state:'active',page:1});
 const {search,environment,state,page}=filters||localFilters;
 const change=(key,value)=>(setFilters||setLocalFilters)(previous=>({...previous,[key]:value,...(key==='page'?{}:{page:1})}));
 const statusUnknown=collectionState.loading||collectionState.error;
 const filtered=loops.filter(l=>(state==='archived'?l.archived:!l.archived)
  &&(!guide||state==='archived'||(state==='closed'?l.guideClosed:!l.guideClosed))
  &&(environment==='all'||l.environment===environment)
  &&`${l.name} ${l.code} ${l.configs.dataset?.externalId||''}`.toLowerCase().includes(search.trim().toLowerCase()));
 const pages=Math.max(1,Math.ceil(filtered.length/20)),current=Math.min(page,pages);
 return <section className="plans-page">
  <header className="plan-heading"><div><h1>{guide?'引导式搭建':'获客方案'}</h1><p>{guide?'逐步搭建或核对已有方案；关闭引导不会停止方案。':'长期管理入口、表单与平台反馈；配置保存与运行应用分别管理。'}</p></div><button className="button primary" onClick={()=>onAction('create')}><Icon name="plus"/>{guide?'新建搭建任务':'新建方案'}</button></header>
  <div className="plan-list-panel"><div className="plan-list-toolbar"><label className="search-field"><Icon name="search"/><input aria-label="搜索方案名称、编号或数据集" placeholder="搜索名称、编号或数据集" value={search} onChange={e=>change('search',e.target.value)}/></label><select aria-label="方案环境" value={environment} onChange={e=>change('environment',e.target.value)}><option value="all">全部环境</option><option value="production">正式业务</option><option value="test">演练测试</option></select><select aria-label={guide?'引导状态':'方案归档状态'} value={state} onChange={e=>change('state',e.target.value)}><option value="active">{guide?'进行中的引导':'未归档方案'}</option>{guide&&<option value="closed">已关闭的引导</option>}<option value="archived">已归档方案</option></select></div>
   {statusUnknown&&<div className="plan-list-status" role={collectionState.error?'alert':'status'}><Icon name={collectionState.error?'alert':'refresh'} size={18}/><span>{collectionState.error||'正在刷新运行状态…'}</span>{collectionState.error&&<button className="button small" onClick={onRetry}>重新读取</button>}</div>}
   <div className="plan-table-wrap"><table className="plan-table"><thead><tr><th>方案名称</th><th>数据集</th><th>运行状态</th><th>引导</th><th>最近更新</th><th>操作</th></tr></thead><tbody>{filtered.slice((current-1)*20,current*20).map(loop=><tr key={loop.id}>
    <td><button className="plan-name" onClick={()=>navigate(`/flow/${loop.id}${guide?'?view=tasks':''}`)}>{loop.name}</button><small>Meta · 网站获客 · {loop.environment==='test'?'演练测试':'正式业务'}</small></td>
    <td data-label="数据集"><span>{loop.configs.dataset?.externalId||'未关联'}</span></td>
    <td data-label="运行状态"><span className={`plan-status is-${statusUnknown?'unknown':loop.runtime?.state||'unknown'}`}>{statusUnknown?(collectionState.loading?'读取中':'状态待读取'):loop.archived?'方案已归档':runtimeLabel(loop.runtime?.state)}</span></td>
    <td data-label="引导"><span>{loop.guideClosed?'引导已关闭':'引导进行中'}</span></td>
    <td data-label="最近更新"><time>{dateText(loop.updatedAt)}</time></td>
    <td><div className="plan-row-actions"><button className="text-button" onClick={()=>navigate(`/flow/${loop.id}${guide?'?view=tasks':''}`)}>{guide?'查看引导':'打开方案'}<Icon name="right" size={16}/></button>{guide?<button className="text-button" onClick={()=>onAction(loop.guideClosed?'reopen_guide':'close_guide',loop)}>{loop.guideClosed?'重新打开':'关闭引导'}</button>:<a href={`/admin/marketing/guided-loop/#/flow/${loop.id}?view=tasks`}>引导</a>}</div></td>
   </tr>)}</tbody></table></div>
   {!filtered.length&&!statusUnknown&&<div className="empty-state"><Icon name="flow" size={28}/><h2>{loops.length?'没有符合条件的记录':guide?'还没有搭建任务':'还没有获客方案'}</h2><p>{loops.length?'调整搜索或筛选即可查看其他记录。':'创建 Meta 网站获客草稿，随后选择数据集与网站范围。不会自动启用或发事件。'}</p>{!loops.length&&<button className="button primary" onClick={()=>onAction('create')}>{guide?'创建第一条流程':'创建第一个方案'}</button>}</div>}
   <footer className="plan-list-footer"><span>{filtered.length} 条记录</span><div><button className="button small" disabled={current===1} onClick={()=>change('page',current-1)}>上一页</button><span>{current} / {pages}</span><button className="button small" disabled={current===pages} onClick={()=>change('page',current+1)}>下一页</button></div></footer>
  </div>
  <p className="plan-footnote">{guide?'引导记录与方案使用同一份配置。':'已应用表示运行配置生效，不代表真实事件、匹配或去重已经验证。'}</p>
  {legacyLoops.length>0&&<details className="plan-legacy"><summary>旧版记录（{legacyLoops.length}）</summary><p>保留原配置和运行含义，不自动迁移。</p>{legacyLoops.map(l=><p key={l.id}><a href={l.url}>{l.name}</a></p>)}</details>}
 </section>;
}

export function PlanWorkspace({loop,view,node,update,navigate,guard,api,busy,onAction,canManage=false,onRuntimeSnapshot}) {
 const active=['config','runtime','verification'].includes(view)?view:'runtime';
 const runtime=useRuntime(loop,api,active==='runtime',onRuntimeSnapshot);
 const observationController=useRef(null),recordsGuard=useRef(null);
 useEffect(()=>{
  if(active!=='verification')return;
  const aggregate={
   get dirty(){return Boolean(observationController.current?.dirty||recordsGuard.current?.dirty);},
   get saving(){return Boolean(observationController.current?.saving);},
   get kind(){return observationController.current?.dirty?'observation':recordsGuard.current?.kind;},
   save:()=>observationController.current?.dirty?Promise.resolve(false):recordsGuard.current?.save()??Promise.resolve(true),
  };
  guard.current=aggregate;
  return()=>{if(guard.current===aggregate)guard.current=null;};
 },[active,guard]);
 return <section className="plans-page">
  <header className="plan-heading"><div><h1>{loop.name}</h1><p>Meta · 网站获客 <span className="plan-dot">·</span> {loop.environment==='test'?'演练测试':'正式业务'} <span className="plan-dot">·</span> {loop.code}</p></div><div className="plan-header-actions"><button className="button" disabled={busy||loop.archived} onClick={()=>onAction('rename',loop)}>重命名</button><a className="button" href={`/admin/marketing/guided-loop/#/flow/${loop.id}?view=tasks`}>{loop.guideClosed?'查看已关闭引导':'打开引导'}</a></div></header>
  {loop.archived&&<div className="plan-archive-note" role="status"><strong>方案已归档</strong><span>停止本方案的运行选择，配置与历史证据保留。恢复后需在运行管理中核对状态。</span></div>}
  <nav className="plan-tabs" aria-label="方案维护"><button aria-current={active==='config'?'page':undefined} onClick={()=>navigate(`/flow/${loop.id}?view=config`)}>方案配置</button><button aria-current={active==='runtime'?'page':undefined} onClick={()=>navigate(`/flow/${loop.id}?view=runtime`)}>运行与版本</button><button aria-current={active==='verification'?'page':undefined} onClick={()=>navigate(`/flow/${loop.id}?view=verification`)}>核验记录</button></nav>
  {active==='config'?<Guidance loop={loop} selectedKey={node} update={update} navigate={navigate} guard={guard} crm busy={busy} api={api} direct onRuntimeSnapshot={onRuntimeSnapshot}/>:active==='runtime'?<div className="plan-runtime"><RuntimePanel runtime={runtime} loop={loop} direct/><div className="plan-lifecycle"><div><strong>{loop.archived?'恢复方案':'归档方案'}</strong><p>{loop.archived?'恢复方案可能重新启用其已有运行范围；先核对共享接入及其他方案。':'归档会停止本方案的事件选择，保留配置与历史。它不同于关闭引导。'}</p></div><button className="button" disabled={busy||!canManage} onClick={()=>onAction(loop.archived?'restore':'archive',loop)}>{loop.archived?'恢复方案':'归档方案'}</button></div>{!canManage&&<p className="plan-footnote">运行启停与归档恢复由系统管理员操作。</p>}</div>:<><RuntimeEvidence loop={loop} api={api} controller={observationController} active direct/><CrmRecords loop={loop} update={update} selectedKey={node} busy={busy} guard={recordsGuard} direct/></>}
 </section>;
}

export function ClosedGuide({loop,onAction,busy}) {
 return <section className="plans-page"><div className="plan-closed"><Icon name="check" size={30}/><h1>此引导已关闭</h1><p>{loop.name}</p><p>方案及运行版本不受影响；关闭不表示配置或真实事件已验证。</p><div><button className="button primary" disabled={busy} onClick={()=>onAction('reopen_guide',loop)}>重新打开引导</button><a className="button" href={`/admin/marketing/plans/#/flow/${loop.id}`}>维护获客方案</a></div></div></section>;
}
