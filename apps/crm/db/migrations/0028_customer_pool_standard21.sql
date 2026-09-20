BEGIN;

-- 客户公海「21 列标准格式」通道（2026-09-18）
-- 只加不删：crm_company 补价值分/国家代码；新增 21 列原样行表。

ALTER TABLE crm_company
  ADD COLUMN IF NOT EXISTS value numeric(6,1),
  ADD COLUMN IF NOT EXISTS country_code text NOT NULL DEFAULT '';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'crm_company_value_check'
      AND conrelid = 'crm_company'::regclass
  ) THEN
    ALTER TABLE crm_company
      ADD CONSTRAINT crm_company_value_check CHECK (
        value IS NULL OR (value >= 1 AND value <= 2000)
      );
  END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_crm_company_value
  ON crm_company(site_id, value DESC NULLS LAST, id DESC);

CREATE TABLE IF NOT EXISTS crm_customer_pool_row (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  company_id bigint NOT NULL REFERENCES crm_company(id) ON DELETE CASCADE,
  contact_id bigint REFERENCES crm_contact(id) ON DELETE SET NULL,
  import_batch_id bigint REFERENCES crm_customer_import_batch(id) ON DELETE SET NULL,
  import_row_id bigint REFERENCES crm_customer_import_row(id) ON DELETE SET NULL,
  row_key char(64) NOT NULL,
  row_number integer NOT NULL DEFAULT 0,
  phone text NOT NULL DEFAULT '',
  email text NOT NULL DEFAULT '',
  country_name text NOT NULL DEFAULT '',
  country_code text NOT NULL DEFAULT '',
  person_name text NOT NULL DEFAULT '',
  company_name text NOT NULL DEFAULT '',
  value numeric(6,1) NOT NULL,
  email_2 text NOT NULL DEFAULT '',
  email_3 text NOT NULL DEFAULT '',
  phone_2 text NOT NULL DEFAULT '',
  phone_3 text NOT NULL DEFAULT '',
  route_type text NOT NULL DEFAULT '',
  route_tier text NOT NULL DEFAULT '',
  account_id text NOT NULL DEFAULT '',
  whatsapp_confirmed text NOT NULL DEFAULT '',
  evidence_v text NOT NULL DEFAULT '',
  identity_i text NOT NULL DEFAULT '',
  tech_t text NOT NULL DEFAULT '',
  priority_p text NOT NULL DEFAULT '',
  restriction_note text NOT NULL DEFAULT '',
  source_channel text NOT NULL DEFAULT '',
  project_signal boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT crm_customer_pool_row_key_check CHECK (row_key ~ '^[0-9a-f]{64}$'),
  CONSTRAINT crm_customer_pool_row_contact_check CHECK (
    length(btrim(phone)) > 0 OR length(btrim(email)) > 0
    OR length(btrim(phone_2)) > 0 OR length(btrim(email_2)) > 0
    OR length(btrim(phone_3)) > 0 OR length(btrim(email_3)) > 0
  ),
  CONSTRAINT crm_customer_pool_row_value_check CHECK (value >= 1 AND value <= 2000),
  CONSTRAINT crm_customer_pool_row_country_check CHECK (
    country_code = '' OR country_code ~ '^[A-Za-z]{2}$'
  )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_crm_customer_pool_row_key
  ON crm_customer_pool_row(site_id, row_key);
CREATE INDEX IF NOT EXISTS idx_crm_customer_pool_row_company
  ON crm_customer_pool_row(company_id, value DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_crm_customer_pool_row_account
  ON crm_customer_pool_row(site_id, account_id, id);
CREATE INDEX IF NOT EXISTS idx_crm_customer_pool_row_country
  ON crm_customer_pool_row(site_id, country_code, company_id);

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

  IF NEW.import_batch_id IS NOT NULL THEN
    SELECT site_id INTO batch_site_id FROM crm_customer_import_batch WHERE id = NEW.import_batch_id;
    IF batch_site_id IS DISTINCT FROM NEW.site_id THEN
      RAISE EXCEPTION 'pool row site must match import batch site';
    END IF;
  END IF;

  IF NEW.import_row_id IS NOT NULL AND NEW.import_batch_id IS NOT NULL THEN
    IF NOT EXISTS (
      SELECT 1 FROM crm_customer_import_row
      WHERE id = NEW.import_row_id AND batch_id = NEW.import_batch_id
    ) THEN
      RAISE EXCEPTION 'pool row import row must belong to import batch';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_crm_customer_pool_row_scope ON crm_customer_pool_row;
CREATE TRIGGER trg_crm_customer_pool_row_scope
BEFORE INSERT OR UPDATE ON crm_customer_pool_row
FOR EACH ROW EXECUTE FUNCTION validate_customer_pool_row_scope();

COMMIT;
