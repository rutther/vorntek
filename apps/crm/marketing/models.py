from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class MarketingProvider(models.Model):
    code = models.CharField('代码', max_length=80, unique=True)
    name = models.CharField('名称', max_length=200)
    enabled = models.BooleanField('启用', default=True)
    capabilities = models.JSONField('能力', default=dict)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'marketing_provider'
        ordering = ['code']

    def __str__(self) -> str:
        return self.name


class MarketingIntegration(models.Model):
    site = models.ForeignKey(
        'sitecore.Site',
        verbose_name='站点',
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    provider = models.ForeignKey(MarketingProvider, verbose_name='平台', on_delete=models.DO_NOTHING)
    name = models.CharField('接入名称', max_length=200)
    integration_type = models.CharField('接入类型', max_length=80)
    public_id = models.CharField('公开 ID', max_length=255)
    secret_ref = models.CharField('密钥引用', max_length=255, blank=True, null=True)
    consent_category = models.CharField('同意分类', max_length=64, default='marketing')
    enabled = models.BooleanField('启用', default=False)
    config_json = models.JSONField('配置 JSON', default=dict)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'marketing_integration'
        ordering = ['provider__code', 'integration_type', 'id']

    def clean(self) -> None:
        super().clean()
        if self.provider_id and self.provider.code == 'meta' and self.integration_type == 'pixel':
            if self.public_id and not self.public_id.isdigit():
                raise ValidationError({'public_id': 'Meta Pixel ID 必须为数字。'})

    def __str__(self) -> str:
        return self.name


class IntegrationCheck(models.Model):
    integration = models.ForeignKey(
        MarketingIntegration,
        verbose_name='营销接入',
        on_delete=models.CASCADE,
        related_name='checks',
    )
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='执行人',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='marketing_integration_checks',
    )
    check_type = models.CharField('检查类型', max_length=32)
    delivery_mode = models.CharField('发送模式', max_length=16, default='live')
    status = models.CharField('检查状态', max_length=16, default='unknown')
    evidence_level = models.CharField('证据级别', max_length=32, default='local')
    summary = models.TextField('结论', blank=True, default='')
    details_json = models.JSONField('检查证据', default=dict)
    checked_at = models.DateTimeField('检查时间', auto_now_add=True, editable=False)

    class Meta:
        managed = False
        db_table = 'marketing_integration_check'
        ordering = ['-checked_at', '-id']
        verbose_name = '营销接入检查'
        verbose_name_plural = '营销接入检查'


class CanonicalEvent(models.Model):
    code = models.CharField('代码', max_length=80, unique=True)
    name = models.CharField('名称', max_length=200)
    description = models.TextField('说明', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'canonical_event'


class ProviderEventMapping(models.Model):
    provider = models.ForeignKey(MarketingProvider, on_delete=models.DO_NOTHING)
    canonical_event = models.ForeignKey(CanonicalEvent, on_delete=models.DO_NOTHING)
    provider_event_name = models.CharField(max_length=200)
    enabled = models.BooleanField(default=True)
    config_json = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'provider_event_mapping'


class TrackingRule(models.Model):
    integration = models.ForeignKey(MarketingIntegration, on_delete=models.DO_NOTHING)
    canonical_event = models.ForeignKey(CanonicalEvent, on_delete=models.DO_NOTHING, blank=True, null=True)
    scope_type = models.CharField(max_length=80)
    scope_value = models.CharField(max_length=255, default='*')
    priority = models.IntegerField(default=100)
    enabled = models.BooleanField(default=True)
    config_json = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'tracking_rule'
