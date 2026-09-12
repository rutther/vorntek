from __future__ import annotations

from dataclasses import dataclass

from django.core.management import call_command
from django.db import transaction

from sitecore.models import Site, SiteLocale


@dataclass(frozen=True)
class LocaleDefinition:
    code: str
    operator_label: str
    native_label: str
    direction: str


SUPPORTED_LOCALES: tuple[LocaleDefinition, ...] = (
    LocaleDefinition('en', '英语', 'English', 'ltr'),
    LocaleDefinition('ar', '阿拉伯语', 'العربية', 'rtl'),
    LocaleDefinition('fr', '法语', 'Français', 'ltr'),
    LocaleDefinition('es', '西班牙语', 'Español', 'ltr'),
    LocaleDefinition('ru', '俄语', 'Русский', 'ltr'),
)


def locale_definition_map() -> dict[str, LocaleDefinition]:
    return {item.code: item for item in SUPPORTED_LOCALES}


def all_site_locales(site: Site) -> list[SiteLocale]:
    return list(SiteLocale.objects.filter(site=site).order_by('sort_order', 'locale_code'))


def available_locale_definitions(site: Site) -> list[LocaleDefinition]:
    existing = {item.locale_code for item in all_site_locales(site)}
    return [item for item in SUPPORTED_LOCALES if item.code not in existing]


def create_locale_from_source(*, site: Site, source_locale: SiteLocale, target_code: str) -> SiteLocale:
    definition = locale_definition_map()[target_code]
    call_command(
        'bootstrap_locale_content',
        site_code=site.code,
        source_locale=source_locale.locale_code,
        target_locale=definition.code,
        target_label=definition.native_label,
        direction=definition.direction,
    )
    return SiteLocale.objects.get(site=site, locale_code=definition.code)


@transaction.atomic
def set_default_locale(*, site: Site, locale: SiteLocale) -> None:
    locked_site = Site.objects.select_for_update().get(pk=site.pk)
    locked_locales = list(
        SiteLocale.objects.select_for_update()
        .filter(site=locked_site)
        .order_by('id')
    )
    target = next((item for item in locked_locales if item.pk == locale.pk), None)
    if target is None:
        raise ValueError('目标语言不属于当前站点。')

    SiteLocale.objects.filter(site=locked_site, is_default=True).exclude(pk=target.pk).update(is_default=False)
    target.enabled = True
    target.is_default = True
    target.save(update_fields=['enabled', 'is_default', 'updated_at'])
    locked_site.default_locale = target.locale_code
    locked_site.save(update_fields=['default_locale', 'updated_at'])

    site.default_locale = locked_site.default_locale
    locale.enabled = True
    locale.is_default = True


@transaction.atomic
def enable_locale(*, locale: SiteLocale) -> None:
    target = SiteLocale.objects.select_for_update().get(pk=locale.pk)
    if target.enabled:
        return
    target.enabled = True
    target.save(update_fields=['enabled', 'updated_at'])
    locale.enabled = True


@transaction.atomic
def disable_locale(*, site: Site, locale: SiteLocale) -> None:
    locked_site = Site.objects.select_for_update().get(pk=site.pk)
    locked_locales = list(
        SiteLocale.objects.select_for_update()
        .filter(site=locked_site)
        .order_by('id')
    )
    target = next((item for item in locked_locales if item.pk == locale.pk), None)
    if target is None:
        raise ValueError('目标语言不属于当前站点。')
    enabled_locales = sum(1 for item in locked_locales if item.enabled)
    if target.is_default:
        raise ValueError('默认语言不能直接停用，请先切换默认语言。')
    if enabled_locales <= 1:
        raise ValueError('至少保留一个启用语言。')
    if not target.enabled:
        return
    target.enabled = False
    target.save(update_fields=['enabled', 'updated_at'])
    locale.enabled = False
