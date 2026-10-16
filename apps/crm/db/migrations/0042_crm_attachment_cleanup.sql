-- Preserve physical cleanup intent after logical anonymization; additive only.
ALTER TABLE crm_attachment
  ADD COLUMN IF NOT EXISTS purge_storage_path text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS purge_attempts integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS purge_error_code varchar(24) NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS purge_last_attempt_at timestamptz;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'crm_attachment'::regclass
                 AND conname = 'crm_attachment_cleanup_state_check') THEN
    ALTER TABLE crm_attachment ADD CONSTRAINT crm_attachment_cleanup_state_check CHECK (
      purge_attempts >= 0 AND purge_error_code IN ('', 'shared', 'filesystem', 'invalid_path')
      AND (purge_storage_path = '' OR (status = 'archived' AND storage_path = ''))
    );
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS crm_attachment_pending_cleanup_idx
  ON crm_attachment (site_id, purge_last_attempt_at, id) WHERE purge_storage_path <> '';
