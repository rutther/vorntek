ALTER TABLE lead_form_definition
  ADD COLUMN IF NOT EXISTS qualified_event_name text NOT NULL DEFAULT 'QualifiedLead';

ALTER TABLE lead_submission
  ADD COLUMN IF NOT EXISTS qualified_at timestamptz,
  ADD COLUMN IF NOT EXISTS won_at timestamptz,
  ADD COLUMN IF NOT EXISTS lost_at timestamptz,
  ADD COLUMN IF NOT EXISTS qualification_score integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS qualification_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS qualification_notes text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS qualification_overridden boolean NOT NULL DEFAULT false;

ALTER TABLE lead_submission
  DROP CONSTRAINT IF EXISTS lead_submission_qualification_score_check;

ALTER TABLE lead_submission
  ADD CONSTRAINT lead_submission_qualification_score_check CHECK (
    qualification_score BETWEEN 0 AND 100
  );

ALTER TABLE lead_event_outbox
  DROP CONSTRAINT IF EXISTS lead_event_outbox_action_source_check;

ALTER TABLE lead_event_outbox
  ADD CONSTRAINT lead_event_outbox_action_source_check CHECK (
    action_source IN (
      'website',
      'system_generated',
      'business_messaging',
      'phone_call',
      'chat',
      'email',
      'other'
    )
  );

CREATE INDEX IF NOT EXISTS idx_lead_submission_qualified
  ON lead_submission(site_id, qualified_at DESC)
  WHERE qualified_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_lead_submission_won
  ON lead_submission(site_id, won_at DESC)
  WHERE won_at IS NOT NULL;
