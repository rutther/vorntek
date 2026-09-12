ALTER TABLE lead_event_outbox
  DROP CONSTRAINT IF EXISTS lead_event_outbox_status_check;

ALTER TABLE lead_event_outbox
  ADD CONSTRAINT lead_event_outbox_status_check CHECK (
    status IN (
      'pending',
      'sending',
      'processing',
      'validated',
      'sent',
      'failed',
      'skipped'
    )
  );
