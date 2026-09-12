CREATE TABLE IF NOT EXISTS crm_company (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  owner_user_id integer REFERENCES auth_user(id) ON DELETE SET NULL,
  team_id bigint REFERENCES sales_team(id) ON DELETE SET NULL,
  name text NOT NULL,
  normalized_name text NOT NULL DEFAULT '',
  website text NOT NULL DEFAULT '',
  industry text NOT NULL DEFAULT '',
  country text NOT NULL DEFAULT '',
  city text NOT NULL DEFAULT '',
  status text NOT NULL DEFAULT 'prospect',
  source_channel text NOT NULL DEFAULT 'website',
  notes text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT crm_company_status_check CHECK (
    status IN ('prospect', 'customer', 'inactive')
  )
);

CREATE INDEX IF NOT EXISTS idx_crm_company_owner
  ON crm_company(site_id, owner_user_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_crm_company_team
  ON crm_company(site_id, team_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_crm_company_identity
  ON crm_company(site_id, normalized_name, country)
  WHERE normalized_name <> '';

CREATE TABLE IF NOT EXISTS crm_contact (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  company_id bigint REFERENCES crm_company(id) ON DELETE SET NULL,
  owner_user_id integer REFERENCES auth_user(id) ON DELETE SET NULL,
  team_id bigint REFERENCES sales_team(id) ON DELETE SET NULL,
  full_name text NOT NULL,
  job_title text NOT NULL DEFAULT '',
  email text NOT NULL DEFAULT '',
  email_normalized text NOT NULL DEFAULT '',
  phone text NOT NULL DEFAULT '',
  phone_normalized text NOT NULL DEFAULT '',
  whatsapp_phone text NOT NULL DEFAULT '',
  country text NOT NULL DEFAULT '',
  preferred_language text NOT NULL DEFAULT '',
  status text NOT NULL DEFAULT 'active',
  notes text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT crm_contact_status_check CHECK (
    status IN ('active', 'inactive', 'do_not_contact')
  )
);

CREATE INDEX IF NOT EXISTS idx_crm_contact_company
  ON crm_contact(site_id, company_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_crm_contact_owner
  ON crm_contact(site_id, owner_user_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_crm_contact_team
  ON crm_contact(site_id, team_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_crm_contact_email
  ON crm_contact(site_id, email_normalized)
  WHERE email_normalized <> '';
CREATE INDEX IF NOT EXISTS idx_crm_contact_phone
  ON crm_contact(site_id, phone_normalized)
  WHERE phone_normalized <> '';

CREATE TABLE IF NOT EXISTS crm_opportunity (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  company_id bigint REFERENCES crm_company(id) ON DELETE SET NULL,
  primary_contact_id bigint REFERENCES crm_contact(id) ON DELETE SET NULL,
  source_submission_id bigint REFERENCES lead_submission(id) ON DELETE SET NULL,
  owner_user_id integer REFERENCES auth_user(id) ON DELETE SET NULL,
  team_id bigint REFERENCES sales_team(id) ON DELETE SET NULL,
  name text NOT NULL,
  stage text NOT NULL DEFAULT 'qualification',
  value_amount numeric(14,2),
  currency text NOT NULL DEFAULT 'USD',
  probability integer NOT NULL DEFAULT 10,
  expected_close_date date,
  product_scope text NOT NULL DEFAULT '',
  capacity_target text NOT NULL DEFAULT '',
  packaging_format text NOT NULL DEFAULT '',
  source_channel text NOT NULL DEFAULT 'website',
  source_detail text NOT NULL DEFAULT '',
  next_step text NOT NULL DEFAULT '',
  next_follow_up_at timestamptz,
  won_reason text NOT NULL DEFAULT '',
  lost_reason text NOT NULL DEFAULT '',
  competitor text NOT NULL DEFAULT '',
  closed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT crm_opportunity_stage_check CHECK (
    stage IN ('qualification', 'discovery', 'solution', 'quotation', 'negotiation', 'on_hold', 'won', 'lost')
  ),
  CONSTRAINT crm_opportunity_probability_check CHECK (
    probability BETWEEN 0 AND 100
  ),
  CONSTRAINT crm_opportunity_value_check CHECK (
    value_amount IS NULL OR value_amount >= 0
  ),
  CONSTRAINT crm_opportunity_currency_check CHECK (
    currency ~ '^[A-Z]{3}$'
  )
);

CREATE INDEX IF NOT EXISTS idx_crm_opportunity_owner_pipeline
  ON crm_opportunity(site_id, owner_user_id, stage, next_follow_up_at, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_crm_opportunity_team_pipeline
  ON crm_opportunity(site_id, team_id, stage, next_follow_up_at, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_crm_opportunity_company
  ON crm_opportunity(site_id, company_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_crm_opportunity_source
  ON crm_opportunity(source_submission_id)
  WHERE source_submission_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS crm_lead_conversion (
  id bigserial PRIMARY KEY,
  submission_id bigint NOT NULL REFERENCES lead_submission(id) ON DELETE RESTRICT,
  company_id bigint REFERENCES crm_company(id) ON DELETE SET NULL,
  contact_id bigint REFERENCES crm_contact(id) ON DELETE SET NULL,
  opportunity_id bigint REFERENCES crm_opportunity(id) ON DELETE SET NULL,
  converted_by_user_id integer REFERENCES auth_user(id) ON DELETE SET NULL,
  metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  converted_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(submission_id),
  CONSTRAINT crm_lead_conversion_target_check CHECK (
    company_id IS NOT NULL OR contact_id IS NOT NULL OR opportunity_id IS NOT NULL
  )
);

CREATE INDEX IF NOT EXISTS idx_crm_lead_conversion_company
  ON crm_lead_conversion(company_id, converted_at DESC)
  WHERE company_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_crm_lead_conversion_opportunity
  ON crm_lead_conversion(opportunity_id)
  WHERE opportunity_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS crm_activity (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  submission_id bigint REFERENCES lead_submission(id) ON DELETE SET NULL,
  company_id bigint REFERENCES crm_company(id) ON DELETE SET NULL,
  contact_id bigint REFERENCES crm_contact(id) ON DELETE SET NULL,
  opportunity_id bigint REFERENCES crm_opportunity(id) ON DELETE SET NULL,
  actor_user_id integer REFERENCES auth_user(id) ON DELETE SET NULL,
  activity_type text NOT NULL,
  direction text NOT NULL DEFAULT 'internal',
  subject text NOT NULL DEFAULT '',
  body text NOT NULL DEFAULT '',
  metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT crm_activity_type_check CHECK (
    activity_type IN ('note', 'call', 'email', 'whatsapp', 'meeting', 'site_visit', 'task', 'system', 'stage_change', 'assignment', 'conversion', 'file')
  ),
  CONSTRAINT crm_activity_direction_check CHECK (
    direction IN ('inbound', 'outbound', 'internal')
  ),
  CONSTRAINT crm_activity_target_check CHECK (
    submission_id IS NOT NULL OR company_id IS NOT NULL OR contact_id IS NOT NULL OR opportunity_id IS NOT NULL
  )
);

CREATE INDEX IF NOT EXISTS idx_crm_activity_submission
  ON crm_activity(submission_id, occurred_at DESC, id DESC)
  WHERE submission_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_crm_activity_company
  ON crm_activity(company_id, occurred_at DESC, id DESC)
  WHERE company_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_crm_activity_contact
  ON crm_activity(contact_id, occurred_at DESC, id DESC)
  WHERE contact_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_crm_activity_opportunity
  ON crm_activity(opportunity_id, occurred_at DESC, id DESC)
  WHERE opportunity_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS crm_task (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  submission_id bigint REFERENCES lead_submission(id) ON DELETE SET NULL,
  company_id bigint REFERENCES crm_company(id) ON DELETE SET NULL,
  contact_id bigint REFERENCES crm_contact(id) ON DELETE SET NULL,
  opportunity_id bigint REFERENCES crm_opportunity(id) ON DELETE SET NULL,
  owner_user_id integer NOT NULL REFERENCES auth_user(id) ON DELETE RESTRICT,
  team_id bigint REFERENCES sales_team(id) ON DELETE SET NULL,
  created_by_user_id integer REFERENCES auth_user(id) ON DELETE SET NULL,
  title text NOT NULL,
  description text NOT NULL DEFAULT '',
  task_type text NOT NULL DEFAULT 'follow_up',
  priority text NOT NULL DEFAULT 'normal',
  status text NOT NULL DEFAULT 'open',
  due_at timestamptz NOT NULL,
  reminder_at timestamptz,
  completed_at timestamptz,
  outcome text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT crm_task_type_check CHECK (
    task_type IN ('follow_up', 'call', 'email', 'whatsapp', 'meeting', 'quote', 'review', 'other')
  ),
  CONSTRAINT crm_task_priority_check CHECK (
    priority IN ('low', 'normal', 'high', 'urgent')
  ),
  CONSTRAINT crm_task_status_check CHECK (
    status IN ('open', 'in_progress', 'completed', 'canceled')
  ),
  CONSTRAINT crm_task_completion_check CHECK (
    (status = 'completed' AND completed_at IS NOT NULL)
    OR (status <> 'completed')
  ),
  CONSTRAINT crm_task_target_check CHECK (
    submission_id IS NOT NULL OR company_id IS NOT NULL OR contact_id IS NOT NULL OR opportunity_id IS NOT NULL
  )
);

CREATE INDEX IF NOT EXISTS idx_crm_task_owner_work
  ON crm_task(site_id, owner_user_id, status, due_at, priority);
CREATE INDEX IF NOT EXISTS idx_crm_task_team_work
  ON crm_task(site_id, team_id, status, due_at, priority);
CREATE INDEX IF NOT EXISTS idx_crm_task_opportunity
  ON crm_task(opportunity_id, status, due_at)
  WHERE opportunity_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS crm_opportunity_stage_history (
  id bigserial PRIMARY KEY,
  opportunity_id bigint NOT NULL REFERENCES crm_opportunity(id) ON DELETE CASCADE,
  from_stage text,
  to_stage text NOT NULL,
  changed_by_user_id integer REFERENCES auth_user(id) ON DELETE SET NULL,
  reason text NOT NULL DEFAULT '',
  changed_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT crm_opportunity_stage_history_from_check CHECK (
    from_stage IS NULL OR from_stage IN ('qualification', 'discovery', 'solution', 'quotation', 'negotiation', 'on_hold', 'won', 'lost')
  ),
  CONSTRAINT crm_opportunity_stage_history_to_check CHECK (
    to_stage IN ('qualification', 'discovery', 'solution', 'quotation', 'negotiation', 'on_hold', 'won', 'lost')
  )
);

CREATE INDEX IF NOT EXISTS idx_crm_opportunity_stage_history
  ON crm_opportunity_stage_history(opportunity_id, changed_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS crm_attachment (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  asset_id bigint NOT NULL REFERENCES media_asset(id) ON DELETE RESTRICT,
  submission_id bigint REFERENCES lead_submission(id) ON DELETE SET NULL,
  company_id bigint REFERENCES crm_company(id) ON DELETE SET NULL,
  contact_id bigint REFERENCES crm_contact(id) ON DELETE SET NULL,
  opportunity_id bigint REFERENCES crm_opportunity(id) ON DELETE SET NULL,
  uploaded_by_user_id integer REFERENCES auth_user(id) ON DELETE SET NULL,
  title text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT crm_attachment_target_check CHECK (
    submission_id IS NOT NULL OR company_id IS NOT NULL OR contact_id IS NOT NULL OR opportunity_id IS NOT NULL
  )
);

CREATE INDEX IF NOT EXISTS idx_crm_attachment_submission
  ON crm_attachment(submission_id, created_at DESC)
  WHERE submission_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_crm_attachment_opportunity
  ON crm_attachment(opportunity_id, created_at DESC)
  WHERE opportunity_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS crm_saved_view (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  user_id integer NOT NULL REFERENCES auth_user(id) ON DELETE CASCADE,
  scope text NOT NULL,
  name text NOT NULL,
  filters_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  columns_json jsonb NOT NULL DEFAULT '[]'::jsonb,
  sort_json jsonb NOT NULL DEFAULT '[]'::jsonb,
  is_default boolean NOT NULL DEFAULT false,
  is_shared boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(site_id, user_id, scope, name),
  CONSTRAINT crm_saved_view_scope_check CHECK (
    scope IN ('leads', 'companies', 'contacts', 'opportunities', 'tasks')
  )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_crm_saved_view_default
  ON crm_saved_view(site_id, user_id, scope)
  WHERE is_default;

CREATE OR REPLACE FUNCTION prevent_crm_timeline_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$;

DROP TRIGGER IF EXISTS crm_activity_prevent_update_delete ON crm_activity;
CREATE TRIGGER crm_activity_prevent_update_delete
BEFORE UPDATE OR DELETE ON crm_activity
FOR EACH ROW
EXECUTE FUNCTION prevent_crm_timeline_mutation();

DROP TRIGGER IF EXISTS crm_stage_history_prevent_update_delete ON crm_opportunity_stage_history;
CREATE TRIGGER crm_stage_history_prevent_update_delete
BEFORE UPDATE OR DELETE ON crm_opportunity_stage_history
FOR EACH ROW
EXECUTE FUNCTION prevent_crm_timeline_mutation();

CREATE OR REPLACE FUNCTION record_crm_opportunity_stage_change()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  actor_setting text;
  reason_setting text;
BEGIN
  actor_setting := current_setting('siteos.actor_user_id', true);
  reason_setting := current_setting('siteos.stage_reason', true);

  IF TG_OP = 'INSERT' THEN
    INSERT INTO crm_opportunity_stage_history(
      opportunity_id, from_stage, to_stage, changed_by_user_id, reason
    ) VALUES (
      NEW.id,
      NULL,
      NEW.stage,
      NULLIF(actor_setting, '')::integer,
      COALESCE(reason_setting, '')
    );
  ELSIF NEW.stage IS DISTINCT FROM OLD.stage THEN
    INSERT INTO crm_opportunity_stage_history(
      opportunity_id, from_stage, to_stage, changed_by_user_id, reason
    ) VALUES (
      NEW.id,
      OLD.stage,
      NEW.stage,
      NULLIF(actor_setting, '')::integer,
      COALESCE(reason_setting, '')
    );
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS crm_opportunity_stage_change ON crm_opportunity;
CREATE TRIGGER crm_opportunity_stage_change
AFTER INSERT OR UPDATE OF stage ON crm_opportunity
FOR EACH ROW
EXECUTE FUNCTION record_crm_opportunity_stage_change();
