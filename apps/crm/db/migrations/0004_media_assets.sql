CREATE TABLE IF NOT EXISTS media_asset (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  asset_type text NOT NULL,
  original_name text NOT NULL,
  title text NOT NULL DEFAULT '',
  alt_text text NOT NULL DEFAULT '',
  caption text NOT NULL DEFAULT '',
  mime_type text NOT NULL DEFAULT '',
  file_ext text NOT NULL,
  file_size_bytes bigint NOT NULL,
  sha256 text NOT NULL,
  storage_path text NOT NULL,
  public_path text NOT NULL,
  status text NOT NULL DEFAULT 'active',
  created_by text NOT NULL DEFAULT 'system',
  config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(site_id, sha256),
  UNIQUE(site_id, public_path),
  CONSTRAINT media_asset_type_check CHECK (
    asset_type IN ('image', 'video', 'model3d', 'document')
  ),
  CONSTRAINT media_asset_status_check CHECK (
    status IN ('active', 'archived')
  ),
  CONSTRAINT media_asset_ext_check CHECK (
    file_ext IN ('.jpg', '.jpeg', '.png', '.webp', '.mp4', '.glb', '.pdf')
  ),
  CONSTRAINT media_asset_size_positive CHECK (file_size_bytes > 0),
  CONSTRAINT media_asset_sha256_format CHECK (sha256 ~ '^[a-f0-9]{64}$'),
  CONSTRAINT media_asset_storage_path_check CHECK (
    storage_path ~ '^storage/assets/(images|videos|models|documents)/[0-9]{4}/[0-9]{2}/[a-f0-9]{16}-[a-z0-9._-]+$'
  ),
  CONSTRAINT media_asset_public_path_check CHECK (
    public_path ~ '^/assets/(images|videos|models|documents)/[0-9]{4}/[0-9]{2}/[a-f0-9]{16}-[a-z0-9._-]+$'
  )
);

CREATE INDEX IF NOT EXISTS idx_media_asset_site_type_status
  ON media_asset(site_id, asset_type, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_media_asset_status_size
  ON media_asset(status, file_size_bytes DESC);

CREATE TABLE IF NOT EXISTS media_asset_binding (
  id bigserial PRIMARY KEY,
  asset_id bigint NOT NULL REFERENCES media_asset(id) ON DELETE CASCADE,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  locale_id bigint REFERENCES site_locale(id) ON DELETE CASCADE,
  entity_type text NOT NULL,
  entity_id bigint,
  role text NOT NULL,
  sort_order integer NOT NULL DEFAULT 100,
  enabled boolean NOT NULL DEFAULT true,
  config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT media_asset_binding_entity_type_check CHECK (
    entity_type IN ('site', 'page_route', 'free_page', 'article', 'component', 'cta')
  ),
  CONSTRAINT media_asset_binding_role_format CHECK (role ~ '^[a-z][a-z0-9_]*$')
);

CREATE INDEX IF NOT EXISTS idx_media_asset_binding_lookup
  ON media_asset_binding(site_id, locale_id, entity_type, entity_id, role, enabled, sort_order);

CREATE INDEX IF NOT EXISTS idx_media_asset_binding_asset
  ON media_asset_binding(asset_id, enabled);
