import React,{useEffect,useRef,useState} from 'react';
import {Modal} from './ui.jsx';
import campaign from './assets/meta-campaign-guide.svg';
import createPixel from './assets/meta-create-pixel.svg';
import created from './assets/meta-pixel-created.svg';
import warning from './assets/meta-conversion-warning.svg';
import standard from './assets/meta-standard-event-route.svg';
import moreEvents from './assets/meta-add-event-code.svg';
import lead from './assets/meta-lead-installation.svg';
import destination from './assets/meta-destination-guide.svg';

const help='/static/console/meta-help/';
export const guideImages=[
 [campaign,'00 广告系列基础','名称 / 竞拍 / 潜在客户','meta-start'],
 [createPixel,'01 创建数据集','创建位置与名称','dataset'],
 [created,'01 创建后返回','继续接入，不是验收','dataset'],
 [warning,'02 设置提示','已选择事件但仍提示','meta-adset'],
 [standard,'02 标准事件路径','标准事件与自定义转化','meta-adset'],
 [moreEvents,'02 查看更多事件','展开安装事件列表','meta-adset'],
 [lead,'02 Lead 安装说明','用户操作 / 成功提交','meta-adset'],
 [destination,'03 广告目标位置','网站与附加组件','website'],
 [help+'meta-open-events-manager-20261007.svg','07 事件管理入口','凭据配置的前置入口','server'],
 [help+'meta-generate-access-token-20261007.svg','07 访问令牌入口','设置页 / 直接集成','server'],
 [help+'meta-open-events-manager-20261009.svg','09 测试观察入口','带标注的操作位置','closed-loop-test'],
 [help+'meta-test-website-events-20261009.svg','09 打开网站测试','网站区域 / 测试按钮','closed-loop-test'],
 [help+'meta-test-pageview-received-20261009.svg','09 浏览事件收到','PageView / 浏览器','closed-loop-test'],
 [help+'meta-lead-event-compare-redacted-20261009.svg','09 浏览器 Lead','完整事件编号与来源','closed-loop-test'],
 [help+'meta-server-event-expanded-redacted-20261009.svg','09 服务器 Lead','展开服务器子记录','closed-loop-test'],
 [help+'meta-dedup-tooltip-20261009.svg','09 两路去重','重复事件说明','closed-loop-test'],
].map(([src,title,description,stepKey],index)=>({id:index,src,title,description,stepKey,group:index<8?'platform':'integration'}));

function Picture({item}){
 const [failed,setFailed]=useState(false),[attempt,setAttempt]=useState(0);
 return failed?<div className="guide-image-failed" role="alert">图示暂时无法读取。<button type="button" className="text-button" onClick={()=>{setFailed(false);setAttempt(n=>n+1);}}>重新加载图示</button></div>:<img key={attempt} src={item.src} alt={item.title} onError={()=>setFailed(true)}/>;
}

export function GuideImageLibrary({onReturn,onStep}){
 const [filter,setFilter]=useState('all'),[selected,setSelected]=useState(null);
 const heading=useRef(null);
 useEffect(()=>{heading.current?.focus({preventScroll:true});heading.current?.scrollIntoView({block:'start'});},[]);
 const items=guideImages.filter(item=>filter==='all'||item.group===filter);
 return <section className="guide-image-library" aria-label="操作图示库">
  <header><div><h2 ref={heading} tabIndex={-1}>操作图示</h2><p>16 个操作示意图，按使用步骤归类；点击图片查看完整尺寸。</p></div><button type="button" className="button" onClick={onReturn}>返回配置任务</button></header>
  <div className="guide-library-toolbar"><div role="group" aria-label="图示分类">{[['all','全部 16'],['platform','平台设置 8'],['integration','接入与测试 8']].map(([key,label])=><button type="button" key={key} aria-pressed={filter===key} onClick={()=>setFilter(key)}>{label}</button>)}</div><span>说明图不是当前实例的实时证据</span></div>
  <div className="guide-library-grid">{items.map(item=><article className="guide-image-card" key={item.id}>
   <button type="button" onClick={()=>setSelected(item)} aria-label={`放大图示：${item.title}`}><img src={item.src} alt="" loading="lazy"/><strong>{item.title}</strong><small>{item.description}</small></button>
  </article>)}</div>
  {selected&&<Modal title={selected.title} wide onClose={()=>setSelected(null)}><div className="guide-library-detail"><p>{selected.description}。操作示意图，不是当前实例的实时证据。</p><Picture item={selected}/></div><footer className="modal-footer"><a className="button" href={selected.src} target="_blank" rel="noopener noreferrer">查看原图</a><button type="button" className="button primary" onClick={()=>{const key=selected.stepKey;setSelected(null);onStep(key);}}>到对应步骤</button></footer></Modal>}
 </section>;
}
