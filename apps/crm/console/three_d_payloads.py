from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.urls import reverse

from sitecore.models import MediaAsset, Site, ThreeDPlacement, ThreeDViewProfile

from .content_access import (
    ASSETS_IMPORT_LOCAL,
    ASSETS_READ,
    ASSETS_WRITE,
    RELEASES_PREVIEW_BUILD,
)
from .payloads import default_site_locale, format_admin_datetime
from .three_d_config import asset_extension, build_splat_viewer_url, purpose_label, slot_label


def _asset_name(asset: MediaAsset) -> str:
    return asset.title or asset.original_name


def _format_size(size: int | None) -> str:
    value = int(size or 0)
    if value >= 1024 * 1024:
        return f'{value / 1024 / 1024:.1f} MB'
    if value >= 1024:
        return f'{value / 1024:.1f} KB'
    return f'{value} B'


def _profile_status_label(status: str) -> str:
    return {
        'active': '启用',
        'disabled': '停用',
        'archived': '归档',
    }.get(status, status or '未知')


def _profile_status_tone(status: str) -> str:
    return {
        'active': 'green',
        'disabled': 'amber',
        'archived': 'slate',
    }.get(status, 'slate')


def _asset_status_label(status: str) -> str:
    return {
        'active': '启用',
        'disabled': '停用',
        'archived': '归档',
    }.get(status, status or '未知')


def _preview_build_guidance(can_build_preview: bool) -> str:
    if can_build_preview:
        return '配置或槽位发生变更后，可生成新的预览产物，再核对前台展示。'
    return '配置或槽位发生变更后，由具备预览构建权限的成员生成新的预览产物。'


def three_d_page_payload(
    *,
    site: Site | None = None,
    capabilities: frozenset[str] = frozenset(),
) -> dict:
    if ASSETS_READ not in capabilities:
        raise PermissionDenied('当前账号没有查看 3D 素材的权限。')
    if site is None:
        site, _locale = default_site_locale()
    model_count = MediaAsset.objects.filter(site=site, asset_type='model3d', status='active').count()
    profile_count = ThreeDViewProfile.objects.filter(site=site).count()
    placement_count = ThreeDPlacement.objects.filter(site=site).count()
    return {
        'sectionKey': 'assets',
        'sectionLabel': '资产',
        'title': '3D 展示管理',
        'description': '管理 3D 文件、展示配置和页面槽位。前台读取发布快照，不直接读取编辑中的数据库状态。',
        'pageType': 'form',
        'workspaceLabel': '3D 展示管理',
        'workspaceMeta': f'3D 资产 {model_count} / 展示配置 {profile_count} / 页面槽位 {placement_count}',
        'searchPlaceholder': '',
        'tabs': [],
        'filters': [],
        'filterGroups': [],
        'actions': [],
        'scopeActions': [],
        'summary': [],
        'table': None,
        'notes': [],
    }


def three_d_asset_page_payload(
    asset: MediaAsset,
    *,
    capabilities: frozenset[str] = frozenset(),
) -> dict:
    if ASSETS_READ not in capabilities:
        raise PermissionDenied('当前账号没有查看 3D 素材的权限。')
    name = _asset_name(asset)
    return {
        'sectionKey': 'assets',
        'sectionLabel': '资产',
        'title': name,
        'description': '查看 3D 源文件、展示配置和页面槽位引用关系。',
        'pageType': 'form',
        'workspaceLabel': name,
        'workspaceMeta': '3D 资产详情',
        'searchPlaceholder': '',
        'tabs': [],
        'filters': [],
        'filterGroups': [],
        'actions': [],
        'scopeActions': [],
        'summary': [],
        'table': None,
        'notes': [],
    }


def three_d_workspace_context(
    *,
    site: Site | None = None,
    capabilities: frozenset[str] = frozenset(),
) -> dict[str, object]:
    if ASSETS_READ not in capabilities:
        raise PermissionDenied('当前账号没有查看 3D 素材的权限。')
    can_write = ASSETS_WRITE in capabilities
    can_import_local = ASSETS_WRITE in capabilities and ASSETS_IMPORT_LOCAL in capabilities
    can_build_preview = RELEASES_PREVIEW_BUILD in capabilities
    if site is None:
        site, _locale = default_site_locale()
    assets = list(
        MediaAsset.objects.filter(site=site, asset_type='model3d', status='active')
        .order_by('-updated_at', '-id')[:80]
    )
    profiles = list(
        ThreeDViewProfile.objects.select_related('asset')
        .filter(site=site)
        .order_by('purpose', 'name')[:200]
    )
    placements = list(
        ThreeDPlacement.objects.select_related('profile', 'profile__asset')
        .filter(site=site)
        .order_by('sort_order', 'slot_code')[:120]
    )

    placements_by_profile: dict[int, list[ThreeDPlacement]] = {}
    profiles_by_asset: dict[int, list[ThreeDViewProfile]] = {}
    for placement in placements:
        placements_by_profile.setdefault(placement.profile_id, []).append(placement)
    for profile in profiles:
        profiles_by_asset.setdefault(profile.asset_id, []).append(profile)

    asset_rows = []
    for asset in assets:
        related_profiles = profiles_by_asset.get(asset.id, [])
        bound_slots = [
            placement
            for profile in related_profiles
            for placement in placements_by_profile.get(profile.id, [])
        ]
        asset_rows.append(
            {
                'id': asset.id,
                'name': _asset_name(asset),
                'ext': (asset.file_ext or asset_extension(asset.public_path)).lower(),
                'size': _format_size(asset.file_size_bytes),
                'publicPath': asset.public_path,
                'updatedAt': format_admin_datetime(asset.updated_at),
                'profileCount': len(related_profiles),
                'slotCount': len(bound_slots),
                'detailUrl': reverse('console:three_d_asset_detail', args=[asset.id]),
                'createProfileUrl': (
                    f"{reverse('console:three_d_profile_create')}?asset_id={asset.id}"
                    if can_write else ''
                ),
            }
        )

    profile_rows = []
    for profile in profiles:
        asset = profile.asset
        ext = asset_extension(asset.public_path)
        viewer_url = build_splat_viewer_url(profile) if ext in {'.sog', '.ply'} else asset.public_path
        linked_slots = placements_by_profile.get(profile.id, [])
        config = profile.config_json or {}
        auto_rotate = bool(config.get('autoRotate', True))
        duration = config.get('autoRotateDuration') or 20
        profile_rows.append(
            {
                'id': profile.id,
                'name': profile.name,
                'code': profile.code,
                'assetName': _asset_name(asset),
                'assetPath': asset.public_path,
                'purpose': profile.purpose or 'general',
                'purposeLabel': purpose_label(profile.purpose),
                'background': profile.background_color,
                'fov': profile.fov,
                'autoRotateLabel': f'自动 / {duration} 秒' if auto_rotate else '固定视角',
                'interactionLabel': '可拖拽' if profile.allow_interaction else '锁定',
                'status': profile.status,
                'statusLabel': _profile_status_label(profile.status),
                'statusTone': _profile_status_tone(profile.status),
                'updatedAt': format_admin_datetime(profile.updated_at),
                'slotLabels': '、'.join(slot_label(item.slot_code) for item in linked_slots) or '未绑定槽位',
                'slotCount': len(linked_slots),
                'editUrl': (
                    reverse('console:three_d_profile_edit', args=[profile.id])
                    if can_write else ''
                ),
                'previewUrl': viewer_url,
            }
        )

    placement_rows = []
    for placement in placements:
        profile = placement.profile
        asset = profile.asset
        placement_rows.append(
            {
                'id': placement.id,
                'name': placement.name,
                'slotCode': placement.slot_code,
                'slotLabel': slot_label(placement.slot_code),
                'profileName': profile.name,
                'profileCode': profile.code,
                'assetName': _asset_name(asset),
                'enabled': placement.enabled,
                'statusLabel': '启用' if placement.enabled else '停用',
                'statusTone': 'green' if placement.enabled else 'amber',
                'updatedAt': format_admin_datetime(placement.updated_at),
                'editUrl': (
                    reverse('console:three_d_placement_edit', args=[placement.id])
                    if can_write else ''
                ),
                'profileEditUrl': (
                    reverse('console:three_d_profile_edit', args=[profile.id])
                    if can_write else ''
                ),
            }
        )

    expected_slots = {'homepage_hero', 'homepage_equipment_preview'}
    active_slot_codes = {row['slotCode'] for row in placement_rows if row['enabled']}
    unbound_slot_count = len(expected_slots - active_slot_codes)

    return {
        'three_d_workspace': {
            'permissions': {
                'canWrite': can_write,
                'canImportLocal': can_import_local,
                'canBuildPreview': can_build_preview,
            },
            'assetCount': len(asset_rows),
            'profileCount': len(profile_rows),
            'placementCount': len(placement_rows),
            'unboundSlotCount': unbound_slot_count,
            'assets': asset_rows,
            'profiles': profile_rows,
            'placements': placement_rows,
            'createProfileUrl': reverse('console:three_d_profile_create') if can_write else '',
            'createPlacementUrl': reverse('console:three_d_placement_create') if can_write else '',
            'buildPreviewUrl': reverse('console:release_build_preview') if can_build_preview else '',
            'assetsUrl': reverse('console:assets'),
            'uploadUrl': reverse('console:asset_upload') if can_write else '',
            'importUrl': reverse('console:asset_import_path') if can_import_local else '',
            'checks': [
                {'label': '3D 资产已入库', 'ok': bool(asset_rows)},
                {'label': '至少一套展示配置', 'ok': bool(profile_rows)},
                {'label': '首页槽位已绑定', 'ok': unbound_slot_count == 0},
            ],
            'previewGuidance': _preview_build_guidance(can_build_preview),
        }
    }


def three_d_asset_detail_context(
    asset: MediaAsset,
    *,
    capabilities: frozenset[str] = frozenset(),
    include_internal_paths: bool = False,
) -> dict[str, object]:
    if ASSETS_READ not in capabilities:
        raise PermissionDenied('当前账号没有查看 3D 素材的权限。')
    can_write = ASSETS_WRITE in capabilities
    can_build_preview = RELEASES_PREVIEW_BUILD in capabilities
    site = asset.site
    profiles = list(
        ThreeDViewProfile.objects.select_related('asset')
        .filter(site=site, asset=asset)
        .order_by('purpose', 'name')
    )
    placements = list(
        ThreeDPlacement.objects.select_related('profile')
        .filter(site=site, profile_id__in=[profile.id for profile in profiles])
        .order_by('sort_order', 'slot_code')
    )
    placements_by_profile: dict[int, list[ThreeDPlacement]] = {}
    for placement in placements:
        placements_by_profile.setdefault(placement.profile_id, []).append(placement)

    profile_rows = []
    placement_rows = []
    for profile in profiles:
        config = profile.config_json or {}
        auto_rotate = bool(config.get('autoRotate', True))
        duration = config.get('autoRotateDuration') or 20
        linked_slots = placements_by_profile.get(profile.id, [])
        profile_rows.append(
            {
                'id': profile.id,
                'name': profile.name,
                'code': profile.code,
                'purpose': profile.purpose or 'general',
                'purposeLabel': purpose_label(profile.purpose),
                'background': profile.background_color,
                'fov': profile.fov,
                'autoRotateLabel': f'自动 / {duration} 秒' if auto_rotate else '固定视角',
                'interactionLabel': '可拖拽' if profile.allow_interaction else '锁定',
                'statusLabel': _profile_status_label(profile.status),
                'statusTone': _profile_status_tone(profile.status),
                'slotLabels': '、'.join(slot_label(item.slot_code) for item in linked_slots) or '未绑定槽位',
                'editUrl': (
                    reverse('console:three_d_profile_edit', args=[profile.id])
                    if can_write else ''
                ),
            }
        )
        for placement in linked_slots:
            placement_rows.append(
                {
                    'id': placement.id,
                    'slotCode': placement.slot_code,
                    'slotLabel': slot_label(placement.slot_code),
                    'name': placement.name,
                    'profileName': profile.name,
                    'enabled': placement.enabled,
                    'statusLabel': '启用' if placement.enabled else '停用',
                    'statusTone': 'green' if placement.enabled else 'amber',
                    'editUrl': (
                        reverse('console:three_d_placement_edit', args=[placement.id])
                        if can_write else ''
                    ),
                    'profileEditUrl': (
                        reverse('console:three_d_profile_edit', args=[profile.id])
                        if can_write else ''
                    ),
                }
            )

    return {
        'three_d_asset_detail': {
            'permissions': {
                'canWrite': can_write,
                'canBuildPreview': can_build_preview,
            },
            'asset': {
                'id': asset.id,
                'name': _asset_name(asset),
                'originalName': asset.original_name,
                'ext': (asset.file_ext or asset_extension(asset.public_path)).lower(),
                'size': _format_size(asset.file_size_bytes),
                'mimeType': asset.mime_type or '未知',
                'publicPath': asset.public_path,
                'storagePath': asset.storage_path if include_internal_paths else '',
                'sha256': asset.sha256 if include_internal_paths else '',
                'status': asset.status,
                'statusLabel': _asset_status_label(asset.status),
                'createdBy': asset.created_by if include_internal_paths else '',
                'createdAt': format_admin_datetime(asset.created_at),
                'updatedAt': format_admin_datetime(asset.updated_at),
            },
            'profileCount': len(profile_rows),
            'placementCount': len(placement_rows),
            'profiles': profile_rows,
            'placements': placement_rows,
            'workspaceUrl': reverse('console:assets_three_d'),
            'assetsUrl': reverse('console:assets'),
            'createProfileUrl': (
                f"{reverse('console:three_d_profile_create')}?asset_id={asset.id}"
                if can_write else ''
            ),
            'buildPreviewUrl': reverse('console:release_build_preview') if can_build_preview else '',
        }
    }
