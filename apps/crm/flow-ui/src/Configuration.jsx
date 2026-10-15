import React, {useState} from "react";
import { DEFS } from "./model.mjs";
import { hasMetaStartGuide, OPERATIONS, META_PIXEL_OPERATION, guideNumber } from "./guidance.mjs";
import { Icon, Note } from "./ui.jsx";
import { GuideIllustration } from "./GuideIllustration.jsx";
import { GuideReferenceFigure } from "./GuideReferenceFigure.jsx";
import destinationIllustration from "./assets/meta-destination-guide.svg";
import eventCodeIllustration from "./assets/meta-add-event-code.svg";
import { MetaDatasetGuide } from "./MetaDatasetGuide.jsx";
import { ServerCredentialGuide } from "./ServerCredentialGuide.jsx";
import {GuideFormSummary,GuideCrmChecks} from './GuideFormSummary.jsx';
import {datasetIdentityConflict} from './datasetIdentity.mjs';

// A step's real form, embedded in the guide. Navigation and draft ownership
// belong to Guidance so hiding one form cannot discard its state.
export function Configuration({ loop, nodeKey, values: enteredValues, onChange: onEdit, onSave, onContinue,
  onReveal, error, notice, dirty, crm = false, active = true, busy = false, last = false, direct = false, runtime, compactRuntime=false, previewNext }) {
  const metaPixel = nodeKey === "pixel" && hasMetaStartGuide(loop);
  const def = DEFS[nodeKey], operation = metaPixel ? META_PIXEL_OPERATION : OPERATIONS[nodeKey];
  const metaDestination = nodeKey === "website" && hasMetaStartGuide(loop);
  const metaDataset = nodeKey === "dataset" && hasMetaStartGuide(loop);
  const datasetBound = crm && nodeKey === "dataset" && Boolean(loop.configs.dataset?.externalId);
  const boundDatasetId = datasetBound ? loop.configs.dataset.externalId : '';
  const boundDataset = datasetBound ? loop.sources?.find(source=>String(source.id)===String(boundDatasetId)) : null;
  const identityConflict = datasetBound && datasetIdentityConflict(loop.configs.dataset, enteredValues);
  const values = datasetBound ? {...enteredValues, externalId:boundDatasetId, source:loop.configs.dataset.source||enteredValues.source} : enteredValues;
  const onChange = next => onEdit(identityConflict ? {...next, externalId:enteredValues.externalId, source:enteredValues.source} : next);
  const illustrated = crm && !direct && hasMetaStartGuide(loop);
  const compactAd = illustrated && nodeKey === 'ad';
  const adObjective = compactAd ? def.fields.find(field=>field.key==='objective') : null;
  const compactCrm = illustrated && nodeKey === 'crm';
  const disabled = loop.archived || busy;
  const [editingContract,setEditingContract]=useState(null);
  const savedContract=loop.configs[nodeKey];
  const hasDraft=Boolean(loop.drafts[nodeKey]);
  const contractNeedsAttention=!savedContract||hasDraft||dirty||Boolean(error)||busy;
  const contractOpen=contractNeedsAttention||editingContract===loop.id;
  const contractId=`runtime-contract-${loop.id}-${nodeKey}`;
  const content=(
    <form className="config-form" data-illustrated={illustrated||undefined} data-runtime-contract={compactRuntime||undefined} noValidate onSubmit={e => { e.preventDefault(); onSave(false); }} aria-label={`${def.title}配置`}>
      <div className="guide-step-body">
        {!direct&&!compactRuntime&&<div className="guide-instructions">
          {compactCrm&&<GuideCrmChecks/>}
          {!compactAd&&!compactCrm&&<>
          {!(illustrated&&metaDataset)&&<><div className="guide-location-heading">
            <h3><Icon name="external" size={16} /> 到哪里操作</h3>
            {metaDestination && !illustrated && <GuideIllustration src={destinationIllustration} title="目标位置" active={active}
              alt="Meta 目标位置示意图：网站、示例网址与浏览器附加组件选项。"
              caption="目标位置：网站 · 浏览器附加组件：无。示例网址需换成自己的实际网址。" />}
            {metaPixel && <GuideIllustration src={eventCodeIllustration} title="添加事件代码" active={active}
              alt="安装 Pixel 像素代码的添加事件代码页，列表下方有查看更多事件，底部有完成按钮。"
              caption="这是手动安装说明。Lead 不在当前列表时先展开“查看更多事件”；截图本身没有证明已安装或已测试。" />}
          </div>
          <p className="guide-location">{metaDataset ? "Meta 广告管理工具 → 广告组 → 数据集；取 ID：事件管理工具 → 数据源 → 设置" : operation.where}</p>
          </>}
          {!metaDataset&&<h3>怎么做</h3>}
          {metaDataset ? <MetaDatasetGuide active={active} inline={illustrated} /> : <ol>{operation.steps.map((line, i) => <li key={i}>{line}</li>)}</ol>}
          </>}
          {illustrated&&(metaDestination||nodeKey==='ad')&&<>
            <GuideReferenceFigure src={destinationIllustration} title="对照 Meta 的目标位置"
              alt="Meta 目标位置示意图：网站、示例网址与浏览器附加组件选项。"
              caption="网站地址需填写自己的真实网址。图示不是当前广告的实时配置。"/>
            {nodeKey==='ad'&&<p>三个广告名称不要求相同，不通过名称调用 Meta API。本网站询盘路径不要残留即时表单绑定。</p>}
          </>}
          {compactAd&&<details className="guide-ad-checklist"><summary>广告三层核对与发布前检查</summary><p>{operation.where}</p><ol>{operation.steps.map((line,i)=><li key={i}>{line}</li>)}</ol><p>{operation.result}</p></details>}
          {compactAd&&<div className="guide-ad-dataset"><span>本流程数据集 <strong>{loop.configs.dataset?.externalId||'尚未登记'}</strong></span><button type="button" className="text-button" onClick={()=>onReveal('dataset')}>查看第 01 步<Icon name="right" size={15}/></button></div>}
          {nodeKey === 'server' && hasMetaStartGuide(loop) && <ServerCredentialGuide datasetId={loop.configs.dataset?.externalId} active={active} />}
          {metaPixel && <div className="loop-test-links"><button type="button" className="text-button" onClick={() => onReveal("server")}>查看 {guideNumber(loop, "server")} 服务器回传<Icon name="right" size={15} /></button></div>}
          {!metaDataset && !compactAd && !compactCrm && <p className="guide-result">{operation.result}</p>}
        </div>}
        <div className="guide-fields">
          {identityConflict&&<div className="form-error dataset-identity-conflict" role="alert">
            <strong>数据集绑定已更新，需要核对后继续</strong>
            <p>原输入编号：{enteredValues.externalId||'未填写'}。当前已绑定编号：{boundDatasetId}。下方锁定字段显示当前绑定；原输入不会覆盖它，备注仍保留。</p>
            <button type="button" className="button" disabled={disabled} onClick={()=>onEdit({...values})}>采用已绑定数据集，保留备注</button>
          </div>}
          {compactRuntime&&<><h4>回传约定 · 单独保存，不应用运行</h4><p className="guide-form-note">核对本站接入与本实例的数据集相同。同一次浏览器与服务器 Lead 应使用相同事件 ID；勾选仅记录要求，去重结果仍需核验。</p></>}
          {compactCrm&&<><GuideFormSummary runtime={runtime} onEdit={()=>onReveal('pixel')}/><h4>设置 CRM 规则</h4><p className="guide-form-note">核对现有接收和去重设置，在这里记录沿用约定；不会新建队列。是否只收到一条询盘，在09测试时核对。</p></>}
          {!illustrated&&<h3>{direct?def.title:'在 CRM 记录本步信息'}</h3>}
          {!illustrated&&<p className="guide-form-note">保存记录不代表外部操作已完成或验证通过。</p>}
          {!compactRuntime&&!compactAd&&["pixel", "server", "ad"].includes(nodeKey) && <div className="read-only-field">
            <span>{direct?'方案数据集':'本流程数据集'}</span><strong>{loop.configs.dataset?.externalId || "尚未登记"}</strong>
            <button type="button" className="text-button" onClick={() => onReveal("dataset")}>{direct?'查看数据集':'查看第 01 步'}<Icon name="chevron" size={15} /></button>
          </div>}
          {def.fields.filter(f=>!compactAd||f.key!=='objective').map(f => illustrated&&metaDataset&&f.key==='source'?(
            <React.Fragment key={f.key}><fieldset className="guide-source-choices">
              <legend>数据源来源 <span className="required">* 必填</span></legend>
              {f.options.map(([value,label])=><label key={value} className={values.source===value?'is-selected':''}>
                <input type="radio" name={`dataset-source-${loop.id}`} value={value} checked={values.source===value}
                  disabled={disabled||datasetBound} onChange={()=>onChange({...values,source:value,externalId:''})}/><span>{label}</span>
              </label>)}
            </fieldset>
            {datasetBound&&<section className="guide-bound-dataset" aria-label="已绑定数据集">
              <div><strong>已绑定数据集</strong><span>已锁定</span></div>
              <p>{boundDataset?.name ? `${boundDataset.name} · ` : ''}{boundDatasetId}</p>
              <small>当前实例已绑定；来源和编号不可在此覆盖。</small>
            </section>}
            </React.Fragment>
          ):f.type === "checkbox" ? (
            <label className="checkbox-field" key={f.key}>
              <input type="checkbox" disabled={disabled} checked={values[f.key] === true} onChange={e => onChange({ ...values, [f.key]: e.target.checked })} />
              <span>{f.label}</span>
            </label>
          ) : (
            <label className="field" key={f.key}>
              {metaDestination && f.key === "url" ? "网址" : f.label}{!f.optional ? <span className="required"> * 必填</span> : <small>选填</small>}
              {crm && nodeKey === "dataset" && f.key === "externalId" && values.source === "existing" && !datasetBound ? <>
                <select disabled={disabled} value={values.externalId || ""} onChange={e => onChange({ ...values, externalId: e.target.value })}>
                  <option value="">选择同站点、同环境的已登记数据源</option>
                  {(loop.sources || []).map(source => <option key={source.id} value={source.id}>{source.name} · {source.id} · {source.origin}</option>)}
                </select>
                {!loop.sources?.length && <small>暂无可复用记录。若 Meta 已有本次使用的数据集，请改选“登记在 Meta 创建的数据集”并填写其数字 ID。</small>}
              </> : f.type === "select" ? (
                <select disabled={disabled || (datasetBound && f.key === "source")} value={values[f.key] || ""} onChange={e => onChange({ ...values, [f.key]: e.target.value,
                  ...(crm && nodeKey === "dataset" && f.key === "source" && !datasetBound ? { externalId: "" } : {}) })}>
                  <option value="">请选择</option>
                  {f.options.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
                </select>
              ) : illustrated&&metaDataset&&f.key==='alias'?(
                <textarea disabled={disabled} value={values[f.key]||''} onChange={e=>onChange({...values,[f.key]:e.target.value})} placeholder={f.placeholder} maxLength={200}/>
              ) : (
                <input disabled={disabled} readOnly={datasetBound && f.key === "externalId"} inputMode={f.key === "externalId" ? "numeric" : undefined}
                  value={values[f.key] || ""} onChange={e => onChange({ ...values, [f.key]: e.target.value })} placeholder={f.placeholder} maxLength={f.key === "url" ? 2000 : 200} />
              )}
              {metaDestination && f.key === "url" && <small>与 Meta「目标位置 → 网址」填写的完整地址一致。这里只登记，不会自动修改 Meta。</small>}
            </label>
          ))}
          {illustrated&&nodeKey==='ad'&&<section className="guide-ad-destination">
            <label className="field">落地页完整网址<input readOnly value={loop.configs.website?.url||''} placeholder="先在03登记完整网址"/></label>
            <div className="guide-ad-url-help"><span>沿用03已保存的网址，与 Meta 目标网址核对。</span><button type="button" className="text-button" onClick={()=>onReveal('website')}>到03修改网址<Icon name="right" size={15}/></button></div>
            <div className="guide-ad-settings">
              {adObjective&&<label className="field">{adObjective.label}{!adObjective.optional&&<span className="required"> * 必填</span>}
                <select disabled={disabled} value={values.objective||''} onChange={e=>onChange({...values,objective:e.target.value})}>
                  <option value="">请选择</option>{adObjective.options.map(([value,label])=><option key={value} value={value}>{label}</option>)}
                </select>
              </label>}
              <label className="field">转化事件<input readOnly value="Lead" aria-describedby={`ad-event-${loop.id}`}/><small id={`ad-event-${loop.id}`}>成为潜在客户 · 本路径固定</small></label>
            </div>
          </section>}
          {datasetBound && <Note>已关联数据集的来源和 ID 只读。更换数据源需要单独处理；当前可以补充备注。</Note>}
          {illustrated&&!compactRuntime&&!compactAd&&<p className="guide-form-note">保存记录不代表外部操作已完成或验证通过。</p>}
          {nodeKey === "dataset" && values.source === "created" && <details className="external-help">
            <summary>在哪里找到数据集 ID？</summary>
            <p>在 Meta 事件管理工具选择该数据集，打开设置，复制数字 ID。这里只登记，不会再创建一个数据集。</p>
          </details>}
          {error && <div className="form-error" role="alert" tabIndex={-1}><Icon name="alert" />{error}</div>}
        </div>
        {compactAd&&<div className="guide-ad-publish-note"><Note>保存本步不会创建或发布广告。预算、受众、素材和发布日期仍须在 Meta 单独核对。</Note></div>}
      </div>
      <footer className="guide-step-actions">
        <div className="guide-footer-context">
        <span className={`guide-save-feedback ${dirty ? "is-dirty" : ""}`} role="status">{busy ? "正在保存…" : dirty ? "有未保存修改" : hasDraft ? (savedContract ? "编辑草稿已暂存 · 正式配置尚未更新" : "草稿已暂存 · 尚未保存正式配置") : notice || (savedContract ? "已保存 · 外部结果待核验" : "尚未保存")}</span>
        {previewNext&&<button type="button" className="text-button" disabled={busy} onClick={previewNext.action}>{previewNext.label}<Icon name="right" size={16}/></button>}
        </div>
        <div>
          <button type="button" className="button" disabled={disabled||identityConflict} onClick={() => onSave(true)}>暂存草稿</button>
          <button type="submit" className={`button ${(last||direct)&&!compactRuntime ? "primary" : ""}`} disabled={disabled||identityConflict}>{compactRuntime?'保存回传约定':direct?'保存配置':'保存本步'}</button>
          {!last&&!direct&&!compactRuntime && <button type="button" className="button primary" disabled={disabled||identityConflict} onClick={onContinue}>保存并继续<Icon name="right" size={17} /></button>}
        </div>
      </footer>
    </form>
  );
  if(!compactRuntime)return content;
  return <section className="guide-runtime-contract">
    <div className="guide-runtime-contract-summary" aria-label="回传约定摘要">
      <div><h4>回传约定</h4><p>{savedContract?`${savedContract.event||'未选择事件'} · ${savedContract.dedup?'要求双路使用相同事件 ID':'未记录同编号要求'}`:'尚未保存，请先填写下方约定。'}</p>
        <small>只记录约定；不应用运行，也不代表 Meta 去重通过。</small></div>
      <button type="button" className="text-button" aria-expanded={contractOpen} aria-controls={contractId}
        disabled={contractOpen&&contractNeedsAttention} onClick={()=>setEditingContract(contractOpen?null:loop.id)}>
        {contractOpen?'收起已保存约定':'修改回传约定'}<Icon name="down" size={16}/>
      </button>
    </div>
    <div id={contractId} aria-label="编辑回传约定" hidden={!contractOpen}>{content}</div>
  </section>;
}
