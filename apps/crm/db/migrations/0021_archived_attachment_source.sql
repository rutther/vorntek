BEGIN;

ALTER TABLE crm_attachment
  DROP CONSTRAINT IF EXISTS crm_attachment_file_source_check;

ALTER TABLE crm_attachment
  ADD CONSTRAINT crm_attachment_file_source_check CHECK (
    (
      status = 'active'
      AND (
        (asset_id IS NOT NULL AND length(btrim(storage_path)) = 0)
        OR (asset_id IS NULL AND length(btrim(storage_path)) > 0)
      )
    )
    OR (
      status = 'archived'
      AND NOT (asset_id IS NOT NULL AND length(btrim(storage_path)) > 0)
    )
  );

COMMIT;
