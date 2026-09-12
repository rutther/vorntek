CREATE UNIQUE INDEX IF NOT EXISTS uq_site_locale_site_id_id
  ON site_locale(site_id, id);

CREATE TABLE IF NOT EXISTS content_access_grant (
  id bigserial PRIMARY KEY,
  user_id integer REFERENCES auth_user(id) ON DELETE CASCADE,
  group_id integer REFERENCES auth_group(id) ON DELETE CASCADE,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  locale_id bigint REFERENCES site_locale(id) ON DELETE CASCADE,
  capability text NOT NULL,
  enabled boolean NOT NULL DEFAULT true,
  granted_by_user_id integer REFERENCES auth_user(id) ON DELETE SET NULL,
  reason text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT content_access_grant_subject_xor CHECK (
    (user_id IS NULL) <> (group_id IS NULL)
  ),
  CONSTRAINT content_access_grant_capability_check CHECK (
    capability IN (
      'content.read',
      'content.write',
      'content.set_published',
      'content.locale.manage',
      'assets.read',
      'assets.write',
      'assets.import_local',
      'releases.read',
      'releases.preview_build'
    )
  ),
  CONSTRAINT content_access_grant_locale_site_fk
    FOREIGN KEY (site_id, locale_id)
    REFERENCES site_locale(site_id, id)
    ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_content_grant_user_site_active
  ON content_access_grant(user_id, site_id, capability)
  WHERE enabled = true
    AND user_id IS NOT NULL
    AND group_id IS NULL
    AND locale_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_content_grant_user_locale_active
  ON content_access_grant(user_id, site_id, locale_id, capability)
  WHERE enabled = true
    AND user_id IS NOT NULL
    AND group_id IS NULL
    AND locale_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_content_grant_group_site_active
  ON content_access_grant(group_id, site_id, capability)
  WHERE enabled = true
    AND group_id IS NOT NULL
    AND user_id IS NULL
    AND locale_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_content_grant_group_locale_active
  ON content_access_grant(group_id, site_id, locale_id, capability)
  WHERE enabled = true
    AND group_id IS NOT NULL
    AND user_id IS NULL
    AND locale_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_content_grant_user_lookup
  ON content_access_grant(user_id, capability, site_id, locale_id)
  WHERE enabled = true AND user_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_content_grant_group_lookup
  ON content_access_grant(group_id, capability, site_id, locale_id)
  WHERE enabled = true AND group_id IS NOT NULL;
