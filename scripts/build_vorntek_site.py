"""Build the fictional Vorntek website from its portable company catalogue."""
import html
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / 'apps/website'
CATALOG = json.loads((ROOT / 'docs/vorntekDemo/catalog.json').read_text(encoding='utf-8'))
PRODUCTS = CATALOG['productLines']

def t(en, zh, tag='span', cls=''):
    return f'<{tag} class="{cls}" data-en="{html.escape(en, quote=True)}" data-zh="{html.escape(zh, quote=True)}">{html.escape(en)}</{tag}>'

DETAILS = {
 'circulatingWaterControl': ('Keep the water loop in view.', '让循环水系统的状态清晰可见。', 'Coordinate circulation, monitor operating conditions and bring pump status into one operator view.', '将循环运行、工况监测与泵组状态集中到同一个操作界面。', ['Pump operation / 泵组运行', 'Temperature and pressure / 温度与压力', 'System alarms / 系统告警']),
 'liquidControl': ('Control the process, not just a valve.', '控制整个过程，而不只是一只阀门。', 'A configurable control concept for liquid transfer, level monitoring and pump-valve coordination.', '围绕液体输送、液位监测和阀泵联动组织的可配置控制系统。', ['Transfer sequence / 输送顺序', 'Level and flow / 液位与流量', 'Controller integration / 控制器集成']),
 'waterQualitySensors': ('Understand the water you depend on.', '了解设备所依赖的水。', 'Explore water-quality measurement requirements, installation options and integration with your control system.', '从测量需求、安装方式到系统对接，梳理适合项目的水质监测方案。', ['Measurement needs / 测量需求', 'Sampling arrangement / 采样安排', 'Maintenance planning / 维护规划']),
 'gasSensors': ('Bring gas measurements into the system.', '让气体测量融入系统。', 'A sensor-integration concept for defined target gases, operating environments and data interfaces.', '围绕目标气体、运行环境和数据接口展开的传感器集成概念。', ['Target gas / 目标气体', 'Installation environment / 安装环境', 'Integration review / 集成审查']),
 'propaneMicroHeaters': ('Compact thermal equipment. Clear boundaries.', '紧凑热能设备，明确应用边界。', 'An exterior concept for compact propane heating equipment, with project-specific application and compliance review.', '用于项目沟通的紧凑型丙烷加热设备外观概念，应用和合规要求须按项目审查。', ['Application brief / 应用需求', 'Installation constraints / 安装限制', 'Market requirements / 目标市场要求']),
 'smallTurbojetEngines': ('Compact propulsion, considered as a system.', '从系统角度理解小型动力。', 'A small turbojet exterior concept for civilian education and controlled test demonstrations. No performance or airworthiness claims.', '面向民用教学和受控试验展示的小型涡喷外观概念，不作性能或适航承诺。', ['Civilian application / 民用用途', 'Test environment / 试验环境', 'Project review / 项目评估']),
 'industrialDataAnalysis': ('From equipment signals to useful decisions.', '从设备信号，到有用的判断。', 'Bring equipment trends, operating states and exception analysis into a practical workspace. Try the synthetic-data demonstration below.', '将设备趋势、运行状态和异常分析汇集到实用工作界面。下方可体验合成数据演示。', ['Data connections / 数据接入', 'Operating trends / 运行趋势', 'Reporting and review / 报表与复盘'])
}

def picture(name, alt, cls=''):
    return f'<img class="{cls}" src="/assets/vorntek/{name}" alt="{html.escape(alt)}" loading="lazy">'

def chart():
    return '''<section class="data-demo" aria-label="Synthetic industrial data demo"><div class="data-toolbar"><strong data-en="Operations snapshot" data-zh="设备运行概览">Operations snapshot</strong><label><span data-en="Window" data-zh="时间范围">Window</span><select id="data-window"><option value="24">24 h</option><option value="168">7 days</option></select></label><a id="data-export" class="data-export" data-en="Export demo CSV" data-zh="导出演示CSV">Export demo CSV</a></div><p class="muted" data-en="Synthetic data · no connected equipment" data-zh="合成数据 · 未连接实际设备">Synthetic data · no connected equipment</p><div id="data-metrics" class="metrics"></div><svg id="data-chart" viewBox="0 0 800 220" role="img" aria-label="Synthetic operating trend"></svg><div id="data-description" aria-live="polite"></div></section>'''

def cards():
    result = []
    for p in PRODUCTS:
        visual = picture(Path(p['asset']).name, p['name'] + ' — AI concept') if p['asset'] else '<div class="data-cover" aria-hidden="true"><span>DATA / INSIGHT</span><div class="bars"><i></i><i></i><i></i><i></i><i></i><i></i></div></div>'
        result.append(f'<a class="product-card" href="/products/{p["id"]}/">{visual}<div class="card-copy">{t(p["name"],p["nameZh"],"h3")}<span class="arrow" aria-hidden="true">↗</span>{t("Explore this field", "了解业务", "p")}</div></a>')
    return '<div class="product-grid">' + ''.join(result) + '</div>'

def form():
    options = ''.join(f'<option value="{p["id"]}" data-en="{p["name"]}" data-zh="{p["nameZh"]}">{p["name"]}</option>' for p in PRODUCTS)
    def field(name,en,zh,required=False,kind='text'):
        return f'<label>{t(en+ (" *" if required else ""),zh+(" *" if required else ""))}<input name="{name}" type="{kind}" {"required" if required else ""} maxlength="{254 if kind == "email" else 120}" autocomplete="{ {"full_name":"name","company":"organization","country":"country-name","email":"email","phone":"tel"}.get(name,"off")}"></label>'
    return f'''<form id="project-form" novalidate><div class="form-title">{t("Tell us what you are working on.","告诉我们你的项目需求。","h2")}{t("Demo form: use fictional details only. Required fields are marked *.","演示表单：仅填写虚构信息，* 为必填。","p")}</div><div class="form-grid">{field('full_name','Name','姓名',True)}{field('company','Company','公司',True)}{field('country','Country / region','国家或地区',True)}<label>{t('Business area *','业务方向 *')}<select name="business_line" required><option value="" data-en="Choose a business area" data-zh="选择业务方向">Choose a business area</option>{options}</select></label>{field('email','Email','邮箱',False,'email')}{field('phone','Phone','电话',False,'tel')}</div>{t('Provide at least one: email or phone.','邮箱或电话至少填写一项。','p','muted')}<label>{t('Project requirement *','项目说明 *')}<textarea name="message" required maxlength="4000" rows="4"></textarea></label><label>{t('Application context (optional)','应用背景（可选）')}<input name="application_context" maxlength="500"><small id="business-help"></small></label><label class="honeypot" aria-hidden="true">Website<input name="website" tabindex="-1" autocomplete="off"></label><label class="consent"><input name="consent" type="checkbox" required>{t('I acknowledge the demo privacy notice and agree to this submission being stored in the demo CRM.','我已阅读演示隐私说明，同意将本次提交存入演示CRM。')}<a href="/privacy/">{t('Privacy notice','隐私说明')}</a></label><button type="submit" class="button primary">{t('Send inquiry','提交询盘')}</button><div id="form-status" role="status" aria-live="polite" tabindex="-1"></div></form>'''

def frame(title,body):
    nav=[('company','Company','公司'),('products','Products','产品与业务'),('solutions','Applications','应用'),('technology','Technology','技术'),('service','Service','服务'),('contact','Contact','联系')]
    links=''.join(f'<a href="/{path}/">{t(en,zh)}</a>' for path,en,zh in nav)
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>{html.escape(title)} · Vorntek</title><meta name="description" content="Vorntek — fictional China-based industrial technology company and website/CRM demonstration."><link rel="icon" href="/assets/vorntek/vorntekLogo.png"><link rel="stylesheet" href="/styles.css?v=vorntek1"><script defer src="/assets/meta-pixel.js"></script><script defer src="/app.js?v=vorntek1"></script></head><body><a class="skip" href="#main">Skip to content</a><header class="site-header"><a class="brand" href="/" aria-label="Vorntek home"><img src="/assets/vorntek/vorntekLogo.png" alt="Vorntek"></a><button class="menu-button" aria-expanded="false" aria-controls="site-nav">Menu</button><nav id="site-nav" aria-label="Primary navigation">{links}</nav><select id="language" aria-label="Language"><option value="en">EN</option><option value="zh">中文</option></select><a class="button primary header-cta" href="/contact/">{t('Start a project','项目咨询')}</a></header><main id="main">{body}</main><footer><div class="footer-main"><img src="/assets/vorntek/vorntekLogo.png" alt="Vorntek">{t('Sense. Control. Understand.','让工业设备可感知、可控制、可分析。','p')}<a href="/admin/">{t('Staff access','后台登录')}</a><a href="/privacy/">{t('Privacy notice','隐私说明')}</a></div><div class="disclosure">{t(CATALOG['contentDisclosure']['en'], CATALOG['contentDisclosure']['zh'])} {t('China / Jiangsu — fictional location. No real business contact or external delivery is enabled.','中国江苏为虚构地点设定。未提供真实业务联系方式，未启用对外发送。')}</div></footer></body></html>'''

def hero(en,zh,suben,subzh,image='vorntekWorkshop.png'):
    return f'<section class="hero"><div class="hero-copy"><div class="eyebrow">VORNTEK / INDUSTRIAL TECHNOLOGIES</div>{t(en,zh,"h1")}{t(suben,subzh,"p")}<a class="button primary" href="/contact/">{t("Discuss your project","沟通项目需求")}</a>{t("Fictional company · AI concept imagery","虚构企业 · AI概念图片","small")}</div>{picture(image,"AI-generated fictional industrial scene","hero-image")}</section>'

def main():
    pages={}
    pages[''] = frame('Industrial technologies', hero('Sense. Control. Understand.','让工业设备可感知、可控制、可分析。','Fluid control. Industrial sensing. Thermal and compact propulsion. Operational data. One connected project conversation.','流体控制、工业传感、热能与小型动力、运行数据。从明确需求到系统对接。') + '<section class="section">'+t('Seven fields. One engineering perspective.','七条业务，一个工程视角。','h2')+t('Explore the fictional Vorntek product and service portfolio.','探索Vorntek的虚构产品与服务体系。','p','section-intro')+cards()+'</section><section class="split section">'+picture('vorntekLoopHero.png','AI circulating-water scene')+'<div>'+t('Start with the operating requirement.','从运行需求出发。','h2')+t('Define the process, signals and operator decisions before choosing a system. Our demo connects that first conversation to the CRM.','先理解工艺、信号和操作决策，再讨论系统。这个演示将最初的项目沟通连接到CRM。','p')+'<a class="text-link" href="/technology/">'+t('Our approach →','了解技术方法 →')+'</a></div></section><section class="section inquiry">'+form()+'</section>')
    pages['products']=frame('Products & services','<section class="section page-intro">'+t('Products & services','产品与服务','h1')+t('Control, sensing, thermal equipment and industrial data — organised around your application.','控制、传感、热能设备与工业数据，围绕应用组织。','p')+cards()+'</section>')
    for p in PRODUCTS:
        en,zh,desc,desczh,features=DETAILS[p['id']]
        visual=picture(Path(p['asset']).name,p['name']+' AI concept','detail-image') if p['asset'] else chart()
        body=f'<section class="section page-intro"><a class="text-link" href="/products/">← {t("All products", "全部业务")}</a>{t(p["name"],p["nameZh"],"p","eyebrow")}{t(en,zh,"h1")}{t(desc,desczh,"p","section-intro")}{visual}<div class="feature-grid">'+''.join('<article>'+t(*s.split(' / '),'h3')+t('Scope defined through project review.','具体范围通过项目评估确认。','p')+'</article>' for s in features)+f'</div><a class="button primary" href="/contact/?business={p["id"]}">{t("Discuss this application","咨询此业务")}</a>{t("Illustrative concept only. Specifications, suitability and compliance have not been validated.","仅为演示概念，规格、适用性及合规性未经验证。","p","muted")}</section>'
        pages['products/'+p['id']]=frame(p['name'],body)
    pages['contact']=frame('Project inquiry','<section class="section inquiry">'+form()+'</section>')
    pages['company']=frame('Company',hero('A China-based industrial technology concept.','一家中国工业技术企业的演示设定。','Vorntek is a fictional manufacturer and systems business created to demonstrate a portable website and CRM.','Vorntek是为展示可独立安装的网站与CRM而创建的虚构制造与系统服务企业。')+'<section class="section">'+t('From a component to an operating system.','从一个部件，到一套运行系统。','h2')+t('The portfolio connects measurement, control and application context. This website does not claim real factories, certificates, customers or product deliveries.','产品体系将测量、控制和应用背景连接起来。本网站不宣称真实工厂、认证、客户或产品交付。','p')+'</section>')
    for slug,en,zh,introen,introzh,blocks in [
      ('technology','Technology approach','技术方法','Understand the process. Define the interface. Verify the behaviour.','理解过程，定义接口，验证行为。',[('Discover','理解需求'),('Integrate','系统集成'),('Verify','验证与交付')]),
      ('solutions','Application scenarios','应用场景','Illustrative project contexts, not customer case studies.','以下为示例应用，不是真实客户案例。',[('Plant water loops','工厂循环水'),('Equipment monitoring','设备状态监测'),('Civilian thermal and test equipment','民用热能与试验设备')]),
      ('service','Project support','项目服务','A clear route from the initial brief to a scoped demonstration.','从初步需求，到范围明确的演示与验证。',[('Requirements review','需求梳理'),('Integration planning','集成规划'),('Operator handoff','操作与交接')])]:
        pages[slug]=frame(en,'<section class="section page-intro">'+t(en,zh,'h1')+t(introen,introzh,'p','section-intro')+'<div class="feature-grid">'+''.join('<article>'+t(a,b,'h2')+t('Discuss the application, constraints and evidence needed before selecting a solution.','在选择方案前，讨论应用、约束以及需要的验证证据。','p')+'</article>' for a,b in blocks)+'</div><a class="button primary" href="/contact/">'+t('Start a conversation','开始项目沟通')+'</a></section>')
    pages['privacy']=frame('Demo privacy notice','<section class="section prose">'+t('Demo privacy notice','演示隐私说明','h1')+t('Use fictional information only.','仅使用虚构信息。','h2')+t('This demonstration can store submitted contact and project fields in its CRM database. It does not send marketing events or messages by default. Do not enter real personal, confidential or safety-critical information.','此演示可将提交的联系人和项目字段存入CRM数据库，默认不发送营销事件或消息。请勿填写真实个人、保密或安全关键的信息。','p')+t('Optional measurement','可选测量','h2')+t('Analytics requires separately enabled configuration and consent. Browser choices do not enable an integration that the server has disabled. No real business contact is provided in this demo.','分析追踪需要独立启用配置并取得同意。浏览器选择不会启用服务器已禁用的集成。本演示不提供真实业务联系方式。','p')+'</section>')
    pages['contact'] = pages['contact'].replace('<h2', '<h1', 1).replace('</h2>', '</h1>', 1)
    for slug,content in pages.items():
        content = content.replace('>Menu</button>', '>'+t('Menu', '菜单')+'</button>')
        content = content.replace('>Skip to content</a>', '>'+t('Skip to content', '跳到主要内容')+'</a>')
        for asset,old_url in [('styles.css','/styles.css?v=vorntek1'),('app.js','/app.js?v=vorntek1'),('assets/meta-pixel.js','/assets/meta-pixel.js')]:
            checksum = hashlib.sha256((SITE/asset).read_text(encoding='utf-8').encode('utf-8')).hexdigest()[:12]
            content = content.replace(old_url, f'/{asset}?v={checksum}')
        directory=SITE/slug;directory.mkdir(parents=True,exist_ok=True);(directory/'index.html').write_text(content,encoding='utf-8')
    (SITE/'robots.txt').write_text('User-agent: *\nDisallow: /\n',encoding='utf-8')
    print(f'Built {len(pages)} Vorntek pages from seven product lines.')

if __name__=='__main__': main()
