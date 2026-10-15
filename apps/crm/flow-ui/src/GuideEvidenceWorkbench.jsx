import React,{useId} from 'react';
import {GuideReferenceFigure} from './GuideReferenceFigure.jsx';
import './guide-evidence.css';

// View only. Selection, persistence, permissions and version guards remain in EvidenceSession.
export function GuideEvidenceWorkbench({loop,channels,statuses,formatTime,data,error,busy,readFailed,canOperate,
  eventId,source,matched,decision,setDecision,notice,validEvent,recorded,canSave,next,onRead,onRecord,onEvent,onSource,onMatched,onReturn}) {
  const current=channels.find(c=>c.key===source);
  const formId=useId();
  const events=data?.recentEvents||[];
  const picture=c=><GuideReferenceFigure src={`/static/console/meta-help/${c.picture}`} alt={c.pictureAlt} caption={c.key==='meta_dedup'?'Meta 重复事件说明示意图':'操作示意图 · 不是当前事件证据'}/>;
  const disabled=!validEvent||!!busy||!canOperate||readFailed;
  return <section className="runtime-panel evidence-panel guide-evidence" aria-label="实际运行证据" aria-busy={!!busy}>
    <header><div><h4>核对本次询盘的两路去重</h4><p className="evidence-subtitle">先对照完整事件编号，再查看 Meta 的重复事件解释。每项观察分别保存。</p></div>
      <button type="button" className="button" disabled={!!busy} onClick={onRead}>{busy==='read'?'刷新中…':'刷新证据'}</button></header>
    {error&&<p role="alert" className="form-error">{error}</p>}
    {readFailed&&data&&<p className="evidence-warning">以下为上次读取结果，暂不允许登记。请先刷新证据，无需重复提交询盘。</p>}
    <div className="evidence-context"><span>数据集 <strong>{loop.configs.dataset?.externalId||'尚未登记'}</strong></span>{data?.revisionKey&&<span>运行版本 <code>{data.revisionKey.slice(0,8)}</code></span>}</div>
    {data?<><dl className="evidence-metrics">{Object.entries({询盘已入库:data.inquiries,回传接口接受:data.serverAccepted,待投递:data.pending,投递失败:data.failed,已跳过:data.skipped}).map(([key,value])=><div key={key}><dt>{key}</dt><dd>{value??'—'}</dd></div>)}</dl>
      <p className="evidence-caption">本站统计，不代替 Meta 核验。“接口接受”不等于浏览器、服务器接收或两路去重通过。</p>
      {(data.failed>0||data.pending>0||data.skipped>0)&&<p className="evidence-warning">有事件尚未确认投递成功。到 <a href="/admin/marketing/events/" target="_blank" rel="noopener">事件运营</a> 查看原因，不要反复提交表单。</p>}</>:
      <p className="evidence-empty">{busy?'正在读取当前运行版本的事件记录…':error?'证据暂未读取成功。可先阅读下方核对说明，刷新成功后再登记。':'还没有活动运行版本。先完成网站接入并在第07步应用；下方图示可提前阅读。'}</p>}
    <div className="evidence-workbench guide-evidence-workbench">
      <aside className="guide-evidence-events">
        {events.length>0?<label className="field">本次核对事件<select aria-label="核对的事件编号" value={eventId} disabled={!!busy||readFailed} onChange={e=>onEvent(e.target.value)}>
          <option value="">选择本次核对的事件</option>{events.map(e=><option key={e.eventId} value={e.eventId}>{e.eventId} · {formatTime(e.at)}</option>)}</select></label>:<h5>暂无可核对事件</h5>}
        {validEvent?<div className="evidence-id"><span>完整事件编号</span><code>{eventId}</code><small>{statuses[events.find(e=>e.eventId===eventId)?.status]||'待核对'}</small></div>:
          <p className="evidence-caption">{events.length?'按提交时间选择，核对完整编号。未选事件不代表登记丢失。':'如果已经提交，先刷新并检查 CRM 记录，不要重复提交。'}</p>}
        <ul className="guide-evidence-channels" aria-label="本事件人工观察进度">{channels.map(c=><li key={c.key}>
          <button type="button" data-observation={c.key} aria-pressed={source===c.key} disabled={disabled} onClick={()=>onSource(c.key)}>
            <strong>{c.title}</strong><span className={recorded(c.key)?'is-recorded':''}>{!validEvent?'选事件后显示':recorded(c.key)?'人工观察已登记':'待核对'}</span>
          </button>
        </li>)}</ul>
        <button type="button" className="text-button evidence-request-only" disabled={disabled} aria-pressed={source==='browser_call'} onClick={()=>onSource('browser_call')}>仅记录浏览器发出请求</button>
        <p className="evidence-caption">只核对选中的这一条询盘，不用再提交一次。</p>
        {validEvent&&<div className="evidence-next"><strong>{next?`${next.key===source?'当前':'下一步'}：核对${next.title}`:'三项观察已登记'}</strong>{!next&&<><p>还需核对网站反馈、CRM 单条接收及同意控制；登记齐全不等于整个闭环通过。</p><a href={`#/flow/${loop.id}?view=verification`}>继续填写实操记录 →</a></>}</div>}
      </aside>
      <section className="guide-evidence-reference" aria-label="Meta 核对图示">
        <h5>在 Meta 展开后看哪里？</h5>
        <ol className="evidence-instructions"><li>同一数据集 → 测试事件；两路都是本次 Lead，不是 PageView。</li><li>展开浏览器和服务器，核对来源及完整事件编号一致。</li><li>点「已复制」旁 ⓘ，查看重复事件说明。</li></ol>
        {picture(channels[1])}
        <div className="guide-evidence-browser-reference"><h6>浏览器参考</h6>{picture(channels[0])}</div>
      </section>
      <section className="guide-evidence-observation" aria-label="登记核对结果">
        <h5>出现这段说明，代表什么？</h5>{picture(channels[2])}
        <p className="evidence-check-instruction">两路 Lead 编号一致，且说明明确表示“因重复而丢弃、避免多计”，才可登记去重。这不是询盘发送失败，不能只凭“已复制”判断。</p>
        {data&&events.length>0?<form id={formId} className="evidence-register" onSubmit={event=>{if(decision!=='observed'){event.preventDefault();return;}onRecord(event);}}>
          <h5>登记实际看到的结果</h5>
          <fieldset className="guide-observation-decision" disabled={disabled||!source}>
            <legend>{current?`本次登记：${current.title}`:source==='browser_call'?'仅记录请求，不证明 Meta 接收':'先选择「浏览器接收」「服务器接收」或「两路去重」'}</legend>
            <label><input type="radio" name="observationDecision" value="observed" checked={decision==='observed'} onChange={()=>setDecision('observed')}/><span>{source==='meta_dedup'?'已核对两路编号，并看到去重说明':source==='browser_call'?'只看到本次浏览器请求，尚未确认接收':`已核对完整编号，并看到${current?.title||'对应结果'}`}</span></label>
            <label><input type="radio" name="observationDecision" value="pending" checked={decision==='pending'} onChange={()=>{setDecision('pending');onMatched(false);}}/><span>还不确定，保留待核对</span></label>
          </fieldset>
          {decision==='pending'&&<p className="evidence-caption">不会登记为已接收或已去重。需要保存问题时，<a className="evidence-pending-link" href={`#/flow/${loop.id}?view=verification`}>到实操记录选择“待继续操作”</a>。</p>}
          <label className="evidence-confirm"><input type="checkbox" name="eventMatched" checked={matched} disabled={disabled||!source||decision!=='observed'} onChange={e=>onMatched(e.target.checked)}/><span>{source==='browser_call'?'我已核对请求中的完整事件编号，但尚未确认 Meta 接收。':'我核对的是本次 Lead，不是 PageView 或接口成功提示。'}</span></label>
          {!canOperate&&<p className="evidence-warning">当前账号只可查看，请系统管理员登记。</p>}
        </form>:<p className="evidence-warning">有当前版本的询盘事件后才能登记。不确定时保持待核对，不必勉强通过。</p>}
      </section>
    </div>
    <details className="evidence-history"><summary>查看已登记记录与常见问题</summary>
      {data?.observations?.length?<ul>{data.observations.map((o,i)=><li key={`${o.at}-${i}`}><strong>{channels.find(c=>c.key===o.source)?.title||'浏览器发出请求'}</strong> · 人工登记 · {formatTime(o.at)}<br/><code>{o.eventId}</code>{!o.currentCredential&&<span> · 接入配置已变化，需重新核对</span>}</li>)}</ul>:<p>{data?'尚未保存人工观察。Meta 中已经收到的事件不会因此消失。':'尚未读取当前运行记录，不表示历史登记丢失。'}</p>}
      <p><strong>“等待动态”或“在 Payload 添加测试码”</strong>不代表已有记录失败。服务器测试码由接入人员配置，不填入询盘表单或 Access Token。</p>
      <p><strong>没有浏览器事件？</strong>如出现 fbevents.js / ERR_BLOCKED_BY_CLIENT，检查浏览器扩展或本机拦截。仅在获准测试范围临时放行，测试后恢复。</p>
      <p>{data?.scope}{data?.sampleLimited?' 更早记录未纳入统计。':''} 教学截图不是当前事件的验收证据；此处不展示客户资料。</p>
    </details>
    <footer className="guide-evidence-actions">
      <div><small>{!data||!events.length?'有当前版本的询盘事件后才能登记。':recorded(source)?'本项已保存，无需重复登记。':!validEvent?'先从「本次核对事件」中选择本次询盘。':!source?'先选择「浏览器接收」「服务器接收」或「两路去重」。':decision==='pending'?'保留待核对，不会登记为已接收或已去重。':decision!=='observed'?'请选择已核对或保留待核对。':!matched?'核对完成后勾选确认，再保存。':'只保存人工观察，不自动判定整个闭环通过。'}</small>
        {notice&&<p role="status" className="evidence-saved">{notice}</p>}</div>
      <div className="guide-evidence-action-buttons">
        {onReturn&&<button type="button" className="button" onClick={onReturn}>返回测试步骤</button>}
        {data&&events.length>0&&<button type="submit" form={formId} className="button primary" disabled={!canSave||decision!=='observed'}>{busy==='save'?'保存中…':recorded(source)?'本项已登记':'保存本项观察'}</button>}
      </div>
    </footer>
  </section>;
}
