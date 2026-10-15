-- Additive per-execution facts; existing event IDs, routing and payloads remain.
ALTER TABLE lead_event_outbox DROP CONSTRAINT IF EXISTS lead_event_outbox_status_check;
ALTER TABLE lead_event_outbox ADD CONSTRAINT lead_event_outbox_status_check
  CHECK (status IN ('pending','sending','processing','sent','failed','skipped','validated','partial','unknown'));

CREATE TABLE lead_event_attempt (
  id uuid PRIMARY KEY,
  outbox_id bigint NOT NULL REFERENCES lead_event_outbox(id) ON DELETE CASCADE,
  generation integer NOT NULL CHECK (generation > 0),
  attempt_number integer NOT NULL CHECK (attempt_number > 0),
  state varchar(16) NOT NULL DEFAULT 'claimed',
  lead_version timestamptz NOT NULL,
  integration_version timestamptz NOT NULL,
  event_id varchar(120) NOT NULL,
  public_id varchar(255) NOT NULL,
  payload_hash varchar(64) NOT NULL,
  delivery_mode varchar(16) NOT NULL,
  result_status varchar(32) NOT NULL DEFAULT '',
  response_json jsonb NOT NULL DEFAULT '{}',
  provider_request_id text NOT NULL DEFAULT '',
  error text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  handoff_at timestamptz,
  accepted_at timestamptz,
  finished_at timestamptz,
  CONSTRAINT event_attempt_generation_unique UNIQUE(outbox_id,generation),
  CONSTRAINT event_attempt_state_check CHECK(state IN ('claimed','handoff','accepted','failed','unknown','cancelled')),
  CONSTRAINT event_attempt_receipt_check CHECK(
    (state <> 'accepted' OR (handoff_at IS NOT NULL AND accepted_at IS NOT NULL AND finished_at IS NOT NULL))
    AND (state NOT IN ('handoff','unknown') OR handoff_at IS NOT NULL)),
  CONSTRAINT event_attempt_result_check CHECK(result_status IN ('','pending','sent','failed','skipped','unknown','processing','validated','partial'))
);
CREATE UNIQUE INDEX event_attempt_one_active ON lead_event_attempt(outbox_id)
  WHERE state IN ('claimed','handoff','unknown');

CREATE OR REPLACE FUNCTION guard_event_attempt_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW.id,NEW.outbox_id,NEW.generation,NEW.attempt_number,NEW.created_at,
      NEW.lead_version,NEW.integration_version,NEW.event_id,NEW.public_id,NEW.payload_hash,NEW.delivery_mode)
     IS DISTINCT FROM
     (OLD.id,OLD.outbox_id,OLD.generation,OLD.attempt_number,OLD.created_at,
      OLD.lead_version,OLD.integration_version,OLD.event_id,OLD.public_id,OLD.payload_hash,OLD.delivery_mode) THEN
    RAISE EXCEPTION 'marketing attempt identity is immutable';
  END IF;
  IF OLD.state IN ('accepted','failed','cancelled') THEN
    RAISE EXCEPTION 'marketing attempt terminal result is immutable';
  END IF;
  IF NOT ((OLD.state='claimed' AND NEW.state IN ('handoff','failed','cancelled'))
      OR (OLD.state='handoff' AND NEW.state IN ('accepted','failed','unknown'))
      OR (OLD.state='unknown' AND NEW.state IN ('accepted','failed'))) THEN
    RAISE EXCEPTION 'invalid marketing attempt transition';
  END IF;
  IF OLD.handoff_at IS NOT NULL AND NEW.handoff_at IS DISTINCT FROM OLD.handoff_at THEN
    RAISE EXCEPTION 'marketing handoff identity is immutable';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER event_attempt_update_guard BEFORE UPDATE ON lead_event_attempt
FOR EACH ROW EXECUTE FUNCTION guard_event_attempt_update();
