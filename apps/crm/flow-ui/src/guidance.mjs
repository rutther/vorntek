// All dirty steps participate, whether their panels are visible or collapsed.
export async function saveDirtySteps(order, dirty, save) {
  for (const key of order) {
    if (dirty[key] && !(await save(key))) return false;
  }
  return true;
}

export const OPERATIONS = {
  dataset: {
    action: "确认并登记数据集",
    where: "Meta 事件管理工具 → 数据源 → 设置",
    steps: ["先确认本次使用的数据集，核对名称，复制数字 ID。已有数据集可以复用，不必每条流程都新建。", "看到“数据集可以使用了”就表示已创建，返回设置取 ID，不要重复创建。", "回到本页登记；若 CRM 已有同站点、同环境的记录，直接选择复用。"],
    result: "带回：数据集的数字 ID，而不是名称。",
  },
  website: {
    action: "设置目标位置",
    subtitle: "Meta 广告 · 目标位置",
    where: "Meta 广告管理工具 → 广告层级 → 目标位置",
    steps: [
      "目标位置：网站。将用户送往自己的网站。",
      "网址：填写用户点击广告后要访问的完整网址。显示链接：本次可暂留空，不用它代替网址。",
      "浏览器附加组件：无。本网站询盘方案使用网站自己的表单，不应用 WhatsApp 推荐，也不选择即时表单。",
      "在浏览器打开所填网址，检查页面语言、询盘入口及隐私同意提示。确认后，将同一网址登记在本页 CRM。",
    ],
    result: "登记：与 Meta「目标位置 → 网址」一致的完整地址。保存本步不会自动修改 Meta 或发布广告。",
  },
  pixel: {
    action: "核对浏览器事件接入",
    where: "网站代码 / 网站集成设置 → Meta 事件管理工具",
    steps: ["核对网站使用的 Pixel 数字 ID 与本流程数据集一致，再记录安装方式。", "检查营销同意逻辑：拒绝时不发送，同意后观察 PageView。此处勾选只记录要求，不会安装代码。", "将实际观察结果留到“核对与实操记录”，不要仅凭保存状态判断接通。"],
    result: "记录：安装方式与同意要求。",
  },
  form: {
    action: "关联网站使用的表单",
    where: "CRM · 获客表单",
    steps: ["确认落地页实际嵌入的表单，在本步选择对应记录。", "本步只建立关联。后续受控提交需单独确认，再检查 CRM 是否只收到一条线索。"],
    result: "选择：当前网站的真实表单。",
  },
  crm: {
    action: "确认线索接收规则",
    where: "CRM · 线索与现有接收设置",
    steps: ["核对现有表单提交进入 CRM 的接收方式与去重规则。", "在本步记录沿用约定；实际验收时再核对来源、UTM、同意证据和事件编号。"],
    result: "记录：当前接收和去重约定，不新建队列。",
  },
  filter: {
    action: "记录拟议筛选条件",
    where: "CRM · 流程计划（尚未生效）",
    steps: ["选择希望采用的回传条件。不满足条件的线索仍应保留。", "本步保存的是计划，需要另行实施与核验，不会修改现有回传。"],
    result: "记录：待实施的条件。",
  },
  server: {
    action: "核对服务器回传约定",
    where: "CRM · 平台接入 → Meta 事件管理工具",
    steps: ["核对本站 Meta 接入所用的数据集与本流程一致。不要在这里填写 Token。", "确认回传 Lead，且同一次浏览器与服务器 Lead 使用相同事件 ID。勾选只记录要求。", "完成配置后检查 CRM 记录；受控提交、投递证据和 Meta 去重结果需另行核验。"],
    result: "记录：事件类型与去重要求。",
  },
  ad: {
    action: "核对广告设置并登记",
    where: "Meta 广告管理工具 · 广告系列 → 广告组 → 广告",
    steps: ["广告系列：本网站获客示例采用“竞拍”与“潜在客户”。分别记录系列、广告组和广告名称，不要把三层当成同一个对象。", "广告组：转化位置核对为“网站”，核对所选数据集和 Lead 事件；广告层核对按钮的落地页。", "你当前示例选择“广告组预算”，它不是所有流程的强制选项。预算、受众、素材需单独核对；本步不会创建或发布广告。"],
    result: "带回：三个层级的名称，保存记录后再核对投放条件。",
  },
};

// Creating or recording an asset never changes the website's installed tracking.
export const META_PIXEL_OPERATION = {
  ...OPERATIONS.pixel,
  steps: [
    "核对实际落地页的 Pixel 数字 ID，与第 01 步一致。不一致先确认切换范围；CRM 登记不会自动改网站。",
    "已有接入先核对，不重复安装；没有接入时，由网站接入人员配置营销同意与事件代码。",
    "若使用 Meta 手动安装向导：在“添加事件代码”页点“查看更多事件”，查找“成为潜在客户（Lead）”。找不到先核对列表，不用“联系”或“查看内容”代替。",
    "Lead 仅在询盘确认提交成功后发送，不放进所有页面的页头。浏览器与服务器两路回传需使用同一个事件编号。",
    "完成网站与 CRM 实际接入后做闭环测试；向导的“完成”和 CRM 保存都不等于测试通过。",
  ],
  result: "记录安装方式与同意要求；在 CRM 保存本步不会安装、切换或验证 Pixel。",
};

import { tasks } from "./model.mjs";

export const META_START_KEY = "meta-start";
export const META_ADSET_KEY = "meta-adset";
export const LOOP_TEST_KEY = "closed-loop-test";

// Shared preset, independent of flow IDs and account-specific Meta assets.
// Static instructions are not persisted config nodes.
export function hasMetaStartGuide(loop) {
  return loop.provider === "meta" && loop.preset === "website";
}

export function guideOrder(loop) {
  const order = tasks(loop);
  return hasMetaStartGuide(loop) ? [META_START_KEY, ...order.flatMap(key => key === "dataset" ? [key, META_ADSET_KEY] : [key]), LOOP_TEST_KEY] : order;
}

export function guideNumber(loop, key) {
  const index = guideOrder(loop).indexOf(key);
  return String(index + (hasMetaStartGuide(loop) ? 0 : 1)).padStart(2, "0");
}

export function nextGuideStep(loop, key) {
  const order = guideOrder(loop);
  return order[order.indexOf(key) + 1];
}

