import React from "react";
import { Icon } from "./ui.jsx";
import { SettingRow } from "./GuideSettingRow.jsx";
import { GuideIllustration } from "./GuideIllustration.jsx";
import warningIllustration from "./assets/meta-conversion-warning.svg";
import { MetaConversionSetup } from "./MetaConversionSetup.jsx";
import { GuideReferenceFigure } from "./GuideReferenceFigure.jsx";
import trackingIllustration from "./assets/meta-standard-event-route.svg";
import leadIllustration from "./assets/meta-lead-installation.svg";

const settings = [
  {
    field: "转化事件", value: "成为潜在客户（Lead）", choice: true, conversionSetup: true,
    note: "以成功提交询盘为目标，不以浏览网页或点击按钮代替。",
    explanation: { title: "事件说明与排查", content: <>
      <p>“成为潜在客户”表示留下联系方式或提交需求，不等于成交。网站必须在询盘成功提交后发送 Lead；在广告组中选中事件不会自动安装追踪。</p>
      <p>“完成注册”用于注册账号；“提交申请”用于正式申请；“查看内容”只是看页面；“联系”表示发起联系，不一定提交成功。一般网站询盘可先使用标准 Lead，无需另建自定义转化。</p>
      <h4>显示“已停用事件”、找不到或无法选择时</h4><ol><li>核对 Meta 数据集数字 ID 与第 01 步一致。</li><li>在 Meta 事件管理工具选择同一个数据集，检查 Lead／成为潜在客户及最近接收时间。</li><li>没有收到时，排查网站是否发送、是否发错数据集；需要测试提交时先确认测试范围，不重复创建数据集。</li></ol>
      <p>仅凭灰色状态无法确定原因。CRM 保存成功不代表 Meta 已收到事件。</p>
    </> },
    caution: "现在先设置广告组，不在本步测试。网站与 CRM 接入完成后，按末尾「闭环测试」核对询盘与事件接收；通过前不要发布。事件无法选择时，不要改选“查看内容”绕过。",
  },
  {
    field: "归因模型", value: "标准", choice: true,
    note: "适合初次建立可核对的统计口径。",
    explanation: { title: "标准与增量的区别", content: <p>归因用于判断一条询盘算不算广告的功劳。“标准”按时间和互动规则计算；“增量”用模型估计没有广告就不会发生的额外结果，并据此优化。增量不保证效果更好。初次配置建议使用标准，后续结合业务数据评估。</p> },
  },
  {
    field: "单次成效费用目标", value: "无 · 暂不填写", choice: true,
    note: "无历史询盘成本时不设目标；积累数据后再评估。",
    explanation: { title: "这和广告预算有什么区别？", content: <p>例如填写 100 元，表达的是希望平均每条询盘成本接近 100 元，不是每天预算，也不是每条询盘的保证价格或绝对上限。目标过低可能限制投放。“无”不代表广告免费或预算无限；预算仍需单独设置。</p> },
  },
  {
    field: "现有价值规则集", value: "暂不应用",
    note: "若页面推荐已有规则集，先核对规则内容与适用业务，不按名称直接应用。",
  },
  {
    field: "价值规则", value: "不启用", choice: true,
    note: "没有客户价值差异的验证依据时，不额外调整出价。",
    explanation: { title: "什么是价值规则？", content: <p>当实际数据证明某类客户更有价值时，可以用规则表达愿意为他们调整出价。该预设面向初次配置，因此建议不启用；并不表示所有账户的价值规则都应关闭。</p> },
  },
  {
    field: "归因设置", value: "点击 7 天 · 互动 1 天 · 浏览 1 天", choice: true,
    note: "这是统计时间范围，不是投放天数。可用选项以 Meta 当前页面为准。",
    explanation: { title: "时间窗说明", content: <><ul><li><strong>点击 7 天：</strong>客户点击广告链接，3 天后回来提交询盘，满足条件时可计入。</li><li><strong>互动 1 天：</strong>符合条件的非链接互动或视频观看后，1 天内提交询盘，满足条件时可计入。Meta 的互动归因说明包含至少 5 秒的视频观看。</li><li><strong>浏览 1 天：</strong>客户看到广告但未点击，1 天内从其他路径提交询盘，满足条件时也可能计入。“浏览”指看到广告。</li></ul><p>广告报表中的询盘不全是点击后立刻提交的，归因数字也不等于因果证明。「互动归因」介绍卡不是另一个必需启用的开关。</p></> },
  },
];

export function MetaAdSetGuide({ open, onToggle, datasetId, onDataset, onReveal, serverNumber, testNumber, onTest, workbench=false }) {
  const Detail=workbench?'details':'div';
  return <section className={"guide-step meta-start-guide meta-adset-guide " + (open ? "is-open" : "")}>
    <h3 className="guide-step-heading"><button id="guide-toggle-meta-adset" className="guide-step-toggle" aria-controls="guide-panel-meta-adset" aria-expanded={open} onClick={onToggle}>
      <span className="task-number">02</span>
      <span className="guide-step-title"><strong>{workbench?'把成功询盘作为 Lead 转化事件':'设置转化事件与广告组'}</strong><small>{workbench?'操作位置：Meta 广告组 → 转化事件 → 成为潜在客户':'首次设置逐步操作 · 归因与投放优化'}</small></span>
      <span className="domain-tag domain-meta"><Icon name="meta" size={15} />Meta</span>
      <span className="guide-step-status">操作指引</span><Icon name={open ? "down" : "chevron"} size={18} />
    </button></h3>
    <div id="guide-panel-meta-adset" hidden={!open} aria-labelledby="guide-toggle-meta-adset">
      {workbench&&<div className="guide-conversion-layout">
        <div>
          <ul className="guide-preparation-facts"><li><span>推荐转化位置</span><strong>网站</strong></li><li><span>转化事件</span><strong>Lead / 成为潜在客户</strong></li></ul>
          <h4>出现“设置转化事件”提示时</h4>
          <ol className="guide-preparation-sequence"><li>打开设置，选择“以标准事件追踪转化”。</li><li>展开“查看更多事件 → 成为潜在客户”。</li><li>核对“用户操作时追踪事件”，不是页面加载即报 Lead。</li><li>已有接入不要重复粘贴代码；点“完成”返回广告组，刷新后核对提示。</li></ol>
          <GuideReferenceFigure src={trackingIllustration} title="选择标准事件路线" alt="设置转化追踪弹窗，上方为以标准事件追踪转化。" caption="选择标准事件路线；无需为此新建自定义转化。"/>
          <p className="guide-preparation-note">Lead 只代表成功提交询盘。点击按钮或打开网页不能作为成功询盘；保存本页不会安装网站代码。</p>
        </div>
        <aside className="guide-preparation-reference">
          <h4>找到这个事件与操作时机</h4>
          <GuideReferenceFigure src={leadIllustration} title="Lead 安装说明" alt="成为潜在客户展开，选择用户操作时追踪事件，显示 Lead 代码和完成按钮。" caption="示例状态不代表本实例已验收。"/>
          <p>页面上的“完成”结束安装说明，不会自动完成网站接入、服务器回传或去重验收。</p>
          <p>尚无确定询盘价值时，关闭“发送事件参数”，不填写转化价值和货币。</p>
        </aside>
      </div>}
      <Detail className={workbench?'guide-conversion-detail':''}>
      {workbench&&<summary>查看提示截图、完整安装步骤与广告组归因设置</summary>}
      <div className="meta-start-body">
        <p className="meta-start-intro"><Icon name="external" size={18} /><span>在 <strong>Meta 广告组 → 转化</strong>，从「数据集」下方继续。以下为网站询盘获客的初始推荐配置，不代表账户当前设置。</span></p>
        <div className="meta-adset-dataset"><div><span>第 01 步已登记的数据集 ID</span><strong>{datasetId || "尚未登记"}</strong><small>{datasetId ? "先核对 Meta 中所选数据集的数字 ID 与此一致，不能只看名称。" : "先回到第 01 步登记数字 ID，再核对 Meta 中的选择。"}</small></div><button className="text-button" type="button" onClick={onDataset}>查看第 01 步<Icon name="chevron" size={15} /></button></div>
        <div className="meta-adset-columns" aria-hidden="true"><span>Meta 设置项</span><span>推荐配置</span></div>
        <ol className="meta-start-settings" aria-label="数据集之后的广告组操作顺序">
          {settings.map(setting => <SettingRow key={setting.field} {...setting}
            accessory={setting.conversionSetup && <GuideIllustration src={warningIllustration} title="尚未设置转化事件" active={open}
              alt="Meta 广告组已选择成为潜在客户，但仍出现设置转化事件提示。"
              caption="选中 Lead 不等于网站已经发送事件。图中的数据集名称仅为示例，以本流程数字 ID 为准。" />}
            explanation={setting.explanation}>
            <small>{setting.note}</small>
            {setting.conversionSetup && <MetaConversionSetup active={open} onReveal={onReveal} serverNumber={serverNumber} testNumber={testNumber} onTest={onTest} />}
            {setting.caution && <p className="meta-start-caution"><Icon name="alert" size={17} /><span>{setting.caution}</span></p>}
          </SettingRow>)}
        </ol>
        <p className="meta-adset-sources">说明参考：<a href="https://about.fb.com/ltam/news/2026/03/simplificando-la-medicion-de-anuncios-para-un-mundo-social-first/" target="_blank" rel="noreferrer">Meta 归因更新</a><span> · </span><a href="https://about.fb.com/br/news/2025/06/aproveite-mais-das-suas-conversoes-apresentando-novas-ferramentas-para-otimizar-o-que-e-mais-valioso-para-o-seu-negocio/amp/" target="_blank" rel="noreferrer">增量归因与价值规则</a>。选项以你当前 Meta 页面为准。</p>
      </div>
      </Detail>
      {workbench?<footer className="guide-step-actions"><div className="guide-footer-context"><span>本页没有读取 Meta 的实时事件接收状态。先完成接入，投放前再核对询盘与事件。</span><button type="button" className="text-button" onClick={onTest}>只查看测试指引<Icon name="right" size={16}/></button></div><button type="button" className="button primary" onClick={()=>onReveal('website')}>继续：登记落地页<Icon name="right" size={16}/></button></footer>:
      <div className="meta-start-stop"><Icon name="alert" size={22} /><div><strong>先完成接入配置，再做第 {testNumber} 步闭环测试</strong><p>本页没有读取 Meta 的实时事件接收状态。现在不要求完成测试，投放前必须核对实际询盘与事件接收。</p><button type="button" className="text-button" onClick={onTest}>查看 {testNumber} 闭环测试<Icon name="right" size={16} /></button></div></div>}
    </div>
  </section>;
}
