import React, { useEffect, useRef, useState } from "react";
import { DEFS, DOMAIN, tasks, saveConfig, saveDraft, toggleFilter } from "./model.mjs";
import { OPERATIONS, saveDirtySteps, guideOrder, guideNumber, nextGuideStep, META_ADSET_KEY, LOOP_TEST_KEY } from "./guidance.mjs";
import { Configuration } from "./Configuration.jsx";
import { Icon } from "./ui.jsx";
import { MetaStartGuide, META_START_KEY, hasMetaStartGuide } from "./MetaStartGuide.jsx";
import { MetaAdSetGuide } from "./MetaAdSetGuide.jsx";
import { ClosedLoopTestGuide } from "./ClosedLoopTestGuide.jsx";
import {useRuntime, RuntimePanel, RuntimeEvidence} from './RuntimePanel.jsx';
import {PixelStep} from './PixelStep.jsx';
import {GuideTaskNavigation} from './GuideTaskNavigation.jsx';
import {GuideFormSummary,GuideCrmChecks} from './GuideFormSummary.jsx';
import {GuideImageLibrary} from './GuideImageLibrary.jsx';
import {datasetIdentityConflict} from './datasetIdentity.mjs';
import './guide-redesign.css';

export function Guidance({ loop, selectedKey, update, navigate, guard, crm, busy, api, direct = false, onStepChange, onRuntimeSnapshot }) {
  const order = tasks(loop);
  const showMetaStart = hasMetaStartGuide(loop);
  const runtime = useRuntime(loop, api, crm && showMetaStart, onRuntimeSnapshot);
  const panelKeys = guideOrder(loop);
  const first = panelKeys.includes(selectedKey) ? selectedKey : order.find(key => !loop.configs[key]) || order[0];
  const taskWorkbench = crm && showMetaStart && !direct;
  const [focusedKey,setFocusedKey] = useState(panelKeys.includes(selectedKey)||selectedKey==='illustrations'?selectedKey:META_START_KEY);
  const lastTask=useRef(panelKeys.includes(selectedKey)?selectedKey:'pixel');
  const [showAll,setShowAll] = useState(false);
  const [opened, setOpened] = useState(() => new Set(direct?[selectedKey||'dataset']:showMetaStart ? [META_START_KEY, first, ...(loop.configs.dataset ? [META_ADSET_KEY] : [])] : [first]));
  const [values, setValues] = useState(() => Object.fromEntries(order.map(key => [key, { ...(loop.drafts[key] || loop.configs[key] || {}) }])));
  const [baseline, setBaseline] = useState(() => Object.fromEntries(order.map(key => [key, JSON.stringify(loop.drafts[key] || loop.configs[key] || {})])));
  const [errors, setErrors] = useState({}), [notices, setNotices] = useState({}), [saving, setSaving] = useState(null);
  const lock = useRef(false), pageRef = useRef(null);
  const [removingFilter,setRemovingFilter]=useState(false);
  const pixelController=useRef(null);
  const [pixelStatus,setPixelStatus]=useState({dirty:false,saving:false});
  const evidenceController=useRef(null);
  const dirty = Object.fromEntries(order.map(key => [key, JSON.stringify(values[key]) !== baseline[key]]));
  if(direct||(crm&&showMetaStart))dirty.pixel=pixelStatus.dirty;
  const savingKey=pixelStatus.saving?'pixel':saving;
  const dirtyCount = Object.values(dirty).filter(Boolean).length;
  const done = order.filter(key => loop.configs[key]).length;
  const reveal = (key, focus = true, syncLocation = true) => {
    if (!panelKeys.includes(key)&&!(taskWorkbench&&key==='illustrations')) return;
    if(panelKeys.includes(key))lastTask.current=key;
    if (taskWorkbench && syncLocation) onStepChange?.(key);
    setFocusedKey(key);
    setShowAll(false);
    setOpened(previous => new Set(direct?[key]:[...previous, key]));
    if (focus) requestAnimationFrame(() => {
      const heading = document.getElementById(`guide-toggle-${key}`);
      heading?.focus({ preventScroll: true });
      heading?.scrollIntoView({ block: "start", behavior: "auto" });
    });
  };
  useEffect(() => { if (panelKeys.includes(selectedKey)||selectedKey==='illustrations') reveal(selectedKey, true, false); }, [selectedKey]);
  const save = async (key, draft = false) => {
    if (lock.current || busy || loop.archived) return false;
    if (crm && key==='dataset' && datasetIdentityConflict(loop.configs.dataset, values.dataset)) {
      setErrors(previous=>({...previous,dataset:'请先核对并采用已绑定数据集，备注将保留。'}));
      reveal('dataset');
      return false;
    }
    lock.current = true;
    setSaving(key);
    setErrors(previous => ({ ...previous, [key]: "" }));
    try {
      const currentValues = values[key];
      const next = (draft ? saveDraft : saveConfig)(loop, key, currentValues);
      const saved = await update(next, "", { action: draft ? "save_draft" : "save_config", nodeKey: key, values: currentValues });
      if (!saved) { reveal(key); return false; }
      setBaseline(previous => ({ ...previous, [key]: JSON.stringify(currentValues) }));
      setNotices(previous => ({ ...previous, [key]: draft ? "草稿已保存 · 尚未计为配置完成" : direct ? '配置已保存 · 尚未应用' : "本步已保存 · 外部结果待核验" }));
      return true;
    } catch (error) {
      setErrors(previous => ({ ...previous, [key]: error.message }));
      reveal(key, false);
      requestAnimationFrame(() => pageRef.current?.querySelector(`#guide-panel-${key} .form-error`)?.focus());
      return false;
    } finally { lock.current = false; setSaving(null); }
  };
  // One guard for the whole page: no child form can clear another step's edits.
  const current = useRef(null);
  current.current = { get dirty(){return dirtyCount > 0 || runtime.dirty || runtime.busy || pixelController.current?.dirty || evidenceController.current?.dirty;},
    get saving(){return Boolean(evidenceController.current?.saving);},
    get kind(){return evidenceController.current?.dirty?'observation':undefined;}, save: async () => {
    if(busy||lock.current||runtime.busy||evidenceController.current?.dirty)return false;
    // Keep the submission from the idle render. Ordinary saves temporarily render
    // Pixel disabled; their accepted response may precede React's next commit.
    const pixelSave=pixelController.current?.dirty?pixelController.current.save:null;
    if(!await saveDirtySteps(order.filter(k=>!(crm&&showMetaStart&&k==='pixel')), dirty, key => save(key, true)))return false;
    if(pixelSave&&!await pixelSave())return false;
    return true;
  } };
  useEffect(() => {
    const aggregate = { get dirty() { return current.current.dirty; }, get saving(){return current.current.saving;}, get kind(){return current.current.kind;}, save: () => current.current.save() };
    guard.current = aggregate;
    const unload = event => { if (aggregate.dirty || lock.current) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", unload);
    return () => { if (guard.current === aggregate) guard.current = null; window.removeEventListener("beforeunload", unload); };
  }, [guard]);
  if(direct) {
    const labels={dataset:'数据集',website:'落地页网址',pixel:'网站与表单',crm:'CRM 接收',server:'回传约定',ad:'广告记录',filter:'拟议筛选规则'};
    const directKeys=order.filter(k=>k!=='form');
    return <section className="plan-configuration" aria-label="方案配置" ref={pageRef}>
      <nav className="plan-config-nav" aria-label="配置类别">{directKeys.map(key=><button id={`guide-toggle-${key}`} key={key} aria-current={opened.has(key)?'page':undefined} aria-controls={`guide-panel-${key}`} onClick={()=>reveal(key,false)}><span>{labels[key]}</span><small>{savingKey===key?'正在保存…':dirty[key]?'未保存':loop.drafts[key]?'草稿已暂存':loop.configs[key]?'已保存':'待配置'}</small></button>)}</nav>
      <div className="plan-config-panels">{directKeys.map(key=><div key={key} id={`guide-panel-${key}`} hidden={!opened.has(key)} aria-labelledby={`guide-toggle-${key}`}>
        {key==='filter'&&<div className="runtime-checks"><p>这是拟议规则，尚未参与实际接收或回传。移除将清除本方案记录的筛选条件，不改变已应用版本。</p>{removingFilter?<div className="runtime-actions"><button className="button danger" disabled={busy||dirtyCount>0} onClick={async()=>{if(await update(toggleFilter(loop,false),'拟议筛选规则已移除',{action:'filter',enabled:false})){setRemovingFilter(false);reveal('crm');}}}>确认移除</button><button className="button" onClick={()=>setRemovingFilter(false)}>取消</button></div>:<button className="button" disabled={busy||dirtyCount>0} onClick={()=>setRemovingFilter(true)}>移除未实现的筛选规则</button>}{dirtyCount>0&&<p>先保存当前修改，再移除规则。</p>}</div>}
        {key==='pixel'?<PixelStep loop={loop} runtime={runtime} update={update} controller={pixelController} onStatusChange={setPixelStatus} busy={busy} direct/>:<Configuration loop={loop} nodeKey={key} values={values[key]} dirty={dirty[key]} crm direct active={opened.has(key)} busy={busy||saving!==null} error={errors[key]} notice={notices[key]} onChange={value=>setValues(previous=>({...previous,[key]:value}))} onReveal={key=>reveal(key)} onSave={draft=>save(key,draft)}/>}
      </div>)}</div>
    </section>;
  }
  const ReadingOptions=taskWorkbench?'details':'div';
  const recordsFooter=<footer className="guidance-footer"><p><strong>配置保存后，还需要核对实际结果</strong><span>检查接收、投递与去重证据，不能仅凭保存数量判断流程已运行。</span></p>
    <button className="button" disabled={busy} onClick={() => navigate(`/flow/${loop.id}?view=verification`)}>核对与实操记录<Icon name="right" size={17} /></button></footer>;
  return (
    <section className={`guidance-page${taskWorkbench?' guide-task-workbench':''}${focusedKey==='illustrations'?' is-image-library':''}`} aria-label="典型操作指引" ref={pageRef}>
      {taskWorkbench&&focusedKey==='illustrations'&&<GuideImageLibrary onReturn={()=>reveal(lastTask.current)} onStep={reveal}/>}
      <ReadingOptions className="guide-reading-options">
      {taskWorkbench&&<summary>操作说明与阅读方式<span>配置保存不等于测试通过</span></summary>}
      <header className="guidance-heading">
        <div><h2>典型操作指引 <span>配置已保存 {done} / {order.length} 项</span></h2>
          <p>{showMetaStart ? `按操作顺序逐步对照；00、02 为设置指引，${guideNumber(loop, LOOP_TEST_KEY)} 为闭环测试，不计入配置保存数量。保存数量不是测试进度。` : "按编号逐步操作，在本页填写并保存。可以同时展开多项，对照查看。"}</p></div>
        <div className="guidance-expand-actions">
          <button className="text-button" onClick={() => {setShowAll(true);setOpened(new Set(panelKeys));}}><Icon name="expand" size={16} />全部展开</button>
          <button className="text-button" onClick={() => {setShowAll(false);setOpened(new Set());}}><Icon name="collapse" size={16} />全部收起</button>
        </div>
      </header>
      <div className="guidance-summary"><Icon name="info" size={17} /><span>{showMetaStart ? `先完成广告、网站与 CRM 接入配置 → ${guideNumber(loop, LOOP_TEST_KEY)} 闭环测试 → 通过后再做发布前检查。配置已保存不代表实际接通。` : "先确认 Meta 数据集，再准备网站与 CRM，最后核对广告。编号为建议顺序，不是强制锁定；保存不会自动执行外部操作。"}</span></div>
      {taskWorkbench&&recordsFooter}
      </ReadingOptions>
      {dirtyCount > 0 && <p className="guide-dirty-summary" role="status">{dirtyCount} 个步骤有未保存修改，收起不会丢失输入。</p>}
      {taskWorkbench&&<GuideTaskNavigation loop={loop} activeKey={showAll?null:focusedKey} dirty={dirty} savingKey={savingKey}
        onSelect={reveal} onImages={()=>reveal('illustrations')} onRecords={()=>navigate(`/flow/${loop.id}?view=verification`)}/>}
      <div className="guidance-steps">
        {showMetaStart && <div hidden={taskWorkbench&&!showAll&&focusedKey!==META_START_KEY}><MetaStartGuide workbench={taskWorkbench} onContinue={()=>reveal('dataset')} open={opened.has(META_START_KEY)} onToggle={() => setOpened(previous => {
          const next = new Set(previous);
          next.has(META_START_KEY) ? next.delete(META_START_KEY) : next.add(META_START_KEY);
          return next;
        })} /></div>}
        {panelKeys.filter(key => key !== META_START_KEY).map(key => {
          if (key === LOOP_TEST_KEY) return <div key={key} hidden={taskWorkbench&&!showAll&&focusedKey!==key}><ClosedLoopTestGuide loop={loop} separateEvidence={taskWorkbench&&!showAll} open={opened.has(key)} onReveal={reveal} onRecord={node => navigate("/flow/" + loop.id + "?view=verification&node=" + node)} onToggle={() => setOpened(previous => { const next = new Set(previous); next.has(key) ? next.delete(key) : next.add(key); return next; })}>{crm?(onReturn=><RuntimeEvidence loop={loop} api={api} controller={evidenceController} active={opened.has(key)&&(!taskWorkbench||showAll||focusedKey===key)} onReturn={onReturn}/>):null}</ClosedLoopTestGuide></div>;
          if (key === META_ADSET_KEY) return <div key={key} hidden={taskWorkbench&&!showAll&&focusedKey!==key}><MetaAdSetGuide workbench={taskWorkbench} open={opened.has(key)} datasetId={loop.configs.dataset?.externalId} onDataset={() => reveal("dataset")} onReveal={reveal} serverNumber={guideNumber(loop, "server")} testNumber={guideNumber(loop, LOOP_TEST_KEY)} onTest={() => reveal(LOOP_TEST_KEY)} onToggle={() => setOpened(previous => { const next = new Set(previous); next.has(key) ? next.delete(key) : next.add(key); return next; })} /></div>;
          const def = DEFS[key], open = opened.has(key);
          const config=<Configuration loop={loop} nodeKey={key} values={values[key]} dirty={dirty[key]} crm={crm} runtime={runtime} active={open} busy={busy || saving !== null} last={!nextGuideStep(loop,key)} compactRuntime={taskWorkbench&&key==='server'}
            previewNext={taskWorkbench&&['dataset','ad'].includes(key)?{label:key==='dataset'?'只查看转化事件':'只查看测试指引',action:()=>reveal(key==='dataset'?META_ADSET_KEY:LOOP_TEST_KEY)}:undefined}
            error={errors[key]} notice={notices[key]} onChange={value=>setValues(previous=>({...previous,[key]:value}))}
            onReveal={reveal} onSave={draft=>save(key,draft)} onContinue={async()=>{if(await save(key))reveal(nextGuideStep(loop,key));}}/>;
          return <section className={`guide-step ${open ? "is-open" : ""}`} key={key} hidden={taskWorkbench&&!showAll&&focusedKey!==key}>
            <h3 className="guide-step-heading"><button id={`guide-toggle-${key}`} className="guide-step-toggle" aria-controls={`guide-panel-${key}`} aria-expanded={open}
              onClick={() => setOpened(previous => { const next = new Set(previous); next.has(key) ? next.delete(key) : next.add(key); return next; })}>
              <span className={`task-number ${loop.configs[key] ? "complete" : ""}`}>{guideNumber(loop,key)}</span>
              <span className="guide-step-title"><strong>{taskWorkbench&&key==='ad'?'登记广告与网站目标位置':taskWorkbench&&key==='server'?'比较草稿与当前运行版本':taskWorkbench&&key==='pixel'?'选择哪些页面记录浏览与成功询盘':showMetaStart && key === "dataset" ? "选择或创建数据集" : showMetaStart && key === "pixel" ? "配置网站 Pixel 接入" : OPERATIONS[key].action}</strong><small>{taskWorkbench&&key==='ad'?'用于对应 Meta 中的实际设置，不通过名称调用 Meta API。':taskWorkbench&&key==='server'?'凭据、配置保存、应用运行、Meta 验收是不同动作。':taskWorkbench&&key==='pixel'?'按网站目录选择范围；PageView 与 Lead 分开设置。':showMetaStart && key === "dataset" ? "已有可复用 · 没有则新建 · 登记数字 ID" : showMetaStart && key === "pixel" ? "本步必做 · 选择页面与询盘表单，保存不等于启用" : OPERATIONS[key].subtitle || def.title}</small></span>
              <span className={`domain-tag domain-${def.domain}`}><Icon name={def.icon} size={15} />{DOMAIN[def.domain].short}</span>
              <span className={`guide-step-status ${dirty[key] ? "is-dirty" : ""}`}>{savingKey === key ? "正在保存…" : dirty[key] ? "未保存修改" : loop.drafts[key] ? "草稿已暂存" : loop.configs[key] ? "已保存 · 待核验" : "待配置"}</span>
              <Icon name={open ? "down" : "chevron"} size={18} />
            </button></h3>
            <div id={`guide-panel-${key}`} hidden={!open} aria-labelledby={`guide-toggle-${key}`}>
              {taskWorkbench&&key==='server'?<RuntimePanel mode={key} runtime={runtime} loop={loop} workbench configuration={config}/>:crm&&showMetaStart&&key==='pixel'?<PixelStep loop={loop} runtime={runtime} update={update} controller={pixelController} onStatusChange={setPixelStatus} busy={busy} workbench={taskWorkbench} onContinue={()=>reveal('form')}/>:crm&&showMetaStart&&key==='form'?<div className="config-form"><div className="guide-form-review"><GuideFormSummary runtime={runtime} onEdit={()=>reveal('pixel')}/><GuideCrmChecks/></div><footer className="guide-step-actions"><span>核对关联不等于实际提交成功</span><div><button className="button primary" disabled={!loop.pixelScopeManaged||busy} onClick={()=>reveal(nextGuideStep(loop,'form'))}>继续核对 CRM 接收 →</button></div></footer></div>:config}
              {!taskWorkbench && showMetaStart && key === "dataset" && <div className="guide-dataset-next"><span>登记后继续设置转化事件；保存不代表 Lead 已接通。</span><button type="button" className="text-button" onClick={() => reveal(META_ADSET_KEY)}>查看 02 设置转化事件<Icon name="right" size={16} /></button></div>}
              {!taskWorkbench && showMetaStart && key === "ad" && <div className="guide-dataset-next"><span>完成实际接入后，再按下一步测试；阅读测试指引不需要重复保存。</span><button type="button" className="text-button" onClick={() => reveal(LOOP_TEST_KEY)}>查看 {guideNumber(loop, LOOP_TEST_KEY)} 闭环测试<Icon name="right" size={16} /></button></div>}
            </div>
          </section>;
        })}
      </div>
      {!taskWorkbench&&recordsFooter}
    </section>
  );
}
