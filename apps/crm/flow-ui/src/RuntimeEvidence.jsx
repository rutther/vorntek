import React, {useEffect, useRef, useState} from 'react';
import {Icon} from './ui.jsx';
import {GuideIllustration} from './GuideIllustration.jsx';
import {GuideEvidenceWorkbench} from './GuideEvidenceWorkbench.jsx';
import './runtime-evidence.css';

const channels = [
  {key:'meta_browser',title:'浏览器接收',icon:'world',label:'Meta 显示浏览器接收',hint:'在 Meta「测试事件」找到本次 Lead（留下潜在客户信息），点右侧箭头展开，再展开来源为「浏览器」的记录。完整事件编号与本次询盘相同，才登记此项；不是看 PageView。',picture:'meta-lead-event-compare-redacted-20261009.svg',pictureTitle:'浏览器接收核对示例',pictureAlt:'打码示例：展开Lead，核对浏览器来源与完整事件编号',pictureCaption:'找到来源为「浏览器」的 Lead，展开后核对完整事件编号。截图是位置示例，不是当前询盘的验证结果。'},
  {key:'meta_server',title:'服务器接收',icon:'cloud',label:'Meta 显示服务器接收',hint:'展开同一条 Lead 下的「服务器」记录，核对完整事件编号与本次询盘相同，即可登记服务器接收。后台“接口接受”不能代替这项观察。',picture:'meta-server-event-expanded-redacted-20261009.svg',pictureTitle:'服务器记录展开示例',pictureAlt:'打码示例：浏览器和服务器两条 Lead 均已展开，红框指出服务器来源，详情显示事件编号位置',pictureCaption:'看红框「服务器」，再核对其下完整事件编号。图中编号已打码；请在你自己的 Meta 页面与本次询盘逐字比较。'},
  {key:'meta_dedup',title:'两路去重',icon:'check',label:'Meta 显示两路去重',hint:'先确认浏览器、服务器两路都是同一条 Lead，完整事件编号一致；再查看「已复制」旁的 ⓘ。若提示明确说明因重复而丢弃、避免多计事件，即可登记两路去重。这是去掉重复的一份，不是本次询盘发送失败。',picture:'meta-dedup-tooltip-20261009.svg',pictureTitle:'重复事件去重提示示例',pictureAlt:'Meta 信息提示：事件已发送但因重复被丢弃，相同事件编号会去重以避免多计事件总数',pictureCaption:'这段说明配合同名、同编号的浏览器与服务器记录，才是本次两路去重的观察依据。仅看到「已复制」三个字不要直接登记通过。'},
];
const statuses = {sent:'接口已接受',pending:'待投递',failed:'投递失败',skipped:'已跳过',sending:'发送中',processing:'处理中'};
const formatTime = value => value ? new Date(value).toLocaleString('zh-CN',{hour12:false}) : '时间未提供';

export function RuntimeEvidence(props) {
  return <EvidenceSession key={props.loop.id} {...props}/>;
}

function EvidenceSession({loop,api,active,direct=false,onReturn,controller}) {
  const [data,setData]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(active?'read':'');
  const [canOperate,setCanOperate]=useState(false),[eventId,setEventId]=useState(''),[source,setSource]=useState('');
  const [matched,setMatched]=useState(false),[notice,setNotice]=useState(''),[readFailed,setReadFailed]=useState(false);
  const [decision,setDecision]=useState('');
  const editState=useRef(null);
  const lock=useRef(false), alive=useRef(true), revision=useRef(null);
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
  const accept=result=>{
    if(!alive.current)return;
    const next=result.evidence;
    if(revision.current && revision.current!==next?.revisionKey){setEventId('');setSource('');setMatched(false);setDecision('');setNotice('运行版本已变化，请重新选择当前事件核对。');}
    revision.current=next?.revisionKey;setData(next);setCanOperate(Boolean(result.canOperate));
    setEventId(id=>next?.recentEvents?.some(e=>e.eventId===id)?id:'');setReadFailed(false);
  };
  const read=async()=>{
    if(!api?.runtimeLoad||lock.current)return;
    lock.current=true;setBusy('read');setError('');
    try{accept(await api.runtimeLoad(loop.id,true));if(alive.current)setMatched(false);}catch(e){if(alive.current){setError(e.message);setReadFailed(true);}}
    finally{lock.current=false;if(alive.current)setBusy('');}
  };
  const observations=(data?.observations||[]).filter(o=>o.eventId===eventId&&o.currentCredential);
  const recorded=key=>observations.some(o=>o.source===key);
  const validEvent=data?.recentEvents?.some(e=>e.eventId===eventId);
  const current=channels.find(c=>c.key===source);
  const next=channels.find(c=>!recorded(c.key));
  const canSave=canOperate&&validEvent&&source&&matched&&!busy&&!readFailed&&!recorded(source);
  const dirty=busy==='save'||Boolean(validEvent&&source&&!recorded(source)&&(decision==='observed'||matched));
  editState.current={dirty,saving:busy==='save'};
  useEffect(()=>{
    if(!controller)return;
    const state={get dirty(){return editState.current.dirty;},get saving(){return editState.current.saving;}};
    controller.current=state;
    return()=>{if(controller.current===state)controller.current=null;};
  },[controller]);
  // Returning to a still-mounted step must not reset an unsaved observation.
  useEffect(()=>{if(active&&!editState.current.dirty)read();},[active]);
  const record=async e=>{
    e.preventDefault();if(lock.current||!canSave)return;
    lock.current=true;setBusy('save');setError('');setNotice('');
    try{accept(await api.runtimeCommand(loop.id,{eventId,source,revisionKey:data.revisionKey},true));if(alive.current)setNotice(`${current?.title||'浏览器请求'}观察已保存。这是人工登记，不是系统自动验收。`);}
    catch(e){if(alive.current)setError(e.message);}
    finally{lock.current=false;if(alive.current)setBusy('');}
  };
  const changeEvent=value=>{setEventId(value);setSource('');setMatched(false);setDecision('');setNotice('');};
  const changeSource=value=>{setSource(value);setMatched(false);setDecision('');setNotice('');};
  if(!direct)return <GuideEvidenceWorkbench loop={loop} channels={channels} statuses={statuses} formatTime={formatTime}
    data={data} error={error} busy={busy} readFailed={readFailed} canOperate={canOperate}
    eventId={eventId} source={source} matched={matched} decision={decision} setDecision={setDecision} notice={notice} validEvent={validEvent}
    recorded={recorded} canSave={canSave} next={next} onRead={read} onRecord={record} onEvent={changeEvent} onReturn={onReturn}
    onSource={changeSource} onMatched={setMatched}/>;
  return <section className="runtime-panel evidence-panel" aria-label="实际运行证据" aria-busy={!!busy}>
    <header><div><h4>核对本次询盘</h4><p className="evidence-subtitle">先看系统记录，再到 Meta 核对；每项观察分别保存。</p></div><button type="button" className="button" disabled={!!busy} onClick={read}>{busy==='read'?'刷新中…':'刷新证据'}</button></header>
    {error&&<p role="alert" className="form-error">{error}</p>}
    {readFailed&&data&&<p className="evidence-warning">以下为上次读取结果，暂不允许登记。请先点击「刷新证据」。</p>}
    {!data?<div className="evidence-empty"><Icon name="info" size={22}/><p>{busy?'正在读取当前运行版本的事件记录…':error?'证据暂未读取成功。请刷新证据，无需重复提交询盘。':direct?'还没有活动运行版本。先到「运行与版本」核对配置。':'还没有活动运行版本。先完成网站接入并在第07步应用。'}</p></div>:<>
      <div className="evidence-context"><span>数据集 <strong>{loop.configs.dataset?.externalId||'请核对接入'}</strong></span><span>运行版本 <code>{data.revisionKey?.slice(0,8)}</code></span></div>
      <dl className="evidence-metrics">{Object.entries({询盘已入库:data.inquiries,回传接口接受:data.serverAccepted,待投递:data.pending,投递失败:data.failed,已跳过:data.skipped}).map(([key,value])=><div key={key}><dt>{key}</dt><dd>{value??0}</dd></div>)}</dl>
      <p className="evidence-caption">这里是本站记录。“接口接受”不等于 Meta 两路去重通过。</p>
      {(data.failed>0||data.pending>0||data.skipped>0)&&<p className="evidence-warning">有事件尚未确认投递成功。到 <a href="/admin/marketing/events/" target="_blank" rel="noopener">事件运营</a> 查看原因；不要反复提交表单。</p>}
      {!data.recentEvents?.length?<div className="evidence-empty"><Icon name="tasks" size={22}/><div><strong>当前版本还没有可核对的询盘事件</strong><p>如果已经提交，先刷新并检查 CRM 记录，不要重复提交。尚未测试时，按上方指引提交一次获准的测试询盘。</p></div></div>:<>
        <div className="evidence-workbench">
          <div className="evidence-compare">
            <h5><span className="evidence-number">1</span>选中本次询盘</h5>
            <label className="field">核对的事件编号 <span className="evidence-required">必选</span><select aria-label="核对的事件编号" value={eventId} disabled={!!busy} onChange={e=>changeEvent(e.target.value)}><option value="">选择本次核对的事件</option>{data.recentEvents.map(e=><option key={e.eventId} value={e.eventId}>{e.eventId} · {formatTime(e.at)}</option>)}</select></label>
            {validEvent?<div className="evidence-id"><span>与 Meta 对照完整编号</span><code>{eventId}</code><small>{statuses[data.recentEvents.find(e=>e.eventId===eventId)?.status]||'待核对'}</small></div>:<p className="evidence-caption">按提交时间选择，不要只比较编号的前几位。</p>}
            <h5><span className="evidence-number">2</span>到 Meta 展开事件</h5>
            <ol className="evidence-instructions"><li>打开事件管理工具 → 同一数据集 → <strong>测试事件</strong>，保持该页打开。</li><li>找到本次提交的 <strong>Lead / 留下潜在客户信息</strong>，点击右侧箭头展开；不是 PageView。</li><li>再展开「浏览器」或「服务器」记录，核对<strong>完整事件编号、来源和接收时间</strong>。两路时间可以略有差异。</li></ol>
            <div className="evidence-picture-label"><strong>展开后看哪里？</strong><GuideIllustration active={active} src="/static/console/meta-help/meta-lead-event-compare-redacted-20261009.svg" title="核对 Lead 编号与来源" alt="打码示例：展开 Lead，来源列区分浏览器与服务器，详情中显示完整事件编号位置" caption="① 先看事件名称；② 对照来源列；③ 在展开详情里核对完整编号。图中打码内容不是要填写的值。"/></div>
            <p className="evidence-caption">图中服务器的「已复制」不是单靠文字就能判定通过：查看旁边 ⓘ 的说明，确认是否为去重结果。</p>
          </div>
          <form className="evidence-register" onSubmit={record}>
            <h5><span className="evidence-number">3</span>登记实际看到的结果</h5>
            <p className="evidence-caption">先按下面三项说明到 Meta 核对；每项右侧「图示」可直接打开，不用先选事件或结果。确认后，在下方选择对应项并保存。</p>
            {!validEvent&&<p className="evidence-caption">尚未选择本次事件，暂不显示登记状态；不代表已有登记丢失。先看图示，再在第1区选择事件查看记录。</p>}
            <ul className="evidence-checklist" aria-label="本事件人工观察进度">{channels.map(c=><li key={c.key}>
              <div className="evidence-check-header"><span><Icon name={recorded(c.key)?'check':c.icon} size={17}/>{c.title}</span><strong className={recorded(c.key)?'is-recorded':''}>{!validEvent?'选事件后显示':recorded(c.key)?'已登记':'待核对'}</strong><GuideIllustration active={active} src={`/static/console/meta-help/${c.picture}`} title={c.pictureTitle} alt={c.pictureAlt} caption={c.pictureCaption}/></div>
              <p className="evidence-check-instruction">{c.hint}</p>
            </li>)}</ul>
            <label className="field">实际观察结果 <span className="evidence-required">必选</span><select aria-label="实际观察结果" value={source} disabled={!validEvent||!!busy||!canOperate} onChange={e=>{setSource(e.target.value);setMatched(false);setNotice('');}}><option value="">按 Meta 画面选择，不确定先不登记</option>{channels.map(c=><option key={c.key} value={c.key}>{c.label}</option>)}<option value="browser_call">仅观察到浏览器发出请求</option></select></label>
            <div className="evidence-selection-help">{current?<p>本次只登记「{current.title}」。请先按上方该项说明核对，再勾选确认；其他项不会自动登记。</p>:source==='browser_call'?<p>只看到浏览器网络请求、未在 Meta 看到接收记录时选此项。它不能算 Meta 已收到。</p>:<p>先登记已确认的一项；不要因为后台显示“接口接受”就选择 Meta 服务器接收或去重。</p>}</div>
            <label className="evidence-confirm"><input type="checkbox" name="eventMatched" checked={matched} disabled={!source||!validEvent||!!busy||!canOperate} onChange={e=>setMatched(e.target.checked)}/><span>{source==='browser_call'?'我已核对请求中的完整事件编号，但尚未确认 Meta 接收。':'我已按上述说明核对完整事件编号，确认所选结果来自这条 Lead。'}</span></label>
            {!canOperate&&<p className="evidence-warning">当前账号只可查看。请系统管理员登记观察结果。</p>}
            <div className="evidence-save"><small>{recorded(source)?'本项已保存，无需重复登记。':!validEvent?'先在「核对的事件编号」中选择本次询盘。':!source?'选择你在 Meta 实际看到的结果。':!matched?'核对完成后勾选确认，再保存。':'仅保存本项观察，不改变接入或发送事件。'}</small><button className="button primary" disabled={!canSave}>{busy==='save'?'保存中…':recorded(source)?'本项已登记':'保存本项观察'}</button></div>
            {notice&&<p role="status" className="evidence-saved"><Icon name="check" size={17}/>{notice}</p>}
          </form>
        </div>
        <div className="evidence-next"><Icon name="right" size={20}/><div><strong>{!validEvent?'现在先选择本次事件':next?`下一步：核对${next.title}`:'本事件三项观察已登记，继续核对其余测试项'}</strong><p>{!validEvent?'不用重新提交询盘；从上面的事件列表选择刚才那一条。':next?next.hint:'还需确认网站提交反馈、CRM 单条接收、同意控制及测试收尾。人工登记齐全不代表整个闭环自动通过。'}</p>{validEvent&&!next&&<a href={`#/flow/${loop.id}?view=verification`}>继续填写实操记录 <Icon name="right" size={15}/></a>}</div></div>
      </>}
      <details className="evidence-history"><summary>查看已登记记录与常见问题</summary>
        {data.observations?.length?<ul>{data.observations.map((o,i)=><li key={`${o.at}-${i}`}><strong>{channels.find(c=>c.key===o.source)?.title||'浏览器发出请求'}</strong> · 人工登记 · {formatTime(o.at)}<br/><code>{o.eventId}</code>{!o.currentCredential&&<span> · 接入配置已变化，需重新核对</span>}</li>)}</ul>:<p>尚未保存人工观察。Meta 中已经收到的事件不会因此消失。</p>}
        <p><strong>“等待动态”或“在 Payload 添加测试码”</strong>不代表已有记录失败。服务器测试码由接入人员配置，不填入询盘表单或 Access Token。</p>
        <p><strong>没有浏览器事件？</strong>如果开发者工具显示 fbevents.js / ERR_BLOCKED_BY_CLIENT，检查浏览器扩展或本机拦截。只在获准测试范围临时放行，测试后恢复；不要关闭所有防护。</p>
        <p>{data.scope}。{data.sampleLimited?'更早记录未纳入统计。':''}此处不展示客户资料。截图只作位置示例，不是当前事件的验收证据。</p>
      </details>
    </>}
  </section>;
}
