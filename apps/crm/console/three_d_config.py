from __future__ import annotations

from decimal import Decimal

from sitecore.models import ThreeDPlacement, ThreeDViewProfile


THREE_D_SLOT_CHOICES: tuple[tuple[str, str], ...] = (
    ('homepage_hero', '首页 Hero 背景'),
    ('homepage_equipment_preview', '首页 3D 展示区'),
)

SPLAT_EXTENSIONS = {'.sog', '.ply'}
MODEL_EXTENSIONS = {'.glb', '.gltf'}


def slot_label(slot_code: str) -> str:
    from .three_d_slots import slot_registry
    mapping = {code:row['label'] for code,row in slot_registry().items()}
    return mapping.get(slot_code, slot_code)


def purpose_label(purpose: str | None) -> str:
    raw = (purpose or 'general').strip() or 'general'
    mapping = {
        'general': '通用展示',
        **dict(THREE_D_SLOT_CHOICES),
    }
    return mapping.get(raw, raw)


def _decimal(value: Decimal | float | int | None) -> float:
    return float(value or 0)


def asset_extension(public_path: str) -> str:
    parts = public_path.rsplit('.', 1)
    return f'.{parts[-1].lower()}' if len(parts) == 2 else ''


def build_splat_viewer_url(profile: ThreeDViewProfile) -> str:
    # Generated only when an enabled slot is frozen into a website candidate.
    # Draft editors must use the authenticated preview route instead.
    return f'/assets/3d/profiles/{profile.pk}/index.html'


def placement_public_config(placement: ThreeDPlacement) -> dict[str, object]:
    profile = placement.profile
    asset = profile.asset
    ext = asset_extension(asset.public_path)
    config = {
        'slotCode': placement.slot_code,
        'slotName': placement.name,
        'enabled': placement.enabled,
        'profile': {
            'id': profile.id,
            'code': profile.code,
            'name': profile.name,
            'purpose': profile.purpose,
            'status': profile.status,
            'backgroundColor': profile.background_color,
            'camera': {
                'position': {
                    'x': _decimal(profile.camera_position_x),
                    'y': _decimal(profile.camera_position_y),
                    'z': _decimal(profile.camera_position_z),
                },
                'target': {
                    'x': _decimal(profile.camera_target_x),
                    'y': _decimal(profile.camera_target_y),
                    'z': _decimal(profile.camera_target_z),
                },
                'fov': _decimal(profile.fov),
            },
            'showUi': profile.show_ui,
            'allowInteraction': profile.allow_interaction,
            'autoRotate': bool((profile.config_json or {}).get('autoRotate', True)),
            'autoRotateDuration': (profile.config_json or {}).get('autoRotateDuration', 20),
            'notes': profile.notes,
        },
        'asset': {
            'id': asset.id,
            'title': asset.title or asset.original_name,
            'publicPath': asset.public_path,
            'ext': ext,
        },
        'viewerUrl': '',
        'modelUrl': '',
        'posterUrl': profile.poster_asset.public_path if profile.poster_asset_id and profile.poster_asset else '',
    }
    if ext in SPLAT_EXTENSIONS:
        config['viewerUrl'] = build_splat_viewer_url(profile)
    elif ext in MODEL_EXTENSIONS:
        config['modelUrl'] = asset.public_path
    return config
