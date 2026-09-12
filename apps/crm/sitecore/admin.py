from django.contrib import admin

from .models import (
    Article,
    ArticleTag,
    Category,
    ComponentBinding,
    ComponentDefinition,
    Cta,
    CtaBinding,
    FreePage,
    MediaAsset,
    MediaAssetBinding,
    NavigationItem,
    NavigationMenu,
    PageRoute,
    Release,
    ReleaseBuild,
    Site,
    SiteLocale,
    Tag,
)


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'default_locale', 'enabled')
    list_filter = ('enabled',)
    search_fields = ('code', 'name')


@admin.register(SiteLocale)
class SiteLocaleAdmin(admin.ModelAdmin):
    list_display = ('site', 'locale_code', 'label', 'is_default', 'enabled', 'sort_order')
    list_filter = ('site', 'enabled', 'is_default')
    search_fields = ('locale_code', 'label')


@admin.register(PageRoute)
class PageRouteAdmin(admin.ModelAdmin):
    list_display = ('path', 'site', 'locale', 'route_type', 'status', 'published_at')
    list_filter = ('site', 'locale', 'route_type', 'status')
    search_fields = ('path', 'page_title', 'meta_description')
    raw_id_fields = ('site', 'locale')


@admin.register(FreePage)
class FreePageAdmin(admin.ModelAdmin):
    list_display = ('route', 'template_key', 'title')
    list_filter = ('template_key',)
    search_fields = ('route__path', 'title', 'summary')
    raw_id_fields = ('route',)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'slug', 'parent', 'site', 'locale', 'status', 'sort_order')
    list_filter = ('site', 'locale', 'status', 'parent')
    search_fields = ('code', 'name', 'slug')
    raw_id_fields = ('site', 'locale', 'parent')


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ('title', 'route', 'category', 'status', 'published_at', 'reading_minutes')
    list_filter = ('status', 'category')
    search_fields = ('title', 'slug', 'excerpt', 'body_markdown')
    raw_id_fields = ('route', 'category')


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'slug', 'site', 'locale')
    list_filter = ('site', 'locale')
    search_fields = ('code', 'name', 'slug')
    raw_id_fields = ('site', 'locale')


@admin.register(ArticleTag)
class ArticleTagAdmin(admin.ModelAdmin):
    list_display = ('article', 'tag')
    raw_id_fields = ('article', 'tag')


@admin.register(NavigationMenu)
class NavigationMenuAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'region', 'site', 'locale', 'enabled', 'sort_order')
    list_filter = ('region', 'site', 'locale', 'enabled')
    search_fields = ('code', 'name')
    raw_id_fields = ('site', 'locale')


@admin.register(NavigationItem)
class NavigationItemAdmin(admin.ModelAdmin):
    list_display = ('label', 'menu', 'parent', 'route', 'href', 'enabled', 'sort_order')
    list_filter = ('menu__region', 'enabled')
    search_fields = ('label', 'href', 'route__path')
    raw_id_fields = ('menu', 'parent', 'route')


@admin.register(ComponentDefinition)
class ComponentDefinitionAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'component_type', 'enabled')
    list_filter = ('component_type', 'enabled')
    search_fields = ('code', 'name', 'description')


@admin.register(ComponentBinding)
class ComponentBindingAdmin(admin.ModelAdmin):
    list_display = ('component', 'site', 'locale', 'scope_type', 'scope_value', 'region', 'priority', 'enabled')
    list_filter = ('scope_type', 'region', 'enabled')
    search_fields = ('component__code', 'scope_value')
    raw_id_fields = ('component', 'site', 'locale')


@admin.register(Cta)
class CtaAdmin(admin.ModelAdmin):
    list_display = ('code', 'label', 'target_type', 'business_goal', 'event', 'enabled')
    list_filter = ('target_type', 'business_goal', 'enabled')
    search_fields = ('code', 'name', 'label', 'target_url')
    raw_id_fields = ('site', 'event')


@admin.register(CtaBinding)
class CtaBindingAdmin(admin.ModelAdmin):
    list_display = ('cta', 'site', 'locale', 'scope_type', 'scope_value', 'position', 'priority', 'enabled')
    list_filter = ('scope_type', 'position', 'enabled')
    search_fields = ('cta__code', 'scope_value')
    raw_id_fields = ('cta', 'site', 'locale')


@admin.register(MediaAsset)
class MediaAssetAdmin(admin.ModelAdmin):
    list_display = ('title', 'asset_type', 'file_ext', 'file_size_bytes', 'status', 'site', 'updated_at')
    list_filter = ('site', 'asset_type', 'status', 'file_ext')
    search_fields = ('title', 'original_name', 'public_path', 'sha256')
    raw_id_fields = ('site',)
    readonly_fields = ('sha256', 'storage_path', 'public_path', 'file_size_bytes', 'created_at', 'updated_at')


@admin.register(MediaAssetBinding)
class MediaAssetBindingAdmin(admin.ModelAdmin):
    list_display = ('asset', 'site', 'locale', 'entity_type', 'entity_id', 'role', 'enabled', 'sort_order')
    list_filter = ('site', 'locale', 'entity_type', 'role', 'enabled')
    search_fields = ('asset__title', 'asset__original_name', 'role')
    raw_id_fields = ('asset', 'site', 'locale')


@admin.register(Release)
class ReleaseAdmin(admin.ModelAdmin):
    list_display = ('release_key', 'site', 'status', 'created_by', 'created_at', 'exported_at', 'built_at', 'published_at')
    list_filter = ('site', 'status')
    search_fields = ('release_key', 'notes')
    raw_id_fields = ('site',)


@admin.register(ReleaseBuild)
class ReleaseBuildAdmin(admin.ModelAdmin):
    list_display = ('build_key', 'release', 'status', 'artifact_path', 'started_at', 'finished_at')
    list_filter = ('status',)
    search_fields = ('build_key', 'artifact_path', 'log_excerpt')
    raw_id_fields = ('release',)
