BEGIN;

CREATE TABLE IF NOT EXISTS crm_company_pool_state (
  company_id bigint PRIMARY KEY REFERENCES crm_company(id) ON DELETE CASCADE,
  state text NOT NULL DEFAULT 'available',
  evidence_status text NOT NULL DEFAULT 'pending',
  version integer NOT NULL DEFAULT 1,
  published_at timestamptz NOT NULL DEFAULT now(),
  claimed_at timestamptz,
  released_at timestamptz,
  archived_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT crm_company_pool_state_state_check CHECK (
    state IN ('available', 'owned', 'review', 'archived')
  ),
  CONSTRAINT crm_company_pool_state_evidence_check CHECK (
    evidence_status IN ('pending', 'declared', 'verified', 'rejected')
  ),
  CONSTRAINT crm_company_pool_state_version_check CHECK (version >= 1),
  CONSTRAINT crm_company_pool_state_archived_check CHECK (
    state <> 'archived' OR archived_at IS NOT NULL
  )
);

CREATE INDEX IF NOT EXISTS idx_crm_company_pool_queue
  ON crm_company_pool_state(state, evidence_status, published_at DESC, company_id DESC);

CREATE TABLE IF NOT EXISTS crm_company_contact_point (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  company_id bigint NOT NULL REFERENCES crm_company(id) ON DELETE CASCADE,
  contact_id bigint REFERENCES crm_contact(id) ON DELETE SET NULL,
  channel text NOT NULL,
  raw_value text NOT NULL,
  normalized_value text NOT NULL DEFAULT '',
  extension text NOT NULL DEFAULT '',
  purpose text NOT NULL DEFAULT 'business',
  status text NOT NULL DEFAULT 'unverified',
  usage_status text NOT NULL DEFAULT 'unknown',
  evidence_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT crm_company_contact_point_channel_check CHECK (
    channel IN ('email', 'phone', 'whatsapp', 'website', 'other')
  ),
  CONSTRAINT crm_company_contact_point_value_check CHECK (length(btrim(raw_value)) > 0),
  CONSTRAINT crm_company_contact_point_purpose_check CHECK (
    purpose IN ('business', 'personal', 'unknown')
  ),
  CONSTRAINT crm_company_contact_point_status_check CHECK (
    status IN ('active', 'unverified', 'do_not_contact', 'invalid')
  ),
  CONSTRAINT crm_company_contact_point_usage_check CHECK (
    usage_status IN ('permitted', 'restricted', 'unknown')
  )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_crm_company_contact_point_identity
  ON crm_company_contact_point(site_id, company_id, channel, normalized_value, extension)
  WHERE normalized_value <> '';
CREATE INDEX IF NOT EXISTS idx_crm_company_contact_point_company
  ON crm_company_contact_point(company_id, status, channel, id);

CREATE TABLE IF NOT EXISTS crm_customer_source (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  company_id bigint NOT NULL REFERENCES crm_company(id) ON DELETE CASCADE,
  submission_id bigint REFERENCES lead_submission(id) ON DELETE SET NULL,
  source_type text NOT NULL,
  intake_method text NOT NULL,
  source_detail text NOT NULL DEFAULT '',
  external_record_id text NOT NULL DEFAULT '',
  occurred_at timestamptz,
  received_at timestamptz NOT NULL DEFAULT now(),
  responsible_user_id integer REFERENCES auth_user(id) ON DELETE SET NULL,
  responsible_team_id bigint REFERENCES sales_team(id) ON DELETE SET NULL,
  attribution_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  evidence_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  evidence_status text NOT NULL DEFAULT 'declared',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT crm_customer_source_type_check CHECK (
    source_type IN ('manual', 'research', 'website_form', 'meta_native', 'other')
  ),
  CONSTRAINT crm_customer_source_intake_check CHECK (
    intake_method IN ('manual_create', 'file_import', 'automatic_receive')
  ),
  CONSTRAINT crm_customer_source_evidence_check CHECK (
    evidence_status IN ('declared', 'verified', 'pending', 'rejected')
  ),
  CONSTRAINT crm_customer_source_submission_check CHECK (
    submission_id IS NULL OR source_type IN ('website_form', 'meta_native')
  ),
  CONSTRAINT crm_customer_source_automatic_check CHECK (
    intake_method <> 'automatic_receive' OR source_type IN ('website_form', 'meta_native', 'other')
  )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_crm_customer_source_external
  ON crm_customer_source(site_id, source_type, external_record_id)
  WHERE external_record_id <> '';
CREATE INDEX IF NOT EXISTS idx_crm_customer_source_company
  ON crm_customer_source(company_id, received_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_crm_customer_source_filter
  ON crm_customer_source(site_id, source_type, intake_method, received_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS crm_customer_import_batch (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  created_by_id integer NOT NULL REFERENCES auth_user(id) ON DELETE RESTRICT,
  namespace text NOT NULL DEFAULT 'customer',
  source_type text NOT NULL,
  adapter_version text NOT NULL DEFAULT 'v1',
  file_sha256 char(64) NOT NULL,
  original_name text NOT NULL,
  status text NOT NULL DEFAULT 'preview',
  counts_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  CONSTRAINT crm_customer_import_batch_namespace_check CHECK (length(btrim(namespace)) > 0),
  CONSTRAINT crm_customer_import_batch_source_check CHECK (
    source_type IN ('manual', 'research', 'website_form', 'meta_native', 'other')
  ),
  CONSTRAINT crm_customer_import_batch_hash_check CHECK (file_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT crm_customer_import_batch_status_check CHECK (
    status IN ('preview', 'processing', 'succeeded', 'partial', 'failed', 'canceled')
  ),
  CONSTRAINT crm_customer_import_batch_completed_check CHECK (
    status NOT IN ('succeeded', 'partial', 'failed', 'canceled') OR completed_at IS NOT NULL
  ),
  UNIQUE(site_id, namespace, file_sha256, adapter_version)
);

CREATE INDEX IF NOT EXISTS idx_crm_customer_import_batch_history
  ON crm_customer_import_batch(site_id, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS crm_customer_import_row (
  id bigserial PRIMARY KEY,
  batch_id bigint NOT NULL REFERENCES crm_customer_import_batch(id) ON DELETE CASCADE,
  row_key char(64) NOT NULL,
  row_number integer NOT NULL,
  payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL,
  company_id bigint REFERENCES crm_company(id) ON DELETE SET NULL,
  result_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  message text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT crm_customer_import_row_key_check CHECK (row_key ~ '^[0-9a-f]{64}$'),
  CONSTRAINT crm_customer_import_row_number_check CHECK (row_number >= 1),
  CONSTRAINT crm_customer_import_row_status_check CHECK (
    status IN ('new', 'supplement', 'unchanged', 'quarantined', 'rejected', 'imported', 'failed')
  ),
  UNIQUE(batch_id, row_key)
);

CREATE INDEX IF NOT EXISTS idx_crm_customer_import_row_batch
  ON crm_customer_import_row(batch_id, row_number, id);
CREATE INDEX IF NOT EXISTS idx_crm_customer_import_row_company
  ON crm_customer_import_row(company_id)
  WHERE company_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS crm_customer_export_job (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  created_by_id integer NOT NULL REFERENCES auth_user(id) ON DELETE RESTRICT,
  status text NOT NULL DEFAULT 'pending',
  scope_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  fields_json jsonb NOT NULL DEFAULT '[]'::jsonb,
  format text NOT NULL DEFAULT 'xlsx',
  record_count integer NOT NULL DEFAULT 0,
  storage_path text NOT NULL DEFAULT '',
  sha256 char(64) NOT NULL DEFAULT '',
  expires_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  CONSTRAINT crm_customer_export_job_status_check CHECK (
    status IN ('pending', 'processing', 'ready', 'failed', 'expired', 'canceled')
  ),
  CONSTRAINT crm_customer_export_job_format_check CHECK (format IN ('xlsx', 'csv')),
  CONSTRAINT crm_customer_export_job_count_check CHECK (record_count >= 0),
  CONSTRAINT crm_customer_export_job_hash_check CHECK (sha256 = '' OR sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT crm_customer_export_job_ready_check CHECK (
    status <> 'ready' OR (
      length(btrim(storage_path)) > 0
      AND sha256 <> ''
      AND completed_at IS NOT NULL
      AND expires_at IS NOT NULL
    )
  )
);

CREATE INDEX IF NOT EXISTS idx_crm_customer_export_job_history
  ON crm_customer_export_job(site_id, created_by_id, created_at DESC, id DESC);

CREATE OR REPLACE FUNCTION validate_company_pool_state_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.state = 'available' AND EXISTS (
    SELECT 1
    FROM crm_company
    WHERE id = NEW.company_id
      AND (owner_user_id IS NOT NULL OR team_id IS NULL)
  ) THEN
    RAISE EXCEPTION 'available pool company must have a team and no owner';
  END IF;
  IF NEW.state = 'owned' AND EXISTS (
    SELECT 1 FROM crm_company WHERE id = NEW.company_id AND owner_user_id IS NULL
  ) THEN
    RAISE EXCEPTION 'owned pool company must have an owner';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION validate_company_contact_point_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  company_site_id bigint;
  contact_site_id bigint;
BEGIN
  SELECT site_id INTO company_site_id FROM crm_company WHERE id = NEW.company_id;
  IF company_site_id IS DISTINCT FROM NEW.site_id THEN
    RAISE EXCEPTION 'customer record site must match company site';
  END IF;

  IF NEW.contact_id IS NOT NULL THEN
    SELECT site_id INTO contact_site_id FROM crm_contact WHERE id = NEW.contact_id;
    IF contact_site_id IS DISTINCT FROM NEW.site_id THEN
      RAISE EXCEPTION 'contact point site must match contact site';
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM crm_contact WHERE id = NEW.contact_id AND company_id = NEW.company_id
    ) THEN
      RAISE EXCEPTION 'contact point contact must belong to company';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION validate_customer_source_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  company_site_id bigint;
  submission_site_id bigint;
  team_site_id bigint;
BEGIN
  SELECT site_id INTO company_site_id FROM crm_company WHERE id = NEW.company_id;
  IF company_site_id IS DISTINCT FROM NEW.site_id THEN
    RAISE EXCEPTION 'customer record site must match company site';
  END IF;

  IF NEW.submission_id IS NOT NULL THEN
    SELECT site_id INTO submission_site_id FROM lead_submission WHERE id = NEW.submission_id;
    IF submission_site_id IS DISTINCT FROM NEW.site_id THEN
      RAISE EXCEPTION 'customer source site must match submission site';
    END IF;
  END IF;
  IF NEW.responsible_team_id IS NOT NULL THEN
    SELECT site_id INTO team_site_id FROM sales_team WHERE id = NEW.responsible_team_id;
    IF team_site_id IS DISTINCT FROM NEW.site_id THEN
      RAISE EXCEPTION 'customer source team must belong to site';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_crm_company_pool_scope ON crm_company_pool_state;
CREATE CONSTRAINT TRIGGER trg_crm_company_pool_scope
AFTER INSERT OR UPDATE ON crm_company_pool_state
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION validate_company_pool_state_scope();

DROP TRIGGER IF EXISTS trg_crm_company_contact_point_scope ON crm_company_contact_point;
CREATE TRIGGER trg_crm_company_contact_point_scope
BEFORE INSERT OR UPDATE ON crm_company_contact_point
FOR EACH ROW EXECUTE FUNCTION validate_company_contact_point_scope();

DROP TRIGGER IF EXISTS trg_crm_customer_source_scope ON crm_customer_source;
CREATE TRIGGER trg_crm_customer_source_scope
BEFORE INSERT OR UPDATE ON crm_customer_source
FOR EACH ROW EXECUTE FUNCTION validate_customer_source_scope();

CREATE OR REPLACE FUNCTION validate_company_pool_owner()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  pool_state text;
BEGIN
  SELECT state INTO pool_state
  FROM crm_company_pool_state
  WHERE company_id = NEW.id;
  IF pool_state = 'available' AND (
    NEW.owner_user_id IS NOT NULL OR NEW.team_id IS NULL
  ) THEN
    RAISE EXCEPTION 'available pool company must have a team and no owner';
  END IF;
  IF pool_state = 'owned' AND NEW.owner_user_id IS NULL THEN
    RAISE EXCEPTION 'owned pool company must have an owner';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_crm_company_pool_owner ON crm_company;
CREATE CONSTRAINT TRIGGER trg_crm_company_pool_owner
AFTER INSERT OR UPDATE OF owner_user_id, team_id ON crm_company
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION validate_company_pool_owner();

COMMIT;
