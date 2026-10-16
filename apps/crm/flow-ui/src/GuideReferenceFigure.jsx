import React,{useState} from 'react';
import {Icon} from './ui.jsx';

// Tutorial only: no instance state or observation can be derived from this image.
export function GuideReferenceFigure({src,alt,caption,title}) {
  const [failed,setFailed]=useState(false);
  return <figure className="guide-reference-figure">
    {title&&<h4>{title}</h4>}
    {failed?<div className="guide-reference-error" role="alert">图示暂时无法读取。
      <button type="button" className="text-button" onClick={()=>setFailed(false)}>重新加载图示</button>
    </div>:<a className="guide-reference-image" href={src} target="_blank" rel="noopener noreferrer" aria-label={`查看原图：${title||alt}`}>
      <img src={src} alt={alt} onError={()=>setFailed(true)}/>
    </a>}
    <figcaption><span>{caption||'操作示意图，不是当前实例的实时证据。'}</span>
      <a href={src} target="_blank" rel="noopener noreferrer">查看原图<Icon name="external" size={13}/></a>
    </figcaption>
  </figure>;
}
