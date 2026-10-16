import React from 'react';
import {GuideReferenceFigure} from './GuideReferenceFigure.jsx';
import {ServerCredentialGuide} from './ServerCredentialGuide.jsx';

// Presentation only: commands and confirmation remain owned by RuntimePanel.
export function GuideRuntimeView({runtime:r,loop,comparison,dependencies,history,controls,configuration}){
 const s=r.state;
 return <section className="runtime-panel guide-runtime-workbench" aria-label="实际运行管理">
  {r.error&&<p role="alert" className="form-error">{r.error} 当前状态尚未重新确认，请刷新检查后再操作。</p>}
  {r.notice&&<p role="status">{r.notice}</p>}
  {r.loading&&<p role="status">正在读取运行状态，暂不允许应用变更…</p>}
  {!s?<div className="guide-runtime-empty"><p>读取本实例的已保存范围、当前生效版本和应用条件。</p><button type="button" className="button" disabled={r.loading} onClick={r.read}>读取实际接入</button></div>:<>
   <div className="guide-runtime-identity">
    <span>当前运行 <strong>{s.activeRevision?<code title={s.activeRevision.key}>{s.activeRevision.key.slice(0,8)}</code>:'尚未应用'}</strong></span>
    <span>范围草稿版本 <strong>{s.version}</strong></span>
    <span>数据集 <strong>{loop.configs.dataset?.externalId||'尚未登记'}</strong></span>
    <span>{s.runtimeState==='paused'?'已暂停':s.runtimeState==='inactive'?'已停用，保留历史':s.runtimeState==='applied'?'运行版本已应用':'配置尚未应用'}</span>
   </div>
   <div className="guide-runtime-comparison">{comparison}</div>
  </>}
   <div className="guide-runtime-grid"><div>{s?<>
    <section className="guide-runtime-checks"><h4>应用前检查</h4>
     {s.blockingChecks.length?<ul>{s.blockingChecks.map(check=><li key={check.code}>{check.message}</li>)}</ul>:<p>内部条件满足；凭据存在不代表 Meta 已认可，应用后仍需实测。</p>}
     {dependencies}
    </section>
    <section className="guide-runtime-impact"><h4>共享接入与本次影响</h4><div>
     <p>同一接入关联 {s.sharedFlows?.length||0} 个方案。{s.sharedFlows?.map(flow=>flow.name).join('、')}</p>
     {s.integration&&!s.integration.enabled&&s.sharedFlows?.some(flow=>flow.state==='inactive')&&<p className="guide-runtime-warning">应用将重新启用此共享接入，其他未暂停方案也可能恢复发送。</p>}
     <p>保留的旧 Pixel：{s.preservedPixelIds.join('、')||'无'}。{s.boundary}</p>
     {s.overlaps.length>0&&<p>范围重叠：{s.overlaps.map(item=>`${item.flowName}（${item.pixelId}）`).join('、')}。同一 Pixel 同一事件只发送一次。</p>}
    </div></section>
   </>:<p>运行条件读取后显示检查结果；回传约定与凭据图示仍可查看。</p>}</div><div className="guide-runtime-side"><aside className="guide-runtime-credentials"><h4>凭据在哪里设置？</h4>
    <p>事件管理工具 → 本数据集 → 设置 → 直接集成</p>
    <GuideReferenceFigure src="/static/console/meta-help/meta-generate-access-token-20261007.svg" title="生成访问口令的位置"
     alt="Meta 设置页中直接集成与生成访问口令的红框位置，操作示意图。" caption="凭据入口示意图，不在这里显示或填写密钥。"/>
    {s?.integration?<a href={`${s.integration.editUrl}?origin=guide&flow=${encodeURIComponent(loop.id)}`} target="_blank" rel="noopener">打开本数据集接入管理 ↗</a>:s?<p>先到04点击“准备接入”，再保存页面与表单范围。</p>:<p>读取成功后显示本数据集的接入管理入口。</p>}
    <ServerCredentialGuide datasetId={loop.configs.dataset?.externalId} active/>
   </aside>{configuration}</div></div>
   {s&&<div className="guide-runtime-history">{history}<p>暂停不会撤回已发出的事件；恢复前同样需要比较范围与共享影响。应用不等于 Meta 已接收到事件。</p></div>}
   {controls}
 </section>;
}
