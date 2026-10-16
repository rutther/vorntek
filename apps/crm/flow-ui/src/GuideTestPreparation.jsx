import React from 'react';
import {Icon} from './ui.jsx';
import {GuideReferenceFigure} from './GuideReferenceFigure.jsx';

// Reading and navigating this guide never submits an inquiry or writes evidence.
export function GuideTestPreparation({loop,onReveal}) {
  const destination=loop.configs.website?.url;
  return <>
    <div className="guide-test-preparation">
      <div className="guide-test-actions">
        <section>
          <h4><span className="evidence-number">1</span>打开同一数据集的测试事件</h4>
          <p>核对数字 ID，进入「测试事件」，记下开始时间。</p>
          <div className="guide-test-dataset"><span>本实例的数据集</span><strong>{loop.configs.dataset?.externalId||'尚未登记'}</strong><button type="button" className="text-button" onClick={()=>onReveal('dataset')}>核对数据集<Icon name="chevron" size={14}/></button></div>
          <GuideReferenceFigure src="/static/console/meta-help/meta-open-events-manager-20261009.svg" title="事件管理工具入口" alt="Meta 广告编辑页入口示例，红框标出前往事件管理工具按钮" caption="点击红框；无提示时直接进入事件管理工具。"/>
        </section>
        <section>
          <h4><span className="evidence-number">2</span>在下方网站测试区打开网址</h4>
          <p>展开「确认你的网站事件设置正确」，填入下方网址，点「测试事件」。不要用上方服务器测试码或图谱 API 探索工具。</p>
          <div className="guide-test-destination" role="group" aria-label="本实例已保存的测试网址">
            <span>本实例已保存的网址</span>
            {destination?<code>{destination}</code>:<strong>尚未登记落地页，请先到第 03 步保存。</strong>}
            <button type="button" className="text-button" onClick={()=>onReveal('website')}>核对落地页<Icon name="chevron" size={14}/></button>
          </div>
          <div className="guide-test-note"><Icon name="info" size={18}/><div>在获准范围接受营销追踪后刷新。若 fbevents.js 报 ERR_BLOCKED_BY_CLIENT，只对测试网站临时放行，测后恢复，不要关闭所有防护。</div></div>
        </section>
      </div>
      <aside className="guide-test-pictures" aria-label="测试操作对照图">
        <h4>你应该点击这里</h4>
        <div className="guide-test-website-picture"><GuideReferenceFigure src="/static/console/meta-help/meta-test-website-events-20261009.svg" alt="Meta 测试事件页面，下方网站测试区填写网址并点击测试事件按钮" caption="网址与测试按钮在下方，不照抄示例网址。"/></div>
        <section className="guide-test-observe">
          <h4><span className="evidence-number">3</span>先看 PageView，再提交一次询盘</h4>
          <p>回 Meta 核对 PageView 的浏览器来源和本次时间，再提交一次获准的测试询盘。已提交过，不要重交。</p>
          <div className="guide-test-pageview-picture"><GuideReferenceFigure src="/static/console/meta-help/meta-test-pageview-received-20261009.svg" alt="Meta 接收 PageView 的示例：处理完毕，来源为浏览器，旁边显示接收时间" caption="仅浏览事件示例，不证明 Lead 或去重。"/></div>
          <p className="guide-test-recovery">报错或超时先查 CRM；同次提交应只有一条询盘，再继续核对 Lead 与去重。<button type="button" className="text-button" onClick={()=>onReveal('form')}>回查本实例表单接入<Icon name="right" size={15}/></button></p>
        </section>
      </aside>
    </div>
  </>;
}
