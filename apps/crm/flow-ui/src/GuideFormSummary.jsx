import React from 'react';

// Read the existing runtime draft, never create a second form association.
export function GuideFormSummary({runtime,onEdit}) {
 const state=runtime?.state;
 const form=state?.forms?.find(item=>String(item.id)===String(state.draft?.formId));
 return <section className="guide-form-association" aria-label="04已保存的表单关联">
  <h4>沿用第04步的表单</h4>
  {runtime?.error?<p role="alert">表单关联读取失败，不能据此判断未配置。<button type="button" className="text-button" onClick={runtime.read}>重试读取关联</button></p>
   :!state?<p role="status">正在读取04已保存的表单关联…</p>
   :<><dl>
    <div><dt>表单</dt><dd>{form?`${form.name} · ${form.code}`:state.draft?.formId?'已关联表单当前不可用，请核对':'请先到04选择并保存表单'}</dd></div>
    <div><dt>接入页面</dt><dd>{state.draft?.leadPaths?.length?state.draft.leadPaths.map(path=><span key={path}>{path}</span>):'尚未保存成功询盘页面'}</dd></div>
    <div><dt>来源记录</dt><dd>页面、来源参数、同意记录</dd></div>
    <div><dt>Lead 触发时机</dt><dd>表单成功入库之后</dd></div>
   </dl><p>这里显示04已保存的关联，不包含尚未保存的编辑；不代表已应用或已收到询盘。</p></>}
  <button type="button" className="button" onClick={onEdit}>返回04修改页面与表单</button>
 </section>;
}

export function GuideCrmChecks(){
 return <section className="guide-crm-checks">
  <h4>稍后测试时看这四件事</h4>
  <ol><li>同一次提交只新增一条询盘。</li><li>来源页面、表单和测试标记对应本次操作。</li><li>同意记录正确保留。</li><li>事件编号可以与 Meta 的 Lead 对照。</li></ol>
  <p className="guide-crm-timeout">提交超时不要立即再点一次。先看 CRM 是否已经入库，避免重复询盘。</p>
  <h4>通知状态单独看</h4><p>邮件通知失败不等于询盘提交失败。查入库记录与通知记录，不混成一个“失败”。</p>
 </section>;
}
