ALTER TABLE lead_submission
  ADD COLUMN IF NOT EXISTS trashed_at timestamptz,
  ADD COLUMN IF NOT EXISTS trashed_by_user_id integer,
  ADD COLUMN IF NOT EXISTS trash_reason text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS restored_at timestamptz,
  ADD COLUMN IF NOT EXISTS restored_by_user_id integer;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conrelid = 'lead_submission'::regclass
      AND conname = 'lead_submission_trashed_by_user_fk'
  ) THEN
    ALTER TABLE lead_submission
      ADD CONSTRAINT lead_submission_trashed_by_user_fk
      FOREIGN KEY (trashed_by_user_id) REFERENCES auth_user(id) ON DELETE SET NULL;
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conrelid = 'lead_submission'::regclass
      AND conname = 'lead_submission_restored_by_user_fk'
  ) THEN
    ALTER TABLE lead_submission
      ADD CONSTRAINT lead_submission_restored_by_user_fk
      FOREIGN KEY (restored_by_user_id) REFERENCES auth_user(id) ON DELETE SET NULL;
  END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS idx_lead_submission_active_queue
  ON lead_submission(site_id, stage, submitted_at DESC, id DESC)
  WHERE trashed_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_lead_submission_trash_queue
  ON lead_submission(site_id, trashed_at DESC, id DESC)
  WHERE trashed_at IS NOT NULL;
