import React from "react";
import { Icon } from "./ui.jsx";
import { GuideIllustration } from "./GuideIllustration.jsx";
import campaignIllustration from "./assets/meta-campaign-guide.svg";
import { SettingRow } from "./GuideSettingRow.jsx";
import {GuideReferenceFigure} from './GuideReferenceFigure.jsx';

export { META_START_KEY, hasMetaStartGuide } from "./guidance.mjs";


export function MetaStartGuide({ open, onToggle, workbench=false, onContinue }) {
  const Detail=workbench?'details':'div';
  return <section className={"guide-step meta-start-guide " + (open ? "is-open" : "")}>
    <h3 className="guide-step-heading">
      <button id="guide-toggle-meta-start" className="guide-step-toggle" aria-controls="guide-panel-meta-start" aria-expanded={open} onClick={onToggle}>
        <span className="task-number">00</span>
        <span className="guide-step-title"><strong>在 Meta 设置广告系列和广告组</strong><small>第 0 步 · 到“数据集”之前为止</small></span>
        <span className="domain-tag domain-meta"><Icon name="meta" size={15} />Meta</span>
        <span className="guide-step-status">静态操作指引</span>
        <Icon name={open ? "down" : "chevron"} size={18} />
      </button>
    </h3>
    <div id="guide-panel-meta-start" hidden={!open} aria-labelledby="guide-toggle-meta-start">
      {workbench&&<div className="guide-preparation-layout">
        <div>
          <h4>1　建立广告系列</h4>
          <ol className="guide-preparation-sequence">
            <li>在 Meta 广告管理工具，为本次广告命名，购买类型选择“竞拍”。</li>
            <li>营销目标选择“潜在客户”。</li>
            <li>进入广告组，将转化位置设置为“网站”。</li>
          </ol>
          <h4>2　停在数据集选择处</h4>
          <p>不要先发布广告。下一步确认数据集，再逐项接入网站与 CRM。</p>
          <ul className="guide-preparation-facts">
            <li><span>成效目标</span><strong>潜在客户人数最大化</strong></li>
            <li><span>预算设置</span><strong>按投放方案单独核对</strong></li>
            <li><span>即时表单建议</span><strong>本网站询盘路线不采用</strong></li>
          </ul>
          <p className="guide-preparation-note">名称只是人工对应信息。广告系列、广告组、广告不必同名，也不靠名称进行 API 匹配。</p>
        </div>
        <aside className="guide-preparation-reference">
          <GuideReferenceFigure src={campaignIllustration} title="对照这张图设置"
            alt="广告系列操作示意图：核对名称、竞拍及潜在客户目标。"
            caption="操作示意图：按标注核对名称、竞拍与目标"/>
          <p>标注对应左侧步骤；图中的名称不是必须照抄的模板。</p>
        </aside>
      </div>}
      <Detail className={workbench?'guide-preparation-detail':undefined}>
      {workbench&&<summary>查看广告系列与广告组的逐项设置说明</summary>}
      <div className="meta-start-body">
        <p className="meta-start-intro"><Icon name="external" size={18} /><span>在 <strong>Meta 广告管理工具</strong>中操作，按下面的顺序设置。本页只供对照，无需填写或保存，也不会自动修改 Meta。</span></p>
        <section className="meta-start-stage" aria-labelledby="meta-start-campaign">
          <div className="meta-start-stage-heading"><div className="meta-start-stage-title"><h4 id="meta-start-campaign"><span>A</span>先设置广告系列</h4><GuideIllustration src={campaignIllustration} title="广告系列设置" active={open} alt="Meta 广告系列设置截图：1 为广告系列名称输入框；2 为购买类型竞拍；3 为已选中的潜在客户目标。" caption="① 广告系列名称　② 购买类型：竞拍　③ 广告系列目标：潜在客户" /></div>
            <p className="meta-start-location">在广告系列编辑页，找到「广告系列名称」和「广告系列详情」。</p></div>
          <ol className="meta-start-settings" aria-label="广告系列操作顺序">
            <SettingRow field="广告系列名称" value="便于识别用途的名称"><small>例如「网站询盘-产品-地区」。在 Meta 名称框中填写，无需点击「创建模板」。</small></SettingRow>
            <SettingRow field="购买类型" value="竞拍" choice><small>网站获客预设采用「竞拍」购买类型。</small></SettingRow>
            <SettingRow field="广告系列目标" value="潜在客户" choice><small>目标是让访客留下联系方式、提交询盘，而不只是增加网页浏览。</small></SettingRow>
            <SettingRow field="广告系列花费限额" optional value="暂不填写"><small>初次配置可留空；此项不等于投放预算，预算需在投放前另行确认。</small></SettingRow>
          </ol>
        </section>
        <p className="meta-start-transition"><Icon name="down" size={20} /><span>然后在 Meta 左侧广告层级中，<strong>点击中间一层的广告组</strong>，进入广告组设置。</span></p>
        <section className="meta-start-stage" aria-labelledby="meta-start-adset">
          <div className="meta-start-stage-heading"><h4 id="meta-start-adset"><span>B</span>再设置广告组</h4>
            <p className="meta-start-location">核对广告组名称，然后找到「转化」区域。只设置到“数据集”上方。</p></div>
          <ol className="meta-start-settings" aria-label="广告组操作顺序">
            <SettingRow field="广告组名称" value="便于识别受众或投放范围的名称"><small>在 Meta 中填写，用于区分同一系列下的广告组。</small></SettingRow>
            <SettingRow field="转化发生位置" value="网站" choice><small>展开下拉框，在「单位置」下选择「网站」：让客户到你的网站填写询盘。</small><p className="meta-start-caution"><Icon name="alert" size={17} /><span>若出现即时表单推荐，<strong>不要点击「立即应用」</strong>。该推荐会改变网站获客路线。</span></p></SettingRow>
            <SettingRow field="成效目标" value="潜在客户人数最大化" choice><small>优化目标是获取留下联系方式的客户；数量最大化不保证询盘质量或成交。</small></SettingRow>
          </ol>
        </section>
      </div>
      <div className="meta-start-stop"><Icon name="alert" size={22} /><div><strong>看到「数据集」时，接着做第 01 步</strong><p>先确认并登记数据集，再按第 02 步继续设置广告组；<b>现在不要点击「发布」</b>。</p><small>展开或阅读本指引，不代表 Meta 设置已经完成，也不计入下方配置保存进度。</small></div></div>
      </Detail>
      {workbench&&<footer className="guide-step-actions"><span>本步只供对照，不保存配置，也不会修改 Meta。</span><button type="button" className="button primary" onClick={onContinue}>继续：选择数据集<Icon name="right" size={16}/></button></footer>}
    </div>
  </section>;
}
