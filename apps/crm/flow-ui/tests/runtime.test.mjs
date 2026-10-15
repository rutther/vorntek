import test from 'node:test';
import assert from 'node:assert/strict';
import {createApiClient} from '../src/apiClient.mjs';
import {build} from 'esbuild';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {JSDOM} from 'jsdom';
test('runtime retries preserve command identity and scoped endpoint',async()=>{
  const calls=[];let first=true;
  const api=createApiClient({apiUrl:'/admin/api/marketing/flows/',csrfToken:'synthetic'},async(url,init)=>{
    calls.push([url,JSON.parse(init.body),init.headers]);
    if(first){first=false;throw Error('lost');}
    return {ok:true,headers:{get:()=> 'application/json'},json:async()=>({runtime:{version:2}})};
  });
  const payload={action:'pause',expectedVersion:1};
  await assert.rejects(api.runtimeCommand('test-flow',payload));await api.runtimeCommand('test-flow',payload);
  assert.equal(calls[0][0],'/admin/api/marketing/flows/test-flow/runtime/');
  assert.equal(calls[0][1].requestId,calls[1][1].requestId);assert.equal(calls[0][2]['X-CSRFToken'],'synthetic');
});
const compiled=await build({entryPoints:[fileURLToPath(new URL('../src/RuntimePanel.jsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',external:['react'],loader:{'.css':'empty'}});
const mod={exports:{}};new Function('require','module','exports',compiled.outputFiles[0].text)(createRequire(import.meta.url),mod,mod.exports);
test('runtime controls separate save, apply, credential entry and external verification',()=>{
  const state={version:2,forms:[{id:1,code:'test',name:'测试表单'}],canOperate:true,blockingChecks:[{code:'credential',message:'缺少凭据'}],integration:{pixelId:'123456',editUrl:'/admin/marketing/integrations/1/'},preservedPixelIds:['999'],overlaps:[],runtimeState:'not_applied'};
  const runtime={state,values:{landing:'/',lead:'/contact/',form:'1'},dirty:false};
  const pixel=renderToStaticMarkup(React.createElement(mod.exports.RuntimePanel,{mode:'pixel',runtime,loop:{configs:{dataset:{externalId:'123456'}}}}));
  assert.match(pixel,/成功入库后发送/);assert.match(pixel,/保存接入范围/);assert.doesNotMatch(pixel,/Access Token.*input/);
  const server=renderToStaticMarkup(React.createElement(mod.exports.RuntimePanel,{mode:'server',runtime,loop:{id:'test-flow',configs:{}}}));
  assert.match(server,/缺少凭据/);assert.match(server,/disabled="">应用方案/);assert.match(server,/999/);
  assert.match(server,/href="\/admin\/marketing\/integrations\/1\/\?origin=guide&amp;flow=test-flow"/);
});

test('guide runtime puts real comparison first and keeps shared impact, inline credential reference and guarded controls',()=>{
 const state={version:5,flowVersion:8,configHash:'synthetic-hash',forms:[{id:1,name:'演练表单'}],canOperate:true,blockingChecks:[],integration:{pixelId:'900001',editUrl:'/admin/marketing/integrations/9/',enabled:false},sharedFlows:[{name:'关联演练方案',state:'inactive'}],preservedPixelIds:['900002'],overlaps:[],runtimeState:'not_applied',draft:{landingPaths:['/','/contact/'],leadPaths:['/contact/'],formId:1,consentRequired:true}};
 const render=extra=>renderToStaticMarkup(React.createElement(mod.exports.RuntimePanel,{mode:'server',workbench:true,runtime:{state,...extra},loop:{id:'local-test',configs:{dataset:{externalId:'900001'}}}}));
 const html=render({});
 assert.match(html,/guide-runtime-workbench/);
 assert.match(html,/范围草稿版本 <strong>5<\/strong>/);
 assert.doesNotMatch(html,/配置版本 <strong>5<\/strong>/);
 assert.ok(html.indexOf('当前生效与待应用范围比较')<html.indexOf('应用前检查'));
 assert.match(html,/<img[^>]+meta-generate-access-token-20261007/);
 assert.match(html,/关联演练方案/);assert.match(html,/其他未暂停方案也可能恢复发送/);
 assert.match(html,/900002/);assert.doesNotMatch(html,/<input[^>]+[Tt]oken/);
 assert.match(render({loading:true}),/disabled="">应用方案/);
 assert.match(render({error:'读取失败'}),/disabled="">应用方案/);
});

test('an applied unchanged plan leads to verification instead of redundant apply',()=>{
 const scope={landingPaths:['/'],leadPaths:['/'],formId:1,consentRequired:true};
 const state={version:2,forms:[{id:1,name:'测试表单'}],canOperate:true,blockingChecks:[],integration:{pixelId:'123456',editUrl:'/admin/marketing/integrations/1/'},preservedPixelIds:[],overlaps:[],runtimeState:'applied',configState:'saved',draft:scope,activeRevision:{key:'test-key',scope},history:[]};
 const render=overrides=>renderToStaticMarkup(React.createElement(mod.exports.RuntimePanel,{runtime:{state:{...state,...overrides}},loop:{id:'test-flow',configs:{}},direct:true}));
 const html=render({});
 assert.match(html,/href="#\/flow\/test-flow\?view=verification"[^>]*>查看核验记录/);
 assert.match(html,/无待应用修改/);assert.doesNotMatch(html,/应用方案<\/button>|本流程/);
 assert.match(render({configState:'unapplied_changes'}),/>应用方案<\/button>/);
 assert.match(render({runtimeState:'paused'}),/>恢复方案<\/button>/);
 const guide=renderToStaticMarkup(React.createElement(mod.exports.RuntimePanel,{runtime:{state},loop:{id:'test-flow',configs:{}}}));
 assert.match(guide,/view=tasks&amp;node=closed-loop-test/);
});

test('dependency status is local evidence, with specific maintenance links',()=>{
 const dependencies=[{key:'draft',label:'待应用表单',form:{id:1,name:'测试表单',editUrl:'/admin/leads/forms/1/'},issues:[],checks:[{label:'接收定义',status:'ready',detail:'已启用网站表单 · 提交事件 Lead'},{label:'邮件通知（选用）',status:'attention',detail:'已设置收件人，邮件服务未配置'}]}];
 const html=renderToStaticMarkup(React.createElement(mod.exports.RuntimeDependencies,{dependencies,canOperate:true}));
 assert.match(html,/本地配置检查/);assert.match(html,/不代表询盘入库或邮件送达/);
 assert.match(html,/href="\/admin\/leads\/forms\/1\/"/);assert.match(html,/href="\/admin\/system\/email\/"/);
 const readonly=renderToStaticMarkup(React.createElement(mod.exports.RuntimeDependencies,{dependencies,canOperate:false}));
 assert.doesNotMatch(readonly,/href="\/admin\/system\/email\/"/);
});

test('compact runtime checks expose every dependency and issue without an extra disclosure',()=>{
 const dependencies=[{key:'draft',label:'待应用表单',form:{id:1,name:'测试表单',editUrl:'/admin/leads/forms/1/'},issues:['表单范围需要重新核对'],checks:[
  {label:'接收定义',status:'ready',detail:'网站表单已启用'},
  {label:'销售阶段事件',status:'ready',detail:'contacted_lead、qualified_lead'},
  {label:'邮件通知（选用）',status:'attention',detail:'收件人存在，邮件服务未配置'},
 ]}];
 for(const canOperate of [true,false])for(const allReady of [false,true]){
  const rows=allReady?dependencies.map(d=>({...d,issues:[],checks:d.checks.map(c=>({...c,status:'ready',detail:c.status==='attention'?'收件人与邮件服务已配置':c.detail}))})):dependencies;
  const html=renderToStaticMarkup(React.createElement(mod.exports.RuntimeDependencies,{dependencies:rows,canOperate,compact:true}));
  const dom=new JSDOM(html),doc=dom.window.document;
  try{
   assert.equal(doc.querySelector('details'),null,'workbench checks must be directly readable even if all are ready');
   assert.equal(doc.querySelectorAll('dl>div').length,3);
   if(!allReady)assert.ok(doc.body.textContent.includes('表单范围需要重新核对'));
   for(const text of ['网站表单已启用','contacted_lead、qualified_lead',allReady?'收件人与邮件服务已配置':'收件人存在，邮件服务未配置'])assert.ok(doc.body.textContent.includes(text));
   assert.match(doc.body.textContent,/不代表.*入库.*送达/);
   assert.match(doc.body.textContent,/销售阶段事件由表单.*维护/);
   assert.equal(Boolean(doc.querySelector('a[href="/admin/system/email/"]')),canOperate);
   assert.ok(doc.querySelector('a[href="/admin/leads/forms/1/"]'));
  }finally{dom.window.close();}
 }
});

test('saving a draft does not relabel the applied version as the edit counter',()=>{
 const scope={landingPaths:['/'],leadPaths:['/'],formId:1,consentRequired:true};
 const state={version:2,forms:[{id:1,name:'测试表单'}],canOperate:true,blockingChecks:[],integration:{pixelId:'123456',editUrl:'/admin/marketing/integrations/1/'},preservedPixelIds:[],overlaps:[],runtimeState:'applied',configState:'saved',draft:scope,activeRevision:{key:'12345678-abcd-4000-9000-123456789012',scope},history:[]};
 const render=overrides=>renderToStaticMarkup(React.createElement(mod.exports.RuntimePanel,{runtime:{state:{...state,...overrides}},loop:{id:'test-flow',configs:{}},direct:true}));
 const before=render({}),after=render({version:3,configState:'unapplied_changes',draft:{...scope,landingPaths:['/contact/']}});
 for(const html of [before,after]){
  assert.match(html,/<span>已应用版本 <code title="12345678-abcd-4000-9000-123456789012">12345678<\/code><\/span>/);
  assert.doesNotMatch(html,/运行配置版本/);
 }
 assert.match(render({version:8,activeRevision:null,runtimeState:'not_applied'}),/尚无已应用版本/);
});
