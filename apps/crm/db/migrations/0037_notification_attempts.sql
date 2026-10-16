-- Additive delivery facts; legacy notification_status values remain unchanged.
CREATE TABLE IF NOT EXISTS lead_notification_attempt (
  id uuid PRIMARY KEY,
  submission_id bigint NOT NULL REFERENCES lead_submission(id) ON DELETE CASCADE,
  kind varchar(16) NOT NULL,
  generation integer NOT NULL CHECK (generation > 0),
  state varchar(16) NOT NULL DEFAULT 'claimed',
  lead_version timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  handoff_at timestamptz,
  accepted_at timestamptz,
  finished_at timestamptz,
  message_hash varchar(64) NOT NULL DEFAULT '',
  error text NOT NULL DEFAULT '',
  policy_disabled boolean NOT NULL DEFAULT false,
  CONSTRAINT notification_attempt_generation_unique UNIQUE (submission_id, kind, generation),
  CONSTRAINT notification_attempt_kind_check CHECK (kind IN ('initial', 'reminder')),
  CONSTRAINT notification_attempt_state_check CHECK (state IN ('claimed', 'handoff', 'accepted', 'failed', 'unknown', 'cancelled')),
  CONSTRAINT notification_attempt_receipt_check CHECK (
    (state <> 'accepted' OR (accepted_at IS NOT NULL AND handoff_at IS NOT NULL AND finished_at IS NOT NULL))
    AND (state NOT IN ('handoff', 'unknown') OR handoff_at IS NOT NULL)
  )
);

CREATE UNIQUE INDEX IF NOT EXISTS notification_attempt_one_active
  ON lead_notification_attempt(submission_id)
  WHERE state IN ('claimed', 'handoff', 'unknown');
CREATE INDEX IF NOT EXISTS idx_notification_attempt_history
  ON lead_notification_attempt(submission_id, kind, created_at DESC);

CREATE OR REPLACE FUNCTION guard_notification_attempt_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.id IS DISTINCT FROM OLD.id
     OR NEW.submission_id IS DISTINCT FROM OLD.submission_id
     OR NEW.kind IS DISTINCT FROM OLD.kind
     OR NEW.generation IS DISTINCT FROM OLD.generation
     OR NEW.created_at IS DISTINCT FROM OLD.created_at
     OR NEW.lead_version IS DISTINCT FROM OLD.lead_version THEN
    RAISE EXCEPTION 'notification attempt identity is immutable';
  END IF;
  IF OLD.state IN ('accepted', 'failed', 'cancelled') THEN
    RAISE EXCEPTION 'notification attempt terminal result is immutable';
  END IF;
  IF NOT (
    (OLD.state = 'claimed' AND NEW.state IN ('handoff', 'failed', 'cancelled'))
    OR (OLD.state = 'handoff' AND NEW.state IN ('accepted', 'failed', 'unknown'))
    OR (OLD.state = 'unknown' AND NEW.state IN ('accepted', 'failed'))
  ) THEN
    RAISE EXCEPTION 'invalid notification attempt transition';
  END IF;
  IF OLD.handoff_at IS NOT NULL AND (
    NEW.handoff_at IS DISTINCT FROM OLD.handoff_at
    OR NEW.message_hash IS DISTINCT FROM OLD.message_hash
  ) THEN
    RAISE EXCEPTION 'notification handoff identity is immutable';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER notification_attempt_update_guard BEFORE UPDATE ON lead_notification_attempt
FOR EACH ROW EXECUTE FUNCTION guard_notification_attempt_update();
