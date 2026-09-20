ALTER TABLE content_access_grant
  DROP CONSTRAINT IF EXISTS content_access_grant_capability_check;

ALTER TABLE content_access_grant
  ADD CONSTRAINT content_access_grant_capability_check CHECK (
    capability IN (
      'content.read',
      'content.write',
      'content.set_published',
      'content.locale.manage',
      'assets.read',
      'assets.write',
      'assets.import_local',
      'releases.read',
      'releases.preview_build',
      'releases.candidate_build',
      'releases.candidate_select'
    )
  );

CREATE UNIQUE INDEX IF NOT EXISTS uq_release_site_id_id
  ON release(site_id, id);

CREATE TABLE IF NOT EXISTS website_release_selection (
  id bigserial PRIMARY KEY,
  request_token uuid NOT NULL UNIQUE,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE RESTRICT,
  release_id bigint NOT NULL,
  version text NOT NULL,
  previous_version text NOT NULL DEFAULT '',
  selected_by text NOT NULL,
  reason text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT website_release_selection_scope_fk
    FOREIGN KEY (site_id, release_id)
    REFERENCES release(site_id, id)
    ON DELETE RESTRICT,
  CONSTRAINT website_release_selection_version_check CHECK (
    version ~ '^[a-f0-9]{64}$'
  ),
  CONSTRAINT website_release_selection_previous_check CHECK (
    previous_version = '' OR previous_version ~ '^[a-f0-9]{64}$'
  ),
  CONSTRAINT website_release_selection_actor_check CHECK (
    char_length(btrim(selected_by)) BETWEEN 1 AND 150
  ),
  CONSTRAINT website_release_selection_reason_check CHECK (
    char_length(btrim(reason)) BETWEEN 8 AND 500
  )
);

CREATE INDEX IF NOT EXISTS idx_website_select_site
  ON website_release_selection(site_id, id DESC);

CREATE OR REPLACE FUNCTION prevent_website_release_selection_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'website_release_selection is append-only';
END;
$$;

DROP TRIGGER IF EXISTS website_release_selection_prevent_update_delete
  ON website_release_selection;

CREATE TRIGGER website_release_selection_prevent_update_delete
BEFORE UPDATE OR DELETE ON website_release_selection
FOR EACH ROW
EXECUTE FUNCTION prevent_website_release_selection_mutation();
