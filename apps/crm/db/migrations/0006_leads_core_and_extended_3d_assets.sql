CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE media_asset
  DROP CONSTRAINT IF EXISTS media_asset_ext_check;

ALTER TABLE media_asset
  ADD CONSTRAINT media_asset_ext_check CHECK (
    file_ext IN ('.jpg', '.jpeg', '.png', '.webp', '.mp4', '.glb', '.ply', '.sog', '.pdf')
  );

CREATE TABLE IF NOT EXISTS lead_form_definition (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  locale_id bigint REFERENCES site_locale(id) ON DELETE CASCADE,
  code text NOT NULL,
  name text NOT NULL,
  category text NOT NULL DEFAULT 'general',
  channel text NOT NULL DEFAULT 'website',
  scope_type text NOT NULL DEFAULT 'site',
  scope_value text NOT NULL DEFAULT '*',
  status text NOT NULL DEFAULT 'active',
  submission_event_name text NOT NULL DEFAULT 'Lead',
  won_event_name text NOT NULL DEFAULT 'Purchase',
  capi_enabled boolean NOT NULL DEFAULT true,
  notify_emails text NOT NULL DEFAULT '',
  success_message text NOT NULL DEFAULT '',
  form_schema jsonb NOT NULL DEFAULT '{}'::jsonb,
  config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(site_id, code),
  CONSTRAINT lead_form_definition_channel_check CHECK (
    channel IN ('website', 'whatsapp', 'messenger', 'phone', 'email', 'custom')
  ),
  CONSTRAINT lead_form_definition_status_check CHECK (
    status IN ('active', 'disabled', 'archived')
  ),
  CONSTRAINT lead_form_definition_scope_type_check CHECK (
    scope_type IN ('site', 'route', 'cta', 'page_type', 'custom')
  )
);

CREATE INDEX IF NOT EXISTS idx_lead_form_definition_scope
  ON lead_form_definition(site_id, locale_id, scope_type, scope_value, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS lead_submission (
  id bigserial PRIMARY KEY,
  submission_key uuid NOT NULL DEFAULT gen_random_uuid(),
  form_id bigint NOT NULL REFERENCES lead_form_definition(id) ON DELETE RESTRICT,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  locale_id bigint REFERENCES site_locale(id) ON DELETE SET NULL,
  route_id bigint REFERENCES page_route(id) ON DELETE SET NULL,
  cta_id bigint REFERENCES cta(id) ON DELETE SET NULL,
  stage text NOT NULL DEFAULT 'new',
  full_name text NOT NULL DEFAULT '',
  email text NOT NULL DEFAULT '',
  phone text NOT NULL DEFAULT '',
  company text NOT NULL DEFAULT '',
  country text NOT NULL DEFAULT '',
  message text NOT NULL DEFAULT '',
  source_url text NOT NULL DEFAULT '',
  referrer_url text NOT NULL DEFAULT '',
  client_ip text NOT NULL DEFAULT '',
  user_agent text NOT NULL DEFAULT '',
  buyer_value numeric(12, 2),
  buyer_currency text NOT NULL DEFAULT 'USD',
  payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  identifiers_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  utm_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  consent_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  submitted_at timestamptz NOT NULL DEFAULT now(),
  stage_updated_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(submission_key),
  CONSTRAINT lead_submission_stage_check CHECK (
    stage IN ('new', 'contacted', 'qualified', 'won', 'lost', 'spam')
  ),
  CONSTRAINT lead_submission_currency_check CHECK (
    buyer_currency ~ '^[A-Z]{3,8}$'
  )
);

CREATE INDEX IF NOT EXISTS idx_lead_submission_lookup
  ON lead_submission(site_id, locale_id, stage, submitted_at DESC);

CREATE INDEX IF NOT EXISTS idx_lead_submission_form
  ON lead_submission(form_id, stage, submitted_at DESC);

CREATE TABLE IF NOT EXISTS lead_event_outbox (
  id bigserial PRIMARY KEY,
  submission_id bigint NOT NULL REFERENCES lead_submission(id) ON DELETE CASCADE,
  integration_id bigint NOT NULL REFERENCES marketing_integration(id) ON DELETE CASCADE,
  stage_key text NOT NULL,
  event_name text NOT NULL,
  event_id text NOT NULL,
  action_source text NOT NULL DEFAULT 'website',
  payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'pending',
  attempts integer NOT NULL DEFAULT 0,
  last_error text NOT NULL DEFAULT '',
  response_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  dispatched_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(submission_id, integration_id, stage_key, event_name),
  CONSTRAINT lead_event_outbox_status_check CHECK (
    status IN ('pending', 'sending', 'sent', 'failed', 'skipped')
  ),
  CONSTRAINT lead_event_outbox_action_source_check CHECK (
    action_source IN ('website', 'phone_call', 'chat', 'email', 'other')
  )
);

CREATE INDEX IF NOT EXISTS idx_lead_event_outbox_status
  ON lead_event_outbox(status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_lead_event_outbox_submission
  ON lead_event_outbox(submission_id, status, created_at DESC);
