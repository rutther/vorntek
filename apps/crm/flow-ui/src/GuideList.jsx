import React,{useState} from 'react';
import {Icon,Note,dateText} from './ui.jsx';
import {DEFS,tasks} from './model.mjs';
import {guideNumber,LOOP_TEST_KEY} from './guidance.mjs';

const runtimeText=state=>({applied:'运行版本已应用',paused:'方案已暂停',inactive:'已停用 · 保留版本',not_applied:'尚未应用',unavailable:'运行功能未启用'})[state]||'状态待读取';
export const GUIDE_TYPES=[
 {id:'meta-website',name:'Meta 网站获客',status:'可用',description:'广告 → 网站表单 → CRM → Pixel / CAPI',available:true},
 {id:'meta-form',name:'Meta 即时表单',status:'待开放',description:'平台内表单 → CRM → 后续反馈'},
 {id:'whatsapp',name:'WhatsApp 点击',status:'暂停',description:'广告 → 对话入口 → 客户跟进'},
 {id:'other',name:'其他广告平台',status:'待开放',description:'Google Ads / TikTok Ads'},
];
export function GuideTypeCards(){return <div className="guide-type-grid" aria-label="闭环类型">{GUIDE_TYPES.map(type=><article key={type.id} className={`guide-type-card ${type.available?'available':''}`}><div><strong>{type.name}</strong><span>{type.status}</span></div><p>{type.description}</p></article>)}</div>;}
export function guideNext(loop){
 const order=tasks(loop),next=order.find(key=>loop.drafts?.[key]||!loop.configs[key]);
 return {done:order.filter(key=>loop.configs[key]).length,total:order.length,hasPendingConfig:Boolean(next),key:next||LOOP_TEST_KEY,title:next?DEFS[next].title:'测试与核对',hint:next?`${loop.drafts?.[next]?'草稿已暂存 · ':''}从第 ${guideNumber(loop,next)} 步继续`:'配置已保存，实际结果需核对'};
}
export function GuideList({loops,navigate,onAction,legacyLoops=[],filters,setFilters,collectionState={},onRetry}){
 const [local,setLocal]=useState({search:'',environment:'all',state:'active',page:1,type:'all'});
 const f=filters||local;const change=(key,value)=>(setFilters||setLocal)(previous=>({...previous,[key]:value,...(key==='page'?{}:{page:1})}));
 const [typesOpen,setTypesOpen]=useState(false);
 const unknown=collectionState.loading||collectionState.error;
 const filtered=loops.filter(loop=>(f.state==='archived'?loop.archived:!loop.archived&&(f.state==='closed'?loop.guideClosed:!loop.guideClosed))&&(f.environment==='all'||loop.environment===f.environment)&&(!f.type||f.type==='all'||`${loop.provider}-${loop.preset}`===f.type)&&`${loop.name} ${loop.code} ${loop.configs.dataset?.externalId||''}`.toLowerCase().includes(f.search.trim().toLowerCase()));
 const pages=Math.max(1,Math.ceil(filtered.length/20)),page=Math.min(f.page,pages);
 return <section className="guide-list-page">
  <header className="guide-page-heading"><div><h1>引导式闭环</h1><p>按步骤实施单个闭环实例，并保留运行与核对记录。</p></div><button className="button primary" onClick={()=>onAction('create')}><Icon name="plus" size={17}/>新建闭环</button></header>
  <div className="guide-list-tabs"><nav aria-label="闭环状态">{[['active','进行中的闭环'],['closed','已关闭引导'],['archived','已归档方案']].map(([value,label])=><button key={value} data-state={value} aria-current={f.state===value?'page':undefined} onClick={()=>change('state',value)}>{label}{value==='active'&&<small>{loops.filter(l=>!l.archived&&!l.guideClosed).length}</small>}</button>)}</nav><button className="text-button" aria-expanded={typesOpen} onClick={()=>setTypesOpen(!typesOpen)}>查看闭环类型</button></div>
  {typesOpen&&<div className="guide-list-types"><GuideTypeCards/><p>同一种类型可以创建多个独立实例。当前仅开放 Meta 网站获客创建。</p></div>}
  <div className="guide-list-filters"><label className="search-field"><input aria-label="搜索方案名称、编号或数据集" placeholder="搜索闭环名称、编号或数据集" value={f.search} onChange={e=>change('search',e.target.value)}/></label><select aria-label="闭环类型" value={f.type||'all'} onChange={e=>change('type',e.target.value)}><option value="all">全部类型</option><option value="meta-website">Meta 网站获客</option></select><select aria-label="方案环境" value={f.environment} onChange={e=>change('environment',e.target.value)}><option value="all">全部环境</option><option value="production">正式业务</option><option value="test">演练测试</option></select><span>{filtered.length} 个实例</span></div>
  {unknown&&<div className="plan-list-status" role={collectionState.error?'alert':'status'}><Icon name={collectionState.error?'alert':'refresh'} size={18}/><span>{collectionState.error||'正在刷新运行状态…'}</span>{collectionState.error&&<button className="button small" onClick={onRetry}>重新读取</button>}</div>}
  <div className="guide-list-table-wrap"><table className="guide-list-table"><thead><tr><th>闭环 / 类型</th><th>关联数据集</th><th>配置 / 运行</th><th>下一步</th><th>操作</th></tr></thead><tbody>{filtered.slice((page-1)*20,page*20).map(loop=>{const next=guideNext(loop),url=loop.archived?`/flow/${loop.id}?view=verification`:`/flow/${loop.id}?view=tasks&node=${next.key}`;return <tr key={loop.id} data-flow={loop.id}>
   <td><button className="guide-list-name" onClick={()=>navigate(url)}>{loop.name}</button><small>Meta 网站获客 · {loop.environment==='test'?'演练测试':'正式业务'}</small></td>
   <td data-label="关联数据集"><span className="guide-dataset-id">{loop.configs.dataset?.externalId||'尚未关联'}</span><small>独立配置与核对记录</small></td>
   <td data-label="配置 / 运行"><span className={`guide-list-count ${next.done===next.total?'all-saved':''}`}>{next.done?`配置 ${next.done}/${next.total}`:'草稿'}</span><small>{unknown?(collectionState.loading?'读取中':'状态待读取'):loop.archived?'方案已归档':runtimeText(loop.runtime?.state)}</small></td>
   <td data-label="下一步"><span>{loop.archived?'查看归档记录':loop.guideClosed?'引导已关闭':next.title}</span><small>{loop.archived||loop.guideClosed?`更新于 ${dateText(loop.updatedAt)}`:next.hint}</small></td>
   <td><div className="guide-list-actions"><button className="button small" data-manage onClick={()=>navigate(`/flow/${loop.id}?view=design`)}>管理</button><button className="button small primary" data-continue onClick={()=>navigate(url)}>{loop.archived?'查看记录':loop.guideClosed?'查看引导':!next.hasPendingConfig?'继续核对':next.done?'继续配置':'打开引导'}</button></div></td>
  </tr>;})}</tbody></table>
  {!filtered.length&&!unknown&&<div className="empty-state"><Icon name="flow" size={28}/><h2>{loops.length?'没有符合条件的记录':'还没有闭环实例'}</h2><p>{loops.length?'调整搜索或筛选即可查看其他记录。':'先选择获客路径，再创建独立实例；不会自动启用事件或发布广告。'}</p>{!loops.length&&<button className="button primary" onClick={()=>onAction('create')}>创建第一条流程</button>}</div>}</div>
  <footer className="guide-list-footer"><span>每条闭环独立保存配置、运行版本与核对记录。</span><div><span>第 {page} 页 · {filtered.length} 条</span><button aria-label="上一页" disabled={page===1} onClick={()=>change('page',page-1)}>‹</button><button aria-label="下一页" disabled={page===pages} onClick={()=>change('page',page+1)}>›</button></div></footer>
  <Note>同一种闭环可以创建多个实例。关闭引导只结束搭建指引，不会停止正在运行的方案。</Note>
  <section className="guide-list-start"><h2>从哪里开始？</h2><dl><div><dt>已有方案，需要继续</dt><dd>从列表进入，不用重新创建</dd></div><div><dt>想了解系统如何连接</dt><dd>进入实例 → 流程全览</dd></div><div><dt>新的产品或独立范围</dt><dd>新建一个实例，避免覆盖已有配置</dd></div><div><dt>只想处理投递异常</dt><dd><a href="/admin/marketing/events/">到事件运营查看具体记录</a></dd></div></dl></section>
  {legacyLoops.length>0&&<details className="plan-legacy"><summary>旧版记录（{legacyLoops.length}）</summary><p>保留原配置和运行含义，不自动迁移。</p>{legacyLoops.map(l=><p key={l.id}><a href={l.url}>{l.name}</a></p>)}</details>}
 </section>;
}
