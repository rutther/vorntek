from __future__ import annotations

import uuid

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
    environment = models.CharField('接入环境', max_length=16, default='production')
    routing_mode = models.CharField('路由模式', max_length=24, default='flow_scoped')
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
        original = type(self).objects.get(pk=self.pk) if self.pk else None
        if original and original.integration_type == 'pixel':
            identity = ('public_id', 'site_id', 'environment', 'routing_mode', 'provider_id', 'integration_type')
            if any(getattr(original, k) != getattr(self, k) for k in identity):
                raise ValidationError({'public_id': '已保存的 Pixel 身份与环境不可覆盖，请建立新的接入记录。'})
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


class MarketingLoop(models.Model):
    loop_key = models.UUIDField('闭环键', default=uuid.uuid4, editable=False, unique=True)
    site = models.ForeignKey(
        'sitecore.Site',
        verbose_name='站点',
        on_delete=models.DO_NOTHING,
        related_name='marketing_loops',
    )
    provider = models.ForeignKey(
        MarketingProvider,
        verbose_name='平台',
        on_delete=models.DO_NOTHING,
        related_name='loops',
    )
    name = models.CharField('闭环名称', max_length=200)
    binding_key = models.CharField('关联编码', max_length=160, unique=True)
    route_code = models.CharField('广告路线', max_length=40)
    environment = models.CharField('环境', max_length=16)
    scope_code = models.CharField('范围代码', max_length=80)
    sequence_no = models.PositiveIntegerField('序号')
    template_version = models.CharField('模板版本', max_length=32, default='meta-web-lead-v1')
    status = models.CharField('状态', max_length=24, default='draft')
    current_task_key = models.CharField('当前任务', max_length=80, default='dataset_setup')
    desired_revision = models.PositiveIntegerField('期望版本', default=1)
    observed_revision = models.PositiveIntegerField('观测版本', default=0)
    config_json = models.JSONField('配置', default=dict)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='创建人',
        on_delete=models.DO_NOTHING,
        related_name='created_marketing_loops',
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='更新人',
        on_delete=models.DO_NOTHING,
        related_name='updated_marketing_loops',
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'marketing_loop'
        ordering = ['-updated_at', '-id']
        verbose_name = '营销闭环'
        verbose_name_plural = '营销闭环'
        constraints = (
            models.UniqueConstraint(
                fields=('site', 'provider', 'route_code', 'environment', 'scope_code', 'sequence_no'),
                name='uq_marketing_loop_identity',
            ),
        )

    def __str__(self) -> str:
        return self.name


class MarketingRuntimeBinding(models.Model):
    loop = models.OneToOneField(MarketingLoop, on_delete=models.PROTECT, related_name='runtime_binding')
    integration = models.ForeignKey(MarketingIntegration, on_delete=models.PROTECT, null=True)
    version = models.PositiveIntegerField(default=0)
    draft_json = models.JSONField(default=dict)
    active_revision_key = models.UUIDField(null=True)
    paused = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = 'marketing_runtime_binding'


class MarketingRuntimeRevision(models.Model):
    revision_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    binding = models.ForeignKey(MarketingRuntimeBinding, on_delete=models.PROTECT, related_name='revisions')
    integration = models.ForeignKey(MarketingIntegration, on_delete=models.PROTECT)
    site = models.ForeignKey('sitecore.Site', on_delete=models.PROTECT)
    environment = models.CharField(max_length=16)
    pixel_id = models.CharField(max_length=255)
    flow_version = models.PositiveIntegerField()
    config_hash = models.CharField(max_length=64)
    config_json = models.JSONField(default=dict)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('运行版本不可修改，请创建新版本。')
        return super().save(*args, **kwargs)

    class Meta:
        managed = False
        db_table = 'marketing_runtime_revision'


class MarketingRuntimeCommand(models.Model):
    binding = models.ForeignKey(MarketingRuntimeBinding, on_delete=models.PROTECT)
    request_id = models.UUIDField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    digest = models.CharField(max_length=64)
    action = models.CharField(max_length=32)
    result_json = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'marketing_runtime_command'
        constraints = [models.UniqueConstraint(fields=('binding', 'request_id'), name='uq_runtime_command')]


class MarketingLoopNode(models.Model):
    loop = models.ForeignKey(
        MarketingLoop,
        verbose_name='闭环',
        on_delete=models.DO_NOTHING,
        related_name='nodes',
    )
    node_key = models.CharField('节点代码', max_length=80)
    node_type = models.CharField('节点类型', max_length=40)
    title = models.CharField('节点名称', max_length=160)
    actor = models.CharField('责任侧', max_length=24)
    sequence_no = models.PositiveIntegerField('显示顺序')
    status = models.CharField('状态', max_length=24, default='pending')
    summary = models.TextField('状态说明', blank=True, default='')
    desired_json = models.JSONField('期望状态', default=dict)
    observed_json = models.JSONField('实际状态', default=dict)
    evidence_json = models.JSONField('状态证据', default=dict)
    last_checked_at = models.DateTimeField('最近检查时间', blank=True, null=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'marketing_loop_node'
        ordering = ['sequence_no', 'id']
        verbose_name = '闭环节点'
        verbose_name_plural = '闭环节点'
        unique_together = (('loop', 'node_key'),)

    def __str__(self) -> str:
        return f'{self.loop.binding_key}:{self.node_key}'


class MarketingLoopEdge(models.Model):
    loop = models.ForeignKey(
        MarketingLoop,
        verbose_name='闭环',
        on_delete=models.DO_NOTHING,
        related_name='edges',
    )
    edge_key = models.CharField('连接代码', max_length=80)
    source_node = models.ForeignKey(
        MarketingLoopNode,
        verbose_name='起点',
        on_delete=models.DO_NOTHING,
        related_name='outgoing_edges',
    )
    target_node = models.ForeignKey(
        MarketingLoopNode,
        verbose_name='终点',
        on_delete=models.DO_NOTHING,
        related_name='incoming_edges',
    )
    signal_code = models.CharField('传递内容', max_length=80)
    status = models.CharField('状态', max_length=24, default='pending')
    observed_json = models.JSONField('实际状态', default=dict)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'marketing_loop_edge'
        ordering = ['id']
        verbose_name = '闭环连接'
        verbose_name_plural = '闭环连接'
        unique_together = (('loop', 'edge_key'),)


class MarketingLoopTask(models.Model):
    loop = models.ForeignKey(
        MarketingLoop,
        verbose_name='闭环',
        on_delete=models.DO_NOTHING,
        related_name='tasks',
    )
    node = models.ForeignKey(
        MarketingLoopNode,
        verbose_name='关联节点',
        on_delete=models.DO_NOTHING,
        blank=True,
        null=True,
        related_name='tasks',
    )
    task_key = models.CharField('任务代码', max_length=80)
    title = models.CharField('任务名称', max_length=200)
    owner_type = models.CharField('责任方', max_length=24)
    sequence_no = models.PositiveIntegerField('步骤顺序')
    status = models.CharField('状态', max_length=24, default='blocked')
    input_json = models.JSONField('操作输入', default=dict)
    result_json = models.JSONField('操作结果', default=dict)
    evidence_json = models.JSONField('完成证据', default=dict)
    started_at = models.DateTimeField('开始时间', blank=True, null=True)
    completed_at = models.DateTimeField('完成时间', blank=True, null=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'marketing_loop_task'
        ordering = ['sequence_no', 'id']
        verbose_name = '闭环引导任务'
        verbose_name_plural = '闭环引导任务'
        unique_together = (('loop', 'task_key'),)


class MarketingLoopAssetBinding(models.Model):
    loop = models.ForeignKey(
        MarketingLoop,
        verbose_name='闭环',
        on_delete=models.DO_NOTHING,
        related_name='asset_bindings',
    )
    node = models.ForeignKey(
        MarketingLoopNode,
        verbose_name='关联节点',
        on_delete=models.DO_NOTHING,
        blank=True,
        null=True,
        related_name='asset_bindings',
    )
    provider = models.ForeignKey(
        MarketingProvider,
        verbose_name='平台',
        on_delete=models.DO_NOTHING,
        related_name='loop_asset_bindings',
    )
    role = models.CharField('资产角色', max_length=40)
    asset_type = models.CharField('资产类型', max_length=40)
    external_id = models.CharField('平台资产 ID', max_length=255, blank=True, default='')
    expected_name = models.CharField('预期名称', max_length=255, blank=True, default='')
    observed_name = models.CharField('实际名称', max_length=255, blank=True, default='')
    reuse_mode = models.CharField('使用方式', max_length=16, default='undecided')
    status = models.CharField('状态', max_length=24, default='planned')
    metadata_json = models.JSONField('附加信息', default=dict)
    last_verified_at = models.DateTimeField('最近验证时间', blank=True, null=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'marketing_loop_asset_binding'
        ordering = ['role', 'id']
        verbose_name = '闭环资产绑定'
        verbose_name_plural = '闭环资产绑定'
        unique_together = (('loop', 'role'),)
        constraints = (
            models.UniqueConstraint(
                fields=('provider', 'asset_type', 'external_id'),
                condition=models.Q(reuse_mode='new') & ~models.Q(external_id=''),
                name='uq_marketing_loop_new_asset_id',
            ),
        )
