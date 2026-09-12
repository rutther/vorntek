BEGIN;

ALTER TABLE crm_attachment
  ALTER COLUMN asset_id DROP NOT NULL,
  ADD COLUMN IF NOT EXISTS original_name text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS mime_type text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS file_size_bytes bigint NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS sha256 char(64) NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS storage_path text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'active';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'crm_attachment_file_size_check'
      AND conrelid = 'crm_attachment'::regclass
  ) THEN
    ALTER TABLE crm_attachment
      ADD CONSTRAINT crm_attachment_file_size_check CHECK (file_size_bytes >= 0);
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'crm_attachment_status_check'
      AND conrelid = 'crm_attachment'::regclass
  ) THEN
    ALTER TABLE crm_attachment
      ADD CONSTRAINT crm_attachment_status_check CHECK (status IN ('active', 'archived'));
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'crm_attachment_file_source_check'
      AND conrelid = 'crm_attachment'::regclass
  ) THEN
    ALTER TABLE crm_attachment
      ADD CONSTRAINT crm_attachment_file_source_check CHECK (
        asset_id IS NOT NULL OR length(btrim(storage_path)) > 0
      );
  END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS idx_crm_attachment_site_status
  ON crm_attachment(site_id, status, created_at DESC, id DESC);

ALTER TABLE crm_saved_view
  ADD COLUMN IF NOT EXISTS team_id bigint REFERENCES sales_team(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_crm_saved_view_shared_team
  ON crm_saved_view(site_id, team_id, scope, name)
  WHERE is_shared AND team_id IS NOT NULL;

COMMIT;
