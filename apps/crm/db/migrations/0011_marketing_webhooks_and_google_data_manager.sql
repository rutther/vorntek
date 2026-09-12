CREATE TABLE IF NOT EXISTS lead_inbound_event (
  id bigserial PRIMARY KEY,
  integration_id bigint NOT NULL REFERENCES marketing_integration(id) ON DELETE CASCADE,
  submission_id bigint REFERENCES lead_submission(id) ON DELETE SET NULL,
  provider_code text NOT NULL,
  event_type text NOT NULL,
  external_event_id text NOT NULL,
  payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'pending',
  attempts integer NOT NULL DEFAULT 0,
  last_error text NOT NULL DEFAULT '',
  received_at timestamptz NOT NULL DEFAULT now(),
  processed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT lead_inbound_event_status_check CHECK (
    status IN ('pending', 'processing', 'processed', 'failed', 'skipped')
  ),
  CONSTRAINT lead_inbound_event_provider_format CHECK (
    provider_code ~ '^[a-z][a-z0-9_]*$'
  )
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_lead_inbound_event_external
  ON lead_inbound_event(provider_code, event_type, external_event_id);

CREATE INDEX IF NOT EXISTS idx_lead_inbound_event_status
  ON lead_inbound_event(status, received_at DESC);

CREATE INDEX IF NOT EXISTS idx_lead_inbound_event_integration
  ON lead_inbound_event(integration_id, status, received_at DESC);

CREATE INDEX IF NOT EXISTS idx_lead_inbound_event_submission
  ON lead_inbound_event(submission_id, received_at DESC)
  WHERE submission_id IS NOT NULL;

ALTER TABLE lead_event_outbox
  DROP CONSTRAINT IF EXISTS lead_event_outbox_status_check;

ALTER TABLE lead_event_outbox
  ADD CONSTRAINT lead_event_outbox_status_check CHECK (
    status IN ('pending', 'sending', 'processing', 'sent', 'failed', 'skipped')
  );

INSERT INTO marketing_provider(code, name, enabled, capabilities)
VALUES
  ('meta', 'Meta', true, '{"pixel":true,"leadgen":true,"capi":true}'::jsonb),
  ('whatsapp', 'WhatsApp', true, '{"cloud_api":true,"ctwa":true,"webhook":true}'::jsonb),
  ('google', 'Google', true, '{"data_manager":true,"enhanced_conversions_for_leads":true}'::jsonb)
ON CONFLICT (code) DO UPDATE SET
  name = EXCLUDED.name,
  enabled = true,
  capabilities = marketing_provider.capabilities || EXCLUDED.capabilities,
  updated_at = now();

INSERT INTO marketing_integration(
  provider_id,
  name,
  integration_type,
  public_id,
  secret_ref,
  consent_category,
  enabled,
  config_json
)
SELECT id, 'Meta Instant Forms', 'leadgen', 'pending-page-id', NULL, 'marketing', false,
       '{"form_code":"project-inquiry","contact_consent_confirmed":false,"marketing_consent_confirmed":false,"queue_initial_crm_event":true}'::jsonb
FROM marketing_provider
WHERE code = 'meta'
ON CONFLICT (provider_id, integration_type, public_id) DO NOTHING;

INSERT INTO marketing_integration(
  provider_id,
  name,
  integration_type,
  public_id,
  secret_ref,
  consent_category,
  enabled,
  config_json
)
SELECT id, 'WhatsApp Cloud API / CTWA', 'cloud_api', 'pending-phone-number-id', NULL, 'marketing', false,
       '{"form_code":"project-inquiry","waba_id":"","contact_consent_confirmed":false,"marketing_consent_confirmed":false}'::jsonb
FROM marketing_provider
WHERE code = 'whatsapp'
ON CONFLICT (provider_id, integration_type, public_id) DO NOTHING;

INSERT INTO marketing_integration(
  provider_id,
  name,
  integration_type,
  public_id,
  secret_ref,
  consent_category,
  enabled,
  config_json
)
SELECT id, 'Google Ads Data Manager', 'data_manager', 'pending-customer-id', NULL, 'marketing', false,
       '{"login_account_id":"","qualified_conversion_action_id":"","converted_conversion_action_id":"","validate_only":true}'::jsonb
FROM marketing_provider
WHERE code = 'google'
ON CONFLICT (provider_id, integration_type, public_id) DO NOTHING;
