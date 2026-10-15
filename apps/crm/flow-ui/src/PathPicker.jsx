import React,{useRef,useState} from 'react';
import {filterPages,mergePaths,pageLabel,localeLabel} from './pixel-model.mjs';

export function PathPicker({id,label,hint,pages,selected,onChange,disabled,error,available=true,manual='',onManualChange=()=>{},initiallyOpen=false,eventName}){
 const [query,setQuery]=useState(''),[manualError,setManualError]=useState('');
 const details=useRef(null);
 const all=[...pages,...selected.filter(path=>!pages.some(p=>p.path===path)).map(path=>({path,title:'待核验路径',locale:'',formCodes:[],missing:true}))];
 const visible=filterPages(all,query);
 const chosen=selected.map(path=>all.find(p=>p.path===path));
 return <section className="pixel-field" aria-labelledby={`${id}-label`}>
  <header><label id={`${id}-label`}><strong>{eventName||label}</strong><span className="required"> ＊必填</span></label><span>可多选 · 已选 {selected.length} 项</span></header>
  <p id={`${id}-hint`}>{hint}</p>
  {eventName&&chosen.length>0&&<div className="pixel-chosen-pages" aria-label={`${label}已选页面`}>{chosen.map(p=><span key={p.path} className="pixel-chosen-page" title={p.title}><span>{pageLabel(p)} <small>{p.path}</small></span><button type="button" disabled={disabled} aria-label={`从${label}移除 ${p.path}`} onClick={()=>{onChange(selected.filter(path=>path!==p.path));details.current.querySelector('summary').focus();}}><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg></button></span>)}</div>}
  <details className={`pixel-picker${eventName?' pixel-picker-guide':''}`} open={initiallyOpen||undefined} ref={details} onKeyDown={e=>{if(e.key==='Escape'){details.current.open=false;details.current.querySelector('summary').focus();}}}>
   <summary id={id} aria-describedby={`${id}-hint`} aria-invalid={Boolean(error)} aria-label={`${label}，已选${selected.length}项：${chosen.map(p=>`${pageLabel(p)} ${p.path}`).join('、')}，点击展开或收起`}>
    {eventName?<span>选择 / 修改{label}</span>:<span className="pixel-selection">{chosen.length?chosen.slice(0,2).map(p=><span className="pixel-selection-item" key={p.path} title={`${p.title} · ${p.path}`}><span>{pageLabel(p)}</span><small>{p.path}</small></span>):<span className="pixel-placeholder">请选择页面</span>}{chosen.length>2&&<span className="pixel-selection-more">+{chosen.length-2}项</span>}</span>}
    <svg className="pixel-picker-chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>
   </summary>
   <div className="pixel-picker-body"><input type="search" aria-label={`搜索${label}`} placeholder="输入页面名称、路径或语言，自动筛选" value={query} disabled={disabled} onChange={e=>setQuery(e.target.value)}/>
    <div className="pixel-options">{visible.map(p=><label key={p.path} className={selected.includes(p.path)?'is-selected':''}>
     <input type="checkbox" checked={selected.includes(p.path)} disabled={disabled||(!selected.includes(p.path)&&selected.length>=30)} onChange={e=>onChange(e.target.checked?[...selected,p.path]:selected.filter(path=>path!==p.path))}/>
     <span><strong>{pageLabel(p)}</strong><small>{p.path}</small>{pageLabel(p)!==p.title&&<small className="pixel-page-title">{p.title}</small>}</span><em>{p.missing?'不在当前目录':localeLabel(p.locale)}</em></label>)}
     {!visible.length&&<p role="status">{available?'没有匹配的页面，请更换关键词或手动添加。':'目录暂不可用，已选内容仍保留。'}</p>}</div>
   </div>
  </details>
  {error&&<p className="pixel-field-error" role="alert">{error}</p>}
  <details className="pixel-manual"><summary>手动添加路径</summary><p>每行一个本站路径，与勾选结果合并；每组最多30项，手填路径也会核验。</p><textarea aria-label={`手动添加${label}`} value={manual} disabled={disabled} onChange={e=>{onManualChange(e.target.value);setManualError('');}}/>
   <div className="pixel-inline-actions"><button type="button" className="button" disabled={disabled||!manual.trim()} onClick={()=>{try{onChange(mergePaths(selected,manual));onManualChange('');setManualError('');}catch(e){setManualError(e.message);}}}>加入已选页面</button></div>{manualError&&<p role="alert" className="pixel-field-error">{manualError}</p>}
  </details>
 </section>;
}

