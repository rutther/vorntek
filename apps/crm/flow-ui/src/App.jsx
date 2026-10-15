import React, { useCallback, useEffect, useRef, useState } from "react";
import { Overview } from "./Overview.jsx";
import { Workspace } from "./Workspace.jsx";
import { Configuration } from "./Configuration.jsx";
import {PlanList, PlanWorkspace, ClosedGuide} from './Plans.jsx';
import {guideOrder} from './guidance.mjs';
import {GuideList} from './GuideList.jsx';
import {GuideCreate} from './GuideCreate.jsx';
import { Icon, Modal, Note } from "./ui.jsx";
import {
  readStore,
  writeStore,
  seedLoops,
  createLoop,
  renameLoop,
  duplicateLoop,
  archiveLoop,
  navigationIntent,
} from "./model.mjs";

export function App({ crm = false, initialData = null, api = null, surface = 'guide' }) {
  const plans = crm && surface === 'plans';
  const [initial] = useState(() => {
    if (crm) return { loops: initialData.loops, error: "" };
    try {
      let loops = readStore(localStorage);
      if (!loops) {
        loops = seedLoops();
        writeStore(localStorage, loops);
      }
      return { loops, error: "" };
    } catch (e) {
      return { loops: [], error: e.message };
    }
  });
  const [loops, setLoopState] = useState(initial.loops),
    [route, setRoute] = useState(location.hash.slice(1) || "/loops"),
    [error, setError] = useState(initial.error),
    [toast, setToast] = useState(""),
    [modal, setModal] = useState(null),
    [pending, setPending] = useState(null),
    [mobileNav, setMobileNav] = useState(false),
    [busy, setBusy] = useState(false),
    [conflict, setConflict] = useState(null);
  const busyRef = useRef(false);
  // Sequential saves can finish before React commits another render. Publish
  // accepted snapshots synchronously so the next command uses their version.
  const loopsRef = useRef(initial.loops);
  const setLoops = useCallback(update => {
    const next = typeof update === 'function' ? update(loopsRef.current) : update;
    loopsRef.current = next;
    setLoopState(next);
  }, []);
  const collectionGeneration = useRef(0);
  // Publish only accepted server snapshots, never infer status from a click.
  // Merge the runtime summary alone so unrelated unsaved/configuration state stays intact.
  const receiveRuntimeSnapshot = useCallback((id, snapshot) => {
    collectionGeneration.current += 1;
    setLoops(previous => previous.map(item => {
      if (item.id !== id || snapshot.flowVersion < item.revision) return item;
      const runtime = {
        state: snapshot.blockingChecks?.some(check => check.code === 'runtime_disabled') ? 'unavailable' : snapshot.runtimeState,
        activeRevision: snapshot.activeRevision?.key || null,
        integrationId: snapshot.integration?.id || null,
      };
      return JSON.stringify(item.runtime) === JSON.stringify(runtime) ? item : {...item, runtime};
    }));
  }, []);
  const [collectionState, setCollectionState] = useState({loading:false,error:''});
  const [collectionRetry, setCollectionRetry] = useState(0);
  const [planFilters, setPlanFilters] = useState({search:'',environment:'all',state:'active',page:1});
  const [filters, setFilters] = useState({
    search: "",
    provider: "all",
    preset: "all",
    environment: "all",
    state: "all",
  });
  const guard = useRef(null),
    routeRef = useRef(route),
    skipHash = useRef(false),
    scroll = useRef(0);
  useEffect(() => {
    if (!crm || route.split('?')[0] !== '/loops' || !api?.load || busy) return;
    const generation = ++collectionGeneration.current;
    let cancelled = false;
    setCollectionState({loading:true,error:''});
    Promise.resolve().then(() => api.load()).then(data => {
      if (cancelled || generation !== collectionGeneration.current) return;
      if (!Array.isArray(data?.loops)) throw new Error('invalid collection');
      setLoops(data.loops);
      setCollectionState({loading:false,error:''});
    }).catch(() => {
      if (cancelled || generation !== collectionGeneration.current) return;
      setCollectionState({loading:false,error:'运行状态读取失败，请重试。现有记录保留，暂不显示旧的运行状态。'});
    });
    return () => { cancelled = true; };
  }, [crm,route,api,busy,collectionRetry]);
  useEffect(() => {
    const onHash = () => {
      if (skipHash.current) {
        skipHash.current = false;
        return;
      }
      const target = location.hash.slice(1) || "/loops";
      if (busyRef.current || guard.current?.saving || guard.current?.dirty) {
        skipHash.current = true;
        location.replace("#" + routeRef.current);
        if(guard.current?.saving)setError('观察正在保存，请等待结果后再离开。');
        else if (!busyRef.current) setPending(target);
      } else {
        routeRef.current = target;
        setRoute(target);
      }
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  useEffect(() => {
    if (!crm) return;
    const protect = (event) => {
      if (busyRef.current || guard.current?.saving || guard.current?.dirty) { event.preventDefault(); event.returnValue = ""; }
    };

    const protectShell = event => {
      const link = event.target.closest?.("a[href]");
      const logout = event.type === "submit" && event.target.matches?.("[data-nc-logout-form]");
      if (!logout && (!link || link.target === "_blank" || document.getElementById("root")?.contains(link))) return;
      if (busyRef.current) { event.preventDefault(); return; }
      if(guard.current?.saving){event.preventDefault();setError('观察正在保存，请等待结果后再离开。');return;}
      if (guard.current?.dirty) {
        if (!window.confirm(guard.current?.kind==='observation'?'本项观察尚未保存，确定放弃本次选择并离开吗？离开不会登记为通过。':"当前配置尚未保存，确定放弃修改并离开吗？")) event.preventDefault();
        else guard.current = null;
      }
    };
    window.addEventListener("beforeunload", protect);
    document.addEventListener("click", protectShell, true);
    document.addEventListener("submit", protectShell, true);
    return () => {
      window.removeEventListener("beforeunload", protect);
      document.removeEventListener("click", protectShell, true);
      document.removeEventListener("submit", protectShell, true);
    };
  }, [crm]);
  useEffect(() => {
    if (toast) {
      const id = setTimeout(() => setToast(""), 4500);
      return () => clearTimeout(id);
    }
  }, [toast]);
  useEffect(() => {
    if (!error) return;
    requestAnimationFrame(() => {
      const notice = document.querySelector("dialog[open] .global-error") || document.querySelector("#root .global-error");
      notice?.focus();
    });
  }, [error]);
  useEffect(() => {
    if (route === "/loops")
      requestAnimationFrame(() => window.scrollTo(0, scroll.current));
    else window.scrollTo(0, 0);
  }, [route]);
  const go = (target) => {
    if (busyRef.current) return;
    if(guard.current?.saving){setError('观察正在保存，请等待结果后再离开。');return;}
    const intent = navigationIntent(
      routeRef.current,
      target,
      guard.current?.dirty,
    );
    if (intent === "stay") return;
    if (route.startsWith("/loops")) scroll.current = window.scrollY;
    if (intent === "confirm") {
      setPending(target);
      return;
    }
    routeRef.current = target;
    setRoute(target);
    location.hash = target;
    setMobileNav(false);
  };
  // Task panels stay mounted: changing the selected step cannot discard edits.
  // Replace only this guide's URL, leaving normal navigation behind the dirty guard.
  const rememberGuideStep = (key) => {
    const currentPath = routeRef.current.split('?')[0].split('/').filter(Boolean);
    const currentLoop = loops.find(item => item.id === currentPath[1]);
    if (!crm || plans || currentPath[0] !== 'flow' || !currentLoop || !(guideOrder(currentLoop).includes(key)||key==='illustrations')) return;
    const target = `/flow/${currentLoop.id}?view=tasks&node=${key}`;
    if (routeRef.current === target) return;
    window.history.replaceState(null, '', '#' + target);
    routeRef.current = target;
    setRoute(target);
  };
  const persist = (next) => {
    if (initial.error) {
      setError(initial.error);
      return false;
    }
    try {
      writeStore(localStorage, next);
      setLoops(next);
      setError("");
      return true;
    } catch (e) {
      setError(e.message);
      return false;
    }
  };
  const command = async (action, target, values = {}, message = "已保存到 CRM") => {
    if (busyRef.current) return false;
    // A list read started before this write must not replace its newer result.
    collectionGeneration.current += 1;
    busyRef.current = true;
    setBusy(true);
    setError("");
    try {
      const { loop: saved } = await api.command(action, target, values);
      setLoops((previous) => previous.some((item) => item.id === saved.id)
        ? previous.map((item) => item.id === saved.id ? saved : item)
        : [saved, ...previous]);
      if (action === "duplicate") {
        // The source also receives a command receipt. Refresh its version while
        // keeping an already-created copy successful if this read is unavailable.
        try {
          const refreshed = await api.load();
          if (Array.isArray(refreshed.loops)) setLoops(refreshed.loops);
        } catch { /* Subsequent stale writes use the normal conflict recovery. */ }
      }
      setConflict(null);
      if (message) setToast(message);
      return saved;
    } catch (e) {
      setError(e.status === 409 ? "这条流程已被其他页面更新。当前输入已保留，请加载最新版本后核对再保存。" : e.message);
      if (e.status === 409 && e.current) setConflict(e.current);
      return false;
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };
  const update = async (loop, message = crm ? "已保存到 CRM" : "已保存到此浏览器", change) => {
    if (crm) {
      if (!change) { setError("当前操作缺少保存命令，请刷新页面后重试。"); return false; }
      const { action, ...values } = change;
      const current = loopsRef.current.find((item) => item.id === loop.id);
      return command(action, current, values, message);
    }
    const next = loopsRef.current.map((l) => (l.id === loop.id ? loop : l));
    if (persist(next)) {
      if (message) setToast(message);
      return true;
    }
    return false;
  };
  const action = (type, loop) => { if (!busyRef.current) {
    if(type==='create'&&crm&&!plans){go('/new');return;}
    if(['close_guide','archive','restore'].includes(type)&&guard.current?.dirty) {setError('当前有未保存修改，请先保存或取消修改，再进行生命周期操作。');return;}
    setError(""); setModal({ type, loop });
  } };
  const parts = route.split("?")[0].split("/").filter(Boolean),
    loop = parts[0] === "flow" ? loops.find((l) => l.id === parts[1]) : null;
  const view =
      new URLSearchParams(route.split("?")[1] || "").get("view") || "design",
    node = new URLSearchParams(route.split("?")[1] || "").get("node");
  const closePending = () => setPending(null),
    leave = async (save) => {
      if (busyRef.current) return;
      if(guard.current?.saving){setError('观察正在保存，请等待结果后再离开。');return;}
      if (save && !(await guard.current?.save())) return;
      guard.current = null;
      const target = pending;
      setPending(null);
      go(target);
    };
  const loadLatest = () => {
    if (!conflict || busyRef.current) return;
    setLoops((previous) => previous.map((item) => item.id === conflict.id ? conflict : item));
    setModal((previous) => previous?.loop?.id === conflict.id ? { ...previous, loop: conflict } : previous);
    setConflict(null);
    setError("");
    setToast("已加载最新版本。当前输入仍保留，请核对后重新保存。");
  };
  const feedback = error && <div className="global-error" role="alert" tabIndex={-1}><Icon name="alert" /><div>{error}{conflict && <button className="button small" disabled={busy} onClick={loadLatest}>加载最新版本，保留当前输入</button>}</div></div>;
  const Main = crm ? "div" : "main";
  return (
    <div className={`app-shell ${crm ? "crm-mode" : ""}${crm&&!plans?' guide-mode':''}`} aria-busy={busy}>
      {!crm && <aside className={`global-sidebar ${mobileNav ? "mobile-open" : ""}`}>
        <a
          className="brand"
          href="#/loops"
          onClick={(e) => {
            e.preventDefault();
            go("/loops");
          }}
        >
          <img src="/static/console/vorntekLogo.png" alt="" />
          <span>
            <strong>Vorntek</strong>
            <small>获客与销售运营</small>
          </span>
        </a>
        <nav aria-label="主导航">
          <button disabled title="本原型只包含引导式">
            <Icon name="dashboard" />
            工作台
          </button>
          <div className="nav-label">
            <Icon name="marketing" />
            营销
          </div>
          <button className="selected" onClick={() => go("/loops")}>
            <Icon name="flow" />
            引导式
          </button>
          <button disabled title="不在本次原型范围">
            <Icon name="tasks" />
            事件运营
          </button>
          <button disabled title="不在本次原型范围">
            <Icon name="form" />
            获客表单
          </button>
          <button disabled title="不在本次原型范围">
            <Icon name="chart" />
            平台与归因
          </button>
        </nav>
        <div className="workspace-label">
          <span className="avatar">V</span>
          <div>
            Vorntek<small>营销工作空间</small>
          </div>
        </div>
      </aside>}
      {!crm && mobileNav && (
        <button
          aria-label="关闭导航遮罩"
          className="nav-backdrop"
          onClick={() => setMobileNav(false)}
        />
      )}
      <div className="app-body">
        {!(crm&&!plans&&((loop&&(!loop.guideClosed||(loop.archived&&view==='verification')))||parts[0]==='loops'||parts[0]==='new'))&&<header className="global-header">
          {!crm && <button
            className="icon-button mobile-menu"
            aria-label="打开导航"
            onClick={() => setMobileNav(!mobileNav)}
          >
            <Icon name="menu" />
          </button>}
          <div className="breadcrumb">
            {!crm && <><span>营销</span><Icon name="chevron" size={16} /></>}
            <button onClick={() => go("/loops")}>{plans ? '方案列表' : crm ? "流程总览" : "引导式"}</button>
            {loop && (
              <>
                <Icon name="chevron" size={16} />
                <button
                  className="crumb-current"
                  onClick={() => go(`/flow/${loop.id}`)}
                >
                  {loop.name}
                </button>
                <button
                  className="icon-button"
                  aria-label="重命名当前流程"
                  disabled={loop.archived || busy}
                  onClick={() => action("rename", loop)}
                >
                  <Icon name="pencil" size={17} />
                </button>
              </>
            )}
          </div>
          {!crm && <div className="header-right">
            <span className="prototype-label">交互原型 · 本地保存</span>
            <span className="avatar small-avatar">P</span>
          </div>}
          {crm && <span className="crm-save-status" role="status">{busy ? "正在保存，请稍候…" : "保存与运行应用分别管理"}</span>}
        </header>}
        {feedback}
        <Main id={crm ? "flow-content" : "main-content"}>
          {crm&&!plans&&parts[0]==='new'?<GuideCreate guard={guard} busy={busy} onCancel={()=>go('/loops')} onCreate={async values=>{
            const saved=await command('create',null,values,'闭环草稿已创建，尚未启用');
            if(!saved)return false;
            guard.current=null;
            go(`/flow/${saved.id}?view=tasks&node=meta-start`);
            return true;
          }}/>:parts[0] === "flow" ? (
            loop ? (
              plans ? <PlanWorkspace key={loop.id} loop={loop} view={view} node={node} update={update} navigate={go} guard={guard} api={api} busy={busy} onAction={action} canManage={initialData?.canManageRuntime} onRuntimeSnapshot={receiveRuntimeSnapshot}/> : crm&&loop.guideClosed&&!(loop.archived&&view==='verification') ? <ClosedGuide loop={loop} onAction={action} busy={busy}/> : parts[2] === "config" ? (
                <Workspace
                  key={loop.id}
                  loop={loop}
                  selectedKey={parts[3]}
                  view="tasks"
                  loops={loops}
                  onAction={action}
                  navigate={go}
                  update={update}
                  guard={guard}
                  crm={crm}
                  api={api}
                  busy={busy}
                  onStepChange={rememberGuideStep}
                  onRuntimeSnapshot={receiveRuntimeSnapshot}
                />
              ) : (
                <Workspace
                  key={loop.id}
                  loop={loop}
                  loops={loops}
                  selectedKey={node}
                  view={view}
                  navigate={go}
                  update={update}
                  onAction={action}
                  feedback={feedback}
                  crm={crm}
                  busy={busy}
                  guard={guard}
                  api={api}
                  onStepChange={rememberGuideStep}
                  onRuntimeSnapshot={receiveRuntimeSnapshot}
                />
              )
            ) : (
              <div className="empty-state">
                <h1>找不到这条流程</h1>
                <p>{crm ? "这条流程不存在，或当前账号没有访问权限。" : "可能来自其他浏览器或旧原型地址。"}</p>
                <button className="button primary" onClick={() => go("/loops")}>
                  返回流程总览
                </button>
              </div>
            )
          ) : (
            crm ? plans ? <PlanList loops={loops} navigate={go} onAction={action} legacyLoops={initialData?.legacyLoops||[]} filters={planFilters} setFilters={setPlanFilters} collectionState={collectionState} onRetry={()=>setCollectionRetry(value=>value+1)}/> : <GuideList loops={loops} navigate={go} onAction={action} legacyLoops={initialData?.legacyLoops||[]} filters={planFilters} setFilters={setPlanFilters} collectionState={collectionState} onRetry={()=>setCollectionRetry(value=>value+1)}/> : <Overview
              loops={loops}
              filters={filters}
              setFilters={setFilters}
              navigate={go}
              onAction={action}
              templates={parts[0] === "templates"}
              crm={crm}
              legacyLoops={initialData?.legacyLoops || []}
            />
          )}
        </Main>
      </div>
      {toast && (
        <div className="toast" role="status">
          <Icon name="check" />
          {toast}
        </div>
      )}
      {modal && (
        <ManagementModal
          type={modal.type}
          loop={modal.loop}
          crm={crm}
          plans={plans}
          busy={busy}
          feedback={feedback}
          onClose={() => { if (!busyRef.current) setModal(null); }}
          onSubmit={async (values) => {
            try {
              if (crm) {
                const type = modal.type === "copy" ? "duplicate" : modal.type;
                const payload = type === "create" ? values : type === "rename" ? { name: values.name } : {};
                const saved = await command(type, modal.loop, payload,
                  type === "create" ? "方案草稿已创建，尚未启用" : type==='close_guide' ? '引导已关闭，方案运行不变' : type==='reopen_guide' ? '引导已重新打开' : "方案已保存到 CRM");
                if (!saved) return false;
                setModal(null);
                if (["create", "duplicate"].includes(type)) go(`/flow/${saved.id}?view=${plans?'config':'tasks'}&node=dataset`);
                if (type === "archive") go("/loops");
                return true;
              }
              if (modal.type === "create") {
                const created = createLoop(values);
                if (persist([created, ...loops])) {
                  setModal(null);
                  setToast("草稿已创建，请从下一项配置开始");
                  go(`/flow/${created.id}?node=dataset`);
                }
              } else if (modal.type === "rename") {
                if (
                  await update(renameLoop(modal.loop, values.name), "流程名称已更新")
                )
                  setModal(null);
              } else if (modal.type === "copy") {
                const copied = duplicateLoop(modal.loop);
                if (persist([copied, ...loops])) {
                  setModal(null);
                  setToast("已复制为独立草稿，请重新确认资产与配置");
                  go(`/flow/${copied.id}?node=dataset`);
                }
              } else {
                if (
                  await update(
                    archiveLoop(modal.loop, modal.type === "archive"),
                    modal.type === "archive"
                      ? "已归档；未操作任何外部资产"
                      : "流程已恢复",
                  )
                ) {
                  setModal(null);
                  if (modal.type === "archive") go("/loops");
                }
              }
            } catch (e) {
              throw e;
            }
          }}
        />
      )}
      {pending && (
        <Modal title={guard.current?.kind==='observation'?'观察尚未保存':'配置尚未保存'} onClose={() => { if (!busyRef.current) closePending(); }}>
          <div className="modal-body">
            <p>{guard.current?.kind==='observation'?'本项观察尚未保存。请继续编辑，在核对区明确点击「保存本项观察」；也可放弃未保存修改后离开。离开不会自动登记为已接收或已去重。':guard.current?.kind==='create'?'新闭环尚未创建。离开将丢弃名称、环境和备注；也可以继续填写。':guard.current?.kind === "evidence" ? "离开前，可以保存当前实操记录。人工记录不代表独立验证。" : "离开前，可以把当前输入保存为草稿。草稿不计为配置完成。"}</p>
          </div>
          <div className="modal-footer">
            <button className="button" disabled={busy} onClick={closePending}>
              继续编辑
            </button>
            <button className="button" disabled={busy} onClick={() => leave(false)}>
              放弃修改
            </button>
            {!['create','observation'].includes(guard.current?.kind)&&<button className="button primary" disabled={busy} onClick={() => leave(true)}>
              {guard.current?.kind === "evidence" ? "保存记录并离开" : "保存草稿并离开"}
            </button>}
          </div>
          {feedback}
        </Modal>
      )}
    </div>
  );
}
function ManagementModal({ type, loop, onClose, onSubmit, crm, busy, feedback, plans }) {
  const [name, setName] = useState(loop?.name || ""),
    [environment, setEnvironment] = useState(crm ? "" : "production"),
    [error, setError] = useState("");
  const title = {
    create: plans ? "新建方案" : "新建搭建任务",
    rename: crm ? "重命名方案" : "重命名流程",
    copy: "复制为草稿",
    archive: crm ? "归档方案" : "归档流程",
    restore: crm ? "恢复方案" : "恢复流程",
    close_guide: '关闭引导',
    reopen_guide: '重新打开引导',
  }[type];
  const submit = async (e) => {
    e.preventDefault();
    if (busy) return;
    try {
      await onSubmit({ name, environment });
    } catch (ex) {
      setError(ex.message);
    }
  };
  return (
    <Modal title={title} onClose={onClose}>
      <form onSubmit={submit}>
        <div className="modal-body">
          {type === "create" && (
            <>
              <div className="context-path">
                <Icon name="meta" />
                <strong>Meta</strong>
                <Icon name="chevron" size={16} />
                <span>网站获客</span>
              </div>
              <p className="subtle">
                {plans?'创建长期维护的方案草稿，随后配置数据集与网站范围。':'创建网站获客方案，并打开逐步搭建指引。'}
              </p>
            </>
          )}
          {["create", "rename"].includes(type) ? (
            <>
              <label className="field">
                {crm?'方案名称':'流程名称'} <span className="required">* 必填</span>
                <input
                  autoFocus
                  required
                  minLength={2}
                  maxLength={200}
                  disabled={busy}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="例如：官网项目咨询"
                />
              </label>
              {type === "create" && (
                <label className="field">
                  运行环境 <span className="required">* 必填</span>
                  <select
                    value={environment}
                    disabled={busy}
                    required
                    onChange={(e) => setEnvironment(e.target.value)}
                  >
                    {crm && <option value="">请选择用途</option>}
                    <option value="production">正式业务</option>
                    <option value="test">演练测试</option>
                  </select>
                  <small>
                    {crm ? "这标记流程用途，保存草稿不会启用广告或事件回传。" : "这里仅作环境标记。本原型不会向正式系统写入任何数据。"}
                  </small>
                </label>
              )}
            </>
          ) : (
            <p>
              {type === "copy"
                ? `将「${loop.name}」的结构复制成新草稿。不会复制数据集 ID、配置值或验证结果。`
                : type==='close_guide' ? `关闭「${loop.name}」的引导任务，保留进度和配置。不暂停方案、不改变运行版本，也不代表实测完成。`
                : type==='reopen_guide' ? `重新打开「${loop.name}」的原有引导，沿用同一方案和配置，不创建副本。`
                : type === "archive"
                  ? crm ? `归档「${loop.name}」将停止本方案的事件选择，保留历史证据。共享接入和其他方案不变；已固定路由的历史事件不会撤回，广告不受此操作控制。` : `归档「${loop.name}」后可从“已归档”恢复。这不会停止广告或关闭真实事件回传。`
                  : crm ? `恢复「${loop.name}」及其已有配置。共享接入已开启且本方案未暂停时，已有运行版本可能恢复生效。恢复不会重放历史事件。`
                  : `恢复「${loop.name}」及其已有配置。`}
            </p>
          )}
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          {type === "create" && (
            <Note>{crm ? "只在 CRM 创建流程草稿，不创建 Meta 数据集、不发布广告。" : "只创建本地草稿，不创建 Meta 数据集、不发布广告。"}</Note>
          )}
          {feedback}
        </div>
        <div className="modal-footer">
          <button type="button" className="button" disabled={busy} onClick={onClose}>
            取消
          </button>
          <button type="submit" className="button primary" disabled={busy}>
            {type === "create"
              ? plans ? '创建方案' : "创建并打开引导"
              : type === "rename"
                ? "保存名称"
                : title}
            {type === "create" && <Icon name="right" />}
          </button>
        </div>
      </form>
    </Modal>
  );
}
