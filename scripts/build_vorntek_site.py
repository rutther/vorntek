"""Build the optional neutral example site; catalogue IDs are not backend enums.

Only generated documents/assets are written. This never prunes a published site.
"""
import argparse
import hashlib
import html
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / 'apps/website'
CATALOG_PATH = ROOT / 'docs/vorntekDemo/catalog.json'


def t(en, zh, tag='span', cls=''):
    return f'<{tag} class="{cls}" data-en="{html.escape(en, quote=True)}" data-zh="{html.escape(zh, quote=True)}">{html.escape(en)}</{tag}>'


def picture(name, alt, cls=''):
    return f'<img class="{cls}" src="/assets/vorntek/{html.escape(name, quote=True)}" alt="{html.escape(alt, quote=True)}" loading="lazy">'


def chart():
    return '''<section class="data-demo" data-demo-kind="requests" aria-label="Synthetic request data demonstration"><div class="data-toolbar"><strong data-en="Request activity" data-zh="请求活动示例">Request activity</strong><label><span data-en="Window" data-zh="时间范围">Window</span><select id="data-window"><option value="24">24 h</option><option value="168">7 days</option></select></label><a id="data-export" class="data-export" data-en="Export demo CSV" data-zh="导出演示CSV">Export demo CSV</a></div><p class="muted" data-en="Synthetic data · not live business results" data-zh="合成数据 · 不是实际业务结果">Synthetic data · not live business results</p><div id="data-metrics" class="metrics"></div><svg id="data-chart" viewBox="0 0 800 220" role="img" aria-label="Synthetic request activity trend"></svg><div id="data-description" aria-live="polite"></div></section>'''


def form():
    return f'''<form id="project-form" data-configured-form data-form-code="project-inquiry" novalidate>
<div class="form-title">{t('Tell us what you need.','告诉我们你的需求。','h2')}
{t('Demonstration: use fictional information only. Fields follow the saved form configuration.','演示环境：仅填写虚构信息。字段以后台保存的表单配置为准。','p')}</div>
<div class="form-grid" data-inquiry-fields></div>
<label class="honeypot" aria-hidden="true">Website<input name="website" tabindex="-1" autocomplete="off"></label>
<label class="consent"><input name="consent" type="checkbox" required>{t('I have read the privacy notice and agree to this request being stored and used for a response.','我已阅读隐私说明，同意保存本次请求并用于回复。')}<a href="/privacy/">{t('Privacy notice','隐私说明')}</a></label>
<button type="submit" class="button primary" disabled>{t('Send inquiry','提交询盘')}</button>
<button type="button" class="button" data-form-retry hidden>{t('Retry loading form','重新加载表单')}</button>
<div data-form-status role="status" aria-live="polite" tabindex="-1">{t('Loading form…','正在加载表单…')}</div>
<noscript>{t('Enable JavaScript to load the configured inquiry form.','请启用JavaScript以加载配置后的询盘表单。')}</noscript></form>'''


class ExampleWebsite:
    def __init__(self, catalog):
        self.catalog = catalog
        self.products = catalog['productLines']
        identifiers = [entry['id'] for entry in self.products]
        if any(not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',key) for key in identifiers) or len(set(identifiers)) != len(identifiers):
            raise ValueError('Catalogue identifiers must be unique local route segments')
        self.assets = {'vorntekLogo.png', catalog['visuals']['hero'], catalog['visuals']['approach']}
        self.assets.update(Path(entry['asset']).name for entry in self.products if entry.get('asset'))
        if any(not re.fullmatch(r'[A-Za-z0-9_-]+\.(svg|png|webp)',name) for name in self.assets):
            raise ValueError('Only local catalogue visual filenames are supported')

    def frame(self, title, body):
        nav=[('company','Company','关于我们'),('products','Services','产品与服务'),('solutions','Applications','应用场景'),('technology','Our approach','工作方法'),('service','Support','项目支持'),('contact','Contact','联系')]
        links=''.join(f'<a href="/{path}/">{t(en,zh)}</a>' for path,en,zh in nav)
        brand=self.catalog['brand']
        return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>{html.escape(title)} · Vorntek</title><meta name="description" content="{html.escape(brand['description'],quote=True)}"><link rel="icon" href="/assets/vorntek/vorntekLogo.png"><link rel="stylesheet" href="/styles.css?v=vorntek1"><script defer src="/assets/meta-pixel.js"></script><script defer src="/app.js?v=vorntek1"></script><script defer src="/inquiry-form.js?v=vorntek1"></script></head><body><a class="skip" href="#main">{t('Skip to content','跳到主要内容')}</a><header class="site-header"><a class="brand" href="/" aria-label="Vorntek home"><img src="/assets/vorntek/vorntekLogo.png" alt="Vorntek"></a><button class="menu-button" aria-expanded="false" aria-controls="site-nav">{t('Menu','菜单')}</button><nav id="site-nav" aria-label="Primary navigation">{links}</nav><select id="language" aria-label="Language"><option value="en">EN</option><option value="zh">中文</option></select><a class="button primary header-cta" href="/contact/">{t('Start a conversation','开始咨询')}</a></header><main id="main">{body}</main><footer><div class="footer-main"><img src="/assets/vorntek/vorntekLogo.png" alt="Vorntek">{t(brand['tagline'],brand['taglineZh'],'p')}<a href="/admin/">{t('Staff access','后台登录')}</a><a href="/privacy/">{t('Privacy notice','隐私说明')}</a></div><div class="disclosure">{t(self.catalog['contentDisclosure']['en'],self.catalog['contentDisclosure']['zh'])}</div></footer></body></html>'''

    def hero(self,en,zh,suben,subzh):
        return f'<section class="hero"><div class="hero-copy"><div class="eyebrow">VORNTEK / FICTIONAL EXAMPLE</div>{t(en,zh,"h1")}{t(suben,subzh,"p")}<a class="button primary" href="/contact/">{t("Discuss your request","沟通你的需求")}</a>{t("Synthetic example · no real client claims","虚构示例 · 不宣称真实客户成果","small")}</div>{picture(self.catalog["visuals"]["hero"],"Illustrated example: request, discussion and next step","hero-image")}</section>'

    def cards(self):
        result=[]
        for entry in self.products:
            visual=picture(Path(entry['asset']).name,entry['name']+' — fictional example') if entry.get('asset') else '<div class="data-cover" aria-hidden="true"><span>DATA / EXAMPLE</span><div class="bars"><i></i><i></i><i></i><i></i><i></i><i></i></div></div>'
            result.append(f'<a class="product-card" href="/products/{entry["id"]}/">{visual}<div class="card-copy">{t(entry["name"],entry["nameZh"],"h3")}<span class="arrow" aria-hidden="true">↗</span>{t("Explore this example","了解这个示例","p")}</div></a>')
        return '<div class="product-grid">'+''.join(result)+'</div>'

    def pages(self):
        brand=self.catalog['brand']
        pages={'':self.frame('A clear next step',self.hero(brand['tagline'],brand['taglineZh'],brand['intro'],brand['introZh'])+'<section class="section">'+t('Different needs. A clear starting point.','需求不同，都有清晰的起点。','h2')+t('These fictional services illustrate configurable content, not fixed software categories.','以下虚构服务展示可配置内容，不是软件固定分类。','p','section-intro')+self.cards()+'</section><section class="split section">'+picture(self.catalog['visuals']['approach'],'Illustrated example: clarify, agree and follow up')+'<div>'+t('Start with the question, not a preset category.','先理解问题，不先套用分类。','h2')+t('An individual request or a team project can begin here. The saved form definition controls which details are needed.','个人咨询或团队项目都能从这里开始，所需资料由后台表单配置决定。','p')+'<a class="text-link" href="/technology/">'+t('Our approach →','了解工作方法 →')+'</a></div></section><section class="section inquiry">'+form()+'</section>')}
        pages['products']=self.frame('Products & services','<section class="section page-intro">'+t('Products & services','产品与服务','h1')+t('Illustrative offerings for different requests. Replace the catalogue to describe your own work.','用不同请求说明虚构服务；替换目录即可表达自己的业务。','p','section-intro')+self.cards()+'</section>')
        for entry in self.products:
            visual=chart() if entry.get('interactiveDemo')=='requests' else (picture(Path(entry['asset']).name,entry['name']+' — fictional example','detail-image') if entry.get('asset') else '')
            features=''.join('<article>'+t(item['en'],item['zh'],'h3')+t('Discuss the scope and evidence needed before making a commitment.','先确认范围和所需证据，再作出约定。','p')+'</article>' for item in entry['features'])
            pages['products/'+entry['id']]=self.frame(entry['name'],'<section class="section page-intro"><a class="text-link" href="/products/">← '+t('All services','全部服务')+'</a>'+t(entry['name'],entry['nameZh'],'p','eyebrow')+t(entry['headline'],entry['headlineZh'],'h1')+t(entry['description'],entry['descriptionZh'],'p','section-intro')+visual+'<div class="feature-grid">'+features+'</div><a class="button primary" href="/contact/">'+t('Discuss this request','咨询这项需求')+'</a>'+t('Fictional example only. This is not a real offer, client case or delivery promise.','仅为虚构示例，不是真实报价、客户案例或交付承诺。','p','muted')+'</section>')
        pages['contact']=self.frame('Project inquiry',('<section class="section inquiry">'+form()+'</section>').replace('<h2','<h1',1).replace('</h2>','</h1>',1))
        pages['company']=self.frame('About Vorntek',self.hero('A fictional team. Real software workflows.','团队是虚构的，软件操作是真实的。','Vorntek demonstrates how a request becomes an organised conversation and a recorded next step.','Vorntek演示一条咨询如何进入有序沟通，并留下可追踪的下一步。')+'<section class="section">'+t('Make the next step understandable.','让下一步可理解。','h2')+t('Content, forms and examples can describe different businesses without changing the underlying software. No real offices, certifications, clients or commercial history are claimed.','内容、表单与示例可以表达不同业务，无需改变底层软件。本示例不宣称真实办公地点、资质、客户或商业履历。','p')+'</section>')
        for slug,en,zh,introen,introzh,blocks in [
          ('technology','Our approach','工作方法','Understand the request. Agree the scope. Record the next step.','理解请求，约定范围，记录下一步。',[('Discover','理解需求'),('Plan together','共同规划'),('Review the result','核对结果')]),
          ('solutions','Application scenarios','应用场景','Different needs, the same traceable process. These are fictional scenarios, not client cases.','不同需求使用同样可追踪的流程，以下是虚构场景，不是客户案例。',[('Individual advice','个人咨询'),('Team project','团队项目'),('Ongoing support','持续支持')]),
          ('service','Project support','项目支持','A clear route from the first question to an agreed follow-up.','从第一个问题，到双方确认的后续行动。',[('Requirements review','需求梳理'),('Progress check','进度核对'),('Handoff and follow-up','交接与跟进')])]:
            pages[slug]=self.frame(en,'<section class="section page-intro">'+t(en,zh,'h1')+t(introen,introzh,'p','section-intro')+'<div class="feature-grid">'+''.join('<article>'+t(a,b,'h2')+t('Clarify the people involved, the constraints and the evidence needed for the next decision.','明确参与者、约束与下一次判断所需的证据。','p')+'</article>' for a,b in blocks)+'</div><a class="button primary" href="/contact/">'+t('Start a conversation','开始咨询')+'</a></section>')
        pages['privacy']=self.frame('Demo privacy notice','<section class="section prose">'+t('Demo privacy notice','演示隐私说明','h1')+t('Use fictional information only.','仅使用虚构信息。','h2')+t('Submitted fields may be stored in the independent CRM. Do not enter real personal, confidential or safety-critical information. No real business contact is provided in this example.','提交的字段可能存入独立CRM。请勿填写真实个人、保密或安全关键信息。本示例不提供真实业务联系方式。','p')+t('Optional measurement','可选测量','h2')+t('External messages and measurement are disabled by default. They require separately authorised server configuration and the applicable consent. A browser choice cannot activate a disabled integration.','外部消息与测量默认关闭，需要另行授权的服务端配置及适用同意；浏览器选择不能启用被停用的接入。','p')+'</section>')
        return pages


def main(*,catalog=None,output=None):
    catalog=json.loads(CATALOG_PATH.read_text(encoding='utf-8')) if catalog is None else catalog
    builder=ExampleWebsite(catalog)
    pages=builder.pages()
    output=SITE if output is None else Path(output)
    static=('styles.css','app.js','inquiry-form.js','assets/meta-pixel.js')
    fingerprints={asset:hashlib.sha256((SITE/asset).read_bytes()).hexdigest()[:12] for asset in static}
    # Resolve every required input before writing any output. A catalogue isn't
    # a complete website if its visuals or browser controllers are missing.
    for name in sorted(builder.assets):
        if not (ROOT/'docs/vorntekDemo/assets'/name).is_file():
            raise ValueError('Catalogue visual is missing: '+name)
    if output.resolve()!=SITE.resolve():
        for asset in static:
            destination=output/asset
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(SITE/asset,destination)
    for slug,content in pages.items():
        for asset,checksum in fingerprints.items():
            content=content.replace('/'+asset+('' if asset=='assets/meta-pixel.js' else '?v=vorntek1'),f'/{asset}?v={checksum}')
        directory=output/slug
        directory.mkdir(parents=True,exist_ok=True)
        (directory/'index.html').write_text(content,encoding='utf-8')
    public_assets=output/'assets/vorntek'
    public_assets.mkdir(parents=True,exist_ok=True)
    for name in sorted(builder.assets):
        shutil.copyfile(ROOT/'docs/vorntekDemo/assets'/name,public_assets/name)
    (output/'robots.txt').write_text('User-agent: *\nDisallow: /\n',encoding='utf-8')
    print(f'Built {len(pages)} example pages from {len(builder.products)} configurable catalogue entries.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog',type=Path,default=CATALOG_PATH)
    parser.add_argument('--output',type=Path,default=SITE)
    args=parser.parse_args()
    main(catalog=json.loads(args.catalog.read_text(encoding='utf-8')),output=args.output)
