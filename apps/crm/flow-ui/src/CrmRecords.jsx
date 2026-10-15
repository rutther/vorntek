import React, { useEffect, useRef, useState } from "react";
import { DEFS, tasks } from "./model.mjs";
import { Icon, Note, dateText } from "./ui.jsx";
import { guideNumber, hasMetaStartGuide, LOOP_TEST_KEY } from "./guidance.mjs";

const results = { observed: "已观察到", issue: "发现问题", pending: "待继续操作" };
export function CrmRecords({ loop, update, busy, guard, selectedKey, navigate, direct=false }) {
  const order = tasks(loop);
  const [nodeKey, setNodeKey] = useState(order.includes(selectedKey) ? selectedKey : order[0]);
  const [result, setResult] = useState("pending"), [note, setNote] = useState(""), [reference, setReference] = useState(""), [error, setError] = useState("");
  const pendingSave = useRef(false), closeout = useRef(null);
  const [saving, setSaving] = useState(false);
  const locked = busy || saving || loop.archived;
  const showCloseout = () => { closeout.current?.focus({preventScroll:true}); closeout.current?.scrollIntoView({block:'center',behavior:'smooth'}); };
  const dirty = Boolean(note || reference);
  const save = async () => {
    if (busy || pendingSave.current || loop.archived) return false;
    if (note.trim().length < 8 || note.trim().length > 2000) {
      setError("请填写 8–2000 字的实操说明，写清观察到的结果或下一步。");
      return false;
    }
    pendingSave.current = true; setSaving(true); setError("");
    try {
      const saved = await update(loop, "本次实操已记录，人工观察不代表独立验证", { action: "evidence", nodeKey, result, note: note.trim(), reference: reference.trim() });
      if (!saved) { setError("本次记录未保存，输入已保留。请查看错误提示后重试。"); return false; }
      setNote(""); setReference(""); setError(""); guard.current = null;
      return true;
    } catch {
      setError("本次记录未保存，输入已保留。请检查连接后重试。"); return false;
    } finally { pendingSave.current = false; setSaving(false); }
  };
  useEffect(() => {
    guard.current = { dirty, save, kind: "evidence" };
    return () => { guard.current = null; };
  }, [dirty, note, reference, nodeKey, result, loop, busy]);
  const disclaimer = <Note>这里只记录本次配置核对与人工观察，不代表 Pixel、CAPI、事件去重或广告投放已经通过独立验证。旧版本记录保留，不自动证明当前配置通过。</Note>;
  const preflight = <section className="crm-preflight">
      <div><h3>检查 CRM 配置</h3><p>只读核对本流程记录与现有 CRM 设置，不向 Meta 或网站发送测试事件。</p></div>
      <button type="button" className="button" disabled={locked} onClick={() => update(loop, "CRM 配置核对结果已记录，外部连接仍待核验", { action: "preflight" })}><Icon name="tasks" />检查 CRM 配置</button>
    </section>;
  const form = <form className="crm-evidence-form" aria-label="记录本次实操" onSubmit={(e) => { e.preventDefault(); save(); }}>
      {direct&&<><h3>记录本次实操</h3><p className="subtle">完成一拍 Meta 操作后，记录观察结果和截图说明；下一次从这里继续。</p></>}
      <div className="crm-evidence-fields">
        <label className="field">对应节点<select disabled={locked} value={nodeKey} onChange={(e) => setNodeKey(e.target.value)}>{order.map(key => <option key={key} value={key}>{!direct&&guideNumber(loop, key)} {DEFS[key].title}</option>)}</select></label>
        <label className="field">观察结果<select disabled={locked} value={result} onChange={(e) => setResult(e.target.value)}>{Object.entries(results).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      </div>
      <label className="field">实操说明 <span className="required">*</span><textarea required minLength={8} maxLength={2000} rows={4} disabled={locked} value={note} onChange={(e) => setNote(e.target.value)} placeholder="记录所在页面、已核对的资产、观察到的结果以及待继续的问题。" /></label>
      <label className="field">证据引用或截图说明（选填）<input maxLength={500} disabled={locked} value={reference} onChange={(e) => setReference(e.target.value)} placeholder="例如：本次对话第 2 张截图，数据集设置页" /><small>最多 500 字。只记录短文字，请勿填写 token、cookie、私钥或客户个人信息。</small></label>
      {!direct&&disclaimer}
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="guide-records-actions"><button className="button primary" disabled={locked}>{busy || saving ? "正在保存…" : "保存本次实操记录"}</button>{!direct&&<button type="button" className="button" onClick={showCloseout}>查看收尾检查</button>}</div>
    </form>;
  const history = loop.history.length ? <div className="verification-list">{loop.history.map((item, index) => {
      const current = item.revision === loop.revision;
      const checks = Array.isArray(item.checks) ? item.checks : item.checks && typeof item.checks === "object" ? Object.entries(item.checks).map(([name, value]) => ({ name, detail: typeof value === "string" ? value : value?.message || value?.detail || "已记录" })) : [];
      const actor = typeof item.actor === "string" ? item.actor : item.actor?.username || item.actor?.name || "CRM 操作者";
      return <article key={`${item.at}-${index}`} className={!current ? "record-expired" : ""}>
        <Icon name={item.result === "issue" ? "alert" : "history"} />
        <div><strong>{item.kind === "preflight" ? "CRM 配置核对" : `${DEFS[item.nodeKey]?.title || "流程"} · 人工实操`} · {results[item.result] || "已记录"}</strong>
          <p>{item.note}</p>
          {checks.length > 0 && <ul className="crm-check-list">{checks.map((check, i) => <li key={i}>{typeof check === "string" ? check : [check.title || check.name || check.key, check.message || check.detail || check.note || check.status].filter(Boolean).join("：")}</li>)}</ul>}
          {item.reference && <p>证据引用：{item.reference}</p>}
          {item.datasetId && <p>数据集 ID：{item.datasetId}</p>}
          <p>{actor} · {dateText(item.at)} · 配置版本 {item.revision} · {current ? "对应当前配置；尚未独立验证" : "配置已变化，此记录已过期"}</p>
        </div>
      </article>;
    })}</div> : <div className="empty-state"><Icon name="history" size={32} /><h3>暂无核对记录</h3><p>从数据源第一项开始，每一拍保存配置或记录实际观察；这里不会自动生成成功结果。</p></div>;
  return <div className={`verification-page crm-records ${!direct?'guide-records':''}`}>
    {direct?<><h2>核对与实操记录</h2>{disclaimer}{preflight}{form}<h3>已有记录</h3>{history}</>:<>
      <header className="guide-records-heading"><div><h2>记录本次实操，保留下一步</h2><p>记录属于本实例和配置版本，不因关闭引导而丢失。</p></div>{hasMetaStartGuide(loop)&&<button type="button" className="button" onClick={()=>navigate('/flow/'+loop.id+'?view=tasks&node='+LOOP_TEST_KEY)}>返回 {guideNumber(loop,LOOP_TEST_KEY)} 闭环测试指引</button>}</header>
      <div className="guide-records-workbench"><div className="guide-records-entry">{form}{preflight}</div><aside className="guide-records-history"><h3>本实例的已有记录</h3>{history}
        <section ref={closeout} id="guide-records-closeout" tabIndex={-1}><h3>收尾还要核对</h3><p>网站提交成功、CRM 单条接收、Meta 两路事件与去重证据、同意控制。任何一项未确认，都不能把整套闭环标为通过。</p><p>临时测试配置由获准人员恢复，审计记录保留。测试通过不等于广告已经发布。</p></section>
      </aside></div>
    </>}
  </div>;
}
