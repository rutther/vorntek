(() => {
  'use strict';
  const businessLines = ['circulatingWaterControl','liquidControl','waterQualitySensors','gasSensors','propaneMicroHeaters','smallTurbojetEngines','industrialDataAnalysis'];
  const hints = [
    ['Application, existing pumps and control needs.','应用、现有泵组与控制需求。'],
    ['Liquid medium, process step and installation environment.','液体介质、工艺环节与安装环境。'],
    ['Measurement need, sampling arrangement and integration.','测量需求、采样安排与系统对接。'],
    ['Target gas, installation environment and safety-critical use.','目标气体、安装环境及是否涉及安全关键用途。'],
    ['Intended use, installation constraints and destination market.','用途、安装限制与目标市场。'],
    ['Civilian use, test facilities and project stage.','民用用途、试验设施与项目阶段。'],
    ['Data sources, user roles and the questions to analyse.','数据来源、使用角色与需要分析的问题。']
  ];
  const readSaved = () => { try { return localStorage.getItem('vorntekLang'); } catch { return null; } };
  let lang = new URLSearchParams(location.search).get('lang') || readSaved() || 'en';
  if (!['en','zh'].includes(lang)) lang = 'en';
  try {localStorage.setItem('vorntekLang',lang);} catch {}
  const language = document.querySelector('#language');
  function translate() {
    document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en';
    document.querySelectorAll('[data-en][data-zh]').forEach(node => { node.textContent = node.dataset[lang]; });
    if(language) language.value = lang;
    updateHint();
  }
  language?.addEventListener('change', () => {
    lang = language.value;
    try {localStorage.setItem('vorntekLang',lang);} catch {}
    const url = new URL(location.href); url.searchParams.set('lang',lang); history.replaceState(null,'',url);
    translate(); renderData();
  });
  const menu = document.querySelector('.menu-button');
  menu?.addEventListener('click', () => { const opened = document.querySelector('#site-nav').classList.toggle('open'); menu.setAttribute('aria-expanded',String(opened)); });
  const form = document.querySelector('#project-form');
  const status = document.querySelector('#form-status');
  const businessSelect = form?.elements.business_line;
  const selected = new URLSearchParams(location.search).get('business');
  if(businessSelect && businessLines.includes(selected)) businessSelect.value=selected;
  function updateHint() {
    if(!businessSelect) return;
    const entry=hints[businessLines.indexOf(businessSelect.value)];
    document.querySelector('#business-help').textContent=entry ? entry[lang==='zh'?1:0] : '';
  }
  businessSelect?.addEventListener('change',updateHint);
  function setStatus(kind,message){if(status){status.dataset.kind=kind;status.textContent=message;}}
  function validateForm(){
    if(!form.checkValidity()){form.reportValidity();setStatus('error',lang==='zh'?'请检查必填项和邮箱格式。':'Check the required fields and email format.');return false;}
    if(!form.elements.email.value.trim()&&!form.elements.phone.value.trim()){setStatus('error',lang==='zh'?'邮箱或电话至少填写一项。':'Provide an email address or phone number.');form.elements.email.focus();return false;}
    if(!businessLines.includes(businessSelect.value)){setStatus('error',lang==='zh'?'请选择业务方向。':'Choose a business area.');return false;}
    for(const name of ['full_name','company','country','message']){if(form.elements[name].value.trim().length<2){setStatus('error',lang==='zh'?'姓名、公司、地区和项目说明至少填写两个字符。':'Use at least two characters for name, company, region and project requirement.');form.elements[name].focus();return false;}}
    return true;
  }
  let submitting=false, formStarted=false, startedAt=new Date().toISOString(), eventId='', fingerprint='';
  function uuid(){if(globalThis.crypto?.randomUUID)return crypto.randomUUID();const b=new Uint8Array(16);crypto.getRandomValues(b);b[6]=(b[6]&15)|64;b[8]=(b[8]&63)|128;return [...b].map((x,i)=>([4,6,8,10].includes(i)?'-':'')+x.toString(16).padStart(2,'0')).join('');}
  function startForm(event){
    if(!submitting)setStatus('idle','');
    if(formStarted || !event.isTrusted || event.target?.name==='website')return;
    formStarted=true;
    document.dispatchEvent(new CustomEvent('nc:form-started',{detail:{formCode:'project-inquiry',inquiryType:businessSelect.value,productCategory:businessSelect.selectedOptions[0]?.dataset.en||''}}));
  }
  form?.addEventListener('input',startForm,{passive:true});
  form?.addEventListener('change',startForm,{passive:true});
  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submitting) return;
    setStatus('idle', '');
    if (!validateForm()) return;
    const formData = new FormData(form);
    const fields=Object.fromEntries(formData.entries());
    const current=JSON.stringify(fields);
    if(current!==fingerprint){eventId=uuid();fingerprint=current;}
    const marketing=typeof window.ncPrivacyConsentGranted==='function' && window.ncPrivacyConsentGranted();
    const attribution=marketing&&typeof window.ncAdAttribution==='function'?window.ncAdAttribution():{};
    const identifiers=marketing&&typeof window.ncMetaIdentifiers==='function'?window.ncMetaIdentifiers():{};
    const utm={};for(const key of ['utm_source','utm_medium','utm_campaign','utm_content','utm_term']){if(attribution[key])utm[key]=attribution[key];}
    const payload={full_name:fields.full_name.trim(),company:fields.company.trim(),country:fields.country.trim(),email:fields.email.trim(),phone:fields.phone.trim(),message:fields.message.trim(),website:fields.website||'',source_url:location.href,referrer_url:document.referrer,source_channel:'website',route_path:location.pathname,client_event_id:eventId,form_started_at:startedAt,extra_fields:{business_line:fields.business_line,product_category:businessSelect.selectedOptions[0].dataset.en,application_context:fields.application_context.trim()},consent:{contact:true,privacy_notice:true,marketing:Boolean(marketing),recorded_at:new Date().toISOString()},utm,...identifiers,gclid:attribution.gclid||'',gbraid:attribution.gbraid||'',wbraid:attribution.wbraid||'',google_session_attributes:marketing&&typeof window.ncGoogleSessionAttributes==='function'?window.ncGoogleSessionAttributes():''};
    const submit=form.querySelector('[type=submit]');
    const abort=new AbortController();const timer=setTimeout(()=>abort.abort(),25000);
    submitting=true;submit.disabled=true;setStatus('pending',lang==='zh'?'正在提交…':'Submitting…');
    try{
      const response=await fetch('/admin/api/leads/forms/project-inquiry/submit/',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify(payload),signal:abort.signal});
      const result=await response.json();
      if(!response.ok||!result.ok)throw new Error(response.status===429?'rate':'rejected');
      setStatus('success',lang==='zh'?(result.duplicate?'此询盘已接收，未重复创建记录。':'询盘已存入演示CRM。'):(result.duplicate?'This inquiry was already received. No duplicate was created.':'Inquiry received in the demo CRM.'));
      if(!result.duplicate)document.dispatchEvent(new CustomEvent('nc:lead-accepted',{detail:{eventId,formCode:'project-inquiry',inquiryType:fields.business_line,productCategory:payload.extra_fields.product_category}}));
      form.reset();updateHint();fingerprint='';eventId='';formStarted=false;startedAt=new Date().toISOString();
    }catch(error){setStatus('error',lang==='zh'?(error.message==='rate'?'提交较频繁，请稍后再试；内容已保留。':'提交未确认成功，内容已保留，请稍后重试。'):(error.message==='rate'?'Too many attempts. Your entries are preserved; try later.':'Submission was not confirmed. Your entries are preserved; please retry.'));}
    finally{clearTimeout(timer);submitting=false;submit.disabled=false;}
  });
  let demoRows=[];
  function renderData(){
    const chart=document.querySelector('#data-chart');if(!chart)return;
    const hours=Number(document.querySelector('#data-window').value);
    demoRows=Array.from({length:hours},(_,i)=>({hour:i,temperature:Number((26+Math.sin(i*.34)*2+.5*Math.cos(i*.13)).toFixed(2)),load:Number((58+Math.cos(i*.24)*11).toFixed(1))}));
    const points=demoRows.map((r,i)=>`${(i/(hours-1)*780+10).toFixed(1)},${(190-(r.temperature-22)*22).toFixed(1)}`).join(' ');
    chart.innerHTML=`<line x1="10" y1="100" x2="790" y2="100" stroke="#dce2e9"/><polyline points="${points}" fill="none" stroke="#2375b8" stroke-width="3"/>`;
    const mean=demoRows.reduce((a,r)=>a+r.temperature,0)/hours;
    document.querySelector('#data-metrics').innerHTML=`<div><b>${mean.toFixed(1)} °C</b><span>${lang==='zh'?'平均温度（模拟）':'Mean temperature (synthetic)'}</span></div><div><b>${hours}</b><span>${lang==='zh'?'合成采样点':'Synthetic samples'}</span></div><div><b>0</b><span>${lang==='zh'?'连接的真实设备':'Real devices connected'}</span></div>`;
    document.querySelector('#data-description').textContent=lang==='zh'?'趋势及CSV均由同一份确定性合成数据生成，仅用于功能演示。':'The chart and CSV use the same deterministic synthetic dataset. Demonstration only.';
    const csv='synthetic_hour,synthetic_temperature_c,synthetic_load_percent\n'+demoRows.map(r=>`${r.hour},${r.temperature},${r.load}`).join('\n')+'\n';
    const download=document.querySelector('#data-export');
    download.href='data:text/csv;charset=utf-8,'+encodeURIComponent(csv);
    download.download=`vorntekSyntheticData${hours}h.csv`;
  }
  document.querySelector('#data-window')?.addEventListener('change',renderData);
  translate();renderData();
})();
