"""Content-driven public templates for the optional Vorntek company package.

Only trusted templates produce HTML; all content values are escaped. The build
does not read databases, change active publications or remove existing files.
"""
from __future__ import annotations

import hashlib
import html
import json
import math
from pathlib import Path
import re
import shutil
from string import Template
from urllib.parse import urlencode

ROOT=Path(__file__).resolve().parents[1]
SITE=ROOT/'apps/website'
CONTENT=ROOT/'docs/vorntekDemo'
ID=re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}')
ARROW='<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M4 12h15m-6-6 6 6-6 6"/></svg>'


def esc(value):return html.escape(str(value),quote=True)


def pair(value):
    if isinstance(value,(list,tuple)) and len(value)==2:return str(value[0]),str(value[1])
    return str(value),str(value)


def t(value,tag='span',cls=''):
    en,zh=pair(value)
    return f'<{tag}'+(f' class="{esc(cls)}"' if cls else '')+f' data-en="{esc(en)}" data-zh="{esc(zh)}">{esc(en)}</{tag}>'


def named(item):return item['name'],item.get('nameZh',item['name'])


def aria(label):
    en,zh=pair(label)
    return f'aria-label="{esc(en)}" data-aria-en="{esc(en)}" data-aria-zh="{esc(zh)}"'


def local_asset(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*\.(?:png|jpg|webp|svg|glb|webm|woff2)',value):
        raise ValueError('Invalid local catalogue resource')
    return value


def template(filename,**values):
    return Template((CONTENT/'templates'/filename).read_text(encoding='utf-8')).substitute(values)


def button(label,href,primary=True,attrs=''):
    return f'<a class="button {"primary" if primary else "secondary"}" href="{esc(href)}" {attrs}>{t(label)}{ARROW}</a>'


class ExampleWebsite:
    def __init__(self,catalog):
        self.catalog=catalog;self.brand=catalog['brand'];self.products=catalog['productLines']
        self.families=catalog.get('families',[]);self.solutions=catalog.get('solutions',[])
        self.editorial=json.loads((CONTENT/'pages.json').read_text(encoding='utf-8'))
        for collection in (self.products,self.families,self.solutions,self.editorial.get('guides',[])):
            identifiers=[row['id'] for row in collection]
            if len(identifiers)!=len(set(identifiers)) or any(not isinstance(v,str) or not ID.fullmatch(v) for v in identifiers):
                raise ValueError('Catalogue IDs must be unique local route segments')
        self.assets={'vorntekLogo.png',local_asset(catalog['visuals']['hero'])}
        self.assets.update({'fonts/manrope.woff2','fonts/OFL.txt'})
        for row in self.products:
            if 'dimensions' in row:
                sizes=row['dimensions']
                if (not isinstance(sizes,list) or len(sizes)!=3 or
                    any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<v<=100000 for v in sizes)):
                    raise ValueError('Equipment dimensions must be three finite positive millimetre values')
            if row.get('asset'):self.assets.add(local_asset(Path(row['asset']).name))
            self.assets.update(local_asset(v) for v in row.get('gallery',[]))
            if row.get('model'):self.assets.add(local_asset('models/'+row['model']))
            if row.get('video'):self.assets.add(local_asset(row['video']))
        self.assets.update(local_asset(row['image']) for row in self.solutions)
        self.hashes={key:hashlib.sha256((SITE/name).read_bytes()).hexdigest()[:12] for key,name in
                     [('css','styles.css'),('app','app.js'),('form','inquiry-form.js'),('pixel','assets/meta-pixel.js')]}
        self.by_id={row['id']:row for row in self.products}

    def image(self,name,alt,cls='',eager=False):
        en,zh=pair(alt)
        return f'<img class="{esc(cls)}" src="/assets/vorntek/{esc(local_asset(name))}" alt="{esc(en)}" data-alt-en="{esc(en)}" data-alt-zh="{esc(zh)}" loading="{"eager" if eager else "lazy"}" decoding="async">'

    def nav(self):
        products=''.join(f'<a href="/products/series/{esc(row["id"])}/">{t(row["name"])}</a>' for row in self.families)
        return (f'<details class="product-menu"><summary>{t(["Products","产品中心"])}</summary><div class="mega-menu"><a href="/products/">{t(["All equipment","全部设备"])}</a>{products}</div></details>'+
                ''.join(f'<a href="/{path}/">{t(label)}</a>' for path,label in [('solutions',['Applications','应用方案']),('technology',['Technology','设计与技术']),('resources',['Resources','技术资源']),('company',['Company','关于我们'])]))

    def frame(self,title,body,description=None,model=False):
        en,zh=pair(title);desc=description or [self.brand['description'],self.brand.get('descriptionZh',self.brand['description'])];de,dz=pair(desc)
        nav=self.nav()
        return template('frame.html',title_en=esc(en+' · Vorntek'),title_zh=esc(zh+' · Vorntek'),description_en=esc(de),description_zh=esc(dz),
                        og_image='/assets/vorntek/'+esc(self.catalog['visuals']['hero']),css_hash=self.hashes['css'],app_hash=self.hashes['app'],form_hash=self.hashes['form'],pixel_hash=self.hashes['pixel'],
                        extra_head=('<script type="module" src="/assets/3d/vendor/model-viewer/4.4.0/model-viewer.min.js"></script>' if model else ''),
                        skip=t(['Skip to content','跳到主要内容']),menu=t(['Menu','菜单']),navigation=nav,contact_action=t(['Discuss your project','沟通项目']),arrow=ARROW,body=body,
                        brand_description=t([self.brand['description'],self.brand.get('descriptionZh',self.brand['description'])]),
                        footer_navigation=nav+f'<a href="/service/">{t(["Service & support","服务支持"])}</a><a href="/articles/">{t(["Articles","文章"])}</a>',
                        privacy=t(['Privacy','隐私说明']),privacy_choices=t(['Privacy choices','隐私选项']),demo=t(['Demonstration website','演示站点']),staff=t(['Staff access','后台登录']))

    def breadcrumb(self,label,parent=None):
        return '<div class="breadcrumb section"><a href="/">'+t(['Home','首页'])+'</a><span>/</span>'+((f'<a href="{parent[0]}">{t(parent[1])}</a><span>/</span>') if parent else '')+t(label)+'</div>'

    def closing(self,product=None):
        href='/contact/'+('?' +urlencode({'product':product['id']}) if product else '')
        return '<section class="closing"><div class="section closing-inner"><div>'+t(['Tell us what the next process needs.','告诉我们，下一道工序需要什么。'],'h2')+t(['Share the component, the operation and the conditions on your production floor.','从工件、作业和现场条件开始，共同梳理设备需求。'],'p')+'</div>'+button(['Discuss your project','沟通项目需求'],href)+'</div></section>'

    def cards(self,rows=None):
        cards=[]
        for row in self.products if rows is None else rows:
            visual=self.image(Path(row['asset']).name,named(row),'product-card-image') if row.get('asset') else ''
            cards.append(f'<a class="product-card" href="/products/{esc(row["id"])}/">{visual}<div class="card-copy">{t(named(row),"h3")}{ARROW}{t([row["description"],row.get("descriptionZh",row["description"])],"p")}</div></a>')
        return '<div class="product-grid">'+''.join(cards)+'</div>'

    def solution_cards(self,rows=None):
        return '<div class="solution-grid">'+''.join(f'<a class="solution-card" href="/solutions/{esc(row["id"])}/">{self.image(row["image"],row["name"])}<div>{t(row["name"],"h3")}{t(row["intro"],"p")}{ARROW}</div></a>' for row in (self.solutions if rows is None else rows))+'</div>'

    def form(self):
        return '<form id="project-form" data-configured-form data-form-code="project-inquiry" novalidate><div class="form-title">'+t(['Start with your application.','从你的应用需求开始。'],'h2')+t(['This demonstration form stores requests. Please use fictional contact details.','演示表单会保存请求，请使用虚构联系资料。'],'p','muted')+'</div><div class="inquiry-context" data-inquiry-context hidden></div><div class="form-grid" data-inquiry-fields></div><label class="honeypot" aria-hidden="true">Website<input name="website" tabindex="-1" autocomplete="off"></label><label class="consent"><input name="consent" type="checkbox" required>'+t(['I agree to this request being stored and used for a response.','我同意保存本次请求并用于回复。'])+'<a href="/privacy/">'+t(['Privacy notice','隐私说明'])+'</a></label><button type="submit" class="button primary" disabled>'+t(['Send inquiry','提交询盘'])+ARROW+'</button><button type="button" class="button secondary" data-form-retry hidden>'+t(['Retry loading form','重新加载表单'])+'</button><div data-form-status role="status" aria-live="polite" tabindex="-1">'+t(['Loading form…','正在加载表单…'])+'</div><noscript>'+t(['Enable JavaScript to load the inquiry form.','请启用JavaScript以加载询盘表单。'])+'</noscript></form>'

    def home(self):
        b=self.brand
        hero='<section class="hero"><div class="hero-copy">'+t([b['tagline'],b['taglineZh']],'h1')+t([b['intro'],b['introZh']],'p','lead')+'<div class="actions">'+button(['Explore equipment','探索设备'],'/products/')+button(['Discuss your application','沟通应用'],'/contact/',False)+'</div></div><div class="hero-visual"><!--siteos:slot:homepage_hero-->'+self.image(self.catalog['visuals']['hero'],['Inspection and assembly equipment in an industrial integration workshop','工业集成车间中的检测与装配设备'],'hero-image',True)+'</div></section>'
        switches=[];panels=[]
        for index,row in enumerate(self.families):
            switches.append(f'<button type="button" role="tab" id="family-{row["id"]}" aria-controls="panel-{row["id"]}" aria-selected="{str(index==0).lower()}" data-family-tab="{row["id"]}">{t(row["name"])}</button>')
            products=[p for p in self.products if p.get('family')==row['id']]
            panels.append(f'<section role="tabpanel" id="panel-{row["id"]}" aria-labelledby="family-{row["id"]}" data-family-panel="{row["id"]}"'+(' hidden' if index else '')+'><div class="family-copy">'+t(row['description'],'p')+f'<a class="text-link" href="/products/series/{row["id"]}/">'+t(['Explore this family','查看产品系列'])+ARROW+'</a></div>'+self.cards(products)+'</section>')
        families='<section class="section family-section"><div class="section-heading">'+t(['Equipment with a defined role.','每一类设备，都有明确任务。'],'h2')+t(['Choose a starting point for your production requirement.','从生产需求出发，找到合适的设备起点。'],'p')+'</div><div class="family-tabs" role="tablist" aria-label="Product families">'+''.join(switches)+'</div>'+''.join(panels)+'</section>'
        apps='<section class="section applications"><div class="section-heading">'+t(['The process comes first.','先理解工艺，再组织设备。'],'h2')+t(['Inspection, assembly and integration begin with different questions.','检测、装配与集成，需要从不同的问题开始。'],'p')+'</div>'+self.solution_cards()+'</section>'
        technical='<section class="technical-story"><div class="section split">'+self.image(self.catalog['visuals']['hero'],['Integrated inspection cell and material transfer','检测单元与物料转运'],'story-image')+'<div>'+t(['A station is only as useful as its connections.','连接清楚，设备才能协同。'],'h2')+t(['Workholding, control signals and material transfer belong to the same engineering conversation. Review interfaces before they become installation problems.','工件定位、控制信号与物料交接，属于同一项工程任务。把接口问题解决在安装之前。'],'p')+'<div class="actions">'+button(['Our engineering approach','了解技术方法'],'/technology/',False)+'</div></div></div></section>'
        guides='<section class="section"><div class="section-heading">'+t(['Prepare for the next engineering discussion.','为下一次工程讨论，准备好信息。'],'h2')+f'<a class="text-link" href="/resources/">{t(["All resources","全部技术资源"])}{ARROW}</a></div>'+self.guide_cards()+'</section>'
        inquiry='<section class="section contact-layout home-inquiry"><div>'+t(['Start the next engineering discussion.','开启下一次工程讨论。'],'h2')+t(['Describe the component, the operation and the site conditions. Product selection starts with the application.','说说工件、作业与现场条件，让设备选择从应用开始。'],'p','lead')+'</div>'+self.form()+'</section>'
        return self.frame([b['tagline'],b['taglineZh']],hero+families+apps+technical+guides+inquiry+self.closing()+'<!--siteos:slot:homepage_equipment_preview-->')

    def guide_cards(self):
        return '<div class="guide-grid">'+''.join(f'<a class="guide-card" href="/resources/guides/{row["id"]}/">{t(row["title"],"h3")}{t(row["intro"],"p")}{ARROW}</a>' for row in self.editorial['guides'])+'</div>'

    def video(self,row):
        if not row.get('video'):return ''
        return '<section class="section video-section"><div class="section-heading">'+t(['Equipment structure in motion','设备结构演示'],'h2')+t(['A rotating view of the equipment layout.','通过旋转视图，了解设备的结构布局。'],'p')+f'</div><video controls playsinline preload="none" poster="/assets/vorntek/{esc(row["gallery"][0])}" '+aria([row['name']+' structure overview',row.get('nameZh',row['name'])+'结构演示'])+f'><source src="/assets/vorntek/{esc(row["video"])}" type="video/webm">'+t(['Your browser does not support this video. The interactive 3D view and pictures are available above.','浏览器无法播放此视频，可使用上方三维视图与图片。'])+'</video></section>'

    def dimension_drawing(self,row):
        if not row.get('dimensions'):return ''
        width,height,depth=row['dimensions']
        scale=min(235/width,220/height,200/depth)
        fw,fh,sw=[round(v*scale,2) for v in (width,height,depth)]
        top=275-fh;front=95;side=410
        title=t(['Overall equipment dimensions','设备外形尺寸'],'h3')
        drawing=(f'<svg class="dimension-drawing" viewBox="0 0 700 360" role="img" aria-labelledby="dimensions-{esc(row["id"])}">'
                 f'<title id="dimensions-{esc(row["id"])}">{esc(row["name"])}: W {width:g} × H {height:g} × D {depth:g} mm</title>'
                 f'<rect x="{front}" y="{top}" width="{fw}" height="{fh}" rx="2"/>'
                 f'<rect x="{side}" y="{top}" width="{sw}" height="{fh}" rx="2"/>'
                 f'<path d="M {front} 290 v 22 m 0 -11 h {fw} m 0 -11 v 22 M {side} 290 v 22 m 0 -11 h {sw} m 0 -11 v 22 M 65 {top} h -20 m 10 0 v {fh} m -10 0 h 20"/>'
                 f'<text x="{front+fw/2}" y="332" text-anchor="middle">W {width:g} mm</text>'
                 f'<text x="{side+sw/2}" y="332" text-anchor="middle">D {depth:g} mm</text>'
                 f'<text x="25" y="{top+fh/2}" text-anchor="middle" transform="rotate(-90 25 {top+fh/2})">H {height:g} mm</text>'
                 '<text x="95" y="25" data-en="Front envelope" data-zh="正面外形">Front envelope</text>'
                 '<text x="410" y="25" data-en="Side envelope" data-zh="侧面外形">Side envelope</text></svg>')
        return '<figure class="equipment-dimensions">'+title+drawing+t(['Overall envelope only. Allow additional space for operation, guarding, connections and service.','此图为设备外形包络；操作、防护、接线与维护空间需另行预留。'],'figcaption')+'</figure>'

    def product(self,row):
        gallery=row.get('gallery') or ([Path(row['asset']).name] if row.get('asset') else [])
        images=self.image(gallery[0],named(row),'gallery-main',True) if gallery else ''
        images+='<div class="gallery-thumbnails">'+''.join(f'<button type="button" data-gallery-src="/assets/vorntek/{esc(name)}" '+aria([f'View {row["name"]} {i+1}',f'{row.get("nameZh",row["name"])}视图 {i+1}'])+f' aria-pressed="{str(i==0).lower()}">{self.image(name,named(row))}</button>' for i,name in enumerate(gallery))+'</div>'
        features=''.join('<article>'+t([v['en'],v['zh']],'h2')+t(v.get('body',[row['description'],row.get('descriptionZh',row['description'])]),'p')+'</article>' for v in row.get('features',[]))
        specs='<table><tbody>'+''.join('<tr><th scope="row">'+t(k)+'</th><td>'+t(v)+'</td></tr>' for k,v in row.get('specs',[]))+'</tbody></table>'
        # This is an explicitly packaged product model, not an unapproved private asset.
        model=(f'<div class="product-model" data-product-model><model-viewer data-model-src="/assets/vorntek/models/{esc(row["model"])}" poster="/assets/vorntek/{esc(gallery[0])}" alt="{esc(row["name"])}" data-alt-en="{esc(row["name"])}" data-alt-zh="{esc(row.get("nameZh",row["name"]))}" camera-controls camera-orbit="35deg 76deg 125%" field-of-view="30deg" shadow-intensity="1" environment-image="neutral" interaction-prompt="none"></model-viewer><button class="button secondary" type="button" data-load-model>{t(["Explore in 3D","查看三维结构"])}{ARROW}</button><p data-model-status role="status"></p></div>' if row.get('model') else '')
        family=next((f for f in self.families if f['id']==row.get('family')),None)
        if row.get('model'):
            code='product_'+row['id']
            if len(code)>80:raise ValueError('Product ID is too long for a configured 3D slot')
            model='<!--siteos:slot:'+code+'-->'+model+'<!--siteos:end:'+code+'-->'
        inquiry=button(['Discuss this equipment','咨询这款设备'],'/contact/?'+urlencode({'product':row['id']}),attrs=f'data-product-context="{esc(row["id"])}"')
        dl=button(['Download specifications','下载规格资料'],f'/resources/specifications/{row["id"]}.txt',False,attrs='download') if row.get('specs') else ''
        body=template('product.html',breadcrumb=self.breadcrumb(named(row),('/products/',['Products','产品中心'])),name=t(named(row)),headline=t([row['headline'],row.get('headlineZh',row['headline'])]),description=t([row['description'],row.get('descriptionZh',row['description'])]),
                      family_link=(f'<a class="text-link" href="/products/series/{family["id"]}/">{t(family["name"])}</a>' if family else ''),inquiry_link=inquiry,download_link=dl,spec_link=t(['Technical information','技术信息']),arrow=ARROW,gallery=images,features=features,specifications=specs,
                      spec_title=t(['Technical overview','技术概览']),spec_note=t(['Layout dimensions describe this equipment concept. Final configuration, performance and acceptance are agreed against the application.','图示尺寸用于设备方案规划，最终配置、性能与验收应结合应用确认。']),dimension_drawing=self.dimension_drawing(row),
                      model_title=t(['Look closer at the equipment.','从不同角度，了解设备结构。']),model_help=t(['Rotate the model to inspect the arrangement. Use the dimension schedule for planning.','旋转模型观察布局，规划尺寸请以参数表为准。']),model=model,video=self.video(row),
                      applications_title=t(['Related applications','相关应用']),solutions=self.solution_cards([v for v in self.solutions if v['id'] in row.get('relatedSolutions',[])]),
                      faq_title=t(['Before we configure your station','配置设备之前']),faq=''.join('<details><summary>'+t(q)+'</summary>'+t(a,'p')+'</details>' for q,a in row.get('faq',[])),closing=self.closing(row))
        return self.frame(named(row),body,[row['description'],row.get('descriptionZh',row['description'])],model=bool(row.get('model')))

    def editorial_page(self,row,label,visual=True,related='',visual_name=None):
        sections=''.join('<article>'+t(v['title'],'h2')+t(v['body'],'p')+'</article>' for v in row['sections'])
        body=template('editorial.html',breadcrumb=self.breadcrumb(label),title=t(row['title']),intro=t(row['intro']),visual=('<div class="editorial-visual">'+self.image(visual_name or self.catalog['visuals']['hero'],row['title'])+'</div>' if visual else ''),sections=sections,related=related,closing=self.closing())
        return self.frame(row['title'],body,row['intro'])

    def pages(self):
        result={'':self.home()}
        for slug,visual in (('company',self.catalog['visuals']['hero']),('technology','inspection-application.png'),('service','assembly-application.png')):
            result[slug]=self.editorial_page(self.editorial[slug],self.editorial[slug]['title'],visual_name=visual)
        intro='<section class="section listing-intro">'+t(['Equipment for the next production step.','面向下一道工序的设备。'],'h1')+t(['Explore inspection, assembly, material flow and integration equipment.','探索检测、装配、物料流转与集成设备。'],'p','lead')+'</section>'
        family_links='<div class="section family-links">'+''.join(f'<a href="/products/series/{f["id"]}/">{t(f["name"])}{ARROW}</a>' for f in self.families)+'</div>'
        result['products']=self.frame(['Equipment','产品中心'],intro+family_links+'<section class="section">'+self.cards()+'</section>'+self.closing())
        for row in self.families:
            related=[p for p in self.products if p.get('family')==row['id']]
            result['products/series/'+row['id']]=self.frame(row['name'],self.breadcrumb(row['name'])+'<section class="section listing-intro">'+t(row['name'],'h1')+t(row['description'],'p','lead')+self.cards(related)+'</section>'+self.closing())
        for row in self.products:
            result['products/'+row['id']]=self.product(row)
            if row.get('specs'):
                schedule='<section class="section document-page">'+t(named(row),'h1')+t(['Technical data sheet','技术资料'],'h2')+'<table>'+''.join('<tr><th>'+t(k)+'</th><td>'+t(v)+'</td></tr>' for k,v in row['specs'])+'</table>'+button(['Return to equipment','返回设备详情'],f'/products/{row["id"]}/',False)+'<button type="button" class="button secondary" data-print>'+t(['Print / save as PDF','打印 / 另存为PDF'])+'</button></section>'
                result['resources/specifications/'+row['id']+'.html']=self.frame(named(row),schedule)
        result['solutions']=self.frame(['Applications','应用方案'],'<section class="section listing-intro">'+t(['Start with the production task.','从生产任务开始。'],'h1')+t(['Equipment makes sense in the context of a complete operation.','把设备放回完整作业过程，才能作出合适选择。'],'p','lead')+self.solution_cards()+'</section>'+self.closing())
        for row in self.solutions:
            sections=[{'title':q,'body':a} for q,a in row['steps']]
            related='<section class="section">'+t(['Equipment for this application','适用设备'],'h2')+self.cards([self.by_id[key] for key in row['products'] if key in self.by_id])+'</section>'
            page=dict(title=row['name'],intro=row['intro'],sections=sections)
            result['solutions/'+row['id']]=self.editorial_page(page,['Applications','应用方案'],True,related,visual_name=row['image'])
        result['resources']=self.frame(['Technical resources','技术资源'],'<section class="section listing-intro">'+t(['Bring the right information to the table.','让工程讨论，有据可依。'],'h1')+t(['Planning guides, equipment data and practical engineering articles.','规划指南、设备资料与实用工程文章。'],'p','lead')+self.guide_cards()+'<div class="resource-actions">'+button(['Browse published articles','阅读已发布文章'],'/articles/',False)+button(['Service & support','服务与支持'],'/service/',False)+'</div></section>'+self.closing())
        resources='<section class="section"><h2>'+t(['Equipment data sheets','设备规格资料'])+'</h2><div class="download-list">'+''.join(f'<a href="/resources/specifications/{row["id"]}.html">{t(named(row))}{ARROW}</a>' for row in self.products if row.get('specs'))+'</div></section>'
        resources+=''.join(self.video(row) for row in self.products if row.get('video'))
        result['resources']=result['resources'].replace('<section class="closing">',resources+'<section class="closing">',1)
        for row in self.editorial['guides']:
            page=dict(title=row['title'],intro=row['intro'],sections=[{'title':a,'body':b} for a,b in row['sections']])
            related='<section class="section">'+self.cards([self.by_id[row['product']]] if row['product'] in self.by_id else [])+'</section>'
            result['resources/guides/'+row['id']]=self.editorial_page(page,['Resources','技术资源'],False,related)
        contact='<section class="section contact-layout"><div>'+t(['Tell us about your production requirement.','说说你的生产需求。'],'h1')+t(['Share the component, operation, target flow and site constraints. We can then identify the questions that need an engineering review.','提供工件、作业内容、目标流量与现场限制，从中明确需要工程评审的问题。'],'p','lead')+'<ul>'+''.join(t(v,'li') for v in [['Component drawing or description','工件图纸或说明'],['Inspection or assembly task','检测或装配任务'],['Flow, interfaces and installation conditions','流量、接口与安装条件']])+'</ul>'+t(['Do not include confidential drawings in the message. Describe the requirement first.','请勿在留言中包含保密图纸，可先描述需求。'],'p','muted')+'</div>'+self.form()+'</section>'
        result['contact']=self.frame(['Discuss your project','沟通项目'],contact)
        demo={'title':['About this demonstration','关于本演示站点'],'intro':['Vorntek is a fictional industrial company used to demonstrate this website and business system.','Vorntek 是用于展示网站与业务系统的虚构工业企业。'],'sections':[{'title':['Company and equipment content','企业与设备资料'],'body':['Product concepts, illustrations and dimensions are authored demonstration material. They are not commercial offers or validated equipment performance. No real customer endorsements are represented.','产品概念、图像与尺寸为编纂的演示资料，不构成商业报价或经验证的设备性能，未使用真实客户背书。']},{'title':['Using the inquiry form','使用询盘表单'],'body':['Requests may be stored in the independent CRM. Use fictional contact details. External delivery is disabled in this environment.','请求可能保存至独立CRM，请使用虚构联系资料。本环境的对外发送关闭。']}]}
        result['demo']=self.editorial_page(demo,['Demonstration website','演示站点'],False)
        privacy={'title':['Privacy notice','隐私说明'],'intro':['Understand what happens when you send a request or change measurement preferences.','了解提交请求或更改测量偏好时会发生什么。'],'sections':[{'title':['Inquiry information','询盘信息'],'body':['Submitted information is stored to organise the request and its follow-up. This demonstration requires fictional details; do not enter personal secrets or confidential project material.','提交内容会保存用于整理请求与后续处理。演示环境请使用虚构资料，不填写个人秘密或保密项目文件。']},{'title':['Optional measurement','可选测量'],'body':['Optional measurement requires an enabled server integration and the relevant browser consent. Your privacy choice alone does not activate an integration.','可选测量需要服务端已启用接入及适用浏览器同意，仅改变偏好不会启用接入。']}]}
        result['privacy']=self.editorial_page(privacy,['Privacy notice','隐私说明'],False)
        # Preserve old bookmarks with an honest route to the current catalogue.
        for slug in ('advice','project-planning','collaboration','learning','support','content-review','activity-insight'):
            if slug not in self.by_id:
                result['products/'+slug]=self.frame(['Explore our equipment','了解设备系列'],'<section class="section listing-intro">'+t(['Explore our current equipment collection.','了解当前设备系列。'],'h1')+self.cards()+'</section>'+self.closing())
        return result


def main(*,catalog=None,output=None):
    if catalog is None:catalog=json.loads((CONTENT/'catalog.json').read_text(encoding='utf-8'))
    builder=ExampleWebsite(catalog);pages=builder.pages();output=SITE if output is None else Path(output)
    for name in sorted(builder.assets):
        if not (CONTENT/'assets'/name).is_file():raise ValueError('Catalogue visual is missing: '+name)
    for name in ('styles.css','app.js','inquiry-form.js','assets/meta-pixel.js'):
        source=SITE/name;target=output/name
        if source.resolve()!=target.resolve():target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    for route,document in pages.items():
        target=output/route if route.endswith('.html') else output/route/'index.html'
        target.parent.mkdir(parents=True,exist_ok=True);target.write_text(document,encoding='utf-8',newline='\n')
    for row in builder.products:
        if not row.get('specs'):continue
        lines=['VORNTEK',row['name']+' / '+row.get('nameZh',row['name']),'']
        for label,value in row['specs']:
            lines.append(' / '.join(dict.fromkeys(pair(label)))+': '+' / '.join(dict.fromkeys(pair(value))))
        lines+=['','Final configuration and acceptance are defined for the application.','最终配置与验收根据具体应用确定。','',f'/products/{row["id"]}/']
        target=output/'resources/specifications'/(row['id']+'.txt')
        target.parent.mkdir(parents=True,exist_ok=True);target.write_text('\n'.join(lines)+'\n',encoding='utf-8',newline='\n')
    for name in sorted(builder.assets):
        target=output/'assets/vorntek'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(CONTENT/'assets'/name,target)
    if any(row.get('model') for row in builder.products):
        source=ROOT/'apps/crm/console/static/console/vendor/model-viewer/4.4.0'
        destination=output/'assets/3d/vendor/model-viewer/4.4.0'
        if source.resolve()!=destination.resolve():
            destination.mkdir(parents=True,exist_ok=True)
            for filename in ('model-viewer.min.js','provenance.json'):
                shutil.copyfile(source/filename,destination/filename)
            shutil.copyfile(source/'LICENSE',destination/'LICENSE.txt')
            for notice in (source/'notices').iterdir():
                if not notice.is_file():continue
                target=destination/'notices'/(notice.name+'.txt')
                target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(notice,target)
    (output/'robots.txt').write_text('User-agent: *\nDisallow: /\n',encoding='utf-8')
    (output/'corporate-build.json').write_text(json.dumps({'schemaVersion':1,'routes':sorted(pages),'contentSha256':hashlib.sha256(json.dumps(catalog,sort_keys=True).encode()).hexdigest()},indent=2),encoding='utf-8')
    (output/'catalogue-context.json').write_text(json.dumps({'products':[{'id':row['id'],'name':row['name'],'nameZh':row.get('nameZh',row['name'])} for row in builder.products]},ensure_ascii=False),encoding='utf-8')
    (output/'three-d-slots.json').write_text(json.dumps({'schemaVersion':1,'slots':[{'code':'product_'+row['id'],'path':'products/'+row['id']+'/index.html','label':row.get('nameZh',row['name'])+' · 3D'} for row in builder.products if row.get('model')]},ensure_ascii=False),encoding='utf-8')
    print(f'Built {len(pages)} corporate routes from {len(builder.products)} configurable equipment entries.')
    return pages
