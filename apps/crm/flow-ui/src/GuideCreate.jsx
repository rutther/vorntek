import React,{useEffect,useRef,useState} from 'react';
import {Note} from './ui.jsx';
import {GuideTypeCards} from './GuideList.jsx';

export function GuideCreate({onCreate,onCancel,guard,busy=false}){
 const [values,setValues]=useState({name:'',environment:'',note:''});
 const [error,setError]=useState(''),[saving,setSaving]=useState(false);
 const lock=useRef(false),current=useRef(null);
 current.current={kind:'create',dirty:!!(values.name||values.environment||values.note),save:async()=>false};
 useEffect(()=>{const proxy={kind:'create',get dirty(){return current.current.dirty||lock.current;},save:async()=>false};guard.current=proxy;return()=>{if(guard.current===proxy)guard.current=null;};},[guard]);
 const set=(key,value)=>setValues(previous=>({...previous,[key]:value}));
 const submit=async event=>{
  event.preventDefault();if(busy||lock.current)return;
  if(values.name.trim().length<2||!['test','production'].includes(values.environment)){setError('请填写至少两个字的闭环名称，并选择业务环境。');return;}
  lock.current=true;setSaving(true);setError('');
  try{if(!await onCreate({...values,name:values.name.trim(),note:values.note.trim()}))setError('本次尚未创建成功，输入已保留。请根据上方提示重试。');}
  catch(e){setError(e.message||'创建失败，输入已保留。');}
  finally{lock.current=false;setSaving(false);}
 };
 const disabled=busy||saving;
 return <section className="guide-create-page">
  <header className="guide-page-heading"><div><h1>新建闭环</h1><p>先选类型，再创建实例。同一类型可以重复使用。</p></div><button className="button" disabled={disabled} onClick={onCancel}>返回闭环列表</button></header>
  <div className="guide-create-paper"><section className="guide-create-types"><h2>选择获客路径</h2><GuideTypeCards/><Note>当前开放的创建路线为 Meta 网站获客。其他类型显示真实开放状态，不伪装成可运行功能。</Note><h3>这条闭环包含什么</h3><dl><div><dt>一张跨系统流程图</dt><dd>看清网站、Meta 与 CRM 的连接关系。</dd></div><div><dt>逐步配置与操作图示</dt><dd>每一步告诉你去哪里操作、看哪里、带回什么。</dd></div><div><dt>独立的运行与核对记录</dt><dd>不会把另一个实例的测试结果当成本次结果。</dd></div></dl></section>
  <form className="guide-create-form" onSubmit={submit}><h2>命名本次实例</h2><p>名称用于区分业务，不需要与 Meta 广告名称相同。</p><label className="field">闭环名称 <span className="required">必填</span><input name="name" required minLength={2} maxLength={200} autoFocus value={values.name} disabled={disabled} onChange={e=>set('name',e.target.value)} placeholder="例如：瓶装水产线 · 东南亚询盘"/><small>建议用“产品 / 市场 / 目的”命名。</small></label><label className="field">业务环境 <span className="required">必填</span><select name="environment" required value={values.environment} disabled={disabled} onChange={e=>set('environment',e.target.value)}><option value="">请选择环境</option><option value="production">正式业务</option><option value="test">演练测试</option></select><small>环境影响后续可选资源范围，请按实际用途选择。</small></label><label className="field">备注（选填）<textarea name="note" maxLength={1000} rows={4} value={values.note} disabled={disabled} onChange={e=>set('note',e.target.value)}/><small>最多1000字，不填写密钥或客户个人信息。</small></label><Note>创建只建立 CRM 草稿，不会创建 Meta 数据集、启用网站追踪或发布广告。</Note>{error&&<p className="form-error" role="alert">{error}</p>}<div className="guide-create-actions"><button className="button primary" disabled={disabled}>{saving?'正在创建…':'创建并打开引导'}</button><button type="button" className="button" disabled={disabled} onClick={onCancel}>取消</button></div></form></div>
 </section>;
}
