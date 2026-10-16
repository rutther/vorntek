/* Corporate presentation; configured inquiry and measurement have separate owners. */
(() => {
  'use strict';
  const saved=()=>{try{return localStorage.getItem('vorntekLang');}catch{return null;}};
  let language=new URLSearchParams(location.search).get('lang')||saved()||'en';
  if(!['en','zh'].includes(language))language='en';
  const select=document.querySelector('#language');
  function translate(){
    document.documentElement.lang=language==='zh'?'zh-CN':'en';
    document.querySelectorAll('[data-en][data-zh]').forEach(node=>{
      if(node.tagName==='META')node.content=node.dataset[language];
      else node.textContent=node.dataset[language];
    });
    document.querySelectorAll('[data-alt-en][data-alt-zh]').forEach(node=>{node.setAttribute('alt',language==='zh'?node.dataset.altZh:node.dataset.altEn);});
    document.querySelectorAll('[data-aria-en][data-aria-zh]').forEach(node=>{node.setAttribute('aria-label',language==='zh'?node.dataset.ariaZh:node.dataset.ariaEn);});
    const title=document.querySelector('title[data-title-en]');
    if(title)document.title=language==='zh'?title.dataset.titleZh:title.dataset.titleEn;
    if(select)select.value=language;
    try{localStorage.setItem('vorntekLang',language);}catch{}
    document.querySelectorAll('a[href]').forEach(link=>{
      const href=link.getAttribute('href');
      if(!href?.startsWith('/')||href.startsWith('//')||href.startsWith('/assets/')||href.startsWith('/admin/'))return;
      const url=new URL(href,location.origin);
      // Article locale paths have their own authoritative switcher.
      if(/^\/(?:zh\/)?articles\/$/.test(url.pathname)){
        link.setAttribute('href',language==='zh'?'/zh/articles/':'/articles/');return;
      }
      if(url.pathname.includes('/articles/'))return;
      url.searchParams.set('lang',language);link.setAttribute('href',url.pathname+url.search+url.hash);
    });
    document.dispatchEvent(new CustomEvent('nc:language-changed',{detail:{language}}));
  }
  select?.addEventListener('change',()=>{
    language=select.value;const url=new URL(location.href);url.searchParams.set('lang',language);
    history.replaceState(null,'',url);translate();
  });
  const menu=document.querySelector('.menu-button');
  menu?.addEventListener('click',()=>{
    const expanded=menu.getAttribute('aria-expanded')!=='true';
    menu.setAttribute('aria-expanded',String(expanded));document.querySelector('#site-nav')?.classList.toggle('open',expanded);
  });
  document.addEventListener('keydown',event=>{
    if(event.key!=='Escape')return;
    document.querySelectorAll('.product-menu[open]').forEach(node=>node.removeAttribute('open'));
    if(menu?.getAttribute('aria-expanded')==='true'){menu.click();menu.focus();}
  });
  const tabs=[...document.querySelectorAll('[data-family-tab]')];
  function activate(tab){
    tabs.forEach(item=>{const active=item===tab;item.setAttribute('aria-selected',String(active));item.tabIndex=active?0:-1;});
    document.querySelectorAll('[data-family-panel]').forEach(panel=>{panel.hidden=panel.dataset.familyPanel!==tab.dataset.familyTab;});
  }
  tabs.forEach((tab,index)=>{
    tab.addEventListener('click',()=>activate(tab));
    tab.addEventListener('keydown',event=>{
      let next;if(event.key==='ArrowRight')next=(index+1)%tabs.length;
      if(event.key==='ArrowLeft')next=(index+tabs.length-1)%tabs.length;
      if(event.key==='Home')next=0;if(event.key==='End')next=tabs.length-1;
      if(next!==undefined){event.preventDefault();activate(tabs[next]);tabs[next].focus();}
    });
  });
  if(tabs.length)activate(tabs[0]);
  document.querySelectorAll('[data-gallery]').forEach(gallery=>{
    const main=gallery.querySelector('.gallery-main');
    gallery.querySelectorAll('[data-gallery-src]').forEach(button=>button.addEventListener('click',()=>{
      main.src=button.dataset.gallerySrc;
      gallery.querySelectorAll('[data-gallery-src]').forEach(other=>other.setAttribute('aria-pressed',String(other===button)));
    }));
  });
  document.querySelectorAll('[data-product-model]').forEach(root=>{
    let model=root.querySelector('model-viewer');
    const button=root.querySelector('[data-load-model]'),status=root.querySelector('[data-model-status]');
    let timer,attempts=0;
    const message=(en,zh)=>{status.textContent=language==='zh'?zh:en;};
    const observe=target=>{
      target.addEventListener('load',()=>{if(target!==model)return;clearTimeout(timer);button.hidden=true;button.disabled=false;message('Drag to rotate. Scroll or pinch to zoom.','拖动旋转，滚轮或双指缩放。');});
      target.addEventListener('error',()=>{if(target!==model)return;clearTimeout(timer);button.disabled=false;message('The 3D view is unavailable. The pictures and specifications remain available.','三维视图暂时不可用，仍可查看图片与规格。');});
    };
    observe(model);
    button?.addEventListener('click',()=>{
      button.disabled=true;message('Loading the equipment view…','正在加载设备视图…');
      let source=model.dataset.modelSrc;
      if(attempts>0){
        const fresh=model.cloneNode(false);fresh.removeAttribute('src');
        model.replaceWith(fresh);model=fresh;observe(model);
        // The pinned viewer also caches failed GLTF instances by URL. A fresh
        // element plus a same-origin retry key must issue a real new request.
        const retry=new URL(source,location.href);retry.searchParams.set('view_retry',String(attempts));
        source=retry.pathname+retry.search;
      }
      attempts++;model.setAttribute('src',source);
      clearTimeout(timer);timer=setTimeout(()=>{button.disabled=false;message('The view is taking longer to load. You can retry or continue with the pictures and specifications.','视图加载较慢，可重试或继续查看图片与规格。');},20000);
    });
  });
  document.querySelectorAll('[data-print]').forEach(button=>button.addEventListener('click',()=>window.print()));
  async function addInquiryContext(){
    const region=document.querySelector('[data-inquiry-context]'),form=document.querySelector('form[data-configured-form]');
    const id=new URLSearchParams(location.search).get('product');
    if(!region||!form||!id||!/^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/.test(id))return;
    try{
      const response=await fetch('/catalogue-context.json',{credentials:'same-origin'});if(!response.ok)return;
      const data=await response.json();const entry=data.products?.find(row=>row.id===id);if(!entry)return;
      const context=language==='zh'?'咨询设备：'+entry.nameZh:'Equipment of interest: '+entry.name;
      region.textContent=context;region.hidden=false;
      const apply=()=>{
        const field=form.querySelector('[name="message"]');if(!field)return false;
        if(!field.value.trim())field.value=context+'\n';return true;
      };
      if(!apply()){
        const observer=new MutationObserver(()=>{if(apply()){observer.disconnect();clearTimeout(expiry);}});
        observer.observe(form,{childList:true,subtree:true});const expiry=setTimeout(()=>observer.disconnect(),30000);
      }
    }catch{/* Inquiry remains usable if optional catalogue context cannot load. */}
  }
  translate();addInquiryContext();
})();
