ALTER TABLE lead_submission
  ADD COLUMN IF NOT EXISTS assignee_id integer REFERENCES auth_user(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS source_channel text NOT NULL DEFAULT 'website',
  ADD COLUMN IF NOT EXISTS source_detail text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS follow_up_notes text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS next_follow_up_at timestamptz,
  ADD COLUMN IF NOT EXISTS contacted_at timestamptz,
  ADD COLUMN IF NOT EXISTS contact_key text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS dedupe_key text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS duplicate_of_id bigint,
  ADD COLUMN IF NOT EXISTS spam_score integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS spam_reason text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS notification_status text NOT NULL DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS notification_error text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS notified_at timestamptz,
  ADD COLUMN IF NOT EXISTS reminder_sent_at timestamptz;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'lead_submission_duplicate_of_fk'
      AND conrelid = 'lead_submission'::regclass
  ) THEN
    ALTER TABLE lead_submission
      ADD CONSTRAINT lead_submission_duplicate_of_fk
      FOREIGN KEY (duplicate_of_id) REFERENCES lead_submission(id) ON DELETE SET NULL;
  END IF;
END
$$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'lead_submission_notification_status_check'
      AND conrelid = 'lead_submission'::regclass
  ) THEN
    ALTER TABLE lead_submission
      ADD CONSTRAINT lead_submission_notification_status_check CHECK (
        notification_status IN ('pending', 'sent', 'failed', 'disabled')
      );
  END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_lead_submission_assignee
  ON lead_submission(site_id, assignee_id, stage, submitted_at DESC);

CREATE INDEX IF NOT EXISTS idx_lead_submission_follow_up
  ON lead_submission(site_id, next_follow_up_at, stage);

CREATE INDEX IF NOT EXISTS idx_lead_submission_contact_key
  ON lead_submission(site_id, contact_key, submitted_at DESC)
  WHERE contact_key <> '';

CREATE INDEX IF NOT EXISTS idx_lead_submission_dedupe_key
  ON lead_submission(site_id, dedupe_key, submitted_at DESC)
  WHERE dedupe_key <> '';

CREATE INDEX IF NOT EXISTS idx_lead_submission_notification
  ON lead_submission(site_id, notification_status, reminder_sent_at, submitted_at DESC);
