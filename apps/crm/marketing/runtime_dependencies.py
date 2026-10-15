"""Local configuration evidence only; no submission, delivery or platform calls."""
from django.urls import reverse
from leads.models import LeadFormDefinition
from leads.email_delivery import email_delivery_configured
from leads.notifications import form_notification_recipients


def runtime_dependencies(site, draft, active_scope=None):
    scopes = []
    if active_scope:
        scopes.append(('active', '当前生效表单', active_scope.get('formId')))
    if not active_scope or draft.get('formId') != active_scope.get('formId'):
        scopes.append(('draft', '待应用表单', draft.get('formId')))
    rows = []
    for key, label, form_id in scopes:
        form_key = str(form_id or '')
        valid_key = form_key.isdigit() and len(form_key) <= 18 and int(form_key) > 0
        form = LeadFormDefinition.objects.filter(site=site, pk=int(form_key)).first() if valid_key else None
        row = {'key':key, 'label':label, 'form':None, 'checks':[], 'issues':[]}
        if not form:
            row['issues'].append('所选表单不存在或不属于本站。' if form_id else '尚未选择网站表单。')
            rows.append(row)
            continue
        row['form'] = {'id':form.pk, 'name':form.name,
            'editUrl':reverse('console:lead_form_edit', args=[form.pk])}
        valid = form.status == 'active' and form.channel == 'website' and form.submission_event_name == 'Lead'
        if not valid:
            row['issues'].append('表单当前不可接收网站询盘，请核对启用状态、渠道与提交事件。')
        row['checks'].append({'label':'接收定义', 'status':'ready' if valid else 'attention',
            'detail':'已启用网站表单 · 提交事件 Lead' if valid else '需核对表单配置'})
        row['checks'].append({'label':'服务器回传', 'status':'ready' if form.capi_enabled else 'attention',
            'detail':'表单回传开关已开启' if form.capi_enabled else '表单回传开关已关闭'})
        stages = [('已联系',form.contacted_event_name),('合格',form.qualified_event_name),('成交',form.won_event_name)]
        row['checks'].append({'label':'销售阶段事件', 'status':'ready' if all(v for _,v in stages) else 'attention',
            'detail':'；'.join(f'{name}：{value or "未配置"}' for name,value in stages)})
        recipients = bool(form_notification_recipients(form))
        mail_ready = email_delivery_configured() if recipients else False
        row['checks'].append({'label':'邮件通知（选用）',
            'status':('ready' if mail_ready else 'attention') if recipients else 'optional',
            'detail':('本地配置就绪，投递待实测' if mail_ready else '已设置收件人，邮件服务未配置')
                     if recipients else '未设置通知收件人'})
        rows.append(row)
    return rows
