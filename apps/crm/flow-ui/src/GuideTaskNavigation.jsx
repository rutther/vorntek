import React from 'react';
import {guideNumber, guideOrder} from './guidance.mjs';

const groups=[
  ['平台与网站',['meta-start','dataset','meta-adset','website','pixel']],
  ['接收与回传',['form','crm','filter','server']],
  ['广告与测试',['ad','closed-loop-test']],
];
const labels={
  'meta-start':'开始前准备',dataset:'选择数据集','meta-adset':'核对转化事件',
  website:'设置落地页',pixel:'设置追踪范围',form:'关联询盘表单',crm:'设置 CRM 规则',
  filter:'拟议筛选规则',server:'服务器回传',ad:'登记广告设置','closed-loop-test':'测试与核对',
};

export function GuideTaskNavigation({loop,activeKey,dirty,savingKey,onSelect,onRecords,onImages}) {
  const keys=guideOrder(loop);
  return <nav className="guide-task-navigation" aria-label="引导步骤">
    <label className="guide-task-compact">当前步骤
      <select aria-label="选择引导步骤" value={activeKey||''} onChange={event=>{
        const key=event.target.value;
        if(key==='illustrations')onImages();else if(key==='records')onRecords();else if(keys.includes(key))onSelect(key);
      }}>
        <option value="" disabled>选择要查看的步骤</option>
        {groups.map(([title,items])=><optgroup key={title} label={title}>{items.filter(key=>keys.includes(key)).map(key=><option key={key} value={key}>{guideNumber(loop,key)} {labels[key]}{savingKey===key?' · 正在保存…':dirty[key]?' · 未保存修改':loop.drafts[key]?' · 草稿已暂存':loop.configs[key]?' · 已保存':''}</option>)}</optgroup>)}
        <optgroup label="参考与历史"><option value="illustrations">全部操作图示</option><option value="records">核对与实操记录</option></optgroup>
      </select>
    </label>
    <div className="guide-task-expanded">
    {groups.map(([title,items])=><div className="guide-task-group" key={title}>
      <h3>{title}</h3>
      {items.filter(key=>keys.includes(key)).map(key=>{
        const status=savingKey===key?'正在保存…':dirty[key]?'未保存修改':loop.drafts[key]?'编辑草稿已暂存，尚未更新正式配置':loop.configs[key]?'配置已保存，不代表测试通过':null;
        return <button type="button" key={key} data-step={key} aria-current={activeKey===key?'step':undefined}
          aria-controls={`guide-panel-${key}`} onClick={()=>onSelect(key)}>
          <span className="guide-nav-number">{guideNumber(loop,key)}</span><span>{labels[key]}</span>
          {status&&<span className={`guide-nav-state ${savingKey===key||dirty[key]||loop.drafts[key]?'dirty':'saved'}`} role="img" aria-label={status} title={status}/>}
        </button>;
      })}
    </div>)}
    <div className="guide-task-group"><h3>参考与历史</h3>
      <button type="button" onClick={onImages}>全部操作图示</button>
      <button type="button" onClick={onRecords}>核对与实操记录</button>
    </div>
    </div>
  </nav>;
}
