import React, { useEffect, useState } from "react";
import { Icon, Badge, Progress, Note, dateText } from "./ui.jsx";
import { filterLoops, summary, STATES, DEFS, tasks } from "./model.mjs";
import { guideNumber } from "./guidance.mjs";

export function Overview({
  loops,
  filters,
  setFilters,
  navigate,
  onAction,
  templates = false,
  crm = false,
  legacyLoops = [],
}) {
  useEffect(()=>{
    const dismiss=e=>{document.querySelectorAll('.row-menu[open]').forEach(menu=>{
      if(e.type==='keydown'&&e.key==='Escape'){menu.open=false;menu.querySelector('summary')?.focus();}
      else if(e.type==='pointerdown'&&!menu.contains(e.target))menu.open=false;
    });};
    document.addEventListener('pointerdown',dismiss);document.addEventListener('keydown',dismiss);
    return()=>{document.removeEventListener('pointerdown',dismiss);document.removeEventListener('keydown',dismiss);};
  },[]);
  const [page, setPage] = useState(1),
    [size, setSize] = useState(20);
  const filtered = filterLoops(loops, filters),
    pages = Math.max(1, Math.ceil(filtered.length / size)),
    current = Math.min(page, pages);
  const set = (key, value) => {
    setFilters({ ...filters, [key]: value });
    setPage(1);
  };
  const activeContext = filterLoops(loops, { ...filters, state: "all" });
  const counts = Object.fromEntries(
    Object.keys(STATES).map((key) => [
      key,
      key === "all"
        ? activeContext.length
        : activeContext.filter((l) => summary(l).state === key).length,
    ]),
  );
  return (
    <section className="overview">
      <header className="page-heading">
        <h1>流程管理</h1>
        <p>查找已有流程，继续配置或处理异常。</p>
      </header>
      <nav className="page-tabs" aria-label="流程管理视图">
        <button
          className={!templates ? "active" : ""}
          onClick={() => navigate("/loops")}
        >
          流程总览
        </button>
        <button
          className={templates ? "active" : ""}
          onClick={() => navigate("/templates")}
        >
          流程模板
        </button>
      </nav>
      {templates ? (
        <Templates onCreate={() => onAction("create")} crm={crm} />
      ) : (
        <>
          <div className="list-toolbar">
            <label className="search-field">
              <Icon name="search" />
              <input
                aria-label="搜索流程名称或编号"
                placeholder="搜索流程名称或编号"
                value={filters.search}
                onChange={(e) => set("search", e.target.value)}
              />
              {filters.search && (
                <button
                  className="icon-button"
                  aria-label="清空搜索"
                  onClick={() => set("search", "")}
                >
                  <Icon name="close" size={15} />
                </button>
              )}
            </label>
            <select
              aria-label="平台筛选"
              value={filters.provider}
              onChange={(e) => {
                setFilters({
                  ...filters,
                  provider: e.target.value,
                  preset: "all",
                });
                setPage(1);
              }}
            >
              <option value="all">全部平台</option>
              <option value="meta">Meta</option>
              <option value="google">Google Ads（未开放）</option>
              <option value="tiktok">TikTok Ads（未开放）</option>
            </select>
            <select
              aria-label="预设筛选"
              value={filters.preset}
              onChange={(e) => set("preset", e.target.value)}
            >
              <option value="all">全部预设</option>
              <option value="website">网站获客</option>
            </select>
            <select
              aria-label="环境筛选"
              value={filters.environment}
              onChange={(e) => set("environment", e.target.value)}
            >
              <option value="all">全部环境</option>
              <option value="production">正式业务</option>
              <option value="test">演练测试</option>
            </select>
            <button
              className="button primary create-button"
              onClick={() => onAction("create")}
            >
              <Icon name="plus" />
              新建流程
            </button>
          </div>
          <div
            className="status-filters"
            role="group"
            aria-label="流程状态筛选"
          >
            {[
              "all",
              "configuring",
              "verify",
              "attention",
              "draft",
              ...(counts.simulated ? ["simulated"] : []),
            ].map((s) => (
              <button
                key={s}
                className={filters.state === s ? "active" : ""}
                onClick={() => set("state", s)}
              >
                {crm && s === "verify" ? "待核验" : STATES[s]} <span>{counts[s]}</span>
              </button>
            ))}
            <button
              className={`archived-tab ${filters.state === "archived" ? "active" : ""}`}
              onClick={() => set("state", "archived")}
            >
              已归档
            </button>
          </div>
          <p className="table-help">左右滑动表格，查看状态与操作</p>
          <div className="table-scroll">
            <table className="flow-table">
              <thead>
                <tr>
                  {[
                    "流程名称",
                    "平台 / 预设",
                    "配置进度",
                    "状态",
                    "下一项待办",
                    "最近更新",
                    "操作",
                  ].map((h) => (
                    <th key={h}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered
                  .slice((current - 1) * size, current * size)
                  .map((loop) => {
                    const s = summary(loop),
                      order = tasks(loop),
                      next = s.state === "attention" && !crm ? "server" : s.next;
                    const nextText =
                      s.state === "attention"
                        ? (crm ? `${DEFS[next].title}：实操发现问题` : "服务器回传失败")
                        : s.state === "verify"
                          ? (crm ? "外部操作与结果待核验" : "验证事件与连接")
                          : s.state === "simulated"
                            ? "演示验证已完成"
                            : `${guideNumber(loop, next)} ${DEFS[next].title}`;
                    const label =
                      s.state === "attention"
                        ? "处理异常"
                        : s.state === "verify"
                          ? (crm ? "查看核对记录" : "去验证")
                          : s.state === "simulated" || s.state === "archived"
                            ? "查看流程"
                            : s.state === "draft"
                              ? "开始配置"
                              : "继续配置";
                    // Both overview entry points open the same flow workspace.
                    // Choose a configuration/verification view inside that flow.
                    const target = `/flow/${loop.id}`;
                    return (
                      <tr key={loop.id}>
                        <td>
                          <div className="flow-name-cell">
                            <Icon name="flow" size={25} />
                            <div>
                              <button
                                className="flow-name"
                                onClick={() => navigate(`/flow/${loop.id}`)}
                              >
                                {loop.name}
                              </button>
                              <span className="subtle small code">
                                {loop.code}
                              </span>
                              {crm && <div className="crm-mobile-row-detail"><Badge state={s.state}>{s.state === "verify" ? "待核验" : STATES[s.state]}</Badge><span>{nextText}</span><div className="crm-mobile-row-actions"><button className="text-button" onClick={() => navigate(target)}>{label}<Icon name="right" size={15} /></button><details className="row-menu"><summary aria-label={`${loop.name}的更多操作`}><Icon name="dots" /></summary><div className="menu-popup">{(loop.archived ? [["restore", "恢复流程"]] : [["rename", "重命名"], ["copy", "复制为草稿"], ["archive", "归档"]]).map(([type, title]) => <button key={type} onClick={(e) => { e.currentTarget.closest("details").open = false; onAction(type, loop); }}>{title}</button>)}</div></details></div></div>}
                            </div>
                          </div>
                        </td>
                        <td>
                          <span>Meta · 网站获客</span>
                          <span className="subtle small">
                            {loop.environment === "test"
                              ? "演练测试"
                              : "正式业务"}
                          </span>
                        </td>
                        <td>
                          <Progress {...s} />
                        </td>
                        <td>
                          <Badge state={s.state}>{crm && s.state === "verify" ? "待核验" : STATES[s.state]}</Badge>
                        </td>
                        <td className="task-cell">
                          {loop.archived ? "—" : nextText}
                        </td>
                        <td className="date-cell">
                          {dateText(loop.updatedAt)}
                        </td>
                        <td>
                          <div className="row-actions">
                            <button
                              className="text-button"
                              onClick={() => navigate(target)}
                            >
                              {label}
                              <Icon name="right" size={17} />
                            </button>
                            <details className="row-menu">
                              <summary aria-label={`${loop.name}的更多操作`}>
                                <Icon name="dots" />
                              </summary>
                              <div className="menu-popup">
                                {!loop.archived && (
                                  <>
                                    <button
                                      onClick={(e) => {
                                        e.currentTarget.closest(
                                          "details",
                                        ).open = false;
                                        onAction("rename", loop);
                                      }}
                                    >
                                      <Icon name="pencil" />
                                      重命名
                                    </button>
                                    <button
                                      onClick={(e) => {
                                        e.currentTarget.closest(
                                          "details",
                                        ).open = false;
                                        onAction("copy", loop);
                                      }}
                                    >
                                      <Icon name="copy" />
                                      复制为草稿
                                    </button>
                                    <hr />
                                  </>
                                )}
                                <button
                                  onClick={(e) => {
                                    e.currentTarget.closest("details").open =
                                      false;
                                    onAction(
                                      loop.archived ? "restore" : "archive",
                                      loop,
                                    );
                                  }}
                                >
                                  <Icon
                                    name={loop.archived ? "restore" : "archive"}
                                  />
                                  {loop.archived ? "恢复流程" : "归档"}
                                </button>
                              </div>
                            </details>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
          {!filtered.length && (
            <div className="empty-state">
              <Icon name="search" size={32} />
              <h3>
                {crm && loops.length === 0 ? "还没有流程，从数据源第一拍开始" : filters.state === "archived"
                  ? "暂无已归档流程"
                  : "没有找到匹配的流程"}
              </h3>
              <p>{crm && loops.length === 0 ? "先创建一条 CRM 草稿，再确认已有 Meta 数据源或登记已创建的数据集 ID。" : "试试其他名称，或清除筛选条件。"}</p>
              {crm && loops.length === 0 ? <button className="button primary" onClick={() => onAction("create")}><Icon name="plus" />创建第一条流程</button> :
              <button
                className="button"
                onClick={() => {
                  setFilters({
                    search: "",
                    provider: "all",
                    preset: "all",
                    environment: "all",
                    state: "all",
                  });
                  setPage(1);
                }}
              >
                清除筛选
              </button>}
            </div>
          )}
          <footer className="list-footer">
            <span>共 {filtered.length} 条流程</span>
            <div className="pagination">
              <select
                aria-label="每页条数"
                value={size}
                onChange={(e) => {
                  setSize(Number(e.target.value));
                  setPage(1);
                }}
              >
                <option value={20}>20 条 / 页</option>
                <option value={50}>50 条 / 页</option>
              </select>
              <button
                className="icon-button bordered"
                disabled={current === 1}
                aria-label="上一页"
                onClick={() => setPage(current - 1)}
              >
                <Icon name="left" size={17} />
              </button>
              <span className="current-page">{current}</span>
              <button
                className="icon-button bordered"
                disabled={current === pages}
                aria-label="下一页"
                onClick={() => setPage(current + 1)}
              >
                <Icon name="right" size={17} />
              </button>
            </div>
          </footer>
          <div className="demo-footer">
            <Icon name="info" size={17} />
            {crm ? "配置与实操记录保存在 CRM；外部资产和运行结果需逐项核验。" : "交互原型 · 数据仅保存在此浏览器，不代表真实运行结果"}
          </div>
          {crm && legacyLoops.length > 0 && <section className="crm-legacy-loops"><h2>旧版流程</h2><p className="subtle">以下记录保留原配置，可进入旧版页面查看；不会自动迁移或覆盖。</p><ul>{legacyLoops.map((item) => <li key={item.id}>{typeof item.url === "string" && item.url.startsWith("/admin/") && !item.url.startsWith("//") ? <a href={item.url}>{item.name}<Icon name="chevron" size={16} /></a> : <span>{item.name}</span>}</li>)}</ul></section>}
        </>
      )}
    </section>
  );
}
function Templates({ onCreate, crm }) {
  return (
    <div className="template-page">
      <h2>从典型流程开始</h2>
      <p className="subtle">
        预设是起点。创建后，你可以配置节点，并在支持的位置添加筛选。
      </p>
      <div className="template-platform">
        <Icon name="meta" size={24} />
        <strong>Meta</strong>
        <span className="subtle">Facebook · Instagram</span>
      </div>
      <article className="template-primary">
        <div className="template-icon">
          <Icon name="world" size={29} />
        </div>
        <div>
          <h3>网站获客</h3>
          <p>
            广告访客进入网站，提交询盘到 CRM，再通过浏览器与服务器事件回传至
            Meta。
          </p>
          <div className="template-facts">
            <span>3 个系统域</span>
            <span>7 个基础配置节点</span>
            <span>{crm ? "可记录拟议筛选" : "可添加询盘筛选"}</span>
          </div>
        </div>
        <button className="button primary" onClick={onCreate}>
          使用此预设
          <Icon name="right" />
        </button>
      </article>
      <div className="unavailable-templates">
        <span>
          即时表单 <small>待开放</small>
        </span>
        <span>
          WhatsApp 点击 <small>暂停</small>
        </span>
        <span>
          Google Ads <small>待开放</small>
        </span>
        <span>
          TikTok Ads <small>待开放</small>
        </span>
      </div>
      <Note>
        {crm ? "新建只在 CRM 建立流程草稿；筛选只记录计划。不会创建 Meta 数据集、发布广告或向外部发送事件。" : "新建只建立本地原型草稿，不会创建 Meta 数据集、发布广告或向外部发送事件。"}
      </Note>
    </div>
  );
}
