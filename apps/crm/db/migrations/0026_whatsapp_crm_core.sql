BEGIN;

ALTER TABLE privacy_retention_policy
  ADD COLUMN IF NOT EXISTS whatsapp_message_retention_days integer NOT NULL DEFAULT 1095,
  ADD COLUMN IF NOT EXISTS whatsapp_media_retention_days integer NOT NULL DEFAULT 365;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'privacy_retention_policy_whatsapp_message_days_check'
      AND conrelid = 'privacy_retention_policy'::regclass
  ) THEN
    ALTER TABLE privacy_retention_policy
      ADD CONSTRAINT privacy_retention_policy_whatsapp_message_days_check
      CHECK (whatsapp_message_retention_days BETWEEN 1 AND 36500);
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'privacy_retention_policy_whatsapp_media_days_check'
      AND conrelid = 'privacy_retention_policy'::regclass
  ) THEN
    ALTER TABLE privacy_retention_policy
      ADD CONSTRAINT privacy_retention_policy_whatsapp_media_days_check
      CHECK (whatsapp_media_retention_days BETWEEN 1 AND 36500);
  END IF;
END;
$$;

CREATE TABLE IF NOT EXISTS whatsapp_template (
  id bigserial PRIMARY KEY,
  integration_id bigint NOT NULL REFERENCES marketing_integration(id) ON DELETE CASCADE,
  name text NOT NULL,
  language text NOT NULL,
  category text NOT NULL DEFAULT 'utility',
  status text NOT NULL DEFAULT 'pending',
  provider_template_id text NOT NULL DEFAULT '',
  version integer NOT NULL DEFAULT 1,
  components_json jsonb NOT NULL DEFAULT '[]'::jsonb,
  quality_rating text NOT NULL DEFAULT '',
  rejection_reason text NOT NULL DEFAULT '',
  synced_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT whatsapp_template_name_check CHECK (length(btrim(name)) BETWEEN 1 AND 512),
  CONSTRAINT whatsapp_template_language_check CHECK (language ~ '^[A-Za-z]{2,3}([_-][A-Za-z]{2})?$'),
  CONSTRAINT whatsapp_template_category_check CHECK (
    category IN ('authentication', 'marketing', 'utility')
  ),
  CONSTRAINT whatsapp_template_status_check CHECK (
    status IN ('pending', 'approved', 'paused', 'disabled', 'rejected', 'deleted')
  ),
  CONSTRAINT whatsapp_template_version_check CHECK (version >= 1),
  UNIQUE(integration_id, name, language, version)
);

CREATE INDEX IF NOT EXISTS idx_whatsapp_template_status
  ON whatsapp_template(integration_id, status, category, name, language, version DESC);

CREATE TABLE IF NOT EXISTS whatsapp_conversation (
  id bigserial PRIMARY KEY,
  integration_id bigint NOT NULL REFERENCES marketing_integration(id) ON DELETE RESTRICT,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  submission_id bigint REFERENCES lead_submission(id) ON DELETE SET NULL,
  company_id bigint REFERENCES crm_company(id) ON DELETE SET NULL,
  contact_id bigint REFERENCES crm_contact(id) ON DELETE SET NULL,
  owner_user_id integer REFERENCES auth_user(id) ON DELETE SET NULL,
  team_id bigint REFERENCES sales_team(id) ON DELETE SET NULL,
  external_contact_id text NOT NULL,
  display_name text NOT NULL DEFAULT '',
  status text NOT NULL DEFAULT 'open',
  unread_count integer NOT NULL DEFAULT 0,
  last_message_preview text NOT NULL DEFAULT '',
  last_message_at timestamptz,
  last_inbound_at timestamptz,
  last_outbound_at timestamptz,
  service_window_expires_at timestamptz,
  closed_at timestamptz,
  opted_out_at timestamptz,
  opt_out_reason text NOT NULL DEFAULT '',
  metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT whatsapp_conversation_contact_check CHECK (length(btrim(external_contact_id)) BETWEEN 3 AND 120),
  CONSTRAINT whatsapp_conversation_status_check CHECK (
    status IN ('open', 'pending', 'closed', 'blocked')
  ),
  CONSTRAINT whatsapp_conversation_unread_check CHECK (unread_count >= 0),
  CONSTRAINT whatsapp_conversation_closed_check CHECK (
    (status = 'closed' AND closed_at IS NOT NULL) OR status <> 'closed'
  ),
  CONSTRAINT whatsapp_conversation_opt_out_check CHECK (
    opted_out_at IS NULL OR length(btrim(opt_out_reason)) > 0
  ),
  UNIQUE(integration_id, external_contact_id)
);

CREATE INDEX IF NOT EXISTS idx_whatsapp_conversation_owner_queue
  ON whatsapp_conversation(site_id, owner_user_id, status, unread_count DESC, last_message_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_whatsapp_conversation_team_queue
  ON whatsapp_conversation(site_id, team_id, status, unread_count DESC, last_message_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_whatsapp_conversation_submission
  ON whatsapp_conversation(submission_id)
  WHERE submission_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_whatsapp_conversation_contact
  ON whatsapp_conversation(contact_id)
  WHERE contact_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS whatsapp_message (
  id bigserial PRIMARY KEY,
  message_key uuid NOT NULL DEFAULT gen_random_uuid(),
  conversation_id bigint NOT NULL REFERENCES whatsapp_conversation(id) ON DELETE CASCADE,
  inbound_event_id bigint REFERENCES lead_inbound_event(id) ON DELETE SET NULL,
  template_id bigint REFERENCES whatsapp_template(id) ON DELETE SET NULL,
  reply_to_id bigint REFERENCES whatsapp_message(id) ON DELETE SET NULL,
  actor_user_id integer REFERENCES auth_user(id) ON DELETE SET NULL,
  direction text NOT NULL,
  message_type text NOT NULL DEFAULT 'text',
  external_message_id text NOT NULL DEFAULT '',
  idempotency_key char(64) NOT NULL DEFAULT '',
  request_fingerprint char(64) NOT NULL DEFAULT '',
  provider_request_id text NOT NULL DEFAULT '',
  sender_id text NOT NULL DEFAULT '',
  recipient_id text NOT NULL DEFAULT '',
  body text NOT NULL DEFAULT '',
  status text NOT NULL DEFAULT 'queued',
  error_code text NOT NULL DEFAULT '',
  error_message text NOT NULL DEFAULT '',
  retryable boolean NOT NULL DEFAULT false,
  attempts integer NOT NULL DEFAULT 0,
  next_attempt_at timestamptz,
  payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  provider_timestamp timestamptz,
  queued_at timestamptz NOT NULL DEFAULT now(),
  sent_at timestamptz,
  delivered_at timestamptz,
  read_at timestamptz,
  failed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT whatsapp_message_direction_check CHECK (
    direction IN ('inbound', 'outbound', 'internal')
  ),
  CONSTRAINT whatsapp_message_type_check CHECK (
    message_type IN ('text', 'image', 'video', 'audio', 'document', 'sticker', 'button', 'interactive', 'template', 'location', 'contacts', 'system', 'unknown')
  ),
  CONSTRAINT whatsapp_message_status_check CHECK (
    status IN ('draft', 'queued', 'sending', 'sent', 'delivered', 'read', 'received', 'recorded', 'failed', 'canceled')
  ),
  CONSTRAINT whatsapp_message_idempotency_check CHECK (
    idempotency_key = '' OR idempotency_key ~ '^[0-9a-f]{64}$'
  ),
  CONSTRAINT whatsapp_message_request_fingerprint_check CHECK (
    request_fingerprint = '' OR request_fingerprint ~ '^[0-9a-f]{64}$'
  ),
  CONSTRAINT whatsapp_message_idempotent_request_check CHECK (
    (idempotency_key = '' AND request_fingerprint = '')
    OR (idempotency_key <> '' AND request_fingerprint <> '')
  ),
  CONSTRAINT whatsapp_message_failure_check CHECK (
    (status = 'failed' AND failed_at IS NOT NULL AND length(btrim(error_message)) > 0)
    OR status <> 'failed'
  ),
  CONSTRAINT whatsapp_message_attempts_check CHECK (attempts >= 0),
  CONSTRAINT whatsapp_message_retry_check CHECK (
    NOT retryable OR (direction = 'outbound' AND status = 'failed')
  ),
  CONSTRAINT whatsapp_message_template_check CHECK (
    message_type <> 'template' OR template_id IS NOT NULL
  ),
  CONSTRAINT whatsapp_message_internal_check CHECK (
    direction <> 'internal' OR status IN ('recorded', 'canceled')
  ),
  UNIQUE(message_key)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_whatsapp_message_external
  ON whatsapp_message(external_message_id)
  WHERE external_message_id <> '';
CREATE UNIQUE INDEX IF NOT EXISTS uq_whatsapp_message_idempotency
  ON whatsapp_message(idempotency_key)
  WHERE idempotency_key <> '';
CREATE INDEX IF NOT EXISTS idx_whatsapp_message_timeline
  ON whatsapp_message(conversation_id, provider_timestamp DESC, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_whatsapp_message_delivery_queue
  ON whatsapp_message(status, next_attempt_at, queued_at, id)
  WHERE direction = 'outbound' AND status IN ('queued', 'sending', 'failed');

CREATE TABLE IF NOT EXISTS whatsapp_media (
  id bigserial PRIMARY KEY,
  message_id bigint NOT NULL REFERENCES whatsapp_message(id) ON DELETE CASCADE,
  external_media_id text NOT NULL DEFAULT '',
  media_type text NOT NULL,
  mime_type text NOT NULL DEFAULT '',
  original_name text NOT NULL DEFAULT '',
  file_size_bytes bigint NOT NULL DEFAULT 0,
  sha256 char(64) NOT NULL DEFAULT '',
  storage_path text NOT NULL DEFAULT '',
  status text NOT NULL DEFAULT 'pending',
  error_message text NOT NULL DEFAULT '',
  attempts integer NOT NULL DEFAULT 0,
  next_attempt_at timestamptz,
  downloaded_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT whatsapp_media_type_check CHECK (
    media_type IN ('image', 'video', 'audio', 'document', 'sticker')
  ),
  CONSTRAINT whatsapp_media_status_check CHECK (
    status IN ('pending', 'downloading', 'ready', 'failed', 'quarantined', 'deleted')
  ),
  CONSTRAINT whatsapp_media_size_check CHECK (file_size_bytes >= 0),
  CONSTRAINT whatsapp_media_attempts_check CHECK (attempts >= 0),
  CONSTRAINT whatsapp_media_sha_check CHECK (sha256 = '' OR sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT whatsapp_media_ready_check CHECK (
    status <> 'ready' OR (length(btrim(storage_path)) > 0 AND file_size_bytes > 0)
  )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_whatsapp_media_external
  ON whatsapp_media(external_media_id)
  WHERE external_media_id <> '';
CREATE INDEX IF NOT EXISTS idx_whatsapp_media_status
  ON whatsapp_media(status, next_attempt_at, created_at, id);

CREATE TABLE IF NOT EXISTS whatsapp_delivery_event (
  id bigserial PRIMARY KEY,
  message_id bigint NOT NULL REFERENCES whatsapp_message(id) ON DELETE CASCADE,
  inbound_event_id bigint REFERENCES lead_inbound_event(id) ON DELETE SET NULL,
  event_fingerprint char(64) NOT NULL,
  status text NOT NULL,
  error_code text NOT NULL DEFAULT '',
  payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  occurred_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT whatsapp_delivery_event_fingerprint_check CHECK (
    event_fingerprint ~ '^[0-9a-f]{64}$'
  ),
  CONSTRAINT whatsapp_delivery_event_status_check CHECK (
    status IN ('sent', 'delivered', 'read', 'failed', 'deleted', 'warning')
  ),
  UNIQUE(event_fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_whatsapp_delivery_event_message
  ON whatsapp_delivery_event(message_id, occurred_at DESC, id DESC);

CREATE OR REPLACE FUNCTION validate_whatsapp_conversation_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  integration_site_id bigint;
  provider_code text;
  integration_kind text;
  target_site_id bigint;
  target_team_id bigint;
  company_team_id bigint;
BEGIN
  SELECT mi.site_id, mp.code, mi.integration_type
    INTO integration_site_id, provider_code, integration_kind
  FROM marketing_integration mi
  JOIN marketing_provider mp ON mp.id = mi.provider_id
  WHERE mi.id = NEW.integration_id;

  IF NOT FOUND OR provider_code <> 'whatsapp' OR integration_kind <> 'cloud_api' THEN
    RAISE EXCEPTION 'WhatsApp conversation requires a WhatsApp Cloud API integration';
  END IF;
  IF integration_site_id IS DISTINCT FROM NEW.site_id THEN
    RAISE EXCEPTION 'WhatsApp conversation site must match its integration site';
  END IF;

  IF NEW.submission_id IS NOT NULL THEN
    SELECT site_id, team_id INTO target_site_id, target_team_id
    FROM lead_submission WHERE id = NEW.submission_id;
    IF NOT FOUND OR target_site_id <> NEW.site_id THEN
      RAISE EXCEPTION 'WhatsApp conversation lead must belong to its site';
    END IF;
    IF NEW.team_id IS NOT NULL AND target_team_id IS NOT NULL AND NEW.team_id <> target_team_id THEN
      RAISE EXCEPTION 'WhatsApp conversation team must match its lead team';
    END IF;
  END IF;

  IF NEW.company_id IS NOT NULL THEN
    SELECT site_id, team_id INTO target_site_id, company_team_id
    FROM crm_company WHERE id = NEW.company_id;
    IF NOT FOUND OR target_site_id <> NEW.site_id THEN
      RAISE EXCEPTION 'WhatsApp conversation company must belong to its site';
    END IF;
    IF NEW.team_id IS NOT NULL AND company_team_id IS NOT NULL AND NEW.team_id <> company_team_id THEN
      RAISE EXCEPTION 'WhatsApp conversation team must match its company team';
    END IF;
  END IF;

  IF NEW.contact_id IS NOT NULL THEN
    SELECT site_id, team_id INTO target_site_id, target_team_id
    FROM crm_contact WHERE id = NEW.contact_id;
    IF NOT FOUND OR target_site_id <> NEW.site_id THEN
      RAISE EXCEPTION 'WhatsApp conversation contact must belong to its site';
    END IF;
    IF NEW.team_id IS NOT NULL AND target_team_id IS NOT NULL AND NEW.team_id <> target_team_id THEN
      RAISE EXCEPTION 'WhatsApp conversation team must match its contact team';
    END IF;
  END IF;

  IF NEW.team_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sales_team WHERE id = NEW.team_id AND site_id = NEW.site_id AND enabled
  ) THEN
    RAISE EXCEPTION 'WhatsApp conversation team must be active and belong to its site';
  END IF;

  IF NEW.owner_user_id IS NOT NULL AND NEW.team_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sales_team_member
    WHERE team_id = NEW.team_id AND user_id = NEW.owner_user_id
  ) THEN
    RAISE EXCEPTION 'WhatsApp conversation owner must be a member of its team';
  END IF;
  IF NEW.owner_user_id IS NOT NULL AND NEW.team_id IS NULL THEN
    RAISE EXCEPTION 'WhatsApp conversation owner requires a team';
  END IF;

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS whatsapp_conversation_validate_scope ON whatsapp_conversation;
CREATE TRIGGER whatsapp_conversation_validate_scope
BEFORE INSERT OR UPDATE OF integration_id, site_id, submission_id, company_id, contact_id, owner_user_id, team_id
ON whatsapp_conversation
FOR EACH ROW EXECUTE FUNCTION validate_whatsapp_conversation_scope();

CREATE OR REPLACE FUNCTION validate_whatsapp_message_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  conversation_integration_id bigint;
  conversation_submission_id bigint;
  related_conversation_id bigint;
  related_integration_id bigint;
  event_integration_id bigint;
  event_submission_id bigint;
BEGIN
  SELECT integration_id, submission_id
    INTO conversation_integration_id, conversation_submission_id
  FROM whatsapp_conversation
  WHERE id = NEW.conversation_id;

  IF NEW.template_id IS NOT NULL THEN
    SELECT integration_id INTO related_integration_id
    FROM whatsapp_template WHERE id = NEW.template_id;
    IF NOT FOUND OR related_integration_id <> conversation_integration_id THEN
      RAISE EXCEPTION 'WhatsApp message template must belong to its conversation integration';
    END IF;
  END IF;

  IF NEW.reply_to_id IS NOT NULL THEN
    SELECT conversation_id INTO related_conversation_id
    FROM whatsapp_message WHERE id = NEW.reply_to_id;
    IF NOT FOUND OR related_conversation_id <> NEW.conversation_id THEN
      RAISE EXCEPTION 'WhatsApp reply target must belong to the same conversation';
    END IF;
  END IF;

  IF NEW.inbound_event_id IS NOT NULL THEN
    SELECT integration_id, submission_id
      INTO event_integration_id, event_submission_id
    FROM lead_inbound_event WHERE id = NEW.inbound_event_id;
    IF NOT FOUND OR event_integration_id <> conversation_integration_id THEN
      RAISE EXCEPTION 'WhatsApp inbound receipt must belong to its conversation integration';
    END IF;
    IF event_submission_id IS NOT NULL
       AND conversation_submission_id IS NOT NULL
       AND event_submission_id <> conversation_submission_id THEN
      RAISE EXCEPTION 'WhatsApp inbound receipt lead must match its conversation lead';
    END IF;
  END IF;

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS whatsapp_message_validate_scope ON whatsapp_message;
CREATE TRIGGER whatsapp_message_validate_scope
BEFORE INSERT OR UPDATE OF conversation_id, inbound_event_id, template_id, reply_to_id
ON whatsapp_message
FOR EACH ROW EXECUTE FUNCTION validate_whatsapp_message_scope();

CREATE OR REPLACE FUNCTION prevent_whatsapp_delivery_event_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  -- Retention deletes the parent message/conversation and must be able to
  -- cascade through evidence rows. Direct edits and direct deletes remain
  -- forbidden so operational history cannot be rewritten selectively.
  IF TG_OP = 'DELETE' AND pg_trigger_depth() > 1 THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'whatsapp_delivery_event is append-only';
END;
$$;

DROP TRIGGER IF EXISTS whatsapp_delivery_event_prevent_update_delete ON whatsapp_delivery_event;
CREATE TRIGGER whatsapp_delivery_event_prevent_update_delete
BEFORE UPDATE OR DELETE ON whatsapp_delivery_event
FOR EACH ROW EXECUTE FUNCTION prevent_whatsapp_delivery_event_mutation();

COMMIT;
