from __future__ import annotations

from django import forms

from sitecore.models import Site, SiteLocale

from .locale_admin import available_locale_definitions


class LocaleBootstrapForm(forms.Form):
    source_locale = forms.ChoiceField(label='内容来源语言')
    target_locale = forms.ChoiceField(label='新增语言')

    def __init__(self, *args, site: Site, current_locale: SiteLocale, **kwargs):
        super().__init__(*args, **kwargs)
        self.site = site
        self.current_locale = current_locale

        source_choices = [
            (item.locale_code, f'{item.label} ({item.locale_code})')
            for item in SiteLocale.objects.filter(site=site, enabled=True).order_by('sort_order', 'locale_code')
        ]
        target_choices = [
            (item.code, f'{item.operator_label} ({item.code})')
            for item in available_locale_definitions(site)
        ]

        self.fields['source_locale'].choices = source_choices
        self.fields['target_locale'].choices = target_choices
        self.fields['source_locale'].initial = current_locale.locale_code

        select_attrs = {'class': 'field-select'}
        self.fields['source_locale'].widget.attrs.update(select_attrs)
        self.fields['target_locale'].widget.attrs.update(select_attrs)

    def clean(self):
        cleaned = super().clean()
        source = cleaned.get('source_locale')
        target = cleaned.get('target_locale')
        if not target:
            raise forms.ValidationError('当前没有可新增的语言。')
        if source == target:
            raise forms.ValidationError('内容来源语言和新增语言不能相同。')
        return cleaned
