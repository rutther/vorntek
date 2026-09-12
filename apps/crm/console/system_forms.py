from __future__ import annotations

import re

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.db.models import Q

from leads.models import SalesTeam, SalesTeamMember
from sitecore.models import Site

from .access import (
    GROUP_BY_ROLE,
    PRIMARY_ROLE_PRIORITY,
    ROLE_LABELS,
    ROLE_SALES,
    ROLE_SALES_MANAGER,
    ROLE_SYSTEM_ADMIN,
    user_role_keys,
)
from .system_versions import sales_team_version_token, system_user_version_token


User = get_user_model()
SALES_ROLES = {ROLE_SALES, ROLE_SALES_MANAGER}
ROLE_CHOICES = tuple((role, ROLE_LABELS[role]) for role in GROUP_BY_ROLE)
TEAM_CODE_RE = re.compile(r'^[a-z][a-z0-9_]*$')


class SalesTeamChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, team):
        status = '' if team.enabled else '（已停用）'
        return f'{team.site.name} / {team.name}{status}'


class ConsoleUserForm(forms.ModelForm):
    version = forms.CharField(required=False, max_length=256, widget=forms.HiddenInput)
    role = forms.ChoiceField(label='主角色', choices=ROLE_CHOICES)
    teams = SalesTeamChoiceField(
        label='销售团队',
        queryset=SalesTeam.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    new_password = forms.CharField(
        label='设置新密码',
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )
    confirm_password = forms.CharField(
        label='确认新密码',
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )

    class Meta:
        model = User
        fields = ('username', 'first_name', 'last_name', 'email', 'is_active')
        labels = {
            'username': '登录名',
            'first_name': '名字',
            'last_name': '姓氏',
            'email': '工作邮箱',
            'is_active': '账号状态',
        }
        widgets = {
            'username': forms.TextInput(attrs={'autocomplete': 'username'}),
            'first_name': forms.TextInput(attrs={'autocomplete': 'given-name'}),
            'last_name': forms.TextInput(attrs={'autocomplete': 'family-name'}),
            'email': forms.EmailInput(attrs={'autocomplete': 'email'}),
        }

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        self.is_create = not bool(self.instance and self.instance.pk)
        self.original_role_keys = user_role_keys(self.instance) if not self.is_create else set()

        team_filter = Q(enabled=True)
        if not self.is_create:
            team_filter |= Q(memberships__user=self.instance)
        self.fields['teams'].queryset = (
            SalesTeam.objects.select_related('site')
            .filter(team_filter)
            .distinct()
            .order_by('site__name', 'name', 'id')
        )
        self.fields['username'].help_text = '登录名创建后不可修改。'
        self.fields['new_password'].help_text = (
            '新账号必须设置初始密码。编辑时留空保持当前密码；重置后旧密码失效，'
            '该账号已登录的会话在下次请求时需要重新登录。'
        )
        self.fields['teams'].help_text = '销售与销售经理至少选择一个团队。'

        if self.is_create:
            self.fields['new_password'].required = True
            self.fields['role'].initial = ROLE_SALES
            self.fields['is_active'].initial = True
        else:
            self.fields['username'].disabled = True
            self.fields['version'].initial = system_user_version_token(self.instance)
            role = self._primary_role_key(self.instance)
            self.fields['role'].initial = role
            self.fields['teams'].initial = list(
                SalesTeamMember.objects.filter(user=self.instance).values_list('team_id', flat=True)
            )
            if self.instance.is_superuser:
                self.fields['role'].disabled = True
                self.fields['is_active'].disabled = True

    @staticmethod
    def _primary_role_key(user) -> str:
        if user.is_superuser:
            return ROLE_SYSTEM_ADMIN
        roles = user_role_keys(user)
        for role in PRIMARY_ROLE_PRIORITY:
            if role in roles:
                return role
        return ROLE_SALES

    @staticmethod
    def _active_system_admins():
        return User.objects.filter(is_active=True).filter(
            Q(is_superuser=True) | Q(groups__name=GROUP_BY_ROLE[ROLE_SYSTEM_ADMIN])
        ).distinct()

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get('role')
        teams = cleaned.get('teams')
        new_password = cleaned.get('new_password') or ''
        confirm_password = cleaned.get('confirm_password') or ''
        is_active = bool(cleaned.get('is_active'))

        if role in SALES_ROLES and teams is not None and not teams.exists():
            self.add_error('teams', '销售与销售经理必须至少属于一个销售团队。')
        if role not in SALES_ROLES and teams is not None and teams.exists():
            self.add_error('teams', '只有销售与销售经理可以选择销售团队，其他角色请取消团队选择。')

        if new_password != confirm_password:
            self.add_error('confirm_password', '两次输入的密码不一致。')
        elif new_password:
            candidate = self.instance
            candidate.username = cleaned.get('username') or candidate.username
            candidate.email = cleaned.get('email') or ''
            candidate.first_name = cleaned.get('first_name') or ''
            candidate.last_name = cleaned.get('last_name') or ''
            try:
                validate_password(new_password, user=candidate)
            except forms.ValidationError as exc:
                self.add_error('new_password', exc)

        if self.is_create:
            return cleaned

        is_self = self.actor.pk == self.instance.pk
        was_system_admin = self.instance.is_superuser or ROLE_SYSTEM_ADMIN in self.original_role_keys
        will_be_system_admin = self.instance.is_superuser or role == ROLE_SYSTEM_ADMIN

        if is_self and not is_active:
            self.add_error('is_active', '不能停用当前正在使用的账号。')
        if is_self and was_system_admin and not will_be_system_admin:
            self.add_error('role', '不能解除当前账号自己的系统管理员角色。')

        removes_active_admin = (
            was_system_admin
            and self.instance.is_active
            and (not is_active or not will_be_system_admin)
        )
        if removes_active_admin and not self._active_system_admins().exclude(pk=self.instance.pk).exists():
            self.add_error(None, '系统至少需要保留一个启用的系统管理员账号。')
        return cleaned

    @transaction.atomic
    def save(self, commit=True):
        if not commit:
            raise ValueError('ConsoleUserForm only supports atomic commit=True saves.')
        user = super().save(commit=False)
        password = self.cleaned_data.get('new_password') or ''
        if password:
            user.set_password(password)
        user.save()

        role = ROLE_SYSTEM_ADMIN if user.is_superuser else self.cleaned_data['role']
        console_groups = {
            role_key: Group.objects.get_or_create(name=group_name)[0]
            for role_key, group_name in GROUP_BY_ROLE.items()
        }
        user.groups.remove(*console_groups.values())
        user.groups.add(console_groups[role])

        SalesTeamMember.objects.filter(user=user).delete()
        if role in SALES_ROLES:
            membership_role = 'manager' if role == ROLE_SALES_MANAGER else 'member'
            SalesTeamMember.objects.bulk_create([
                SalesTeamMember(team=team, user=user, membership_role=membership_role)
                for team in self.cleaned_data['teams']
            ])
        self.instance = user
        return user


class SalesTeamForm(forms.ModelForm):
    version = forms.CharField(required=False, max_length=256, widget=forms.HiddenInput)

    class Meta:
        model = SalesTeam
        fields = ('site', 'code', 'name', 'enabled')
        labels = {
            'site': '所属站点',
            'code': '团队代码',
            'name': '团队名称',
            'enabled': '团队状态',
        }
        widgets = {
            'code': forms.TextInput(attrs={'autocomplete': 'off'}),
            'name': forms.TextInput(attrs={'autocomplete': 'organization'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        site_filter = Q(enabled=True)
        if self.instance and self.instance.pk:
            site_filter |= Q(pk=self.instance.site_id)
        self.fields['site'].queryset = Site.objects.filter(site_filter).distinct().order_by('name')
        self.fields['code'].help_text = '小写字母开头，仅使用小写字母、数字和下划线。创建后不可修改。'
        if self.instance and self.instance.pk:
            self.fields['site'].disabled = True
            self.fields['code'].disabled = True
            self.fields['version'].initial = sales_team_version_token(self.instance)

    def clean_code(self):
        code = (self.cleaned_data.get('code') or '').strip().lower()
        if not TEAM_CODE_RE.fullmatch(code):
            raise forms.ValidationError('团队代码格式不正确。')
        return code

    def clean(self):
        cleaned = super().clean()
        site = cleaned.get('site')
        code = cleaned.get('code')
        if site and code:
            duplicate = SalesTeam.objects.filter(site=site, code=code)
            if self.instance and self.instance.pk:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                self.add_error('code', '该站点已经存在相同的团队代码。')
        return cleaned
