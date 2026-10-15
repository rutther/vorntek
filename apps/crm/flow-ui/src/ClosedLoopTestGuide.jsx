import React, {useEffect,useRef,useState} from "react";
import { Icon } from "./ui.jsx";
import { SettingRow } from "./GuideSettingRow.jsx";
import { GuideIllustration } from "./GuideIllustration.jsx";
import { GuideTestPreparation } from "./GuideTestPreparation.jsx";
import { DEFS, tasks } from "./model.mjs";
import { guideNumber, LOOP_TEST_KEY, META_ADSET_KEY } from "./guidance.mjs";

export function ClosedLoopTestGuide({ loop, open, onToggle, onReveal, onRecord, children, separateEvidence = false }) {
  const evidence = useRef(null);
  const section = useRef(null);
  const returnRequested = useRef(false);
  const [evidenceOnly,setEvidenceOnly]=useState(false);
  useEffect(()=>setEvidenceOnly(false),[loop.id]);
  const focusEvidence=()=>{
    evidence.current?.scrollIntoView({block:"start",behavior:"instant"});
    const selector=evidence.current?.querySelector('select[aria-label="核对的事件编号"]:not(:disabled)');
    (selector||evidence.current)?.focus({preventScroll:true});
  };
  useEffect(()=>{
    if(evidenceOnly)focusEvidence();
    else if(returnRequested.current){
      returnRequested.current=false;
      const heading=section.current?.querySelector('.guide-step-toggle');
      heading?.focus({preventScroll:true});
      heading?.scrollIntoView({block:'start',behavior:'instant'});
    }
  },[evidenceOnly]);
  const showEvidence = () => {
    if (!children) { onRecord("server"); return; }
    if(evidenceOnly)focusEvidence();
    else setEvidenceOnly(true);
  };
  const returnToSteps=()=>{returnRequested.current=true;setEvidenceOnly(false);};
  const embeddedActions=typeof children==='function';
  const number = guideNumber(loop, LOOP_TEST_KEY);
  const missing = tasks(loop).filter(key => !loop.configs[key]);
  return <section ref={section} className={"guide-step meta-start-guide " + (open ? "is-open" : "")+(evidenceOnly?' is-evidence-focused':'')}>
    <h3 className="guide-step-heading" hidden={evidenceOnly}><button id={"guide-toggle-" + LOOP_TEST_KEY} className="guide-step-toggle" aria-controls={"guide-panel-" + LOOP_TEST_KEY} aria-expanded={open} onClick={onToggle}>
      <span className="task-number">{number}</span><span className="guide-step-title"><strong>先打开测试观察，再提交一次询盘</strong><small>{separateEvidence?'先完成接入，不用先发布广告。保持 Meta 测试页打开。':'闭环测试 · 保持 Meta 测试页打开，按顺序核对'}</small></span>
      <span className="domain-tag domain-crm"><Icon name="tasks" size={15} />网站 / CRM / Meta</span><span className="guide-step-status">人工测试指引</span><Icon name={open ? "down" : "chevron"} size={18} />
    </button></h3>
    <div id={"guide-panel-" + LOOP_TEST_KEY} hidden={!open} aria-labelledby={"guide-toggle-" + LOOP_TEST_KEY}>
      <div className="meta-start-body" hidden={evidenceOnly}>
        {!separateEvidence&&<p className="meta-start-intro"><Icon name="info" size={18} /><span>先完成网站、CRM 和 Meta 的接入，再按下列顺序测试。不用先投放广告；本页不会自动提交询盘、发送事件或判定通过。</span></p>}
        {missing.length > 0 && <div className="loop-test-prerequisites"><strong>还有 {missing.length} 项配置未登记，先完成接入准备</strong><div>{missing.map(key => <button type="button" className="text-button" key={key} onClick={() => onReveal(key)}>{guideNumber(loop, key)} {DEFS[key].title}</button>)}</div></div>}
        <GuideTestPreparation loop={loop} onReveal={onReveal}/>
        <details className="guide-test-details"><summary>完整测试说明：准备、同意控制、CRM 核对与收尾</summary>
        <ol className="meta-start-settings" aria-label="闭环测试操作顺序">
          <SettingRow field="测试前准备" value="确认环境和测试范围">
            <small>打开登记的落地页、CRM「事件运营」和 Meta「事件管理工具」。实际安装与接入须已完成；仅在本模块保存配置不等于已经接通。</small>
            <small>生产测试会生成记录，也可能触发通知、分配与回传。先获准本次测试及数据处理范围，使用获准的测试联系方式，不冒用客户资料。</small>
          </SettingRow>
          <SettingRow field="准备观察" value="打开同一数据集的「测试事件」" accessory={<GuideIllustration
            src="/static/console/meta-help/meta-open-events-manager-20261009.svg"
            title="进入事件管理工具"
            alt="Meta 广告编辑页中的入口示例，红框标出「前往事件管理工具」按钮。"
            caption="点击红框中的「前往事件管理工具」。图中的 Pixel 提示仅为入口示例，不代表当前接入状态。"
            active={open}
          />}>
            <small>若在广告编辑页看到图中提示，点击「前往事件管理工具」；没有此提示也可直接打开事件管理工具。进入后选择本流程登记的数据集，核对数字 ID，再点「测试事件」，记录开始时间。</small>
            <small>若服务器测试需要测试事件码，由接入人员在获准范围内配置；不要把码或密钥填写到实操记录。</small>
            <small>看不到入口或无法确认测试模式时先停止，请接入人员确认；不要换数据集或盲目重复提交。</small>
          </SettingRow>
          <SettingRow field="打开网站测试" value="填写网站网址，再点「测试事件」" accessory={<GuideIllustration
            src="/static/console/meta-help/meta-test-website-events-20261009.svg"
            title="打开网站测试"
            alt="Meta 测试事件页面：展开下方「确认你的网站事件设置正确」，在网址框填写联系页网址，再点击右下角「测试事件」。"
            caption="看下方的网站测试区域。填写本方案实际网址，不要照抄图片里的打码网址；上方服务器测试代码不是网址。"
            active={open}
          />}>
            <small>展开「确认你的网站事件设置正确」，输入本方案要测试的网页网址，点击右下角「测试事件」。此处不用点击上方的「图谱 API 探索工具」。</small>
            <small>跳转到网站是正常现象。保留 Meta 测试页，在网站按本次获准范围接受营销追踪，再刷新网页；先不用填表。</small>
          </SettingRow>
          <SettingRow field="先看浏览事件" value="回到 Meta「测试事件」查看 PageView" accessory={<GuideIllustration
            src="/static/console/meta-help/meta-test-pageview-received-20261009.svg"
            title="查看浏览事件结果"
            alt="Meta 测试事件结果示例：正在接收动态，列表出现 PageView，状态为处理完毕，来源为浏览器，并显示接收时间。"
            caption="结果就在同一个「测试事件」页。核对 PageView、来源「浏览器」和本次接收时间；截图只是结果示例，不是当前方案的实时状态。"
            active={open}
          />} explanation={{ title: "没有出现事件？", content: <>
            <p>先核对数据集和网站的营销同意。若浏览器控制台显示 fbevents.js 的 ERR_BLOCKED_BY_CLIENT，说明追踪脚本被本地拦截；检查广告拦截或防追踪设置，只对测试网站临时放行，不必关闭全部扩展。</p>
            <p>放行后刷新网站，再回 Meta 查看；测试结束恢复临时设置。仍无记录时保留报错截图交给接入人员，不要反复提交表单。</p>
          </> }}>
            <small>回到刚才的 Meta「测试事件」页，稍等片刻。出现 PageView，且来源为「浏览器」、接收时间对应本次操作，才说明这次浏览事件已收到。</small>
            <small>PageView 不代表询盘 Lead、服务器回传或去重通过。先确认浏览事件，再按下面步骤提交一次获准的测试询盘；截图中的两条记录也不是要求重复提交。</small>
          </SettingRow>
          <SettingRow field="提交测试询盘" value="从网站实际表单提交一次">
            <small>打开本流程登记的网页，在已获准的测试条件下完成营销同意，填写必填项。留言标明 TEST / DO NOT CONTACT 和唯一测试标记；只点击一次提交，记录时间和网站反馈。</small>
            <small>网站报错或超时时先检查 CRM 是否已经收到，避免重复提交。拒绝营销同意的对照测试应能提交表单，但不发送营销追踪事件；两种结果分别记录。</small>
          </SettingRow>
          <SettingRow field="核对 CRM" value="同一笔提交，只新增一条询盘">
            <small>按测试标记和时间查找询盘，核对表单、来源页面、同意记录及事件编号。没有记录回查表单与接收；重复记录回查去重。</small>
            <div className="loop-test-links"><button type="button" className="text-button" onClick={() => onReveal("form")}>回查表单接入</button><button type="button" className="text-button" onClick={() => onRecord("crm")}>记录 CRM 接收结果<Icon name="right" size={15} /></button></div>
          </SettingRow>
          <SettingRow field="核对 Meta" value="收到对应的「成为潜在客户」事件">
            <small>核对事件名称 Lead、接收时间、来源和事件编号；它应对应刚才成功提交的询盘，而不是仅浏览网页或点击按钮。</small>
            <small>同时使用浏览器 Pixel 与服务器回传时，核对两路 Lead 的事件编号一致，并取得 Meta 的去重证据。只看到服务器接收，或 API 返回成功，不能算浏览器和去重也通过；证据不足记为待继续。</small>
            <div className="loop-test-links"><button type="button" className="text-button" onClick={() => onReveal("pixel")}>排查浏览器事件</button><button type="button" className="text-button" onClick={() => onReveal("server")}>排查服务器回传</button><button type="button" className="text-button" onClick={showEvidence}>记录 Meta 接收与去重结果<Icon name="right" size={15} /></button></div>
          </SettingRow>
          <SettingRow field="判定与收尾" value="证据完整后，再做发布前检查">
            <small>通过标准：网站提交成功、CRM 单条接收、Meta 收到对应事件；两路接入时去重有证据，同意控制符合约定。任一项失败或未确认都不能算闭环通过。</small>
            <small>在实操记录写清测试时间、测试标记、询盘编号、事件编号、逐项观察及截图说明，不记录个人资料或密钥。由获准人员恢复临时测试配置、按约定标记或归档测试数据；不要直接删除审计记录。</small>
            <small>测试通过也不等于广告已发布，预算、素材、受众和发布条件仍需最终核对。本页不会自动改变流程的验证状态。</small>
            <div className="loop-test-links"><button type="button" className="text-button" onClick={() => onReveal(META_ADSET_KEY)}>回到 02 复核转化事件<Icon name="right" size={15} /></button></div>
          </SettingRow>
        </ol>
        </details>
        <div className="guide-test-continue">
          <div className="guide-test-boundary"><strong>没有测试证据，就不能认定闭环已接通</strong><span>本页不会自动提交询盘、发送事件；人工记录不等于自动验收。</span></div>
          <button type="button" className="button primary" onClick={showEvidence}>继续：核对本次询盘<Icon name="right" size={16}/></button>
        </div>
      </div>
      <div ref={evidence} className="evidence-anchor" tabIndex={-1} hidden={separateEvidence&&!evidenceOnly}>{embeddedActions?children(evidenceOnly?returnToSteps:undefined):children}</div>
      {evidenceOnly&&!embeddedActions&&<footer className="guide-step-actions"><span>不确定时保持待核对，不必勉强通过。</span><button type="button" className="button" onClick={returnToSteps}>返回测试步骤</button></footer>}
    </div>
  </section>;
}
