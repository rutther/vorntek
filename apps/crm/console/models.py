from django.conf import settings
from django.contrib.auth.models import Group
from django.db import models

from sitecore.models import Release, Site, SiteLocale


CONTENT_ACCESS_CAPABILITIES = (
    'content.read',
    'content.write',
    'content.set_published',
    'content.locale.manage',
    'assets.read',
    'assets.write',
    'assets.import_local',
    'releases.read',
    'releases.preview_build',
    'releases.candidate_build',
    'releases.candidate_select',
    'releases.deploy',
)


class ContentAccessGrant(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='用户主体',
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name='content_access_grants',
    )
    group = models.ForeignKey(
        Group,
        verbose_name='用户组主体',
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name='content_access_grants',
    )
    site = models.ForeignKey(
        Site,
        verbose_name='站点',
        on_delete=models.CASCADE,
        related_name='content_access_grants',
    )
    locale = models.ForeignKey(
        SiteLocale,
        verbose_name='语言范围',
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name='content_access_grants',
    )
    capability = models.CharField('能力', max_length=64)
    enabled = models.BooleanField('启用', default=True)
    granted_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='授权人',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='granted_content_access_grants',
    )
    reason = models.TextField('授权原因', blank=True, default='')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta:
        managed = False
        db_table = 'content_access_grant'
        ordering = ['user_id', 'group_id', 'site_id', 'locale_id', 'capability', 'id']
        constraints = (
            models.CheckConstraint(
                condition=(
                    models.Q(user__isnull=False, group__isnull=True)
                    | models.Q(user__isnull=True, group__isnull=False)
                ),
                name='content_access_grant_subject_xor',
            ),
            models.CheckConstraint(
                condition=models.Q(capability__in=CONTENT_ACCESS_CAPABILITIES),
                name='content_access_grant_capability_check',
            ),
            models.UniqueConstraint(
                fields=('user', 'site', 'capability'),
                condition=models.Q(
                    enabled=True,
                    user__isnull=False,
                    group__isnull=True,
                    locale__isnull=True,
                ),
                name='uq_content_grant_user_site_active',
            ),
            models.UniqueConstraint(
                fields=('user', 'site', 'locale', 'capability'),
                condition=models.Q(
                    enabled=True,
                    user__isnull=False,
                    group__isnull=True,
                    locale__isnull=False,
                ),
                name='uq_content_grant_user_locale_active',
            ),
            models.UniqueConstraint(
                fields=('group', 'site', 'capability'),
                condition=models.Q(
                    enabled=True,
                    group__isnull=False,
                    user__isnull=True,
                    locale__isnull=True,
                ),
                name='uq_content_grant_group_site_active',
            ),
            models.UniqueConstraint(
                fields=('group', 'site', 'locale', 'capability'),
                condition=models.Q(
                    enabled=True,
                    group__isnull=False,
                    user__isnull=True,
                    locale__isnull=False,
                ),
                name='uq_content_grant_group_locale_active',
            ),
        )
        indexes = (
            models.Index(
                fields=('user', 'capability', 'site', 'locale'),
                condition=models.Q(enabled=True, user__isnull=False),
                name='idx_content_grant_user_lookup',
            ),
            models.Index(
                fields=('group', 'capability', 'site', 'locale'),
                condition=models.Q(enabled=True, group__isnull=False),
                name='idx_content_grant_group_lookup',
            ),
        )

    def __str__(self) -> str:
        subject = self.user_id if self.user_id is not None else self.group_id
        subject_type = 'user' if self.user_id is not None else 'group'
        locale = self.locale_id if self.locale_id is not None else '*'
        return f'{subject_type}:{subject}:{self.site_id}:{locale}:{self.capability}'


class WebsiteReleaseSelection(models.Model):
    request_token = models.UUIDField('请求令牌', unique=True)
    site = models.ForeignKey(
        Site,
        verbose_name='站点',
        on_delete=models.PROTECT,
        related_name='website_release_selections',
    )
    release = models.ForeignKey(
        Release,
        verbose_name='整站候选',
        on_delete=models.PROTECT,
        related_name='website_selections',
    )
    version = models.CharField('候选版本', max_length=64)
    previous_version = models.CharField('前一选择版本', max_length=64, blank=True, default='')
    selected_by = models.TextField('选择人')
    reason = models.TextField('选择原因')
    created_at = models.DateTimeField('选择时间', auto_now_add=True, editable=False)

    class Meta:
        managed = False
        db_table = 'website_release_selection'
        ordering = ['-id']
        indexes = (
            models.Index(
                fields=('site', '-id'),
                name='idx_website_select_site',
            ),
        )

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError('WebsiteReleaseSelection is append-only.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('WebsiteReleaseSelection is append-only.')

    def __str__(self) -> str:
        return f'{self.site_id}:{self.version}'


class WebsiteDeploymentOperation(models.Model):
    request_token = models.UUIDField('请求令牌', unique=True)
    site = models.ForeignKey(
        Site,
        verbose_name='站点',
        on_delete=models.PROTECT,
        related_name='website_deployment_operations',
    )
    selection = models.ForeignKey(
        WebsiteReleaseSelection,
        verbose_name='候选选择',
        on_delete=models.PROTECT,
        related_name='deployment_operations',
    )
    release = models.ForeignKey(
        Release,
        verbose_name='整站候选',
        on_delete=models.PROTECT,
        related_name='website_deployment_operations',
    )
    version = models.CharField('部署版本', max_length=64)
    previous_version = models.CharField('前一部署版本', max_length=64, blank=True, default='')
    deployed_by = models.TextField('部署人')
    reason = models.TextField('部署原因')
    status = models.CharField('状态', max_length=16, default='prepared')
    error_code = models.CharField('失败代码', max_length=100, blank=True, default='')
    created_at = models.DateTimeField('准备时间', auto_now_add=True, editable=False)
    completed_at = models.DateTimeField('完成时间', blank=True, null=True, editable=False)

    class Meta:
        managed = False
        db_table = 'website_deployment_operation'
        ordering = ['-id']

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError('WebsiteDeploymentOperation transitions require the deployment service.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('WebsiteDeploymentOperation cannot be deleted.')


class WebsiteReleaseDeployment(models.Model):
    operation = models.OneToOneField(
        WebsiteDeploymentOperation,
        verbose_name='部署操作',
        on_delete=models.PROTECT,
        related_name='receipt',
    )
    request_token = models.UUIDField('请求令牌', unique=True)
    site = models.ForeignKey(
        Site,
        verbose_name='站点',
        on_delete=models.PROTECT,
        related_name='website_deployments',
    )
    selection = models.ForeignKey(
        WebsiteReleaseSelection,
        verbose_name='候选选择',
        on_delete=models.PROTECT,
        related_name='deployment_receipts',
    )
    release = models.ForeignKey(
        Release,
        verbose_name='整站候选',
        on_delete=models.PROTECT,
        related_name='website_deployments',
    )
    version = models.CharField('部署版本', max_length=64)
    previous_version = models.CharField('前一部署版本', max_length=64, blank=True, default='')
    deployed_by = models.TextField('部署人')
    reason = models.TextField('部署原因')
    created_at = models.DateTimeField('部署时间', auto_now_add=True, editable=False)

    class Meta:
        managed = False
        db_table = 'website_release_deployment'
        ordering = ['-id']

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError('WebsiteReleaseDeployment is append-only.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('WebsiteReleaseDeployment is append-only.')


class AuditLog(models.Model):
    actor = models.TextField('操作者', default='system')
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='操作者账号',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='siteos_audit_entries',
    )
    action = models.TextField('动作')
    entity_table = models.TextField('对象类型')
    entity_id = models.BigIntegerField('对象 ID', blank=True, null=True)
    before_json = models.JSONField('变更前', blank=True, null=True)
    after_json = models.JSONField('变更后', blank=True, null=True)
    request_id = models.TextField('请求 ID', blank=True, default='')
    source = models.TextField('来源', default='console')
    metadata_json = models.JSONField('上下文', default=dict)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)

    class Meta:
        managed = False
        db_table = 'audit_log'
        ordering = ['-created_at', '-id']
        verbose_name = '审计记录'
        verbose_name_plural = '审计记录'

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError('AuditLog is append-only.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('AuditLog is append-only.')
