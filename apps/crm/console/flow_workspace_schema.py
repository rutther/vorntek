"""Versioned configuration-record contract; nothing here activates a runtime."""
import copy
import math
import re
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from leads.models import LeadFormDefinition

TEMPLATE = 'flow-scheme-1'
BASE_TASKS = ('dataset', 'website', 'pixel', 'form', 'crm', 'server', 'ad')
NODES = {
    'dataset': ('Meta 数据集', 'meta', 'meta_dataset'),
    'website': ('网站落地页', 'website', 'website'),
    'pixel': ('Pixel 事件', 'website', 'website'),
    'form': ('询盘表单', 'website', 'web_form'),
    'crm': ('CRM 线索', 'crm', 'crm'),
    'filter': ('有效询盘筛选', 'crm', 'crm'),
    'server': ('服务器回传', 'crm', 'capi'),
    'ad': ('Meta 广告', 'meta', 'meta_ad'),
}


def select(key, label, options):
    return {'key': key, 'label': label, 'type': 'select', 'options': options}


DEFINITIONS = {
    'dataset': {
        'fields': [select('source', '数据源来源', [['existing', '复用 CRM 已登记的数据集'], ['created', '登记在 Meta 创建的数据集']]),
                   {'key':'externalId', 'label':'数据集 ID', 'placeholder':'Meta 页面显示的数字 ID'},
                   {'key':'alias', 'label':'数据集备注', 'optional':True}],
        'description': '将 Meta 数据源与当前流程关联。登记不会创建数据集，也不会改变网站或回传设置。',
        'guidance': ['在 Meta 事件管理工具选择本次使用的数据集，核对名称和数字 ID。',
                     '如果页面已提示“数据集可以使用了”，不要再次创建；返回设置复制数字 ID。',
                     '复用时从本站同环境已登记资产中选择；其他资产需核对后登记。不要改变商务管理平台的全局开关。',
                     '保存后可继续网站配置；权限、事件到达和去重仍需下一拍截图/实测。'],
    },
    'website': {
        'fields':[{'key':'url','label':'实际落地页网址','placeholder':'请输入广告实际打开的完整网址'}],
        'guidance':['填写广告按钮实际打开的网址，包含必要的语言或页面路径。',
                    '在浏览器人工检查页面、询盘入口及隐私同意；保存不会发布或修改网站。'],
    },
    'pixel': {
        'fields':[select('mode','计划/现有安装方式',[['managed','本站系统管理接入'],['manual','网站代码安装'],['partner','网站平台集成']]),
                  {'key':'consent','label':'要求获得营销同意后才发送浏览器事件','type':'checkbox'}],
        'description':'记录浏览器接入约定；本页不会安装 Pixel 或启用事件。',
        'guidance':['核对网站 Pixel 使用的数字 ID 与本流程数据集一致。',
                    '拒绝营销同意时应不发送事件；同意后再观察浏览器 PageView。',
                    '把截图交给助手逐项核对后，可在“核对记录”记下观察；勾选不代表已验证。'],
    },
    'form': {
        'fields':[select('formId','本站接入表单',[])],
        'guidance':['选择本网站实际使用的表单，选项来自 CRM 当前记录。',
                    '这里只建立流程关联，不修改表单。后续需单独授权一次受控提交，核对CRM只收到一条线索。'],
    },
    'crm': {
        'fields':[select('queue','接收方式',[['existing','沿用当前 CRM 接收规则']]),
                  select('duplicate','重复处理',[['existing','沿用当前 CRM 去重规则']])],
        'description':'关联现有线索接收能力，不创建虚构队列，也不改分配或去重规则。',
        'guidance':['真实接收和去重以现有表单/CRM服务为准。',
                    '下一拍需要核对来源、UTM、同意证据及事件编号；保存本页只记录沿用约定。'],
    },
    'filter': {
        'fields':[select('rule','拟议回传条件',[['qualified','线索阶段达到已合格'],['submitted','成功提交且未标记垃圾']])],
        'description':'筛选是待实施的流程计划，尚未应用到真实回传。未满足条件的线索仍应保留。',
        'guidance':['选择希望采用的条件，留待实操核对事件合同与实现。',
                    '保存或移除此节点都不会启停已有回传；不得将图中的“是/否”当作已生效路由。'],
    },
    'server': {
        'fields':[select('event','计划回传事件',[['Lead','Lead · 潜在客户']]),
                  {'key':'dedup','label':'要求同一次浏览器/服务器 Lead 使用相同事件 ID','type':'checkbox'}],
        'description':'记录服务器回传与去重约定；实际投递仍由现有 CRM 接入配置控制。',
        'guidance':['先核对本站启用的 Meta 接入是否指向本流程数据集。不要在本页输入 Token。',
                    '配置预检查只核对CRM已有设置，不向Meta发送事件。',
                    '真实验收需要受控提交、Outbox投递证据及Meta浏览器/服务器去重截图，不能只看复选框。'],
    },
    'ad': {
        'fields':[{'key':'campaignName','label':'广告系列名称'}, {'key':'adSetName','label':'广告组名称'},
                  {'key':'adName','label':'广告名称'}, select('objective','转化位置',[['website','网站']])],
        'description':'分别记录广告系列、广告组和广告。记录名称不会创建、发布或暂停广告。',
        'guidance':['用户在Meta广告管理工具逐屏操作；先核对系列目标，再核对广告组网站转化位置。',
                    '核对广告组所选数据集与Lead事件，以及广告按钮实际落地页。',
                    '预算、受众、素材和发布需要后续实操逐项核对；当前不要据配置记录直接投放。'],
    },
}


def definitions(site):
    defs = copy.deepcopy(DEFINITIONS)
    defs['form']['fields'][0]['options'] = [
        [str(f.pk), f.name] for f in LeadFormDefinition.objects.filter(
            site=site, channel='website', status='active').order_by('id')]
    return defs


def task_keys(workspace):
    result = list(BASE_TASKS)
    if workspace.get('filterEnabled'):
        result.insert(5, 'filter')
    return result


def clean_name(value):
    if not isinstance(value, str):
        raise ValidationError('流程名称必须为文字。')
    name = ' '.join(value.split())
    if not 2 <= len(name) <= 200:
        raise ValidationError('流程名称需要 2–200 个字符。')
    return name


def clean_values(site, node_key, values, *, draft=False):
    if node_key not in NODES or not isinstance(values, dict):
        raise ValidationError('节点或配置内容无效。')
    fields = definitions(site)[node_key]['fields']
    allowed = {f['key'] for f in fields}
    if set(values) - allowed:
        raise ValidationError('配置含有不支持的字段；不要填写密码、Token 或客户个人信息。')
    clean = {}
    for field in fields:
        key = field['key']
        if draft and key not in values:
            continue
        value = values.get(key, False if field.get('type') == 'checkbox' else '')
        if field.get('type') == 'checkbox':
            if not isinstance(value, bool):
                raise ValidationError(f'{field["label"]}必须为勾选值。')
            if not draft and value is not True:
                raise ValidationError(f'请确认：{field["label"]}。')
        else:
            if not isinstance(value, str) or len(value) > (2000 if key == 'url' else 200):
                raise ValidationError(f'{field["label"]}格式或长度不正确。')
            value = value.strip()
            if not draft and not field.get('optional') and not value:
                raise ValidationError(f'请填写或选择{field["label"]}。')
            if not draft and field.get('type') == 'select' and value not in [o[0] for o in field['options']]:
                raise ValidationError(f'请选择当前可用的{field["label"]}。')
        clean[key] = value
    if not draft and node_key == 'dataset' and not re.fullmatch(r'[1-9][0-9]{5,24}', clean['externalId']):
        raise ValidationError('数据集 ID 必须是 6–25 位数字。')
    if not draft and node_key == 'website':
        try:
            url = urlsplit(clean['url'])
            valid = url.scheme in {'http','https'} and url.hostname and '.' in url.hostname and not url.username and not url.password
        except ValueError:
            valid = False
        if not valid:
            raise ValidationError('请填写有效的 http/https 落地页网址，不能包含凭据。')
    return clean


def clean_positions(values, keys):
    if not isinstance(values, dict) or set(values) - set(keys):
        raise ValidationError('节点位置无效。')
    result = {}
    for key, pos in values.items():
        if not isinstance(pos, dict) or set(pos) != {'x','y'}:
            raise ValidationError('节点位置无效。')
        if any(isinstance(pos[k],bool) or not isinstance(pos[k],(float,int)) or not math.isfinite(pos[k]) for k in pos):
            raise ValidationError('节点坐标必须是有限数字。')
        result[key] = {'x':max(8,min(112 if key=='filter' else 77,pos['x'])),
                       'y':max(65,min(545 if key=='filter' else 572,pos['y']))}
    return result
