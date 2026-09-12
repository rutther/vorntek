CREATE TABLE IF NOT EXISTS three_d_view_profile (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  asset_id bigint NOT NULL REFERENCES media_asset(id) ON DELETE RESTRICT,
  poster_asset_id bigint REFERENCES media_asset(id) ON DELETE SET NULL,
  code text NOT NULL,
  name text NOT NULL,
  purpose text NOT NULL DEFAULT 'general',
  status text NOT NULL DEFAULT 'active',
  background_color text NOT NULL DEFAULT '#f3f5f8',
  camera_position_x numeric(12, 4) NOT NULL DEFAULT 8,
  camera_position_y numeric(12, 4) NOT NULL DEFAULT 5,
  camera_position_z numeric(12, 4) NOT NULL DEFAULT -8,
  camera_target_x numeric(12, 4) NOT NULL DEFAULT 0,
  camera_target_y numeric(12, 4) NOT NULL DEFAULT 0,
  camera_target_z numeric(12, 4) NOT NULL DEFAULT 0,
  fov numeric(8, 3) NOT NULL DEFAULT 60,
  show_ui boolean NOT NULL DEFAULT false,
  allow_interaction boolean NOT NULL DEFAULT true,
  notes text NOT NULL DEFAULT '',
  config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(site_id, code),
  CONSTRAINT three_d_view_profile_status_check CHECK (
    status IN ('active', 'disabled', 'archived')
  ),
  CONSTRAINT three_d_view_profile_bg_check CHECK (
    background_color ~ '^#[A-Fa-f0-9]{6}$'
  ),
  CONSTRAINT three_d_view_profile_code_check CHECK (
    code ~ '^[a-z][a-z0-9_]*$'
  ),
  CONSTRAINT three_d_view_profile_fov_range CHECK (
    fov >= 10 AND fov <= 120
  )
);

CREATE INDEX IF NOT EXISTS idx_three_d_view_profile_asset
  ON three_d_view_profile(site_id, asset_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS three_d_placement (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  slot_code text NOT NULL,
  name text NOT NULL,
  profile_id bigint NOT NULL REFERENCES three_d_view_profile(id) ON DELETE RESTRICT,
  enabled boolean NOT NULL DEFAULT true,
  sort_order integer NOT NULL DEFAULT 100,
  notes text NOT NULL DEFAULT '',
  config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(site_id, slot_code),
  CONSTRAINT three_d_placement_slot_code_check CHECK (
    slot_code ~ '^[a-z][a-z0-9_]*$'
  )
);

CREATE INDEX IF NOT EXISTS idx_three_d_placement_lookup
  ON three_d_placement(site_id, enabled, sort_order, slot_code);

INSERT INTO three_d_view_profile (
  site_id,
  asset_id,
  code,
  name,
  purpose,
  status,
  background_color,
  camera_position_x,
  camera_position_y,
  camera_position_z,
  camera_target_x,
  camera_target_y,
  camera_target_z,
  fov,
  show_ui,
  allow_interaction,
  notes
)
SELECT
  s.id,
  a.id,
  'homepage_hero_default',
  '首页 Hero 背景 3D',
  'homepage_hero',
  'active',
  '#0d1117',
  6,
  4,
  -6,
  0,
  0,
  0,
  50,
  false,
  false,
  '首页通栏背景默认 3D 视图。'
FROM site s
JOIN LATERAL (
  SELECT id
  FROM media_asset
  WHERE site_id = s.id
    AND asset_type = 'model3d'
    AND status = 'active'
    AND (
      title LIKE '%灌装机核心1%'
      OR original_name = '滁州灌装机核心1.sog'
      OR public_path LIKE '%041a34b7dd25a61b-1.sog'
    )
  ORDER BY id DESC
  LIMIT 1
) a ON true
WHERE s.code = 'siteos_demo'
ON CONFLICT (site_id, code) DO NOTHING;

INSERT INTO three_d_view_profile (
  site_id,
  asset_id,
  code,
  name,
  purpose,
  status,
  background_color,
  camera_position_x,
  camera_position_y,
  camera_position_z,
  camera_target_x,
  camera_target_y,
  camera_target_z,
  fov,
  show_ui,
  allow_interaction,
  notes
)
SELECT
  s.id,
  a.id,
  'homepage_equipment_default',
  '首页设备预览 3D',
  'homepage_equipment_preview',
  'active',
  '#f3f5f8',
  8,
  5,
  -8,
  0,
  0,
  0,
  60,
  false,
  true,
  '首页 3D Equipment Preview 默认视图。'
FROM site s
JOIN LATERAL (
  SELECT id
  FROM media_asset
  WHERE site_id = s.id
    AND asset_type = 'model3d'
    AND status = 'active'
    AND (
      title LIKE '%灌装机核心2%'
      OR original_name = '滁州灌装机核心2.sog'
      OR public_path LIKE '%501dcec44f0cabe4-2.sog'
    )
  ORDER BY id DESC
  LIMIT 1
) a ON true
WHERE s.code = 'siteos_demo'
ON CONFLICT (site_id, code) DO NOTHING;

INSERT INTO three_d_placement (site_id, slot_code, name, profile_id, enabled, sort_order, notes)
SELECT
  p.site_id,
  'homepage_hero',
  '首页 Hero 背景',
  p.id,
  true,
  100,
  '首页顶部通栏背景 3D 槽位。'
FROM three_d_view_profile p
WHERE p.code = 'homepage_hero_default'
ON CONFLICT (site_id, slot_code) DO NOTHING;

INSERT INTO three_d_placement (site_id, slot_code, name, profile_id, enabled, sort_order, notes)
SELECT
  p.site_id,
  'homepage_equipment_preview',
  '首页 3D 设备预览',
  p.id,
  true,
  110,
  '首页设备预览区域 3D 槽位。'
FROM three_d_view_profile p
WHERE p.code = 'homepage_equipment_default'
ON CONFLICT (site_id, slot_code) DO NOTHING;
