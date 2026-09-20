from __future__ import annotations

import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


def crm_mutation_receipt_expiry():
    return timezone.now() + timedelta(days=30)


class LeadFormDefinition(models.Model):
    site = models.ForeignKey('sitecore.Site', verbose_name='站点', on_delete=models.DO_NOTHING)
    locale = models.ForeignKey('sitecore.SiteLocale', verbose_name='语言', on_delete=models.DO_NOTHING, blank=True, null=True)
    code = models.CharField('代码', max_length=80)
    name = models.CharField('名称', max_length=200)
    category = models.CharField('分类', max_length=80, default='general')
    channel = models.CharField('渠道', max_length=32, default='website')
    scope_type = models.CharField('作用域类型', max_length=32, default='site')
    scope_value = models.CharField('作用域值', max_length=255, default='*')
    status = models.CharField('状态', max_length=32, default='active')
    submission_event_name = models.CharField('提交事件名', max_length=80, default='Lead')
    contacted_event_name = models.CharField('已联系事件名', max_length=80, default='contacted_lead')
    qualified_event_name = models.CharField('合格事件名', max_length=80, default='qualified_lead')
    won_event_name = models.CharField('成交事件名', max_length=80, default='converted')
    capi_enabled = models.BooleanField('启用 CAPI', default=True)
    notify_emails = models.TextField('通知邮箱', blank=True)
    success_message = models.TextField('成功提示', blank=True)
    form_schema = models.JSONField('表单结构', default=dict)
    config_json = models.JSONField('配置 JSON', default=dict)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'lead_form_definition'
        ordering = ['site__code', 'category', 'code']
        verbose_name = '线索表单'
        verbose_name_plural = '线索表单'

    def __str__(self) -> str:
        return f'{self.code}: {self.name}'


class SalesTeam(models.Model):
    site = models.ForeignKey('sitecore.Site', verbose_name='站点', on_delete=models.DO_NOTHING)
    code = models.CharField('代码', max_length=80)
    name = models.CharField('名称', max_length=200)
    enabled = models.BooleanField('启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'sales_team'
        ordering = ['site__code', 'name', 'id']
        verbose_name = '销售团队'
        verbose_name_plural = '销售团队'

    def __str__(self) -> str:
        return self.name


class SalesTeamMember(models.Model):
    team = models.ForeignKey(
        SalesTeam,
        verbose_name='团队',
        on_delete=models.DO_NOTHING,
        related_name='memberships',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='用户',
        on_delete=models.DO_NOTHING,
        related_name='sales_team_memberships',
    )
    membership_role = models.CharField('团队角色', max_length=32, default='member')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'sales_team_member'
        ordering = ['team__name', 'membership_role', 'user_id']
        verbose_name = '销售团队成员'
        verbose_name_plural = '销售团队成员'

    def __str__(self) -> str:
        return f'{self.team}:{self.user}:{self.membership_role}'


class LeadSubmission(models.Model):
    submission_key = models.UUIDField('提交键', default=uuid.uuid4, editable=False)
    form = models.ForeignKey(LeadFormDefinition, verbose_name='表单', on_delete=models.DO_NOTHING)
    site = models.ForeignKey('sitecore.Site', verbose_name='站点', on_delete=models.DO_NOTHING)
    locale = models.ForeignKey('sitecore.SiteLocale', verbose_name='语言', on_delete=models.DO_NOTHING, blank=True, null=True)
    route = models.ForeignKey('sitecore.PageRoute', verbose_name='来源路由', on_delete=models.DO_NOTHING, blank=True, null=True)
    cta = models.ForeignKey('sitecore.Cta', verbose_name='来源 CTA', on_delete=models.DO_NOTHING, blank=True, null=True)
    stage = models.CharField('阶段', max_length=32, default='new')
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='负责人',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='assigned_lead_submissions',
    )
    team = models.ForeignKey(
        SalesTeam,
        verbose_name='销售团队',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='lead_submissions',
    )
    full_name = models.TextField('姓名', blank=True)
    email = models.TextField('邮箱', blank=True)
    phone = models.TextField('电话', blank=True)
    company = models.TextField('公司', blank=True)
    country = models.TextField('国家', blank=True)
    message = models.TextField('留言', blank=True)
    source_url = models.TextField('提交页面', blank=True)
    referrer_url = models.TextField('来源页面', blank=True)
    source_channel = models.CharField('来源渠道', max_length=32, default='website')
    source_detail = models.TextField('来源说明', blank=True)
    client_ip = models.TextField('客户端 IP', blank=True)
    user_agent = models.TextField('客户端 UA', blank=True)
    buyer_value = models.DecimalField('成交金额', max_digits=12, decimal_places=2, blank=True, null=True)
    buyer_currency = models.CharField('币种', max_length=8, default='USD')
    payload_json = models.JSONField('扩展字段', default=dict)
    identifiers_json = models.JSONField('标识符', default=dict)
    utm_json = models.JSONField('UTM', default=dict)
    consent_json = models.JSONField('同意信息', default=dict)
    config_json = models.JSONField('配置 JSON', default=dict)
    follow_up_notes = models.TextField('跟进备注', blank=True)
    next_follow_up_at = models.DateTimeField('下次跟进时间', blank=True, null=True)
    contacted_at = models.DateTimeField('首次联系时间', blank=True, null=True)
    qualified_at = models.DateTimeField('合格时间', blank=True, null=True)
    won_at = models.DateTimeField('成交时间', blank=True, null=True)
    lost_at = models.DateTimeField('流失时间', blank=True, null=True)
    qualification_score = models.IntegerField('合格评分', default=0)
    qualification_json = models.JSONField('合格判定', default=dict)
    qualification_notes = models.TextField('合格判定说明', blank=True)
    qualification_overridden = models.BooleanField('人工覆盖评分', default=False)
    contact_key = models.CharField('联系人指纹', max_length=64, blank=True, default='')
    dedupe_key = models.CharField('提交指纹', max_length=64, blank=True, default='')
    duplicate_of = models.ForeignKey(
        'self',
        verbose_name='重复线索来源',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='duplicate_submissions',
    )
    spam_score = models.IntegerField('垃圾评分', default=0)
    spam_reason = models.TextField('垃圾原因', blank=True)
    notification_status = models.CharField('通知状态', max_length=32, default='pending')
    notification_error = models.TextField('通知错误', blank=True)
    notified_at = models.DateTimeField('通知时间', blank=True, null=True)
    reminder_sent_at = models.DateTimeField('提醒时间', blank=True, null=True)
    submitted_at = models.DateTimeField('提交时间')
    stage_updated_at = models.DateTimeField('阶段更新时间')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'lead_submission'
        ordering = ['-submitted_at', '-id']
        verbose_name = '线索提交'
        verbose_name_plural = '线索提交'

    def __str__(self) -> str:
        return f'{self.form.code}#{self.id}'


class LeadEventOutbox(models.Model):
    submission = models.ForeignKey(LeadSubmission, verbose_name='线索', on_delete=models.DO_NOTHING)
    integration = models.ForeignKey('marketing.MarketingIntegration', verbose_name='营销接入', on_delete=models.DO_NOTHING)
    stage_key = models.CharField('阶段键', max_length=32)
    event_name = models.CharField('事件名', max_length=80)
    event_id = models.CharField('事件 ID', max_length=120)
    action_source = models.CharField('动作来源', max_length=32, default='website')
    payload_json = models.JSONField('事件载荷', default=dict)
    status = models.CharField('状态', max_length=32, default='pending')
    delivery_mode = models.CharField('发送模式', max_length=16, default='live')
    attempts = models.IntegerField('尝试次数', default=0)
    last_error = models.TextField('最后错误', blank=True)
    response_json = models.JSONField('响应 JSON', default=dict)
    provider_request_id = models.TextField('平台请求 ID', blank=True, default='')
    provider_received_at = models.DateTimeField('平台接收时间', blank=True, null=True)
    provider_processed_at = models.DateTimeField('平台处理时间', blank=True, null=True)
    match_status = models.CharField('匹配状态', max_length=32, default='unknown')
    last_attempt_at = models.DateTimeField('最近尝试时间', blank=True, null=True)
    next_attempt_at = models.DateTimeField('下次尝试时间', blank=True, null=True)
    dispatched_at = models.DateTimeField('发送时间', blank=True, null=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'lead_event_outbox'
        ordering = ['-created_at', '-id']
        verbose_name = '线索回传队列'
        verbose_name_plural = '线索回传队列'

    def __str__(self) -> str:
        return f'{self.event_name}:{self.status}:{self.id}'


class ConsentRecord(models.Model):
    submission = models.ForeignKey(
        LeadSubmission,
        verbose_name='线索',
        on_delete=models.CASCADE,
        related_name='consent_records',
    )
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='操作人',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='lead_consent_records',
    )
    purpose = models.CharField('用途', max_length=32)
    decision = models.CharField('决定', max_length=16)
    source = models.CharField('来源', max_length=80, default='website_form')
    policy_version = models.CharField('隐私政策版本', max_length=80, blank=True, default='')
    evidence_json = models.JSONField('证据', default=dict)
    captured_at = models.DateTimeField('记录时间', auto_now_add=True, editable=False)

    class Meta:
        managed = False
        db_table = 'lead_consent_record'
        ordering = ['-captured_at', '-id']
        verbose_name = '同意记录'
        verbose_name_plural = '同意记录'


class PrivacyRequest(models.Model):
    site = models.ForeignKey('sitecore.Site', verbose_name='站点', on_delete=models.CASCADE)
    submission = models.ForeignKey(
        LeadSubmission,
        verbose_name='关联线索',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='privacy_requests',
    )
    handled_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='处理人',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='handled_privacy_requests',
    )
    request_type = models.CharField('请求类型', max_length=32)
    status = models.CharField('状态', max_length=32, default='pending')
    requester_name = models.TextField('请求人姓名', blank=True, default='')
    requester_email = models.TextField('请求人邮箱', blank=True, default='')
    requester_phone = models.TextField('请求人电话', blank=True, default='')
    subject_key = models.TextField('数据主体标识', blank=True, default='')
    request_json = models.JSONField('请求内容', default=dict)
    resolution = models.TextField('处理结果', blank=True, default='')
    requested_at = models.DateTimeField('请求时间', default=timezone.now)
    verified_at = models.DateTimeField('身份核验时间', blank=True, null=True)
    completed_at = models.DateTimeField('完成时间', blank=True, null=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'privacy_request'
        ordering = ['-requested_at', '-id']
        verbose_name = '隐私请求'
        verbose_name_plural = '隐私请求'


class RetentionPolicy(models.Model):
    site = models.OneToOneField(
        'sitecore.Site',
        verbose_name='站点',
        on_delete=models.CASCADE,
        related_name='privacy_retention_policy',
    )
    enabled = models.BooleanField('启用自动保留策略', default=False)
    lead_pii_retention_days = models.IntegerField('线索个人信息保留天数', default=0)
    inbound_payload_retention_days = models.IntegerField('Webhook 原始载荷保留天数', default=90)
    outbox_payload_retention_days = models.IntegerField('回传载荷保留天数', default=180)
    privacy_request_retention_days = models.IntegerField('隐私请求身份信息保留天数', default=365)
    whatsapp_message_retention_days = models.IntegerField('WhatsApp 消息保留天数', default=1095)
    whatsapp_media_retention_days = models.IntegerField('WhatsApp 媒体保留天数', default=365)
    updated_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='更新人',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='updated_privacy_retention_policies',
    )
    config_json = models.JSONField('扩展配置', default=dict)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'privacy_retention_policy'
        verbose_name = '隐私保留策略'
        verbose_name_plural = '隐私保留策略'


class LeadInboundEvent(models.Model):
    integration = models.ForeignKey(
        'marketing.MarketingIntegration',
        verbose_name='营销接入',
        on_delete=models.DO_NOTHING,
    )
    submission = models.ForeignKey(
        LeadSubmission,
        verbose_name='关联线索',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    provider_code = models.CharField('平台代码', max_length=80)
    event_type = models.CharField('事件类型', max_length=80)
    external_event_id = models.TextField('外部事件 ID')
    payload_json = models.JSONField('原始载荷', default=dict)
    status = models.CharField('处理状态', max_length=32, default='pending')
    attempts = models.IntegerField('处理次数', default=0)
    last_error = models.TextField('最后错误', blank=True)
    received_at = models.DateTimeField('接收时间')
    processed_at = models.DateTimeField('处理时间', blank=True, null=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'lead_inbound_event'
        ordering = ['-received_at', '-id']
        verbose_name = '外部线索事件'
        verbose_name_plural = '外部线索事件'

    def __str__(self) -> str:
        return f'{self.provider_code}:{self.event_type}:{self.status}:{self.id}'


class Company(models.Model):
    site = models.ForeignKey('sitecore.Site', verbose_name='站点', on_delete=models.DO_NOTHING)
    owner_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='负责人',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='owned_crm_companies',
    )
    team = models.ForeignKey(
        SalesTeam,
        verbose_name='销售团队',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='crm_companies',
    )
    name = models.TextField('企业名称')
    normalized_name = models.TextField('标准化企业名称', blank=True, default='')
    website = models.TextField('网站', blank=True, default='')
    industry = models.TextField('行业', blank=True, default='')
    country = models.TextField('国家/地区', blank=True, default='')
    city = models.TextField('城市', blank=True, default='')
    country_code = models.CharField('国家代码（ISO2）', max_length=2, blank=True, default='')
    value = models.DecimalField('价值分', max_digits=6, decimal_places=1, blank=True, null=True)
    status = models.CharField('状态', max_length=32, default='prospect')
    source_channel = models.CharField('来源渠道', max_length=32, default='website')
    notes = models.TextField('备注', blank=True, default='')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'crm_company'
        ordering = ['name', 'id']
        verbose_name = '企业'
        verbose_name_plural = '企业'

    def __str__(self) -> str:
        return self.name


class Contact(models.Model):
    site = models.ForeignKey('sitecore.Site', verbose_name='站点', on_delete=models.DO_NOTHING)
    company = models.ForeignKey(
        Company,
        verbose_name='企业',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='contacts',
    )
    owner_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='负责人',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='owned_crm_contacts',
    )
    team = models.ForeignKey(
        SalesTeam,
        verbose_name='销售团队',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='crm_contacts',
    )
    full_name = models.TextField('姓名')
    job_title = models.TextField('职位', blank=True, default='')
    email = models.TextField('邮箱', blank=True, default='')
    email_normalized = models.TextField('标准化邮箱', blank=True, default='')
    phone = models.TextField('电话', blank=True, default='')
    phone_normalized = models.TextField('标准化电话', blank=True, default='')
    whatsapp_phone = models.TextField('WhatsApp', blank=True, default='')
    country = models.TextField('国家/地区', blank=True, default='')
    preferred_language = models.CharField('首选语言', max_length=16, blank=True, default='')
    status = models.CharField('状态', max_length=32, default='active')
    notes = models.TextField('备注', blank=True, default='')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'crm_contact'
        ordering = ['full_name', 'id']
        verbose_name = '联系人'
        verbose_name_plural = '联系人'

    def __str__(self) -> str:
        return self.full_name


class Opportunity(models.Model):
    site = models.ForeignKey('sitecore.Site', verbose_name='站点', on_delete=models.DO_NOTHING)
    company = models.ForeignKey(
        Company,
        verbose_name='企业',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='opportunities',
    )
    primary_contact = models.ForeignKey(
        Contact,
        verbose_name='主要联系人',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='primary_opportunities',
    )
    source_submission = models.ForeignKey(
        LeadSubmission,
        verbose_name='原始线索',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='opportunities',
    )
    owner_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='负责人',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='owned_crm_opportunities',
    )
    team = models.ForeignKey(
        SalesTeam,
        verbose_name='销售团队',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='crm_opportunities',
    )
    name = models.TextField('项目名称')
    stage = models.CharField('销售阶段', max_length=32, default='qualification')
    value_amount = models.DecimalField('预计金额', max_digits=14, decimal_places=2, blank=True, null=True)
    currency = models.CharField('币种', max_length=8, default='USD')
    probability = models.IntegerField('成交概率', default=10)
    expected_close_date = models.DateField('预计成交日期', blank=True, null=True)
    product_scope = models.TextField('产品范围', blank=True, default='')
    capacity_target = models.TextField('产能目标', blank=True, default='')
    packaging_format = models.TextField('包装形式', blank=True, default='')
    source_channel = models.CharField('来源渠道', max_length=32, default='website')
    source_detail = models.TextField('来源说明', blank=True, default='')
    next_step = models.TextField('下一步', blank=True, default='')
    next_follow_up_at = models.DateTimeField('下次跟进时间', blank=True, null=True)
    won_reason = models.TextField('成交原因', blank=True, default='')
    lost_reason = models.TextField('丢单原因', blank=True, default='')
    competitor = models.TextField('竞争对手', blank=True, default='')
    closed_at = models.DateTimeField('关闭时间', blank=True, null=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'crm_opportunity'
        ordering = ['-updated_at', '-id']
        verbose_name = '项目/商机'
        verbose_name_plural = '项目/商机'

    def __str__(self) -> str:
        return self.name


class LeadConversion(models.Model):
    submission = models.OneToOneField(
        LeadSubmission,
        verbose_name='原始线索',
        on_delete=models.DO_NOTHING,
        related_name='conversion',
    )
    company = models.ForeignKey(
        Company,
        verbose_name='企业',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='lead_conversions',
    )
    contact = models.ForeignKey(
        Contact,
        verbose_name='联系人',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='lead_conversions',
    )
    opportunity = models.ForeignKey(
        Opportunity,
        verbose_name='项目/商机',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='lead_conversions',
    )
    converted_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='转换人',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='crm_lead_conversions',
    )
    metadata_json = models.JSONField('转换信息', default=dict)
    converted_at = models.DateTimeField('转换时间', auto_now_add=True, editable=False)

    class Meta:
        managed = False
        db_table = 'crm_lead_conversion'
        ordering = ['-converted_at', '-id']
        verbose_name = '线索转换'
        verbose_name_plural = '线索转换'


class Activity(models.Model):
    site = models.ForeignKey('sitecore.Site', verbose_name='站点', on_delete=models.DO_NOTHING)
    submission = models.ForeignKey(
        LeadSubmission,
        verbose_name='原始线索',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='activities',
    )
    company = models.ForeignKey(
        Company,
        verbose_name='企业',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='activities',
    )
    contact = models.ForeignKey(
        Contact,
        verbose_name='联系人',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='activities',
    )
    opportunity = models.ForeignKey(
        Opportunity,
        verbose_name='项目/商机',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='activities',
    )
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='记录人',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='crm_activities',
    )
    activity_type = models.CharField('活动类型', max_length=32)
    direction = models.CharField('方向', max_length=16, default='internal')
    subject = models.TextField('主题', blank=True, default='')
    body = models.TextField('内容', blank=True, default='')
    metadata_json = models.JSONField('附加信息', default=dict)
    occurred_at = models.DateTimeField('发生时间')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)

    class Meta:
        managed = False
        db_table = 'crm_activity'
        ordering = ['-occurred_at', '-id']
        verbose_name = '活动'
        verbose_name_plural = '活动'


class Task(models.Model):
    site = models.ForeignKey('sitecore.Site', verbose_name='站点', on_delete=models.DO_NOTHING)
    submission = models.ForeignKey(
        LeadSubmission,
        verbose_name='原始线索',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='tasks',
    )
    company = models.ForeignKey(
        Company,
        verbose_name='企业',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='tasks',
    )
    contact = models.ForeignKey(
        Contact,
        verbose_name='联系人',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='tasks',
    )
    opportunity = models.ForeignKey(
        Opportunity,
        verbose_name='项目/商机',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='tasks',
    )
    owner_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='负责人',
        on_delete=models.DO_NOTHING,
        related_name='crm_tasks',
    )
    team = models.ForeignKey(
        SalesTeam,
        verbose_name='销售团队',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='crm_tasks',
    )
    created_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='创建人',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='created_crm_tasks',
    )
    title = models.TextField('任务')
    description = models.TextField('说明', blank=True, default='')
    task_type = models.CharField('任务类型', max_length=32, default='follow_up')
    priority = models.CharField('优先级', max_length=16, default='normal')
    status = models.CharField('状态', max_length=16, default='open')
    due_at = models.DateTimeField('到期时间')
    reminder_at = models.DateTimeField('提醒时间', blank=True, null=True)
    completed_at = models.DateTimeField('完成时间', blank=True, null=True)
    outcome = models.TextField('结果', blank=True, default='')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'crm_task'
        ordering = ['due_at', '-priority', 'id']
        verbose_name = '任务'
        verbose_name_plural = '任务'


class CrmMutationReceipt(models.Model):
    """Durable idempotency receipt for CRM write workflows."""

    site = models.ForeignKey('sitecore.Site', verbose_name='站点', on_delete=models.DO_NOTHING)
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='操作人',
        on_delete=models.DO_NOTHING,
        related_name='crm_mutation_receipts',
    )
    mutation_scope = models.CharField('操作范围', max_length=64)
    token_hash = models.CharField('幂等令牌摘要', max_length=64)
    fingerprint_hash = models.CharField('请求指纹', max_length=64)
    status = models.CharField('状态', max_length=16, default='processing')
    entity_table = models.CharField('对象表', max_length=80, blank=True, default='')
    entity_id = models.BigIntegerField('对象 ID', blank=True, null=True)
    result_json = models.JSONField('安全结果', default=dict)
    expires_at = models.DateTimeField('过期时间', default=crm_mutation_receipt_expiry)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'crm_mutation_receipt'
        ordering = ['-created_at', '-id']
        verbose_name = 'CRM 幂等回执'
        verbose_name_plural = 'CRM 幂等回执'


class OpportunityStageHistory(models.Model):
    opportunity = models.ForeignKey(
        Opportunity,
        verbose_name='项目/商机',
        on_delete=models.DO_NOTHING,
        related_name='stage_history',
    )
    from_stage = models.CharField('原阶段', max_length=32, blank=True, null=True)
    to_stage = models.CharField('新阶段', max_length=32)
    changed_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='操作人',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='crm_stage_changes',
    )
    reason = models.TextField('原因', blank=True, default='')
    changed_at = models.DateTimeField('变更时间', auto_now_add=True, editable=False)

    class Meta:
        managed = False
        db_table = 'crm_opportunity_stage_history'
        ordering = ['-changed_at', '-id']
        verbose_name = '商机阶段历史'
        verbose_name_plural = '商机阶段历史'


class CrmAttachment(models.Model):
    site = models.ForeignKey('sitecore.Site', verbose_name='站点', on_delete=models.DO_NOTHING)
    asset = models.ForeignKey(
        'sitecore.MediaAsset',
        verbose_name='公开素材文件',
        on_delete=models.DO_NOTHING,
        blank=True,
        null=True,
    )
    submission = models.ForeignKey(
        LeadSubmission,
        verbose_name='原始线索',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='crm_attachments',
    )
    company = models.ForeignKey(
        Company,
        verbose_name='企业',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='crm_attachments',
    )
    contact = models.ForeignKey(
        Contact,
        verbose_name='联系人',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='crm_attachments',
    )
    opportunity = models.ForeignKey(
        Opportunity,
        verbose_name='项目/商机',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='crm_attachments',
    )
    uploaded_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='上传人',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='crm_attachments',
    )
    title = models.TextField('标题', blank=True, default='')
    original_name = models.TextField('原始文件名', blank=True, default='')
    mime_type = models.TextField('MIME 类型', blank=True, default='')
    file_size_bytes = models.BigIntegerField('文件大小', default=0)
    sha256 = models.CharField('SHA-256', max_length=64, blank=True, default='')
    storage_path = models.TextField('私有存储路径', blank=True, default='')
    status = models.CharField('状态', max_length=16, default='active')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)

    class Meta:
        managed = False
        db_table = 'crm_attachment'
        ordering = ['-created_at', '-id']
        verbose_name = '销售附件'
        verbose_name_plural = '销售附件'


class SavedView(models.Model):
    site = models.ForeignKey('sitecore.Site', verbose_name='站点', on_delete=models.DO_NOTHING)
    team = models.ForeignKey(
        SalesTeam,
        verbose_name='共享团队',
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name='saved_views',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='用户',
        on_delete=models.DO_NOTHING,
        related_name='crm_saved_views',
    )
    scope = models.CharField('范围', max_length=32)
    name = models.TextField('名称')
    filters_json = models.JSONField('筛选条件', default=dict)
    columns_json = models.JSONField('显示列', default=list)
    sort_json = models.JSONField('排序', default=list)
    is_default = models.BooleanField('默认视图', default=False)
    is_shared = models.BooleanField('团队共享', default=False)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'crm_saved_view'
        ordering = ['scope', 'name', 'id']
        verbose_name = '保存视图'
        verbose_name_plural = '保存视图'
        unique_together = (('site', 'user', 'scope', 'name'),)


class WhatsAppTemplate(models.Model):
    integration = models.ForeignKey(
        'marketing.MarketingIntegration',
        verbose_name='WhatsApp 接入',
        # These tables are unmanaged: PostgreSQL owns the FK action.  Using
        # DO_NOTHING keeps Django's deletion collector from querying WhatsApp
        # tables that intentionally do not exist in legacy SQLite unit tests.
        on_delete=models.DO_NOTHING,
        related_name='whatsapp_templates',
    )
    name = models.TextField('模板名称')
    language = models.CharField('语言', max_length=32)
    category = models.CharField('分类', max_length=32, default='utility')
    status = models.CharField('状态', max_length=32, default='pending')
    provider_template_id = models.TextField('平台模板 ID', blank=True, default='')
    version = models.IntegerField('版本', default=1)
    components_json = models.JSONField('模板组件', default=list)
    quality_rating = models.TextField('质量评级', blank=True, default='')
    rejection_reason = models.TextField('拒绝原因', blank=True, default='')
    synced_at = models.DateTimeField('同步时间', blank=True, null=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'whatsapp_template'
        ordering = ['name', 'language', '-version', 'id']
        verbose_name = 'WhatsApp 模板'
        verbose_name_plural = 'WhatsApp 模板'
        unique_together = (('integration', 'name', 'language', 'version'),)


class WhatsAppConversation(models.Model):
    integration = models.ForeignKey(
        'marketing.MarketingIntegration',
        verbose_name='WhatsApp 接入',
        on_delete=models.DO_NOTHING,
        related_name='whatsapp_conversations',
    )
    site = models.ForeignKey('sitecore.Site', verbose_name='站点', on_delete=models.DO_NOTHING)
    submission = models.ForeignKey(
        LeadSubmission,
        verbose_name='原始线索',
        on_delete=models.DO_NOTHING,
        blank=True,
        null=True,
        related_name='whatsapp_conversations',
    )
    company = models.ForeignKey(
        Company,
        verbose_name='企业',
        on_delete=models.DO_NOTHING,
        blank=True,
        null=True,
        related_name='whatsapp_conversations',
    )
    contact = models.ForeignKey(
        Contact,
        verbose_name='联系人',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='whatsapp_conversations',
    )
    owner_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='负责人',
        on_delete=models.DO_NOTHING,
        blank=True,
        null=True,
        related_name='owned_whatsapp_conversations',
    )
    team = models.ForeignKey(
        SalesTeam,
        verbose_name='销售团队',
        on_delete=models.DO_NOTHING,
        blank=True,
        null=True,
        related_name='whatsapp_conversations',
    )
    external_contact_id = models.TextField('WhatsApp 联系人 ID')
    display_name = models.TextField('显示名称', blank=True, default='')
    status = models.CharField('状态', max_length=24, default='open')
    unread_count = models.IntegerField('未读数', default=0)
    last_message_preview = models.TextField('最后消息摘要', blank=True, default='')
    last_message_at = models.DateTimeField('最后消息时间', blank=True, null=True)
    last_inbound_at = models.DateTimeField('最后入站时间', blank=True, null=True)
    last_outbound_at = models.DateTimeField('最后出站时间', blank=True, null=True)
    service_window_expires_at = models.DateTimeField('客服窗口截止时间', blank=True, null=True)
    closed_at = models.DateTimeField('关闭时间', blank=True, null=True)
    opted_out_at = models.DateTimeField('停止联系时间', blank=True, null=True)
    opt_out_reason = models.TextField('停止联系原因', blank=True, default='')
    metadata_json = models.JSONField('扩展信息', default=dict)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'whatsapp_conversation'
        ordering = ['-last_message_at', '-updated_at', '-id']
        verbose_name = 'WhatsApp 会话'
        verbose_name_plural = 'WhatsApp 会话'
        unique_together = (('integration', 'external_contact_id'),)


class WhatsAppMessage(models.Model):
    message_key = models.UUIDField('消息键', default=uuid.uuid4, editable=False, unique=True)
    conversation = models.ForeignKey(
        WhatsAppConversation,
        verbose_name='会话',
        on_delete=models.DO_NOTHING,
        related_name='messages',
    )
    inbound_event = models.ForeignKey(
        LeadInboundEvent,
        verbose_name='入站收据',
        on_delete=models.DO_NOTHING,
        blank=True,
        null=True,
        related_name='whatsapp_messages',
    )
    template = models.ForeignKey(
        WhatsAppTemplate,
        verbose_name='消息模板',
        on_delete=models.DO_NOTHING,
        blank=True,
        null=True,
        related_name='messages',
    )
    reply_to = models.ForeignKey(
        'self',
        verbose_name='回复消息',
        on_delete=models.DO_NOTHING,
        blank=True,
        null=True,
        related_name='replies',
    )
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='操作人',
        on_delete=models.DO_NOTHING,
        blank=True,
        null=True,
        related_name='whatsapp_messages',
    )
    direction = models.CharField('方向', max_length=16)
    message_type = models.CharField('类型', max_length=24, default='text')
    external_message_id = models.TextField('平台消息 ID', blank=True, default='')
    idempotency_key = models.CharField('幂等键', max_length=64, blank=True, default='')
    request_fingerprint = models.CharField('请求指纹', max_length=64, blank=True, default='')
    provider_request_id = models.TextField('平台请求 ID', blank=True, default='')
    sender_id = models.TextField('发送方', blank=True, default='')
    recipient_id = models.TextField('接收方', blank=True, default='')
    body = models.TextField('消息正文', blank=True, default='')
    status = models.CharField('状态', max_length=24, default='queued')
    error_code = models.TextField('错误代码', blank=True, default='')
    error_message = models.TextField('错误说明', blank=True, default='')
    retryable = models.BooleanField('允许重试', default=False)
    attempts = models.IntegerField('尝试次数', default=0)
    next_attempt_at = models.DateTimeField('下次尝试时间', blank=True, null=True)
    payload_json = models.JSONField('平台载荷', default=dict)
    provider_timestamp = models.DateTimeField('平台时间', blank=True, null=True)
    queued_at = models.DateTimeField('排队时间', default=timezone.now)
    sent_at = models.DateTimeField('发送时间', blank=True, null=True)
    delivered_at = models.DateTimeField('送达时间', blank=True, null=True)
    read_at = models.DateTimeField('已读时间', blank=True, null=True)
    failed_at = models.DateTimeField('失败时间', blank=True, null=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'whatsapp_message'
        ordering = ['provider_timestamp', 'created_at', 'id']
        verbose_name = 'WhatsApp 消息'
        verbose_name_plural = 'WhatsApp 消息'


class WhatsAppMedia(models.Model):
    message = models.ForeignKey(
        WhatsAppMessage,
        verbose_name='消息',
        on_delete=models.DO_NOTHING,
        related_name='media_items',
    )
    external_media_id = models.TextField('平台媒体 ID', blank=True, default='')
    media_type = models.CharField('媒体类型', max_length=24)
    mime_type = models.TextField('MIME 类型', blank=True, default='')
    original_name = models.TextField('原始文件名', blank=True, default='')
    file_size_bytes = models.BigIntegerField('文件大小', default=0)
    sha256 = models.CharField('SHA-256', max_length=64, blank=True, default='')
    storage_path = models.TextField('私有存储路径', blank=True, default='')
    status = models.CharField('状态', max_length=24, default='pending')
    error_message = models.TextField('错误说明', blank=True, default='')
    attempts = models.IntegerField('下载尝试次数', default=0)
    next_attempt_at = models.DateTimeField('下次下载时间', blank=True, null=True)
    downloaded_at = models.DateTimeField('下载完成时间', blank=True, null=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'whatsapp_media'
        ordering = ['message_id', 'id']
        verbose_name = 'WhatsApp 媒体'
        verbose_name_plural = 'WhatsApp 媒体'


class WhatsAppDeliveryEvent(models.Model):
    message = models.ForeignKey(
        WhatsAppMessage,
        verbose_name='消息',
        # PostgreSQL owns the CASCADE.  DO_NOTHING prevents Django's collector
        # from issuing a direct evidence-row DELETE, which the append-only
        # trigger correctly rejects; parent retention deletes still cascade.
        on_delete=models.DO_NOTHING,
        related_name='delivery_events',
    )
    inbound_event = models.ForeignKey(
        LeadInboundEvent,
        verbose_name='入站收据',
        on_delete=models.DO_NOTHING,
        blank=True,
        null=True,
        related_name='whatsapp_delivery_events',
    )
    event_fingerprint = models.CharField('事件指纹', max_length=64, unique=True)
    status = models.CharField('状态', max_length=24)
    error_code = models.TextField('错误代码', blank=True, default='')
    payload_json = models.JSONField('平台载荷', default=dict)
    occurred_at = models.DateTimeField('发生时间')
    received_at = models.DateTimeField('接收时间', default=timezone.now)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)

    class Meta:
        managed = False
        db_table = 'whatsapp_delivery_event'
        ordering = ['-occurred_at', '-id']
        verbose_name = 'WhatsApp 投递事件'
        verbose_name_plural = 'WhatsApp 投递事件'


class CompanyPoolState(models.Model):
    company = models.OneToOneField(
        Company,
        verbose_name='企业',
        on_delete=models.DO_NOTHING,
        primary_key=True,
        related_name='pool_state',
    )
    state = models.CharField('公海状态', max_length=24, default='available')
    evidence_status = models.CharField('证据状态', max_length=24, default='pending')
    version = models.PositiveIntegerField('并发版本', default=1)
    published_at = models.DateTimeField('进入公海时间', default=timezone.now)
    claimed_at = models.DateTimeField('领取时间', blank=True, null=True)
    released_at = models.DateTimeField('释放时间', blank=True, null=True)
    archived_at = models.DateTimeField('归档时间', blank=True, null=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'crm_company_pool_state'
        ordering = ['-published_at', '-company_id']
        verbose_name = '客户公海状态'
        verbose_name_plural = '客户公海状态'


class CompanyContactPoint(models.Model):
    site = models.ForeignKey('sitecore.Site', verbose_name='站点', on_delete=models.DO_NOTHING)
    company = models.ForeignKey(
        Company,
        verbose_name='企业',
        on_delete=models.DO_NOTHING,
        related_name='contact_points',
    )
    contact = models.ForeignKey(
        Contact,
        verbose_name='联系人',
        on_delete=models.DO_NOTHING,
        blank=True,
        null=True,
        related_name='contact_points',
    )
    channel = models.CharField('联系方式类型', max_length=24)
    raw_value = models.TextField('原始值')
    normalized_value = models.TextField('规范值', blank=True, default='')
    extension = models.CharField('分机', max_length=32, blank=True, default='')
    purpose = models.CharField('用途', max_length=24, default='business')
    status = models.CharField('状态', max_length=24, default='unverified')
    usage_status = models.CharField('使用状态', max_length=24, default='unknown')
    evidence_json = models.JSONField('证据', default=dict)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'crm_company_contact_point'
        ordering = ['company_id', 'channel', 'id']
        verbose_name = '企业联系方式'
        verbose_name_plural = '企业联系方式'


class CustomerSource(models.Model):
    site = models.ForeignKey('sitecore.Site', verbose_name='站点', on_delete=models.DO_NOTHING)
    company = models.ForeignKey(
        Company,
        verbose_name='企业',
        on_delete=models.DO_NOTHING,
        related_name='customer_sources',
    )
    submission = models.ForeignKey(
        LeadSubmission,
        verbose_name='原始表单提交',
        on_delete=models.DO_NOTHING,
        blank=True,
        null=True,
        related_name='customer_sources',
    )
    source_type = models.CharField('数据来源', max_length=32)
    intake_method = models.CharField('进入方式', max_length=32)
    source_detail = models.TextField('来源详情', blank=True, default='')
    external_record_id = models.TextField('外部记录 ID', blank=True, default='')
    occurred_at = models.DateTimeField('来源发生时间', blank=True, null=True)
    received_at = models.DateTimeField('系统接收时间', default=timezone.now)
    responsible_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='责任人',
        on_delete=models.DO_NOTHING,
        blank=True,
        null=True,
        related_name='customer_sources',
    )
    responsible_team = models.ForeignKey(
        SalesTeam,
        verbose_name='责任团队',
        on_delete=models.DO_NOTHING,
        blank=True,
        null=True,
        related_name='customer_sources',
    )
    attribution_json = models.JSONField('营销归因', default=dict)
    evidence_json = models.JSONField('来源证据', default=dict)
    evidence_status = models.CharField('证据状态', max_length=24, default='declared')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'crm_customer_source'
        ordering = ['company_id', 'received_at', 'id']
        verbose_name = '客户数据来源'
        verbose_name_plural = '客户数据来源'


class CustomerImportBatch(models.Model):
    site = models.ForeignKey('sitecore.Site', verbose_name='站点', on_delete=models.DO_NOTHING)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='创建人',
        on_delete=models.DO_NOTHING,
        related_name='customer_import_batches',
    )
    namespace = models.CharField('导入命名空间', max_length=80, default='customer')
    source_type = models.CharField('数据来源', max_length=32)
    adapter_version = models.CharField('适配器版本', max_length=32, default='v1')
    file_sha256 = models.CharField('文件 SHA-256', max_length=64)
    original_name = models.TextField('原始文件名')
    status = models.CharField('状态', max_length=24, default='preview')
    counts_json = models.JSONField('行数统计', default=dict)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)
    completed_at = models.DateTimeField('完成时间', blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'crm_customer_import_batch'
        ordering = ['-created_at', '-id']
        verbose_name = '客户导入批次'
        verbose_name_plural = '客户导入批次'
        unique_together = (('site', 'namespace', 'file_sha256', 'adapter_version'),)


class CustomerImportRow(models.Model):
    batch = models.ForeignKey(
        CustomerImportBatch,
        verbose_name='导入批次',
        on_delete=models.DO_NOTHING,
        related_name='rows',
    )
    row_key = models.CharField('行幂等键', max_length=64)
    row_number = models.PositiveIntegerField('源文件行号')
    payload_json = models.JSONField('规范化数据', default=dict)
    status = models.CharField('处理状态', max_length=24)
    company = models.ForeignKey(
        Company,
        verbose_name='匹配企业',
        on_delete=models.DO_NOTHING,
        blank=True,
        null=True,
        related_name='import_rows',
    )
    result_json = models.JSONField('处理结果', default=dict)
    message = models.TextField('说明', blank=True, default='')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'crm_customer_import_row'
        ordering = ['batch_id', 'row_number', 'id']
        verbose_name = '客户导入行'
        verbose_name_plural = '客户导入行'
        unique_together = (('batch', 'row_key'),)


class CustomerExportJob(models.Model):
    site = models.ForeignKey('sitecore.Site', verbose_name='站点', on_delete=models.DO_NOTHING)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='创建人',
        on_delete=models.DO_NOTHING,
        related_name='customer_export_jobs',
    )
    status = models.CharField('状态', max_length=24, default='pending')
    scope_json = models.JSONField('导出范围', default=dict)
    fields_json = models.JSONField('导出字段', default=list)
    format = models.CharField('格式', max_length=16, default='xlsx')
    record_count = models.PositiveIntegerField('记录数', default=0)
    storage_path = models.TextField('私有存储路径', blank=True, default='')
    sha256 = models.CharField('文件 SHA-256', max_length=64, blank=True, default='')
    expires_at = models.DateTimeField('过期时间', blank=True, null=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)
    completed_at = models.DateTimeField('完成时间', blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'crm_customer_export_job'
        ordering = ['-created_at', '-id']
        verbose_name = '客户导出任务'
        verbose_name_plural = '客户导出任务'


class CustomerPoolRow(models.Model):
    site = models.ForeignKey('sitecore.Site', verbose_name='站点', on_delete=models.DO_NOTHING)
    company = models.ForeignKey(
        Company,
        verbose_name='企业',
        on_delete=models.DO_NOTHING,
        related_name='pool_rows',
    )
    contact = models.ForeignKey(
        Contact,
        verbose_name='联系人',
        on_delete=models.DO_NOTHING,
        blank=True,
        null=True,
        related_name='pool_rows',
    )
    import_batch = models.ForeignKey(
        CustomerImportBatch,
        verbose_name='导入批次',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='pool_rows',
    )
    import_row = models.ForeignKey(
        CustomerImportRow,
        verbose_name='导入行',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='pool_rows',
    )
    row_key = models.CharField('行幂等键', max_length=64)
    row_number = models.PositiveIntegerField('源文件行号', default=0)
    phone = models.TextField('电话', blank=True, default='')
    email = models.TextField('邮箱', blank=True, default='')
    country_name = models.TextField('国家名称', blank=True, default='')
    country_code = models.CharField('国家代码', max_length=2, blank=True, default='')
    person_name = models.TextField('人物姓名', blank=True, default='')
    company_name = models.TextField('企业名称', blank=True, default='')
    value = models.DecimalField('价值分', max_digits=6, decimal_places=1)
    email_2 = models.TextField('邮箱2', blank=True, default='')
    email_3 = models.TextField('邮箱3', blank=True, default='')
    phone_2 = models.TextField('电话2', blank=True, default='')
    phone_3 = models.TextField('电话3', blank=True, default='')
    route_type = models.TextField('路线类型', blank=True, default='')
    route_tier = models.TextField('路线层级', blank=True, default='')
    account_id = models.TextField('账户 ID', blank=True, default='')
    whatsapp_confirmed = models.TextField('WhatsApp 确认', blank=True, default='')
    evidence_v = models.TextField('证据 V', blank=True, default='')
    identity_i = models.TextField('身份 I', blank=True, default='')
    tech_t = models.TextField('技术 T', blank=True, default='')
    priority_p = models.TextField('优先级 P', blank=True, default='')
    restriction_note = models.TextField('限制注记', blank=True, default='')
    source_channel = models.TextField('来源渠道', blank=True, default='')
    project_signal = models.BooleanField('项目/采购信号', default=False)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'crm_customer_pool_row'
        ordering = ['company_id', '-value', 'id']
        verbose_name = '客户公海标准行'
        verbose_name_plural = '客户公海标准行'
        unique_together = (('site', 'row_key'),)
