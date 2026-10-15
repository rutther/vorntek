export const STORAGE_KEY = "new-crown-flow-scheme-1-v1";
export const DOMAIN = {
  meta: { label: "Meta 平台", short: "Meta" },
  website: { label: "网站", short: "网站" },
  crm: { label: "CRM 与服务器", short: "CRM" },
};
export const DEFS = {
  dataset: {
    title: "Meta 数据集",
    domain: "meta",
    icon: "database",
    description: "选择用于接收网站与 CRM 事件的数据源。",
    input: "浏览器事件 / 服务器事件",
    output: "Meta 事件管理工具",
    where: "Meta 事件管理工具 → CRM 登记",
    fields: [
      {
        key: "source",
        label: "数据源来源",
        type: "select",
        options: [
          ["existing", "使用已有数据集"],
          ["created", "已在 Meta 新建，登记 ID"],
        ],
      },
      {
        key: "externalId",
        label: "数据集 ID",
        placeholder: "填写 Meta 中的数字 ID",
      },
      {
        key: "alias",
        label: "数据集备注",
        optional: true,
        placeholder: "便于识别，可不填写",
      },
    ],
  },
  website: {
    title: "网站落地页",
    domain: "website",
    icon: "world",
    description: "记录广告实际打开的落地页网址。",
    input: "广告访客",
    output: "询盘表单 / Pixel 事件",
    where: "网站",
    fields: [
      {
        key: "url",
        label: "落地页网址",
        placeholder: "请输入广告实际打开的完整网址",
      },
    ],
  },
  pixel: {
    title: "Pixel 事件",
    domain: "website",
    icon: "code",
    description: "选择网站浏览器事件的接入方式。",
    input: "网站访问与交互",
    output: "Meta 数据集",
    where: "网站代码 / 网站集成设置",
    fields: [
      {
        key: "mode",
        label: "安装方式",
        type: "select",
        options: [
          ["manual", "网站代码安装"],
          ["partner", "网站平台集成"],
        ],
      },
      { key: "consent", label: "仅在获得营销同意后发送事件", type: "checkbox" },
    ],
  },
  form: {
    title: "询盘表单",
    domain: "website",
    icon: "form",
    description: "选择这条流程收集询盘的表单。",
    input: "访客填写与提交",
    output: "CRM 线索",
    where: "CRM → 获客表单",
    fields: [
      {
        key: "formId",
        label: "接入表单",
        type: "select",
        options: [
          ["project-inquiry", "官网项目询盘（演示）"],
          ["quick-inquiry", "快速询盘（演示）"],
        ],
      },
    ],
  },
  crm: {
    title: "CRM 线索",
    domain: "crm",
    icon: "users",
    description: "指定询盘接收队列和重复线索处理方式。",
    input: "网站表单提交",
    output: "筛选条件 / 服务器回传",
    where: "CRM",
    fields: [
      {
        key: "queue",
        label: "接收队列",
        type: "select",
        options: [
          ["marketing", "营销线索队列（演示）"],
          ["sales", "销售待分配队列（演示）"],
        ],
      },
      {
        key: "duplicate",
        label: "重复线索处理",
        type: "select",
        options: [
          ["merge", "按联系方式关联已有线索"],
          ["review", "交由人工核对"],
        ],
      },
    ],
  },
  filter: {
    title: "有效询盘筛选",
    domain: "crm",
    icon: "filter",
    description: "只有满足条件的业务事件继续回传；其他线索仍保留在 CRM。",
    input: "CRM 线索",
    output: "符合条件 → 回传；否则 → 保留",
    where: "CRM",
    optional: true,
    fields: [
      {
        key: "rule",
        label: "回传条件",
        type: "select",
        options: [
          ["qualified", "线索阶段达到「已合格」"],
          ["submitted", "已成功提交且未标记垃圾"],
        ],
      },
    ],
  },
  server: {
    title: "服务器回传",
    domain: "crm",
    icon: "cloud",
    description: "将符合条件的业务事件回传至 Meta。",
    input: "CRM 线索 · 满足筛选条件",
    output: "Meta 数据集",
    where: "CRM → 平台接入",
    fields: [
      {
        key: "event",
        label: "回传事件",
        type: "select",
        options: [
          ["Lead", "Lead · 潜在客户"],
          ["qualified_lead", "qualified_lead · 合格线索"],
        ],
      },
      {
        key: "dedup",
        label: "浏览器和服务器使用同一事件 ID 去重",
        type: "checkbox",
      },
    ],
  },
  ad: {
    title: "Meta 广告",
    domain: "meta",
    icon: "meta",
    description: "记录与本流程关联的广告。此原型不会创建、发布或暂停广告。",
    input: "广告投放",
    output: "网站访客",
    where: "Meta 广告管理工具",
    fields: [
      {
        key: "adName",
        label: "关联广告名称",
        placeholder: "例如：非洲灌装线网站获客",
      },
      {
        key: "objective",
        label: "转化位置",
        type: "select",
        options: [["website", "网站"]],
      },
    ],
  },
};
const BASE_TASKS = [
  "dataset",
  "website",
  "pixel",
  "form",
  "crm",
  "server",
  "ad",
];
const now = () => new Date().toISOString();
function cleanName(value) {
  const name = String(value ?? "")
    .trim()
    .replace(/\s+/g, " ");
  if (name.length < 2 || name.length > 200)
    throw Error("流程名称需要 2–200 个字符。");
  return name;
}
export function createLoop({ name, environment }) {
  if (!["test", "production"].includes(environment))
    throw Error("请选择运行环境。");
  const id = crypto.randomUUID();
  return {
    id,
    code: `LC-${id.slice(0, 8).toUpperCase()}`,
    name: cleanName(name),
    environment,
    provider: "meta",
    preset: "website",
    configs: {},
    drafts: {},
    filterEnabled: false,
    positions: {},
    revision: 1,
    check: null,
    history: [],
    archived: false,
    updatedAt: now(),
  };
}
export function tasks(loop) {
  if (Number.isInteger(loop.version) && Array.isArray(loop.taskKeys))
    return loop.taskKeys.filter((key) => Object.hasOwn(DEFS, key));
  const keys = [...BASE_TASKS];
  if (loop.filterEnabled) keys.splice(5, 0, "filter");
  return keys;
}
export function moveNode(loop,key,position){
 writable(loop);
 if(!tasks(loop).includes(key)||!Number.isFinite(position.x)||!Number.isFinite(position.y))throw Error('节点位置无效。');
 const width=key==='filter'?150:185;
 return {...loop,positions:{...loop.positions,[key]:{x:Math.max(8,Math.min(270-width-8,position.x)),y:Math.max(65,Math.min(key==='filter'?545:572,position.y))}},updatedAt:now()};
}
export function renameLoop(loop, name) {
  return { ...loop, name: cleanName(name), updatedAt: now() };
}
function writable(loop) {
  if (loop.archived) throw Error("请先恢复已归档流程。");
}
export function validateConfig(key, values) {
  const def = DEFS[key];
  if (!def) throw Error("节点不存在。");
  for (const f of def.fields) {
    if (f.optional) continue;
    const v = values[f.key];
    if (f.type === "checkbox") {
      if (v !== true) throw Error(`请确认：${f.label}。`);
    } else if (!String(v ?? "").trim()) throw Error(`请填写或选择${f.label}。`);
    if (f.options && !f.options.some((o) => o[0] === v))
      throw Error(`请选择有效的${f.label}。`);
  }
  if (
    key === "dataset" &&
    !/^[1-9]\d{5,24}$/.test(String(values.externalId).trim())
  )
    throw Error("数据集 ID 必须是 6–25 位数字。");
  if (key === "website") {
    let url;
    try {
      url = new URL(values.url);
    } catch {
      throw Error("请填写完整的网站网址，例如 https://example.com/。");
    }
    if (
      !["http:", "https:"].includes(url.protocol) ||
      !url.hostname.includes(".")
    )
      throw Error("网址必须使用 http 或 https，并包含有效域名。");
  }
  return true;
}
export function saveConfig(loop, key, values) {
  writable(loop);
  if (!tasks(loop).includes(key)) throw Error("该节点不在当前流程中。");
  validateConfig(key, values);
  const clean = {};
  for (const field of DEFS[key].fields) {
    clean[field.key] =
      field.type === "checkbox"
        ? values[field.key] === true
        : String(values[field.key] ?? "").trim();
  }
  const changed = JSON.stringify(loop.configs[key]) !== JSON.stringify(clean);
  const drafts = { ...loop.drafts };
  delete drafts[key];
  return {
    ...loop,
    configs: { ...loop.configs, [key]: clean },
    drafts,
    revision: loop.revision + (changed ? 1 : 0),
    check: changed ? null : loop.check,
    updatedAt: now(),
  };
}
export function saveDraft(loop, key, values) {
  writable(loop);
  if (!tasks(loop).includes(key)) throw Error("该节点不在当前流程中。");
  return {
    ...loop,
    drafts: { ...loop.drafts, [key]: { ...values } },
    updatedAt: now(),
  };
}
export function nodeState(loop, key) {
  if (Number.isInteger(loop.version)) {
    const latest = loop.history.find((item) => item.kind === "manual" && item.nodeKey === key && item.revision === loop.revision);
    if (latest?.result === "issue") return "issue";
    if (loop.configs[key]) return "saved";
    if (loop.drafts[key]) return "draft";
    return "pending";
  }
  if (
    loop.check?.revision === loop.revision &&
    loop.check.result === "fail" &&
    key === "server"
  )
    return "failed";
  if (loop.configs[key]) return "saved";
  if (loop.drafts[key]) return "draft";
  return "pending";
}
export function summary(loop) {
  const order = tasks(loop),
    done = order.filter((key) => loop.configs[key]).length;
  const crm = Number.isInteger(loop.version);
  const issue = crm ? order.find((key) => nodeState(loop, key) === "issue") : null;
  const check = !crm && loop.check?.revision === loop.revision ? loop.check : null;
  const state = loop.archived
    ? "archived"
    : issue || check?.result === "fail"
      ? "attention"
      : check?.result === "pass"
        ? "simulated"
        : done === order.length
          ? "verify"
          : done > 0
            ? "configuring"
            : "draft";
  return {
    done,
    total: order.length,
    next: issue || (order.includes(loop.nextTask) ? loop.nextTask : order.find((k) => !loop.configs[k])) || "dataset",
    state,
  };
}
export const STATES = {
  all: "全部",
  configuring: "配置中",
  verify: "待验证",
  attention: "需处理",
  draft: "草稿",
  simulated: "模拟通过",
  archived: "已归档",
};
export function duplicateLoop(loop) {
  const copy = createLoop({
    name: `${loop.name.slice(0, 192)} · 副本`,
    environment: loop.environment,
  });
  return {
    ...copy,
    filterEnabled: loop.filterEnabled,
    positions: structuredClone(loop.positions),
  };
}
export function archiveLoop(loop, archived) {
  return { ...loop, archived, updatedAt: now() };
}
export function toggleFilter(loop, enabled) {
  writable(loop);
  if (loop.filterEnabled === enabled) return loop;
  const configs = { ...loop.configs },
    drafts = { ...loop.drafts };
  if (!enabled) {
    delete configs.filter;
    delete drafts.filter;
  }
  return {
    ...loop,
    filterEnabled: enabled,
    configs,
    drafts,
    revision: loop.revision + 1,
    check: null,
    updatedAt: now(),
  };
}
export function connections(loop) {
  return [
    {
      source: "ad",
      target: "website",
      label: "访客到站",
      from: "right",
      to: "left",
    },
    { source: "website", target: "form", from: "bottom", to: "top" },
    { source: "website", target: "pixel", from: "right", to: "right" },
    {
      source: "form",
      target: "crm",
      label: "提交询盘",
      from: "right",
      to: "left",
    },
    ...(loop.filterEnabled
      ? [
          { source: "crm", target: "filter", from: "bottom", to: "top" },
          {
            source: "filter",
            target: "server",
            label: "是",
            from: "bottom",
            to: "top",
          },
          {
            source: "filter",
            target: "retain",
            label: "否",
            from: "right",
            to: "top",
          },
        ]
      : [{ source: "crm", target: "server", from: "bottom", to: "top" }]),
    {
      source: "pixel",
      target: "dataset",
      label: "浏览器事件",
      from: "bottom",
      to: "right",
    },
    {
      source: "server",
      target: "dataset",
      label: "服务器事件",
      from: "left",
      to: "bottom",
    },
  ];
}
export function simulateCheck(loop, result) {
  if (Number.isInteger(loop.version)) throw Error("CRM 流程不提供模拟验证。");
  writable(loop);
  const s = summary(loop);
  if (s.done !== s.total) throw Error("请先完成所有节点配置，再模拟验证。");
  if (!["pass", "fail"].includes(result)) throw Error("请选择模拟结果。");
  const check = { result, revision: loop.revision, at: now(), simulated: true };
  return {
    ...loop,
    check,
    history: [check, ...loop.history].slice(0, 20),
    updatedAt: now(),
  };
}
export function filterLoops(
  loops,
  {
    search = "",
    state = "all",
    environment = "all",
    provider = "all",
    preset = "all",
  } = {},
) {
  return loops.filter((l) => {
    const s = summary(l).state;
    return (
      (state === "archived" ? l.archived : !l.archived) &&
      (state === "all" || state === s) &&
      (environment === "all" || environment === l.environment) &&
      (provider === "all" || provider === l.provider) &&
      (preset === "all" || preset === l.preset) &&
      `${l.name} ${l.code}`.toLowerCase().includes(search.trim().toLowerCase())
    );
  });
}
export function writeStore(storage, loops) {
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, loops }));
  } catch {
    throw Error(
      "本地保存失败。请检查浏览器存储空间；你的输入仍保留在当前页面。",
    );
  }
}
export function navigationIntent(current, target, dirty) {
  return current === target ? "stay" : dirty ? "confirm" : "navigate";
}
function validRecord(loop) {
  const object = (v) =>
    v !== null && typeof v === "object" && !Array.isArray(v);
  const validCheck = (c) =>
    object(c) &&
    ["pass", "fail"].includes(c.result) &&
    Number.isInteger(c.revision) &&
    c.revision >= 1 &&
    c.revision <= loop.revision &&
    c.simulated === true &&
    Number.isFinite(Date.parse(c.at));
  if (
    !object(loop) ||
    typeof loop.id !== "string" ||
    !loop.id ||
    typeof loop.code !== "string" ||
    typeof loop.name !== "string" ||
    cleanName(loop.name) !== loop.name ||
    !["production", "test"].includes(loop.environment) ||
    loop.provider !== "meta" ||
    loop.preset !== "website" ||
    typeof loop.filterEnabled !== "boolean" ||
    typeof loop.archived !== "boolean" ||
    !Number.isInteger(loop.revision) ||
    loop.revision < 1 ||
    !Number.isFinite(Date.parse(loop.updatedAt))
  )
    return false;
  if (
    !object(loop.positions) ||
    !object(loop.configs) ||
    !object(loop.drafts) ||
    !Array.isArray(loop.history)
  )
    return false;
  for (const [key, position] of Object.entries(loop.positions)) {
    if (
      !DEFS[key] ||
      !object(position) ||
      !Number.isFinite(position.x) ||
      !Number.isFinite(position.y)
    )
      return false;
  }
  for (const [key, values] of Object.entries(loop.configs)) {
    if (!tasks(loop).includes(key) || !object(values)) return false;
    validateConfig(key, values);
  }
  for (const [key, values] of Object.entries(loop.drafts)) {
    if (
      !tasks(loop).includes(key) ||
      !object(values) ||
      !Object.values(values).every(
        (v) => typeof v === "string" || typeof v === "boolean",
      )
    )
      return false;
  }
  if (loop.check !== null && !validCheck(loop.check)) return false;
  return loop.history.every(validCheck);
}
export function readStore(storage) {
  let raw;
  try {
    raw = storage.getItem(STORAGE_KEY);
  } catch {
    throw Error("无法读取本地草稿。请允许浏览器本地存储后重试。");
  }
  if (raw === null) return null;
  try {
    const d = JSON.parse(raw);
    if (
      d.version !== 1 ||
      !Array.isArray(d.loops) ||
      !d.loops.every(validRecord) ||
      new Set(d.loops.map((l) => l.id)).size !== d.loops.length
    )
      throw Error();
    return d.loops;
  } catch {
    throw Error("本地草稿格式损坏，未覆盖原数据。请先备份或检查浏览器存储。");
  }
}
export function seedLoops() {
  const complete = {
    dataset: {
      source: "existing",
      externalId: "123456789012345",
      alias: "演示数据集",
    },
    website: { url: "https://example.com/contact/" },
    pixel: { mode: "manual", consent: true },
    form: { formId: "project-inquiry" },
    crm: { queue: "marketing", duplicate: "merge" },
    filter: { rule: "qualified" },
    server: { event: "Lead", dedup: true },
    ad: { adName: "演示网站获客广告", objective: "website" },
  };
  return [
    "非洲灌装线询盘",
    "官网通用询盘",
    "中东饮料线询盘",
    "网站线索演示",
  ].map((name, i) => {
    const l = createLoop({
      name,
      environment: i === 3 ? "test" : "production",
    });
    l.id = `demo-${i + 1}`;
    l.code = `LC-00${i + 1}`;
    l.filterEnabled = true;
    l.updatedAt = `2026-09-26T0${i + 1}:20:00.000Z`;
    l.configs =
      i === 3
        ? {}
        : i === 0
          ? Object.fromEntries(
              ["dataset", "website", "pixel"].map((k) => [k, complete[k]]),
            )
          : structuredClone(complete);
    if (i === 2) {
      l.check = {
        result: "fail",
        revision: 1,
        at: l.updatedAt,
        simulated: true,
      };
      l.history = [l.check];
    }
    return l;
  });
}
