-- Correlate SMTP logs with the exact prepared attempt; legacy rows stay empty.
ALTER TABLE lead_notification_attempt
  ADD COLUMN IF NOT EXISTS message_id varchar(512) NOT NULL DEFAULT '';

CREATE OR REPLACE FUNCTION guard_notification_message_id_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.handoff_at IS NOT NULL AND NEW.message_id IS DISTINCT FROM OLD.message_id THEN
    RAISE EXCEPTION 'notification SMTP message identity is immutable after handoff';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER notification_message_id_update_guard BEFORE UPDATE ON lead_notification_attempt
FOR EACH ROW EXECUTE FUNCTION guard_notification_message_id_update();
