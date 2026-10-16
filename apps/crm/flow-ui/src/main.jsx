import './hk7-baseline.css';
import './runtime-dependencies.css';
import React from 'react';
import {createRoot} from 'react-dom/client';
import {App} from './App.jsx';
import {createApiClient} from './apiClient.mjs';
import {DEFS} from './model.mjs';

const root=createRoot(document.getElementById('root'));
async function start(){
  try{
    const element=document.getElementById('flow-bootstrap');
    if(!element)throw Error('CRM bootstrap missing');
    const bootstrap=JSON.parse(element.textContent);
    if(bootstrap.mode!=='crm'||!bootstrap.csrfToken)throw Error('Invalid CRM bootstrap');
    const api=createApiClient(bootstrap);
    const data=await api.load();
    if(!Array.isArray(data.loops)||!data.definitions)throw Error('Invalid CRM response');
    for(const [key,value] of Object.entries(data.definitions)){
      if(DEFS[key])Object.assign(DEFS[key],value);
    }
    root.render(<App crm initialData={data} api={api} surface={bootstrap.surface||'guide'}/>);
  }catch(error){
    root.render(<div className="global-error" role="alert"><p>无法读取 CRM 工作区设置：{error.message}</p><button className="button" onClick={start}>重新读取</button></div>);
  }
}
start();
