/* Real public form definitions drive fields; no industry-specific fallback. */
(() => {
  'use strict';
  const core=new Set(['full_name','email','phone','company','country','message']);
  const types=new Set(['text','textarea','select','email','tel','number','date']);
  const reserved=new Set(['website','consent','client_event_id','form_started_at','measurement_context']);
  const labels={full_name:['Name','姓名'],email:['Email','邮箱'],phone:['Phone','电话'],
    company:['Company','公司'],country:['Country / region','国家或地区'],message:['Your request','咨询内容']};
  const zh=()=>document.documentElement.lang.toLowerCase().startsWith('zh');
  const copy=(en,cn)=>zh()?cn:en;
  const localized=(node,en,cn)=>{
    node.dataset.en=en;node.dataset.zh=cn;node.textContent=copy(en,cn);return node;
  };
  async function request(url,options={}){
    const controller=new AbortController();let timer;
    const deadline=new Promise((_,reject)=>{
      timer=setTimeout(()=>{controller.abort();reject(Error('timeout'));},25000);
    });
    try{
      return await Promise.race([(async()=>{
        const response=await fetch(url,{credentials:'same-origin',cache:'no-store',...options,signal:controller.signal});
        const result=await response.json();
        if(!response.ok||result.ok!==true)throw Error(response.status===429?'rate':'rejected');
        return result;
      })(),deadline]);
    }finally{clearTimeout(timer);}
  }
  document.querySelectorAll('form[data-configured-form]').forEach(form=>{
    const code=form.dataset.formCode||'';
    const fieldsRoot=form.querySelector('[data-inquiry-fields]');
    const status=form.querySelector('[data-form-status]');
    const submit=form.querySelector('[type=submit]');
    const retry=form.querySelector('[data-form-retry]');
    const consent=form.querySelector('input[name=consent][type=checkbox]');
    if(!fieldsRoot||!status||!submit||!consent)return;
    let fields=[],ready=false,busy=false,loading=false,started=false;
    let startedAt=new Date().toISOString(),eventId='',fingerprint='';
    const setStatus=(kind,en,cn=en)=>{status.dataset.kind=kind;localized(status,en,cn);};
    const formCodeValid=/^[a-zA-Z0-9_-]{1,100}$/.test(code);
    const base='/admin/api/leads/forms/'+encodeURIComponent(code)+'/';

    async function load(){
      if(loading||busy)return;
      loading=true;ready=false;submit.disabled=true;
      if(retry){retry.hidden=true;retry.disabled=true;}
      setStatus('pending','Loading form…','正在加载表单…');
      try{
        if(!formCodeValid)throw Error('invalid form');
        const result=await request(base+'definition/');
        const definition=result.form;
        if(definition?.code!==code||!Array.isArray(definition.fields)||!definition.fields.length||definition.fields.length>64)
          throw Error('invalid definition');
        const names=new Set();
        for(const field of definition.fields){
          if(!field||!/[a-z]/.test(field.name)||!/^[a-z][a-z0-9_]{0,79}$/.test(field.name)
            ||reserved.has(field.name)||names.has(field.name)||!types.has(field.type)
            ||typeof field.required!=='boolean'||typeof field.label!=='string'||field.label.length>120)
            throw Error('invalid field');
          names.add(field.name);
          if(field.type==='select'&&(!Array.isArray(field.options)||field.options.length>200))throw Error('invalid options');
          for(const option of field.options||[]){
            const value=typeof option==='string'?option:option?.value;
            const label=typeof option==='string'?option:option?.label??value;
            if(typeof value!=='string'||!value.trim()||value.length>120||typeof label!=='string'||label.length>120)
              throw Error('invalid option');
          }
        }
        if(!names.has('email')&&!names.has('phone'))throw Error('missing contact');
        const fragment=document.createDocumentFragment();
        fields=definition.fields.map(field=>{
          const label=document.createElement('label');
          if(field.type==='textarea')label.classList.add('inquiry-field-wide');
          const builtin=labels[field.name];
          const pair=builtin&&field.label===builtin[1]?builtin:[field.label,field.label];
          const caption=document.createElement('span');
          localized(caption,pair[0]+(field.required?' *':''),pair[1]+(field.required?' *':''));
          label.append(caption);
          const input=document.createElement(field.type==='textarea'?'textarea':field.type==='select'?'select':'input');
          input.name=field.name;input.required=field.required;
          if(input.tagName==='INPUT')input.type=field.type;
          if(field.type==='number')input.step='any';
          if(field.type==='textarea')input.rows=4;
          if(field.type==='select'){
            const empty=document.createElement('option');empty.value='';
            input.append(localized(empty,'Choose an option','请选择'));
            for(const option of field.options){
              const item=document.createElement('option');
              item.value=typeof option==='string'?option:option.value;
              item.textContent=typeof option==='string'?option:option.label??option.value;
              input.append(item);
            }
          }else{
            input.maxLength=({full_name:120,email:254,phone:40,company:200,country:120,message:4000})[field.name]||500;
            input.placeholder=typeof field.placeholder==='string'?field.placeholder:'';
          }
          input.autocomplete=({full_name:'name',email:'email',phone:'tel',company:'organization',country:'country-name'})[field.name]||'off';
          label.append(input);fragment.append(label);
          return {...field,input};
        });
        fieldsRoot.replaceChildren(fragment);ready=true;submit.disabled=false;
        setStatus('idle','Provide an email or phone. Required fields are marked *.','邮箱或电话至少填写一项，* 为必填。');
      }catch(_error){
        setStatus('error','The form could not be loaded. Retry loading before submitting.','表单暂时无法加载，请先重试加载后再提交。');
        if(retry){retry.hidden=false;retry.disabled=false;}
      }finally{loading=false;}
    }
    retry?.addEventListener('click',load);
    function start(event){
      if(busy||!ready||!event.isTrusted||event.target?.name==='website')return;
      setStatus('idle','');
      if(!started){started=true;document.dispatchEvent(new CustomEvent('nc:form-started',{detail:{formCode:code}}));}
    }
    form.addEventListener('input',start,{passive:true});form.addEventListener('change',start,{passive:true});
    form.addEventListener('submit',async event=>{
      event.preventDefault();if(!ready||busy)return;
      setStatus('idle','');
      if(!form.checkValidity()||fields.some(field=>field.required&&!field.input.value.trim())){
        form.reportValidity();setStatus('error','Check the required fields and formats.','请检查必填项和格式。');return;
      }
      const values=Object.fromEntries(fields.map(field=>[field.name,field.input.value.trim()]));
      if(!values.email&&!values.phone){
        setStatus('error','Provide an email address or phone number.','邮箱或电话至少填写一项。');
        fields.find(field=>field.name==='email'||field.name==='phone')?.input.focus();return;
      }
      const current=JSON.stringify(values);
      if(current!==fingerprint){eventId=crypto.randomUUID();fingerprint=current;}
      const marketing=typeof window.ncPrivacyConsentGranted==='function'&&window.ncPrivacyConsentGranted()===true;
      const attribution=marketing&&typeof window.ncAdAttribution==='function'?window.ncAdAttribution():{};
      const identifiers=marketing&&typeof window.ncMetaIdentifiers==='function'?window.ncMetaIdentifiers():{};
      const payload={...identifiers,extra_fields:{},source_url:location.href,referrer_url:document.referrer,
        source_channel:'website',route_path:location.pathname,client_event_id:eventId,form_started_at:startedAt,
        website:form.querySelector('input[name=website]')?.value||'',
        consent:{contact:consent.checked,privacy_notice:consent.checked,marketing,recorded_at:new Date().toISOString()},utm:{}};
      for(const [name,value] of Object.entries(values)){
        if(core.has(name))payload[name]=value;else payload.extra_fields[name]=value;
      }
      if(marketing){
        for(const key of ['utm_source','utm_medium','utm_campaign','utm_content','utm_term'])if(attribution[key])payload.utm[key]=attribution[key];
        for(const key of ['gclid','gbraid','wbraid'])payload[key]=attribution[key]||'';
        payload.google_session_attributes=typeof window.ncGoogleSessionAttributes==='function'?window.ncGoogleSessionAttributes():'';
      }
      busy=true;submit.disabled=true;form.setAttribute('aria-busy','true');
      setStatus('pending','Submitting…','正在提交…');
      try{
        payload.measurement_context=marketing&&typeof window.ncMeasurementContext==='function'?await window.ncMeasurementContext():'';
        const result=await request(base+'submit/',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify(payload)});
        if(result.duplicate)setStatus('success','Already received; no duplicate was created.','此询盘已接收，未重复创建。');
        else setStatus('success','Inquiry received.','询盘已接收。');
        if(result.measurement?.shouldTrack||!result.duplicate)
          document.dispatchEvent(new CustomEvent('nc:lead-accepted',{detail:{formCode:code,
            eventId:result.measurement?.eventId||eventId,measurement:result.measurement}}));
        form.reset();started=false;fingerprint='';eventId='';startedAt=new Date().toISOString();
      }catch(error){
        if(error.message==='rate')setStatus('error','Too many attempts. Entries are preserved; try later.','提交较频繁，内容已保留，请稍后重试。');
        else setStatus('error','Submission was not confirmed. Entries are preserved; retry the same request.','提交未确认成功，内容已保留，请重试同一次请求。');
      }finally{busy=false;submit.disabled=false;form.setAttribute('aria-busy','false');}
    });
    load();
  });
})();
