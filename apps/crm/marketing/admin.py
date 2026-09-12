from django.contrib import admin

from .models import (
    CanonicalEvent,
    MarketingIntegration,
    MarketingProvider,
    ProviderEventMapping,
    TrackingRule,
)


@admin.register(MarketingProvider)
class MarketingProviderAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'enabled')
    list_filter = ('enabled',)
    search_fields = ('code', 'name')


@admin.register(MarketingIntegration)
class MarketingIntegrationAdmin(admin.ModelAdmin):
    list_display = ('name', 'provider', 'integration_type', 'public_id', 'consent_category', 'enabled')
    list_filter = ('provider', 'integration_type', 'consent_category', 'enabled')
    search_fields = ('name', 'public_id')


@admin.register(CanonicalEvent)
class CanonicalEventAdmin(admin.ModelAdmin):
    list_display = ('code', 'name')
    search_fields = ('code', 'name')


@admin.register(ProviderEventMapping)
class ProviderEventMappingAdmin(admin.ModelAdmin):
    list_display = ('provider', 'canonical_event', 'provider_event_name', 'enabled')
    list_filter = ('provider', 'enabled')
    search_fields = ('provider_event_name', 'canonical_event__code')


@admin.register(TrackingRule)
class TrackingRuleAdmin(admin.ModelAdmin):
    list_display = ('integration', 'scope_type', 'scope_value', 'canonical_event', 'priority', 'enabled')
    list_filter = ('scope_type', 'enabled', 'integration__provider')
    search_fields = ('scope_value', 'integration__name')
