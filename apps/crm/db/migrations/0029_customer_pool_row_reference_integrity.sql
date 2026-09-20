BEGIN;

-- Keep applied migration 0028 immutable. Tighten the optional import reference
-- as an append-only upgrade: batch and row must be absent together or refer to
-- the same site and batch.

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'crm_customer_pool_row_import_pair_check'
      AND conrelid = 'crm_customer_pool_row'::regclass
  ) THEN
    ALTER TABLE crm_customer_pool_row
      ADD CONSTRAINT crm_customer_pool_row_import_pair_check CHECK (
        (import_batch_id IS NULL AND import_row_id IS NULL)
        OR (import_batch_id IS NOT NULL AND import_row_id IS NOT NULL)
      ) NOT VALID;
  END IF;
END
$$;

ALTER TABLE crm_customer_pool_row
  VALIDATE CONSTRAINT crm_customer_pool_row_import_pair_check;

CREATE OR REPLACE FUNCTION validate_customer_pool_row_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  company_site_id bigint;
  contact_site_id bigint;
  batch_site_id bigint;
BEGIN
  SELECT site_id INTO company_site_id FROM crm_company WHERE id = NEW.company_id;
  IF company_site_id IS DISTINCT FROM NEW.site_id THEN
    RAISE EXCEPTION 'pool row site must match company site';
  END IF;

  IF NEW.contact_id IS NOT NULL THEN
    SELECT site_id INTO contact_site_id FROM crm_contact WHERE id = NEW.contact_id;
    IF contact_site_id IS DISTINCT FROM NEW.site_id THEN
      RAISE EXCEPTION 'pool row site must match contact site';
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM crm_contact WHERE id = NEW.contact_id AND company_id = NEW.company_id
    ) THEN
      RAISE EXCEPTION 'pool row contact must belong to company';
    END IF;
  END IF;

  IF (NEW.import_batch_id IS NULL) IS DISTINCT FROM (NEW.import_row_id IS NULL) THEN
    RAISE EXCEPTION 'pool row import batch and row must be set together';
  END IF;

  IF NEW.import_batch_id IS NOT NULL THEN
    SELECT site_id INTO batch_site_id
    FROM crm_customer_import_batch
    WHERE id = NEW.import_batch_id;
    IF batch_site_id IS DISTINCT FROM NEW.site_id THEN
      RAISE EXCEPTION 'pool row site must match import batch site';
    END IF;
    IF NOT EXISTS (
      SELECT 1
      FROM crm_customer_import_row
      WHERE id = NEW.import_row_id AND batch_id = NEW.import_batch_id
    ) THEN
      RAISE EXCEPTION 'pool row import row must belong to import batch';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

COMMIT;
