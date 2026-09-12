ALTER TABLE marketing_integration
  ADD COLUMN IF NOT EXISTS site_id bigint;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conrelid = 'marketing_integration'::regclass
      AND conname = 'marketing_integration_site_fk'
  ) THEN
    ALTER TABLE marketing_integration
      ADD CONSTRAINT marketing_integration_site_fk
      FOREIGN KEY (site_id) REFERENCES site(id) ON DELETE CASCADE;
  END IF;
END;
$$;

UPDATE marketing_integration AS integration
SET site_id = site.id
FROM site
WHERE integration.site_id IS NULL
  AND site.code = 'siteos_demo';

CREATE OR REPLACE FUNCTION bind_legacy_marketing_integrations_to_default_site()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.code = 'siteos_demo' THEN
    UPDATE marketing_integration
    SET site_id = NEW.id,
        updated_at = now()
    WHERE site_id IS NULL;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS site_bind_legacy_marketing_integrations ON site;
CREATE TRIGGER site_bind_legacy_marketing_integrations
AFTER INSERT OR UPDATE OF code ON site
FOR EACH ROW
EXECUTE FUNCTION bind_legacy_marketing_integrations_to_default_site();

CREATE INDEX IF NOT EXISTS idx_marketing_integration_site
  ON marketing_integration(site_id, provider_id, integration_type, enabled);

ALTER TABLE lead_event_outbox
  ADD COLUMN IF NOT EXISTS delivery_mode text NOT NULL DEFAULT 'live',
  ADD COLUMN IF NOT EXISTS provider_request_id text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS provider_received_at timestamptz,
  ADD COLUMN IF NOT EXISTS provider_processed_at timestamptz,
  ADD COLUMN IF NOT EXISTS match_status text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS last_attempt_at timestamptz,
  ADD COLUMN IF NOT EXISTS next_attempt_at timestamptz;

UPDATE lead_event_outbox
SET delivery_mode = 'validation'
WHERE status = 'validated';

UPDATE lead_event_outbox AS outbox
SET delivery_mode = 'test'
FROM marketing_integration AS integration
WHERE outbox.integration_id = integration.id
  AND COALESCE(integration.config_json ->> 'test_event_code', '') <> ''
  AND outbox.status <> 'validated';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conrelid = 'lead_event_outbox'::regclass
      AND conname = 'lead_event_outbox_delivery_mode_check'
  ) THEN
    ALTER TABLE lead_event_outbox
      ADD CONSTRAINT lead_event_outbox_delivery_mode_check
      CHECK (delivery_mode IN ('test', 'validation', 'live'));
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conrelid = 'lead_event_outbox'::regclass
      AND conname = 'lead_event_outbox_match_status_check'
  ) THEN
    ALTER TABLE lead_event_outbox
      ADD CONSTRAINT lead_event_outbox_match_status_check
      CHECK (match_status IN ('unknown', 'not_available', 'matched', 'partial', 'unmatched'));
  END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS idx_lead_event_outbox_delivery_health
  ON lead_event_outbox(integration_id, delivery_mode, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_lead_event_outbox_retry_schedule
  ON lead_event_outbox(next_attempt_at, status)
  WHERE status = 'failed' AND next_attempt_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS marketing_integration_check (
  id bigserial PRIMARY KEY,
  integration_id bigint NOT NULL REFERENCES marketing_integration(id) ON DELETE CASCADE,
  actor_user_id integer REFERENCES auth_user(id) ON DELETE SET NULL,
  check_type text NOT NULL,
  delivery_mode text NOT NULL DEFAULT 'live',
  status text NOT NULL DEFAULT 'unknown',
  evidence_level text NOT NULL DEFAULT 'local',
  summary text NOT NULL DEFAULT '',
  details_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  checked_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT marketing_integration_check_type_check CHECK (
    check_type IN ('configuration', 'connection', 'test_event', 'webhook', 'delivery')
  ),
  CONSTRAINT marketing_integration_check_mode_check CHECK (
    delivery_mode IN ('test', 'validation', 'live')
  ),
  CONSTRAINT marketing_integration_check_status_check CHECK (
    status IN ('pending', 'passed', 'warning', 'failed', 'unknown')
  ),
  CONSTRAINT marketing_integration_check_evidence_check CHECK (
    evidence_level IN ('local', 'provider_response', 'provider_processed')
  )
);

CREATE INDEX IF NOT EXISTS idx_marketing_integration_check_latest
  ON marketing_integration_check(integration_id, check_type, checked_at DESC);

CREATE TABLE IF NOT EXISTS lead_consent_record (
  id bigserial PRIMARY KEY,
  submission_id bigint NOT NULL REFERENCES lead_submission(id) ON DELETE CASCADE,
  actor_user_id integer REFERENCES auth_user(id) ON DELETE SET NULL,
  purpose text NOT NULL,
  decision text NOT NULL,
  source text NOT NULL DEFAULT 'website_form',
  policy_version text NOT NULL DEFAULT '',
  evidence_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  captured_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT lead_consent_record_purpose_check CHECK (
    purpose IN ('contact', 'privacy_notice', 'analytics', 'marketing', 'personalization')
  ),
  CONSTRAINT lead_consent_record_decision_check CHECK (
    decision IN ('granted', 'denied', 'withdrawn')
  )
);

CREATE INDEX IF NOT EXISTS idx_lead_consent_record_timeline
  ON lead_consent_record(submission_id, purpose, captured_at DESC);

INSERT INTO lead_consent_record(
  submission_id,
  purpose,
  decision,
  source,
  policy_version,
  evidence_json,
  captured_at
)
SELECT
  submission.id,
  consent.purpose,
  CASE
    WHEN lower(COALESCE(submission.consent_json ->> consent.purpose, '')) IN ('true', '1', 'yes', 'on')
      THEN 'granted'
    ELSE 'denied'
  END,
  COALESCE(NULLIF(submission.consent_json ->> 'source_contract', ''), 'legacy_snapshot'),
  COALESCE(submission.consent_json ->> 'policy_version', ''),
  jsonb_build_object('backfilled', true),
  submission.submitted_at
FROM lead_submission AS submission
CROSS JOIN (VALUES ('contact'), ('privacy_notice'), ('marketing')) AS consent(purpose)
WHERE submission.consent_json ? consent.purpose
  AND NOT EXISTS (
    SELECT 1
    FROM lead_consent_record AS existing
    WHERE existing.submission_id = submission.id
      AND existing.purpose = consent.purpose
  );

CREATE TABLE IF NOT EXISTS privacy_request (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  submission_id bigint REFERENCES lead_submission(id) ON DELETE SET NULL,
  handled_by_user_id integer REFERENCES auth_user(id) ON DELETE SET NULL,
  request_type text NOT NULL,
  status text NOT NULL DEFAULT 'pending',
  requester_name text NOT NULL DEFAULT '',
  requester_email text NOT NULL DEFAULT '',
  requester_phone text NOT NULL DEFAULT '',
  subject_key text NOT NULL DEFAULT '',
  request_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  resolution text NOT NULL DEFAULT '',
  requested_at timestamptz NOT NULL DEFAULT now(),
  verified_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT privacy_request_type_check CHECK (
    request_type IN ('export', 'delete', 'restrict', 'withdraw_consent')
  ),
  CONSTRAINT privacy_request_status_check CHECK (
    status IN ('pending', 'verified', 'processing', 'completed', 'rejected')
  )
);

CREATE INDEX IF NOT EXISTS idx_privacy_request_queue
  ON privacy_request(site_id, status, requested_at);

CREATE OR REPLACE FUNCTION prevent_marketing_evidence_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$;

DROP TRIGGER IF EXISTS marketing_integration_check_prevent_update_delete
  ON marketing_integration_check;
CREATE TRIGGER marketing_integration_check_prevent_update_delete
BEFORE UPDATE OR DELETE ON marketing_integration_check
FOR EACH ROW
EXECUTE FUNCTION prevent_marketing_evidence_mutation();

DROP TRIGGER IF EXISTS lead_consent_record_prevent_update_delete
  ON lead_consent_record;
CREATE TRIGGER lead_consent_record_prevent_update_delete
BEFORE UPDATE OR DELETE ON lead_consent_record
FOR EACH ROW
EXECUTE FUNCTION prevent_marketing_evidence_mutation();
