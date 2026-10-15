import React, { useState } from "react";
import { GuideIllustration } from "./GuideIllustration.jsx";
import { GuideReferenceFigure } from "./GuideReferenceFigure.jsx";
import createIllustration from "./assets/meta-create-pixel.svg";
import createdIllustration from "./assets/meta-pixel-created.svg";

// Reading either path never changes the selected source, saved identity or draft.
export function MetaDatasetGuide({ active, inline = false }) {
  const [creating, setCreating] = useState(false);
  if(inline)return <div className="dataset-walkthrough guide-dataset-reference" aria-label="数据集创建与复用指引">
    <h3>Meta 里还没有数据集？</h3>
    <ol><li>在广告组的数据集位置选择新建。</li><li>填写名称并创建，再返回广告组。</li><li>到事件管理工具 → 数据源 → 设置，复制数字 ID，在本页登记。</li></ol>
    <GuideReferenceFigure src={createIllustration} title="创建数据集" alt="Meta 创建 Pixel 像素代码弹窗，输入名称后点击创建。" caption="填写自己的名称；创建前核对账户及条款。"/>
    <GuideReferenceFigure src={createdIllustration} title="创建后返回广告组" alt="完成 Pixel 设置弹窗，提供返回创建广告组选项。" caption="返回广告组继续接入，不代表测试已通过。"/>
    <details className="guide-dataset-more"><summary>已有数据集？查看复用与新建完整说明</summary><MetaDatasetGuide active={active}/></details>
  </div>;
  return <div className="dataset-walkthrough" aria-label="数据集创建与复用指引">
    <p>先看是否已有可用数据集，再按下面对应路径操作。</p>
    <details className="guide-path">
      <summary>已有数据集：直接复用</summary>
      <ol>
        <li>在 Meta 广告组的「数据集」中选择要使用的数据集；到「事件管理工具 → 数据源 → 设置」核对并复制数字 ID。</li>
        <li>本页 CRM 若已有同站点、同环境的记录，选「复用 CRM 已登记的数据集」。</li>
        <li>Meta 已有、CRM 尚未登记的，选「登记在 Meta 创建的数据集」，填入刚复制的数字 ID。无需再创建。</li>
      </ol>
    </details>
    <details className="guide-path" onToggle={event => setCreating(event.currentTarget.open)}>
      <summary>没有可用数据集：在 Meta 新建</summary>
      <ol>
        <li>在 Meta 广告组的「数据集」区域找到创建入口，进入「创建 Pixel 像素代码」。</li>
        <li><div className="guide-location-heading"><strong>命名并创建</strong><GuideIllustration src={createIllustration} title="创建 Pixel 像素代码" active={active && creating}
          alt="创建 Pixel 像素代码弹窗：输入名称，右下角为创建 Pixel 像素代码按钮。"
          caption="填写自己的站点或业务名称；图中的名称仅为示例，不要照抄。创建前核对账户和条款。" /></div>填写便于辨认的名称；核对账户及条款后，点击「创建 Pixel 像素代码」。</li>
        <li><div className="guide-location-heading"><strong>创建后返回广告组</strong><GuideIllustration src={createdIllustration} title="完成 Pixel 像素代码设置" active={active && creating}
          alt="完成 Pixel 像素代码设置弹窗，提供立即编写代码与返回创建广告组选项。"
          caption="按本流程先点“返回创建广告组”；网站代码接入在第 04 步处理。不要为进入下一步重复创建。" /></div>出现「完成 Pixel 像素代码设置」时，点「返回创建广告组」。现在不用点「立即编写代码」。</li>
        <li>打开「事件管理工具 → 数据源」，选择刚创建的数据集，在「设置」中复制数字 ID。</li>
        <li>回到本页 CRM，来源选「登记在 Meta 创建的数据集」，填入数字 ID，点击「保存并继续」，进入第 02 步设置转化事件。</li>
      </ol>
    </details>
    <p className="guide-result">本步完成的是创建或选定数据集并登记 ID。网站还需接入代码；CRM 不会替你创建 Meta 资产。</p>
  </div>;
}
