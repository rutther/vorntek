import React from "react";
import { GuideIllustration } from "./GuideIllustration.jsx";
import trackingIllustration from "./assets/meta-standard-event-route.svg";
import leadIllustration from "./assets/meta-lead-installation.svg";
import { Icon } from "./ui.jsx";

export function MetaConversionSetup({ active, onReveal, serverNumber, testNumber, onTest }) {
  return <div className="conversion-walkthrough">
    <h4>首次设置：出现「设置转化事件」提示时</h4>
    <ol aria-label="首次设置转化事件操作顺序">
      <li>点击黄色提示框里的「设置转化事件」。</li>
      <li><div className="guide-location-heading"><span>弹窗选择上方「以标准事件追踪转化」。</span><GuideIllustration src={trackingIllustration} title="选择追踪方式" active={active}
        alt="设置转化追踪弹窗，上方为以标准事件追踪转化，下方为追踪自定义转化事件。"
        caption="点上方“以标准事件追踪转化”，进入安装说明；不是点下面的自定义转化。" /></div></li>
      <li>在「添加事件代码」页，向下点「查看更多事件」，再展开「成为潜在客户」。不用选「联系」或「提交申请」。</li>
      <li><div className="guide-location-heading"><span>选「用户操作时追踪事件」。</span><GuideIllustration src={leadIllustration} title="Lead 安装说明" active={active}
        alt="成为潜在客户已展开，选中用户操作时追踪事件，发送事件参数关闭，下面显示尚无动态与完成按钮。"
        caption="这是代码示例界面，不是安装成功状态。询盘没有确定价值时关闭参数；网站应在提交成功后发送 Lead。" /></div>没有确定的询盘价值时，关闭「发送事件参数」，不填转化价值和货币。</li>
      <li>点右下角「完成」，返回广告组。这只是关闭安装说明，黄色提示可能仍在；不要反复创建数据集。</li>
    </ol>
    <p className="conversion-next"><strong>接下来：</strong>继续第 03 步填写网站网址，第 04 步接入网站代码，再做第 {testNumber} 步闭环测试。只有网站询盘成功后实际发送并收到 Lead，才能核验接通；不要把示例点击代码直接粘贴成“点按钮就算询盘”。</p>
    <div className="loop-test-links"><button type="button" className="text-button" onClick={() => onReveal("pixel")}>查看 04 浏览器事件接入<Icon name="right" size={15} /></button><button type="button" className="text-button" onClick={() => onReveal("server")}>查看 {serverNumber} 服务器回传<Icon name="right" size={15} /></button><button type="button" className="text-button" onClick={onTest}>查看 {testNumber} 闭环测试<Icon name="right" size={15} /></button></div>
    <p>已有接入且同一数据集已收到 Lead 的，可跳过安装说明，继续核对下面的广告组设置。</p>
  </div>;
}
