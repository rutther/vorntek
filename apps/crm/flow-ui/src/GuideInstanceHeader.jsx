import React from 'react';
import {Icon} from './ui.jsx';

export function GuideInstanceHeader({loop,loops,done,total,busy,navigate,onAction}) {
  const state=loop.runtime?.state;
  const runtimeText={applied:'运行版本已应用',paused:'方案已暂停',inactive:'已停用 · 保留版本',not_applied:'尚未应用',unavailable:'运行功能未启用'}[state]||'运行状态待读取';
  return <header className="guide-instance-header" aria-label="当前闭环实例">
    <div className="guide-instance-identity">
      <div className="breadcrumb">
        <select aria-label="切换流程" value={loop.id} disabled={busy} onChange={e=>navigate(`/flow/${e.target.value}?view=tasks`)}>
          {loops.filter(item=>!item.archived||item.id===loop.id).map(item=><option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
        <button type="button" className="icon-button" aria-label="重命名当前流程" disabled={busy||loop.archived} onClick={()=>onAction('rename',loop)}><Icon name="pencil" size={17}/></button>
      </div>
      <div className="guide-instance-meta">
        <span>Meta / 网站获客</span><span>·</span><span>{loop.environment==='test'?'演练测试':'正式业务'}</span><span>·</span><span>配置版本 {loop.revision}</span>
        {loop.note&&<details className="guide-instance-note"><summary>实例备注</summary><p>{loop.note}</p></details>}
      </div>
    </div>
    <div className="guide-instance-actions">
      <span className="guide-config-badge">配置已保存 {done}/{total}</span>
      <span className="guide-runtime-badge">{runtimeText}</span>
      <button type="button" className="button" disabled={busy} onClick={()=>navigate('/loops')}>切换闭环</button>
      <button type="button" className="text-button" disabled={busy} onClick={()=>onAction('create')}>新建闭环</button>
    </div>
    <div className="guide-instance-maintenance">
      <a href={`/admin/marketing/plans/#/flow/${loop.id}`}>维护获客方案</a>
      {loop.guideClosed?<span>引导已关闭</span>:<button type="button" className="text-button" disabled={busy||loop.archived} onClick={()=>onAction('close_guide',loop)}>关闭引导</button>}
      <small>关闭引导不会暂停方案</small>
    </div>
  </header>;
}
