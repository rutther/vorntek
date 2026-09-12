from __future__ import annotations

from django import forms
from django.utils.text import slugify

from sitecore.models import Category, Site, SiteLocale

from .taxonomy import category_depth_map, category_label_map, descendant_ids, ordered_categories


CATEGORY_STATUS_CHOICES = [
    ('draft', '草稿'),
    ('published', '已发布'),
    ('archived', '已归档'),
]


class CategoryEditorForm(forms.Form):
    name = forms.CharField(
        label='分类名称',
        max_length=160,
        widget=forms.TextInput(attrs={'placeholder': '例如：公司新闻'}),
    )
    code = forms.CharField(
        label='分类代码',
        max_length=64,
        help_text='程序员和系统依赖这个稳定代码。建议使用小写英文和下划线。',
        widget=forms.TextInput(attrs={'placeholder': 'company_news'}),
    )
    slug = forms.CharField(
        label='URL 别名',
        max_length=160,
        required=False,
        help_text='用于文章列表筛选和前台链接。建议使用小写英文、数字和短横线。',
        widget=forms.TextInput(attrs={'placeholder': 'company-news'}),
    )
    parent = forms.ModelChoiceField(
        label='上级分类',
        queryset=Category.objects.none(),
        required=False,
        empty_label='设为一级分类',
    )
    status = forms.ChoiceField(label='状态', choices=CATEGORY_STATUS_CHOICES, initial='published')
    sort_order = forms.IntegerField(label='排序', min_value=0, initial=100)
    description = forms.CharField(
        label='分类说明',
        required=False,
        widget=forms.Textarea(attrs={'rows': 3, 'placeholder': '给运营和程序员看的简短说明。'}),
    )
    seo_title = forms.CharField(label='SEO 标题', required=False, max_length=220)
    seo_description = forms.CharField(
        label='SEO 描述',
        required=False,
        widget=forms.Textarea(attrs={'rows': 2}),
    )

    def __init__(
        self,
        *args,
        site: Site,
        locale: SiteLocale,
        category: Category | None = None,
        allow_publish: bool = False,
        **kwargs,
    ):
        self.site = site
        self.locale = locale
        self.category = category
        self.allow_publish = bool(allow_publish)

        ordered = ordered_categories(site, locale)
        self.depth_map = category_depth_map(ordered)
        labels = category_label_map(ordered)

        if category and not kwargs.get('initial'):
            seo_json = category.seo_json or {}
            kwargs['initial'] = {
                'name': category.name,
                'code': category.code,
                'slug': category.slug,
                'parent': category.parent_id,
                'status': category.status,
                'sort_order': category.sort_order,
                'description': category.description,
                'seo_title': seo_json.get('title', ''),
                'seo_description': seo_json.get('description', ''),
            }

        super().__init__(*args, **kwargs)
        if not self.allow_publish:
            self.fields['status'].choices = [
                choice for choice in CATEGORY_STATUS_CHOICES if choice[0] != 'published'
            ]
        if not category and not self.allow_publish and not self.is_bound:
            self.fields['status'].initial = 'draft'

        available = [row for row in ordered if self.depth_map.get(row.id, 0) < 2]
        if category:
            blocked = {category.id, *descendant_ids(ordered, category.id)}
            available = [row for row in available if row.id not in blocked]

        self.fields['parent'].queryset = Category.objects.filter(id__in=[row.id for row in available]).order_by(
            'sort_order', 'name', 'id'
        )
        self.fields['parent'].label_from_instance = lambda obj: labels.get(obj.id, obj.name)
        self.fields['parent'].help_text = '最多只支持三级分类。这里只能选择一级或二级分类作为上级。'

    def clean_code(self) -> str:
        value = (self.cleaned_data.get('code') or '').strip().lower().replace('-', '_')
        if not value:
            raise forms.ValidationError('请填写分类代码。')
        if not value[0].isalpha():
            raise forms.ValidationError('分类代码必须以字母开头。')
        normalized = ''.join(ch for ch in value if ch.isalnum() or ch == '_')
        if normalized != value:
            raise forms.ValidationError('分类代码只允许小写英文、数字和下划线。')

        query = Category.objects.filter(site=self.site, locale=self.locale, code=normalized)
        if self.category:
            query = query.exclude(id=self.category.id)
        if query.exists():
            raise forms.ValidationError('这个分类代码已经存在。')
        return normalized

    def clean_slug(self) -> str:
        raw = (self.cleaned_data.get('slug') or self.cleaned_data.get('name') or '').strip()
        slug = slugify(raw, allow_unicode=False)
        if not slug:
            raise forms.ValidationError('请填写可用的 URL 别名。')

        query = Category.objects.filter(site=self.site, locale=self.locale, slug=slug)
        if self.category:
            query = query.exclude(id=self.category.id)
        if query.exists():
            raise forms.ValidationError('这个 URL 别名已经存在。')
        return slug

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('status') == 'published' and not self.allow_publish:
            self.add_error('status', '当前账号不能把分类设为已发布。')
        parent = cleaned.get('parent')
        if parent and parent.site_id != self.site.id:
            self.add_error('parent', '上级分类不属于当前站点。')
        if parent and parent.locale_id != self.locale.id:
            self.add_error('parent', '上级分类语言不一致。')
        if parent and self.depth_map.get(parent.id, 0) >= 2:
            self.add_error('parent', '只支持三级分类。请选一级或二级分类作为上级。')
        return cleaned

    def save(self) -> Category:
        seo_json = {
            'title': (self.cleaned_data.get('seo_title') or '').strip(),
            'description': (self.cleaned_data.get('seo_description') or '').strip(),
        }
        category = self.category or Category(site=self.site, locale=self.locale)
        category.name = self.cleaned_data['name'].strip()
        category.code = self.cleaned_data['code']
        category.slug = self.cleaned_data['slug']
        category.parent = self.cleaned_data.get('parent')
        category.status = self.cleaned_data['status']
        category.sort_order = self.cleaned_data['sort_order']
        category.description = (self.cleaned_data.get('description') or '').strip()
        category.seo_json = {key: value for key, value in seo_json.items() if value}
        category.save()
        return category
