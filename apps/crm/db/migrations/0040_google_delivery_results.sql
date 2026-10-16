-- Accepted upload and final processing are separate immutable facts.
-- Legacy accepted requests have generation 0, not a fabricated upload attempt.
CREATE TABLE lead_event_result (
  id uuid PRIMARY KEY,
  outbox_id bigint NOT NULL REFERENCES lead_event_outbox(id) ON DELETE CASCADE,
  attempt_id uuid REFERENCES lead_event_attempt(id) ON DELETE CASCADE,
  generation integer NOT NULL CHECK (generation >= 0),
  attempt_number integer NOT NULL CHECK (attempt_number >= 0),
  provider_request_id text NOT NULL CHECK (length(provider_request_id) > 0),
  status varchar(32) NOT NULL CHECK (status IN ('sent','failed','partial')),
  response_json jsonb NOT NULL DEFAULT '{}',
  error text NOT NULL DEFAULT '',
  completed_at timestamptz NOT NULL,
  CONSTRAINT event_result_request_unique UNIQUE(outbox_id,generation,attempt_number,provider_request_id),
  CONSTRAINT event_result_generation_check CHECK (
    (attempt_id IS NULL AND generation=0) OR (attempt_id IS NOT NULL AND generation>0))
);

CREATE OR REPLACE FUNCTION guard_event_result() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE accepted lead_event_attempt;
BEGIN
  IF TG_OP='UPDATE' THEN
    RAISE EXCEPTION 'provider terminal result is immutable';
  END IF;
  IF NEW.attempt_id IS NOT NULL THEN
    SELECT * INTO accepted FROM lead_event_attempt WHERE id=NEW.attempt_id;
    IF accepted.state <> 'accepted' OR accepted.result_status <> 'processing'
       OR (NEW.outbox_id,NEW.generation,NEW.attempt_number,NEW.provider_request_id)
         IS DISTINCT FROM
          (accepted.outbox_id,accepted.generation,accepted.attempt_number,accepted.provider_request_id) THEN
      RAISE EXCEPTION 'provider result does not identify its accepted upload';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER event_result_guard BEFORE INSERT OR UPDATE ON lead_event_result
FOR EACH ROW EXECUTE FUNCTION guard_event_result();
