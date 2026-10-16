import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  ControlButton,
  Handle,
  Position,
  MarkerType,
  applyNodeChanges,
  BaseEdge,
  getSmoothStepPath,
} from "@xyflow/react";
import {
  DEFS,
  DOMAIN,
  tasks,
  summary,
  nodeState,
  toggleFilter,
  simulateCheck,
  moveNode,
} from "./model.mjs";
import { Icon, Badge, Note, Modal, dateText } from "./ui.jsx";
import { CrmRecords } from "./CrmRecords.jsx";
import { Guidance } from "./Guidance.jsx";
import {guideNumber} from './guidance.mjs';
import { observeCanvasSize } from "./canvasResize.mjs";
import {GuideInstanceHeader} from './GuideInstanceHeader.jsx';
import {flowLayout,flowConnections} from './flowLayout.mjs';

const laneWidth = 270;
const statusText = {
  pending: "待配置",
  draft: "草稿未完成",
  saved: "已保存 · 待验证",
  failed: "模拟异常",
  issue: "实操发现问题",
};
function FlowNode({ id, data, selected }) {
  const def = DEFS[id];
  if (id === "retain")
    return (
      <div className="retain-node">
        <Handle type="target" position={Position.Top} id="top" />
        <Icon name="form" size={18} />
        <span>保留线索</span>
      </div>
    );
  return (
    <div
      className={`workflow-node domain-${def.domain} ${selected ? "is-selected" : ""} ${id === "filter" ? "filter-node" : ""}`}
    >
      {[
        ["left", Position.Left],
        ["right", Position.Right],
        ["top", Position.Top],
        ["bottom", Position.Bottom],
      ].map(([key, position]) => (
        <React.Fragment key={key}>
          <Handle type="target" position={position} id={key} />
          <Handle type="source" position={position} id={key} />
        </React.Fragment>
      ))}
      {id==='website'&&data.browserBranch&&<Handle type="source" position={Position.Bottom} id="browser-bottom" style={{left:'22%'}}/>}
      <Icon name={def.icon} size={26} />
      <div className="node-copy">
        <strong>{id === "filter" ? "有效询盘？" : def.title}</strong>
        <span className="domain-tag">{DOMAIN[def.domain].short}</span>
        <small>{statusText[data.state]}</small>
      </div>
    </div>
  );
}
function Lane({ data }) {
  return (
    <div className={`lane lane-${data.domain}`}>
      <div className="lane-heading">
        <Icon
          name={
            data.domain === "meta"
              ? "meta"
              : data.domain === "website"
                ? "world"
                : "users"
          }
          size={23}
        />
        <strong>{DOMAIN[data.domain].label}</strong>
      </div>
    </div>
  );
}
const nodeTypes = { flow: FlowNode, lane: Lane };
function RoutedEdge(props) {
  let [path, labelX, labelY] = getSmoothStepPath({
    ...props,
    borderRadius: 9,
    offset: 22,
  });
  const bridge=props.data?.bridge;
  if(Number.isFinite(props.data?.browserBranchLeft)){
    const left=props.data.browserBranchLeft;
    path=`M ${props.sourceX} ${props.sourceY} V ${props.sourceY+22} H ${left} V ${props.targetY-22} H ${props.targetX} V ${props.targetY}`;
    labelX=left;labelY=(props.sourceY+props.targetY)/2;
  }
  if(bridge&&Math.abs(props.sourceY-props.targetY)<1&&bridge.x>props.sourceX+8&&bridge.x<props.targetX-8&&props.sourceY>bridge.top&&props.sourceY<bridge.bottom){
    const y=props.sourceY,x=bridge.x;
    path=`M ${props.sourceX} ${y} L ${x-7} ${y} Q ${x} ${y-15} ${x+7} ${y} L ${props.targetX} ${props.targetY}`;
    labelY=y-19;
  }
  // A bridge and white underlay separate intersecting but unrelated event paths.
  return (
    <>
      <path d={path} fill="none" stroke="#fbfcfe" strokeWidth={6} />
      <BaseEdge
        id={props.id}
        path={path}
        label={props.label}
        labelX={labelX}
        labelY={labelY}
        labelStyle={props.labelStyle}
        labelBgStyle={props.labelBgStyle}
        labelBgPadding={props.labelBgPadding}
        style={props.style}
        markerEnd={props.markerEnd}
      />
    </>
  );
}
const edgeTypes = { routed: RoutedEdge };
function graphNodes(loop, selected, guide=false) {
  const {positions:layout,laneHeight}=flowLayout(loop,guide);
  const groups = ["meta", "website", "crm"].map((domain, i) => ({
    id: `lane-${domain}`,
    type: "lane",
    position: { x: i * laneWidth, y: 0 },
    data: { domain },
    style: { width: laneWidth, height: laneHeight },
    draggable: false,
    selectable: false,
    focusable: false,
    zIndex: -1,
  }));
  const nodes = Object.keys(DEFS)
    .filter((key) => key !== "filter" || loop.filterEnabled)
    .map((key) => ({
      id: key,
      type: "flow",
      data: { state: nodeState(loop, key), browserBranch:guide&&!loop.filterEnabled },
      parentId: `lane-${DEFS[key].domain}`,
      extent: "parent",
      position: loop.positions[key] || layout[key],
      selected: key === selected,
      draggable: !loop.archived,
      ariaLabel: `${DEFS[key].title}，${DOMAIN[DEFS[key].domain].label}，${statusText[nodeState(loop, key)]}`,
    }));
  if (loop.filterEnabled)
    nodes.push({
      id: "retain",
      type: "flow",
      data: {},
      parentId: "lane-crm",
      extent: "parent",
      position: layout.retain,
      draggable: false,
      selectable: false,
      focusable: false,
    });
  return [...groups, ...nodes];
}

export function Workspace({
  loop,
  loops,
  selectedKey,
  view,
  navigate,
  update,
  onAction,
  crm = false,
  api = null,
  busy = false,
  guard,
  feedback,
  onStepChange,
  onRuntimeSnapshot,
}) {
  const NodePicker = crm ? 'details' : 'div';
  const [selected, setSelected] = useState(
      tasks(loop).includes(selectedKey) ? selectedKey : "server",
    ),
    [inspector, setInspector] = useState(true),
    [addOpen, setAddOpen] = useState(false),
    [rf, setRf] = useState(null),
    [checkChoice, setCheckChoice] = useState("pass"),
    [checkError, setCheckError] = useState("");
  const [nodes, setNodes] = useState(() => graphNodes(loop, selected, crm));
  const {positions:layout}=flowLayout(loop,crm);
  const canvasRef = useRef(null);
  useEffect(() => {
    if (!rf || view !== "design") return;
    return observeCanvasSize(canvasRef.current, () => rf.fitView({ padding: 0.07 }));
  }, [rf, view]);
  useEffect(() => setNodes(graphNodes(loop, selected, crm)), [loop, selected, crm]);
  useEffect(() => { if (tasks(loop).includes(selectedKey)) setSelected(selectedKey); }, [selectedKey]);
  const s = summary(loop),
    def = DEFS[selected] || DEFS.server,
    order = tasks(loop);
  const edges = useMemo(
    () =>
      flowConnections(loop,crm).map((e, i) => ({
        ...e,
        id: `edge-${e.source}-${e.target}`,
        sourceHandle: e.from,
        targetHandle: e.to,
        type: "routed",
        zIndex: e.source === "form" ? 2 : 0,
        data:e.data||(e.source==='form'&&(!crm||loop.filterEnabled)?{bridge:{x:270+Math.max(loop.positions.website?.x??layout.website.x,loop.positions.pixel?.x??layout.pixel.x)+207,top:(loop.positions.website?.y??layout.website.y)+45,bottom:(loop.positions.pixel?.y??layout.pixel.y)+45}}:undefined),
        markerEnd: {
          type: MarkerType.ArrowClosed,
          width: 16,
          height: 16,
          color: "#56647b",
        },
        style: { stroke: "#64748b", strokeWidth: 1.35 },
        labelStyle: { fill: "#334155", fontSize: 12, fontWeight: 500 },
        labelBgStyle: { fill: "#fbfcfe", fillOpacity: 0.95 },
        labelBgPadding: [5, 3],
        pathOptions: { borderRadius: 9, offset: i === 2 ? 22 : 16 },
      })),
    [loop.filterEnabled,loop.positions,crm],
  );
  const changeView = (next) =>
    navigate(`/flow/${loop.id}?view=${next}&node=${selected}`);
  const openConfig = (key) => navigate(crm?`/flow/${loop.id}?view=tasks&node=${key}`:`/flow/${loop.id}/config/${key}`);
  const selectNode = (key) => {
    if (DEFS[key]) {
      setSelected(key);
      setInspector(true);
      if (window.matchMedia("(max-width:760px)").matches)
        requestAnimationFrame(() =>
          document
            .querySelector(".node-inspector")
            ?.scrollIntoView({ block: "start" }),
        );
    }
  };
  const changeNodes = async (changes) => {
    if (busy && changes.some((change) => change.type === "position")) return;
    setNodes((ns) => applyNodeChanges(changes, ns));
    const committed = changes.filter(
      (c) =>
        c.type === "position" &&
        c.position &&
        c.dragging !== true &&
        DEFS[c.id],
    );
    if (committed.length && !loop.archived) {
      const next = committed.reduce(
        (current, c) => moveNode(current, c.id, c.position),
        loop,
      );
      if (!(await update(next, "", { action: "positions", positions: next.positions }))) setNodes(graphNodes(loop, selected, crm));
    }
  };
  return (
    <section className="workflow-page">
      {crm?<GuideInstanceHeader loop={loop} loops={loops} done={s.done} total={s.total} busy={busy} navigate={navigate} onAction={onAction}/>:<div className="context-toolbar">
        <button className="back-link" onClick={() => navigate("/loops")}>
          <Icon name="left" size={17} />
          流程总览
        </button>
        <div className="context-static">
          <Icon name="meta" />
          Meta
        </div>
        <div className="context-static">
          <Icon name="world" />
          网站获客
        </div>
        <select
          aria-label="切换流程"
          value={loop.id}
          disabled={busy}
          onChange={(e) => navigate(`/flow/${e.target.value}`)}
        >
          {loops
            .filter((l) => !l.archived || l.id === loop.id)
            .map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
              </option>
            ))}
        </select>
        <Badge state={s.state} />
        <button
          className="button primary context-new"
          onClick={() => onAction("create")}
        >
          <Icon name="plus" />
          新建流程
        </button>
      </div>}
      <div className="workflow-tabs">
        <nav aria-label="流程工作区视图">
          {[
            ["design", "流程全览"],
            ["tasks", "配置任务"],
            ["verification", crm ? "核对与实操记录" : "验证记录"],
          ].map(([key, label]) => (
            <button
              key={key}
              className={view === key ? "active" : ""}
              onClick={() => changeView(key)}
            >
              {label}
              {key === "tasks" && !crm && (
                <small>
                  配置已保存 {s.done}/{s.total}
                </small>
              )}
            </button>
          ))}
        </nav>
        <span className="saved-label">
          <Icon name="check" size={15} />
          {crm ? (busy ? "正在保存…" : `已载入 CRM · 版本 ${loop.version}`) : "已保存到本机"}
        </span>
        {view==='design'&&<button
          className="icon-button"
          aria-label={inspector ? "收起节点摘要" : "展开节点摘要"}
          onClick={() => setInspector(!inspector)}
        >
          <Icon name={inspector ? "collapse" : "expand"} />
        </button>}
      </div>
      {loop.archived && (
        <Note warning>
          这条流程已归档。可以查看，但恢复前不能修改。
          <button
            className="text-button"
            onClick={() => onAction("restore", loop)}
          >
            恢复流程
          </button>
        </Note>
      )}
      {view === "design" ? (
        <div className={`workflow-body ${inspector ? "" : "inspector-hidden"}`}>
          <div className="canvas-wrap">
            <div className="canvas-toolbar">
              <button
                className="button small"
                disabled={loop.archived || busy}
                onClick={() => setAddOpen(true)}
              >
                <Icon name="plus" size={17} />
                {crm?'添加拟议节点':'添加节点'}
              </button>
              {crm&&<div className="guide-canvas-controls" aria-label="画布视图操作">
                <button className="text-button" aria-label="缩小画布" disabled={!rf} onClick={()=>rf.zoomOut()}>−</button>
                <button className="text-button" aria-label="放大画布" disabled={!rf} onClick={()=>rf.zoomIn()}>+</button>
                <button className="text-button" disabled={!rf} onClick={()=>rf.fitView({padding:0.07})}>适应画布</button>
                <button className="text-button" disabled={!rf||busy||loop.archived} onClick={async()=>{if(await update({...loop,positions:{}},'已恢复默认布局',{action:'positions',positions:{}}))setTimeout(()=>rf.fitView({padding:0.07}),30);}}>恢复布局</button>
              </div>}
              <div className="next-task">
                {s.done < s.total ? (
                  <>
                    <span>下一项</span>
                    <button onClick={() => openConfig(s.next)}>
                      {guideNumber(loop, s.next)}{" "}
                      {DEFS[s.next].title}
                      <Icon name="right" size={15} />
                    </button>
                  </>
                ) : (
                  <button onClick={() => changeView("verification")}>
                    {crm ? "查看核对记录" : "查看模拟验证"}
                    <Icon name="right" size={15} />
                  </button>
                )}
              </div>
            </div>
            {crm && <p className="guide-canvas-scroll-hint">左右滚动查看三条泳道；使用 + / − 缩放，或展开下方节点选择。仅查看不会保存布局。</p>}
            <div className="flow-canvas" ref={canvasRef} role={crm ? 'region' : undefined} aria-label={crm ? '三系统连接图，可横向滚动' : undefined} tabIndex={crm ? 0 : undefined}>
              <ReactFlow
                nodes={nodes}
                edges={edges}
                nodeTypes={nodeTypes}
                edgeTypes={edgeTypes}
                onInit={setRf}
                onNodesChange={changeNodes}
                onNodeClick={(_, node) => selectNode(node.id)}
                onNodeDoubleClick={(_, node) => {
                  if (DEFS[node.id]) openConfig(node.id);
                }}
                nodesConnectable={false}
                nodesDraggable={!busy && !loop.archived}
                ariaLabelConfig={{
                  "controls.ariaLabel": "画布视图操作",
                  "controls.zoomIn.ariaLabel": "放大画布",
                  "controls.zoomOut.ariaLabel": "缩小画布",
                  "controls.fitView.ariaLabel": "适应画布",
                }}
                deleteKeyCode={null}
                fitView
                fitViewOptions={{ padding: 0.07 }}
                minZoom={0.55}
                maxZoom={1.5}
                zoomOnDoubleClick={false}
                aria-label="三系统泳道流程画布"
              >
                <Background color="#dce4ee" gap={20} size={1} />
                {!crm&&<Controls showInteractive={false}>
                  <ControlButton
                    title="恢复节点布局"
                    disabled={loop.archived || busy}
                    onClick={async () => {
                      if (await update({ ...loop, positions: {} }, "已恢复默认布局", { action: "positions", positions: {} }))
                        setTimeout(() => rf?.fitView({ padding: 0.07 }), 30);
                    }}
                  >
                    <Icon name="settings" size={16} />
                  </ControlButton>
                </Controls>}
              </ReactFlow>
            </div>
            <NodePicker className={crm ? 'guide-node-picker' : undefined}>
            {crm && <summary>按名称选择节点</summary>}
            <div className="mobile-node-list">
              {order.map((key, i) => (
                <button
                  key={key}
                  className={selected === key ? "selected" : ""}
                  onClick={() => selectNode(key)}
                >
                  <span>{guideNumber(loop, key)}</span>
                  <Icon name={DEFS[key].icon} />
                  <strong>{DEFS[key].title}</strong>
                  <small>{DOMAIN[DEFS[key].domain].short}</small>
                </button>
              ))}
            </div>
            </NodePicker>
            <footer className="canvas-footer">
              <span>节点只能在所属域内移动 · 基础连接受保护</span>
              <span>{crm ? "CRM 配置记录 · 外部运行待核验" : "流程草稿 · 未连接真实系统"}</span>
            </footer>
          </div>
          {inspector && (
            <aside className="node-inspector">
              <div className="inspector-title">
                <h2>{def.title}</h2>
                <span className={`domain-tag domain-${def.domain}`}>
                  {DOMAIN[def.domain].label}
                </span>
              </div>
              <p className="subtle small">当前选中节点 · {DOMAIN[def.domain].label}</p>
              <div className="inspector-block">
                <h3>配置状态</h3>
                <span className={`node-state ${nodeState(loop, selected)}`}>
                  <span className="status-dot" />
                  {statusText[nodeState(loop, selected)]}
                </span>
              </div>
              <section className="inspector-protocol" aria-label="节点输入与输出">
                <h3>输入与输出</h3>
                <dl>
                  <div><dt>输入</dt><dd>{def.input}</dd></div>
                  <div><dt>输出</dt><dd>{def.output}</dd></div>
                  {selected === 'server' && <>
                    <div><dt>事件</dt><dd>{loop.configs?.server?.event || '尚未保存'}</dd></div>
                    <div><dt>去重约定</dt><dd>{loop.configs?.server?.dedup === true ? '两路使用同一事件编号' : loop.configs?.server?.dedup === false ? '尚未约定两路同一事件编号' : '尚未保存'}</dd></div>
                  </>}
                </dl>
                {selected === 'server' && <p className="small subtle">已保存的约定不代表 Meta 接收或去重通过。实际投递由 CRM 接入控制，运行版本与人工观察分别核对。</p>}
              </section>
              <hr />
              {selected !== 'server' && <div className="inspector-description">
                <h3>节点说明</h3>
                <p>{def.description}</p>
                {crm && <p>操作位置：{def.where}</p>}
              </div>}
              <button
                className="button primary full"
                disabled={loop.archived || busy}
                onClick={() => openConfig(selected)}
              >
                {selected === 'server' ? '查看回传配置' : '打开配置页'}
                <Icon name="right" />
              </button>
              {!crm && <p className="small subtle">在独立页面按步骤配置，保存后返回此节点。</p>}
              <hr />
              <button
                className="text-button"
                onClick={() => changeView("tasks")}
              >
                <Icon name="tasks" />
                查看节点待办
                <Icon name="chevron" />
              </button>
              {def.optional && (
                <button
                  className="text-button danger optional-remove"
                  disabled={loop.archived || busy}
                  onClick={async () => {
                    if (
                      await update(
                        toggleFilter(loop, false),
                        crm ? "拟议筛选节点已移除，现有回传规则未改变" : "筛选节点已移除，基础回传连接已恢复",
                        { action: "filter", enabled: false },
                      )
                    )
                      setSelected("server");
                  }}
                >
                  <Icon name="trash" size={17} />
                  移除可选筛选
                </button>
              )}
            </aside>
          )}
        </div>
      ) : view === "tasks" ? (
        <Guidance loop={loop} selectedKey={selectedKey} update={update} navigate={navigate} guard={guard} crm={crm} busy={busy} api={api} onStepChange={onStepChange} onRuntimeSnapshot={onRuntimeSnapshot}/>
      ) : crm ? (
        <CrmRecords loop={loop} update={update} busy={busy} guard={guard} selectedKey={selectedKey || selected} navigate={navigate} />
      ) : (
        <div className="verification-page">
          <h2>验证记录</h2>
          <p className="subtle">
            配置已保存 {s.done}/{s.total}{" "}
            项。以下仅演示结果展示和失败恢复，不发送真实请求。
          </p>
          <Note warning>
            此原型不能证明 Pixel、CAPI 或 Meta
            已接通。模拟通过不会标记为“运行中”。
          </Note>
          <div className="simulate-toolbar">
            <label>
              模拟结果
              <select
                value={checkChoice}
                onChange={(e) => setCheckChoice(e.target.value)}
              >
                <option value="pass">所有检查通过</option>
                <option value="fail">服务器回传失败</option>
              </select>
            </label>
            <button
              className="button primary"
              disabled={loop.archived || s.done !== s.total}
              onClick={async () => {
                try {
                  await update(
                    simulateCheck(loop, checkChoice),
                    "模拟验证结果已记录",
                  );
                  setCheckError("");
                } catch (e) {
                  setCheckError(e.message);
                }
              }}
            >
              运行模拟验证
            </button>
            {s.done !== s.total && (
              <button
                className="text-button"
                onClick={() => openConfig(s.next)}
              >
                先完成剩余配置
                <Icon name="right" />
              </button>
            )}
          </div>
          {checkError && <p className="form-error">{checkError}</p>}
          {loop.history.length ? (
            <div className="verification-list">
              {loop.history.map((item, i) => (
                <article key={`${item.at}-${i}`}>
                  <Icon name={item.result === "pass" ? "check" : "alert"} />
                  <div>
                    <strong>
                      {item.result === "pass"
                        ? "模拟检查通过"
                        : "模拟检查失败：服务器回传"}
                    </strong>
                    <p>
                      配置版本 {item.revision} · {dateText(item.at)} ·{" "}
                      {item.revision === loop.revision
                        ? "适用于当前配置"
                        : "配置已变化，此结果已失效"}
                    </p>
                  </div>
                  {item.result === "fail" && (
                    <button
                      className="text-button"
                      onClick={() => openConfig("server")}
                    >
                      检查回传配置
                      <Icon name="right" />
                    </button>
                  )}
                </article>
              ))}
            </div>
          ) : (
            <div className="empty-state">
              <Icon name="history" size={34} />
              <h3>还没有验证记录</h3>
              <p>先完成配置，再模拟查看成功与失败反馈。</p>
            </div>
          )}
        </div>
      )}
      {crm&&view==='design'&&<footer className="guide-graph-next"><div><strong>下一步：{s.done<s.total?`配置${DEFS[s.next].title}`:'核对本次实际结果'}</strong><p>{s.done<s.total?'在对应任务填写并保存配置；不会自动操作外部平台。':'核对网站、CRM 与 Meta 的实际证据，不能只凭保存数量判断通过。'}</p></div><button className="button primary" onClick={()=>s.done<s.total?openConfig(s.next):navigate(`/flow/${loop.id}?view=tasks&node=closed-loop-test`)}>{s.done<s.total?'继续配置':'继续核对'}</button></footer>}
      {addOpen && (
        <Modal title="添加流程节点" onClose={() => { if (!busy) setAddOpen(false); }}>
          <div className="modal-body">
            <p className="subtle">
              {crm ? "在流程草稿中记录拟议的筛选节点；本次基础功能不启用筛选，也不改变现有回传规则。" : "当前预设支持在 CRM 线索与服务器回传之间插入筛选。系统自动连接兼容节点。"}
            </p>
            <div className="add-node-option">
              <Icon name="filter" size={28} />
              <div>
                <strong>有效询盘筛选</strong>
                <p>{crm ? "记录拟议条件，等待后续实施核验。" : "符合条件才回传；其他线索保留。"}</p>
              </div>
              <button
                className="button primary"
                disabled={loop.filterEnabled || busy}
                onClick={async () => {
                  if (
                    await update(toggleFilter(loop, true), crm ? "拟议筛选节点已记录，尚未启用" : "已添加筛选节点与配置任务", { action: "filter", enabled: true })
                  ) {
                    setSelected("filter");
                    setAddOpen(false);
                  }
                }}
              >
                {loop.filterEnabled ? "已添加" : "添加"}
              </button>
            </div>
            <Note>
              基础节点和系统归属受保护。可以移动节点，但不能把网站节点拖进 CRM
              域。
            </Note>
            {feedback}
          </div>
          <div className="modal-footer">
            <button className="button" disabled={busy} onClick={() => setAddOpen(false)}>
              返回画布
            </button>
          </div>
        </Modal>
      )}
    </section>
  );
}
