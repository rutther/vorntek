from __future__ import annotations

from django.db import models


class UnmanagedModel(models.Model):
    class Meta:
        abstract = True
        managed = False


class TimestampedModel(UnmanagedModel):
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    updated_at = models.DateTimeField('更新时间', auto_now=True, editable=False)

    class Meta(UnmanagedModel.Meta):
        abstract = True


class Site(TimestampedModel):
    code = models.CharField('代码', max_length=80, unique=True)
    name = models.CharField('名称', max_length=200)
    base_url = models.TextField('基础网址', blank=True)
    default_locale = models.CharField('默认语言', max_length=16, default='en')
    enabled = models.BooleanField('启用', default=True)
    config_json = models.JSONField('配置 JSON', default=dict)

    class Meta(UnmanagedModel.Meta):
        db_table = 'site'
        ordering = ['code']

    def __str__(self) -> str:
        return self.name


class SiteLocale(TimestampedModel):
    site = models.ForeignKey(Site, verbose_name='站点', on_delete=models.CASCADE, related_name='locales')
    locale_code = models.CharField('语言代码', max_length=16)
    label = models.CharField('语言名称', max_length=100)
    direction = models.CharField('文字方向', max_length=8, default='ltr')
    is_default = models.BooleanField('默认语言', default=False)
    enabled = models.BooleanField('启用', default=True)
    sort_order = models.IntegerField('排序', default=100)

    class Meta(UnmanagedModel.Meta):
        db_table = 'site_locale'
        ordering = ['site__code', 'sort_order', 'locale_code']
        unique_together = (('site', 'locale_code'),)

    def __str__(self) -> str:
        return f'{self.site.code}:{self.label}'


class PageRoute(TimestampedModel):
    site = models.ForeignKey(Site, verbose_name='站点', on_delete=models.CASCADE, related_name='routes')
    locale = models.ForeignKey(SiteLocale, verbose_name='语言', on_delete=models.PROTECT, related_name='routes')
    path = models.TextField('路径')
    route_type = models.CharField('路由类型', max_length=32)
    status = models.CharField('状态', max_length=32, default='draft')
    page_title = models.TextField('页面标题')
    meta_description = models.TextField('SEO 描述', blank=True)
    canonical_url = models.TextField('Canonical URL', blank=True)
    og_title = models.TextField('OG 标题', blank=True)
    og_description = models.TextField('OG 描述', blank=True)
    og_image_url = models.TextField('OG 图片', blank=True)
    robots = models.TextField('Robots', default='index,follow,max-image-preview:large')
    published_at = models.DateTimeField('发布时间', blank=True, null=True)

    class Meta(UnmanagedModel.Meta):
        db_table = 'page_route'
        ordering = ['site__code', 'locale__sort_order', 'path']
        unique_together = (('site', 'locale', 'path'),)

    def __str__(self) -> str:
        return self.path


class FreePage(TimestampedModel):
    route = models.OneToOneField(PageRoute, verbose_name='路由', on_delete=models.CASCADE, related_name='free_page')
    template_key = models.CharField('模板', max_length=80)
    title = models.TextField('标题')
    summary = models.TextField('摘要', blank=True)
    hero_json = models.JSONField('首屏内容', default=dict)
    body_json = models.JSONField('正文内容', default=dict)
    config_json = models.JSONField('配置 JSON', default=dict)

    class Meta(UnmanagedModel.Meta):
        db_table = 'free_page'

    def __str__(self) -> str:
        return self.title


class Category(TimestampedModel):
    site = models.ForeignKey(Site, verbose_name='站点', on_delete=models.CASCADE, related_name='categories')
    locale = models.ForeignKey(SiteLocale, verbose_name='语言', on_delete=models.PROTECT, related_name='categories')
    parent = models.ForeignKey(
        'self',
        verbose_name='上级分类',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='children',
    )
    code = models.CharField('代码', max_length=80)
    name = models.TextField('名称')
    slug = models.CharField('Slug', max_length=160)
    description = models.TextField('说明', blank=True)
    status = models.CharField('状态', max_length=32, default='published')
    sort_order = models.IntegerField('排序', default=100)
    seo_json = models.JSONField('SEO JSON', default=dict)

    class Meta(UnmanagedModel.Meta):
        db_table = 'category'
        ordering = ['locale__sort_order', 'sort_order', 'name']
        unique_together = (('site', 'locale', 'slug'), ('site', 'locale', 'code'))

    def __str__(self) -> str:
        return self.name


class Article(TimestampedModel):
    route = models.OneToOneField(PageRoute, verbose_name='路由', on_delete=models.CASCADE, related_name='article')
    category = models.ForeignKey(
        Category,
        verbose_name='分类',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='articles',
    )
    title = models.TextField('标题')
    slug = models.CharField('Slug', max_length=160)
    excerpt = models.TextField('摘要', blank=True)
    body_markdown = models.TextField('Markdown 正文', blank=True)
    body_json = models.JSONField('结构化正文', default=dict)
    cover_image_url = models.TextField('封面图片', blank=True)
    author_name = models.TextField('作者', blank=True)
    status = models.CharField('状态', max_length=32, default='draft')
    published_at = models.DateTimeField('发布时间', blank=True, null=True)
    reading_minutes = models.IntegerField('阅读分钟数', default=1)
    seo_json = models.JSONField('SEO JSON', default=dict)
    config_json = models.JSONField('配置 JSON', default=dict)
    tags = models.ManyToManyField('Tag', through='ArticleTag', related_name='articles')

    class Meta(UnmanagedModel.Meta):
        db_table = 'article'
        ordering = ['-published_at', '-id']

    def __str__(self) -> str:
        return self.title


class Tag(TimestampedModel):
    site = models.ForeignKey(Site, verbose_name='站点', on_delete=models.CASCADE, related_name='tags')
    locale = models.ForeignKey(SiteLocale, verbose_name='语言', on_delete=models.PROTECT, related_name='tags')
    code = models.CharField('代码', max_length=80)
    name = models.TextField('名称')
    slug = models.CharField('Slug', max_length=160)

    class Meta(UnmanagedModel.Meta):
        db_table = 'tag'
        ordering = ['name']
        unique_together = (('site', 'locale', 'code'), ('site', 'locale', 'slug'))

    def __str__(self) -> str:
        return self.name


class ArticleTag(UnmanagedModel):
    article = models.ForeignKey(Article, on_delete=models.CASCADE)
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)

    class Meta(UnmanagedModel.Meta):
        db_table = 'article_tag'
        unique_together = (('article', 'tag'),)


class NavigationMenu(TimestampedModel):
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name='navigation_menus')
    locale = models.ForeignKey(SiteLocale, on_delete=models.PROTECT, related_name='navigation_menus')
    code = models.CharField(max_length=80)
    name = models.TextField()
    region = models.CharField(max_length=32)
    enabled = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=100)

    class Meta(UnmanagedModel.Meta):
        db_table = 'navigation_menu'
        unique_together = (('site', 'locale', 'code'),)


class NavigationItem(TimestampedModel):
    menu = models.ForeignKey(NavigationMenu, on_delete=models.CASCADE, related_name='items')
    parent = models.ForeignKey('self', on_delete=models.CASCADE, blank=True, null=True, related_name='children')
    route = models.ForeignKey(PageRoute, on_delete=models.SET_NULL, blank=True, null=True)
    label = models.TextField()
    href = models.TextField(blank=True)
    sort_order = models.IntegerField(default=100)
    enabled = models.BooleanField(default=True)
    config_json = models.JSONField(default=dict)

    class Meta(UnmanagedModel.Meta):
        db_table = 'navigation_item'


class ComponentDefinition(TimestampedModel):
    code = models.CharField(max_length=80, unique=True)
    name = models.TextField()
    component_type = models.CharField(max_length=32)
    description = models.TextField(blank=True)
    enabled = models.BooleanField(default=True)
    config_schema = models.JSONField(default=dict)

    class Meta(UnmanagedModel.Meta):
        db_table = 'component_definition'


class ComponentBinding(TimestampedModel):
    component = models.ForeignKey(ComponentDefinition, on_delete=models.CASCADE, related_name='bindings')
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name='component_bindings')
    locale = models.ForeignKey(SiteLocale, on_delete=models.CASCADE, blank=True, null=True)
    scope_type = models.CharField(max_length=32)
    scope_value = models.TextField(default='*')
    region = models.CharField(max_length=32)
    priority = models.IntegerField(default=100)
    enabled = models.BooleanField(default=True)
    config_json = models.JSONField(default=dict)

    class Meta(UnmanagedModel.Meta):
        db_table = 'component_binding'


class Cta(TimestampedModel):
    site = models.ForeignKey(Site, verbose_name='站点', on_delete=models.CASCADE, related_name='ctas')
    code = models.CharField('代码', max_length=80)
    name = models.TextField('名称')
    label = models.TextField('显示文字')
    target_url = models.TextField('目标网址')
    target_type = models.CharField('目标类型', max_length=32, default='internal')
    business_goal = models.CharField('业务目标', max_length=80, default='lead')
    event = models.ForeignKey(
        'marketing.CanonicalEvent',
        verbose_name='标准事件',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    enabled = models.BooleanField('启用', default=True)
    config_json = models.JSONField('配置 JSON', default=dict)

    class Meta(UnmanagedModel.Meta):
        db_table = 'cta'
        ordering = ['business_goal', 'code']
        unique_together = (('site', 'code'),)

    def __str__(self) -> str:
        return self.name


class CtaBinding(TimestampedModel):
    cta = models.ForeignKey(Cta, on_delete=models.CASCADE, related_name='bindings')
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name='cta_bindings')
    locale = models.ForeignKey(SiteLocale, on_delete=models.CASCADE, blank=True, null=True)
    scope_type = models.CharField(max_length=32)
    scope_value = models.TextField(default='*')
    position = models.CharField(max_length=32, default='content_bottom')
    priority = models.IntegerField(default=100)
    enabled = models.BooleanField(default=True)
    config_json = models.JSONField(default=dict)

    class Meta(UnmanagedModel.Meta):
        db_table = 'cta_binding'


class Release(UnmanagedModel):
    site = models.ForeignKey(Site, verbose_name='站点', on_delete=models.CASCADE, related_name='releases')
    release_key = models.CharField('发布键', max_length=255, unique=True)
    status = models.CharField('状态', max_length=32, default='draft')
    snapshot_manifest = models.JSONField('快照清单', default=dict)
    artifact_path = models.TextField('产物路径', blank=True)
    created_by = models.TextField('创建者', default='system')
    notes = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)
    exported_at = models.DateTimeField('导出时间', blank=True, null=True)
    built_at = models.DateTimeField('构建时间', blank=True, null=True)
    published_at = models.DateTimeField('发布时间', blank=True, null=True)

    class Meta(UnmanagedModel.Meta):
        db_table = 'release'
        ordering = ['-created_at', '-id']


class ReleaseBuild(UnmanagedModel):
    release = models.ForeignKey(Release, verbose_name='发布', on_delete=models.CASCADE, related_name='builds')
    build_key = models.CharField('构建键', max_length=255)
    status = models.CharField('状态', max_length=32, default='queued')
    artifact_path = models.TextField('产物路径', blank=True)
    log_excerpt = models.TextField('日志摘要', blank=True)
    config_json = models.JSONField('配置 JSON', default=dict)
    started_at = models.DateTimeField('开始时间', blank=True, null=True)
    finished_at = models.DateTimeField('结束时间', blank=True, null=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, editable=False)

    class Meta(UnmanagedModel.Meta):
        db_table = 'release_build'
        ordering = ['-created_at', '-id']
        unique_together = (('release', 'build_key'),)


class MediaAsset(TimestampedModel):
    site = models.ForeignKey(Site, verbose_name='站点', on_delete=models.CASCADE, related_name='media_assets')
    asset_type = models.CharField('资产类型', max_length=32)
    original_name = models.TextField('原始文件名')
    title = models.TextField('标题', blank=True)
    alt_text = models.TextField('替代文本', blank=True)
    caption = models.TextField('说明', blank=True)
    mime_type = models.TextField('MIME 类型', blank=True)
    file_ext = models.CharField('扩展名', max_length=16)
    file_size_bytes = models.BigIntegerField('文件大小')
    sha256 = models.CharField('SHA-256', max_length=64)
    storage_path = models.TextField('存储路径')
    public_path = models.TextField('公开路径')
    status = models.CharField('状态', max_length=32, default='active')
    created_by = models.TextField('创建者', default='system')
    config_json = models.JSONField('配置 JSON', default=dict)

    class Meta(UnmanagedModel.Meta):
        db_table = 'media_asset'
        ordering = ['-updated_at', '-id']
        unique_together = (('site', 'sha256'), ('site', 'public_path'))

    def __str__(self) -> str:
        return self.title or self.original_name


class MediaAssetBinding(TimestampedModel):
    asset = models.ForeignKey(MediaAsset, on_delete=models.CASCADE, related_name='bindings')
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name='media_asset_bindings')
    locale = models.ForeignKey(SiteLocale, on_delete=models.CASCADE, blank=True, null=True)
    entity_type = models.CharField(max_length=32)
    entity_id = models.BigIntegerField(blank=True, null=True)
    role = models.CharField(max_length=80)
    sort_order = models.IntegerField(default=100)
    enabled = models.BooleanField(default=True)
    config_json = models.JSONField(default=dict)

    class Meta(UnmanagedModel.Meta):
        db_table = 'media_asset_binding'


class ThreeDViewProfile(TimestampedModel):
    site = models.ForeignKey(Site, verbose_name='站点', on_delete=models.CASCADE, related_name='three_d_profiles')
    asset = models.ForeignKey(MediaAsset, verbose_name='3D 资产', on_delete=models.PROTECT, related_name='three_d_profiles')
    poster_asset = models.ForeignKey(
        MediaAsset,
        verbose_name='海报资产',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='three_d_poster_profiles',
    )
    code = models.CharField('代码', max_length=80)
    name = models.TextField('名称')
    purpose = models.TextField('用途', default='general')
    status = models.CharField('状态', max_length=32, default='active')
    background_color = models.CharField('背景色', max_length=7, default='#f3f5f8')
    camera_position_x = models.DecimalField(max_digits=12, decimal_places=4, default=8)
    camera_position_y = models.DecimalField(max_digits=12, decimal_places=4, default=5)
    camera_position_z = models.DecimalField(max_digits=12, decimal_places=4, default=-8)
    camera_target_x = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    camera_target_y = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    camera_target_z = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    fov = models.DecimalField(max_digits=8, decimal_places=3, default=60)
    show_ui = models.BooleanField(default=False)
    allow_interaction = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    config_json = models.JSONField(default=dict)

    class Meta(UnmanagedModel.Meta):
        db_table = 'three_d_view_profile'
        ordering = ['name', 'id']
        unique_together = (('site', 'code'),)

    def __str__(self) -> str:
        return self.name


class ThreeDPlacement(TimestampedModel):
    site = models.ForeignKey(Site, verbose_name='站点', on_delete=models.CASCADE, related_name='three_d_placements')
    slot_code = models.CharField('槽位代码', max_length=80)
    name = models.TextField('名称')
    profile = models.ForeignKey(ThreeDViewProfile, verbose_name='展示配置', on_delete=models.PROTECT, related_name='placements')
    enabled = models.BooleanField('启用', default=True)
    sort_order = models.IntegerField('排序', default=100)
    notes = models.TextField('备注', blank=True)
    config_json = models.JSONField('配置 JSON', default=dict)

    class Meta(UnmanagedModel.Meta):
        db_table = 'three_d_placement'
        ordering = ['sort_order', 'id']
        unique_together = (('site', 'slot_code'),)

    def __str__(self) -> str:
        return self.name
