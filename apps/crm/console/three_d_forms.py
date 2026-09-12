from __future__ import annotations

import re
from decimal import Decimal

from django import forms
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from sitecore.models import MediaAsset, Site, ThreeDPlacement, ThreeDViewProfile

from .three_d_config import THREE_D_SLOT_CHOICES


HEX_COLOR_RE = re.compile(r'^#[A-Fa-f0-9]{6}$')


class ThreeDViewProfileForm(forms.Form):
    code = forms.CharField(label='配置代码', max_length=80, required=False)
    name = forms.CharField(label='配置名称', max_length=180)
    asset = forms.ModelChoiceField(label='3D 资产', queryset=MediaAsset.objects.none())
    purpose = forms.CharField(label='用途标记', max_length=64, required=False)
    status = forms.ChoiceField(
        label='状态',
        choices=(('active', '启用'), ('disabled', '停用'), ('archived', '归档')),
    )
    background_color = forms.CharField(label='背景色', max_length=7, help_text='例如 #0d1117 或 #f3f5f8')
    camera_position_x = forms.DecimalField(label='相机 X', max_digits=12, decimal_places=4)
    camera_position_y = forms.DecimalField(label='相机 Y', max_digits=12, decimal_places=4)
    camera_position_z = forms.DecimalField(label='相机 Z', max_digits=12, decimal_places=4)
    camera_target_x = forms.DecimalField(label='目标 X', max_digits=12, decimal_places=4)
    camera_target_y = forms.DecimalField(label='目标 Y', max_digits=12, decimal_places=4)
    camera_target_z = forms.DecimalField(label='目标 Z', max_digits=12, decimal_places=4)
    fov = forms.DecimalField(label='视场角', max_digits=8, decimal_places=3)
    show_ui = forms.BooleanField(label='显示交互 UI', required=False)
    allow_interaction = forms.BooleanField(label='允许用户拖拽缩放', required=False)
    auto_rotate = forms.BooleanField(label='自动旋转', required=False)
    auto_rotate_duration = forms.DecimalField(label='旋转周期（秒）', max_digits=8, decimal_places=2, required=False)
    notes = forms.CharField(label='说明', required=False, widget=forms.Textarea(attrs={'rows': 4}))

    def __init__(
        self,
        *args,
        site: Site,
        profile: ThreeDViewProfile | None = None,
        initial_asset_id: int | None = None,
        **kwargs,
    ):
        self.site = site
        self.profile = profile
        super().__init__(*args, **kwargs)
        self.fields['asset'].queryset = (
            MediaAsset.objects.filter(site=site, asset_type='model3d', status='active')
            .order_by('-updated_at', '-id')
        )

        if profile and not self.is_bound:
            config = profile.config_json or {}
            self.initial.update(
                {
                    'code': profile.code,
                    'name': profile.name,
                    'asset': profile.asset_id,
                    'purpose': profile.purpose,
                    'status': profile.status,
                    'background_color': profile.background_color,
                    'camera_position_x': profile.camera_position_x,
                    'camera_position_y': profile.camera_position_y,
                    'camera_position_z': profile.camera_position_z,
                    'camera_target_x': profile.camera_target_x,
                    'camera_target_y': profile.camera_target_y,
                    'camera_target_z': profile.camera_target_z,
                    'fov': profile.fov,
                    'show_ui': profile.show_ui,
                    'allow_interaction': profile.allow_interaction,
                    'auto_rotate': bool(config.get('autoRotate', True)),
                    'auto_rotate_duration': config.get('autoRotateDuration', 20),
                    'notes': profile.notes,
                }
            )
        elif initial_asset_id and not self.is_bound:
            asset = self.fields['asset'].queryset.filter(id=initial_asset_id).first()
            if asset:
                self.initial.setdefault('asset', asset.id)
                self.initial.setdefault('name', asset.title or asset.original_name)
                self.initial.setdefault('purpose', 'general')
                self.initial.setdefault('status', 'active')
                self.initial.setdefault('background_color', '#f3f5f8')
                self.initial.setdefault('camera_position_x', Decimal('8'))
                self.initial.setdefault('camera_position_y', Decimal('5'))
                self.initial.setdefault('camera_position_z', Decimal('-8'))
                self.initial.setdefault('camera_target_x', Decimal('0'))
                self.initial.setdefault('camera_target_y', Decimal('0'))
                self.initial.setdefault('camera_target_z', Decimal('0'))
                self.initial.setdefault('fov', Decimal('60'))
                self.initial.setdefault('allow_interaction', True)
                self.initial.setdefault('auto_rotate', True)
                self.initial.setdefault('auto_rotate_duration', Decimal('20'))

    def clean_code(self):
        value = (self.cleaned_data.get('code') or '').strip().lower()
        if not value:
            name = (self.cleaned_data.get('name') or '').strip()
            value = slugify(name, allow_unicode=False).replace('-', '_')
        value = re.sub(r'[^a-z0-9_]+', '_', value).strip('_')
        if not value:
            raise forms.ValidationError('请填写可读的配置名称，系统才能生成配置代码。')
        if not re.match(r'^[a-z][a-z0-9_]*$', value):
            raise forms.ValidationError('配置代码只能使用小写英文、数字和下划线，且必须以字母开头。')
        query = ThreeDViewProfile.objects.filter(site=self.site, code=value)
        if self.profile:
            query = query.exclude(id=self.profile.id)
        if query.exists():
            raise forms.ValidationError('该配置代码已存在，请换一个。')
        return value

    def clean_background_color(self):
        value = (self.cleaned_data.get('background_color') or '').strip()
        if not HEX_COLOR_RE.match(value):
            raise forms.ValidationError('背景色必须是 #RRGGBB 格式。')
        return value.lower()

    def clean_fov(self):
        value = self.cleaned_data['fov']
        if value < 10 or value > 120:
            raise forms.ValidationError('视场角建议控制在 10 到 120 之间。')
        return value

    def clean_auto_rotate_duration(self):
        value = self.cleaned_data.get('auto_rotate_duration')
        if value is None:
            return Decimal('20')
        if value < 5 or value > 120:
            raise forms.ValidationError('旋转周期建议控制在 5 到 120 秒之间。')
        return value

    @transaction.atomic
    def save(self) -> ThreeDViewProfile:
        payload = {
            'site': self.site,
            'asset': self.cleaned_data['asset'],
            'code': self.cleaned_data['code'],
            'name': self.cleaned_data['name'].strip(),
            'purpose': (self.cleaned_data.get('purpose') or 'general').strip() or 'general',
            'status': self.cleaned_data['status'],
            'background_color': self.cleaned_data['background_color'],
            'camera_position_x': self.cleaned_data['camera_position_x'],
            'camera_position_y': self.cleaned_data['camera_position_y'],
            'camera_position_z': self.cleaned_data['camera_position_z'],
            'camera_target_x': self.cleaned_data['camera_target_x'],
            'camera_target_y': self.cleaned_data['camera_target_y'],
            'camera_target_z': self.cleaned_data['camera_target_z'],
            'fov': self.cleaned_data['fov'],
            'show_ui': bool(self.cleaned_data.get('show_ui')),
            'allow_interaction': bool(self.cleaned_data.get('allow_interaction')),
            'notes': (self.cleaned_data.get('notes') or '').strip(),
            'updated_at': timezone.now(),
        }
        config_json = {
            **((self.profile.config_json if self.profile else {}) or {}),
            'autoRotate': bool(self.cleaned_data.get('auto_rotate')),
            'autoRotateDuration': float(self.cleaned_data.get('auto_rotate_duration') or Decimal('20')),
        }
        if self.profile is None:
            profile = ThreeDViewProfile.objects.create(**payload, config_json=config_json)
        else:
            for key, value in payload.items():
                setattr(self.profile, key, value)
            self.profile.config_json = config_json
            self.profile.save()
            profile = self.profile
        return profile


class ThreeDPlacementForm(forms.Form):
    slot_code = forms.ChoiceField(label='槽位代码', choices=THREE_D_SLOT_CHOICES)
    name = forms.CharField(label='槽位名称', max_length=180)
    profile = forms.ModelChoiceField(label='展示配置', queryset=ThreeDViewProfile.objects.none())
    enabled = forms.BooleanField(label='启用槽位', required=False)
    notes = forms.CharField(label='说明', required=False, widget=forms.Textarea(attrs={'rows': 4}))

    def __init__(self, *args, site: Site, placement: ThreeDPlacement | None = None, **kwargs):
        self.site = site
        self.placement = placement
        super().__init__(*args, **kwargs)
        self.fields['profile'].queryset = (
            ThreeDViewProfile.objects.filter(site=site)
            .exclude(status='archived')
            .order_by('name')
        )
        if placement and not self.is_bound:
            self.initial.update(
                {
                    'slot_code': placement.slot_code,
                    'name': placement.name,
                    'profile': placement.profile_id,
                    'enabled': placement.enabled,
                    'notes': placement.notes,
                }
            )

    def clean_slot_code(self):
        value = (self.cleaned_data.get('slot_code') or '').strip()
        query = ThreeDPlacement.objects.filter(site=self.site, slot_code=value)
        if self.placement:
            query = query.exclude(id=self.placement.id)
        if query.exists():
            raise forms.ValidationError('该槽位代码已存在。')
        return value

    @transaction.atomic
    def save(self) -> ThreeDPlacement:
        payload = {
            'site': self.site,
            'slot_code': self.cleaned_data['slot_code'],
            'name': self.cleaned_data['name'].strip(),
            'profile': self.cleaned_data['profile'],
            'enabled': bool(self.cleaned_data.get('enabled')),
            'notes': (self.cleaned_data.get('notes') or '').strip(),
            'updated_at': timezone.now(),
        }
        if self.placement is None:
            placement = ThreeDPlacement.objects.create(sort_order=100, **payload)
        else:
            for key, value in payload.items():
                setattr(self.placement, key, value)
            self.placement.save()
            placement = self.placement
        return placement
