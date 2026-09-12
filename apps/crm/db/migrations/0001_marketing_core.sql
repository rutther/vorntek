CREATE TABLE IF NOT EXISTS marketing_provider (
  id bigserial PRIMARY KEY,
  code text NOT NULL UNIQUE,
  name text NOT NULL,
  enabled boolean NOT NULL DEFAULT true,
  capabilities jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT marketing_provider_code_format CHECK (code ~ '^[a-z][a-z0-9_]*$')
);

CREATE TABLE IF NOT EXISTS marketing_integration (
  id bigserial PRIMARY KEY,
  provider_id bigint NOT NULL REFERENCES marketing_provider(id) ON DELETE RESTRICT,
  name text NOT NULL,
  integration_type text NOT NULL,
  public_id text NOT NULL,
  secret_ref text,
  consent_category text NOT NULL DEFAULT 'marketing',
  enabled boolean NOT NULL DEFAULT false,
  config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT marketing_integration_type_format CHECK (integration_type ~ '^[a-z][a-z0-9_]*$'),
  CONSTRAINT marketing_integration_consent_category CHECK (
    consent_category IN ('necessary', 'analytics', 'marketing', 'personalization')
  )
);

CREATE INDEX IF NOT EXISTS idx_marketing_integration_provider
  ON marketing_integration(provider_id, enabled);

CREATE TABLE IF NOT EXISTS canonical_event (
  id bigserial PRIMARY KEY,
  code text NOT NULL UNIQUE,
  name text NOT NULL,
  description text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT canonical_event_code_format CHECK (code ~ '^[a-z][a-z0-9_]*$')
);

CREATE TABLE IF NOT EXISTS provider_event_mapping (
  id bigserial PRIMARY KEY,
  provider_id bigint NOT NULL REFERENCES marketing_provider(id) ON DELETE RESTRICT,
  canonical_event_id bigint NOT NULL REFERENCES canonical_event(id) ON DELETE RESTRICT,
  provider_event_name text NOT NULL,
  enabled boolean NOT NULL DEFAULT true,
  config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(provider_id, canonical_event_id, provider_event_name)
);

CREATE INDEX IF NOT EXISTS idx_provider_event_mapping_lookup
  ON provider_event_mapping(provider_id, canonical_event_id, enabled);

CREATE TABLE IF NOT EXISTS tracking_rule (
  id bigserial PRIMARY KEY,
  integration_id bigint NOT NULL REFERENCES marketing_integration(id) ON DELETE CASCADE,
  canonical_event_id bigint REFERENCES canonical_event(id) ON DELETE RESTRICT,
  scope_type text NOT NULL,
  scope_value text NOT NULL DEFAULT '*',
  priority integer NOT NULL DEFAULT 100,
  enabled boolean NOT NULL DEFAULT true,
  config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT tracking_rule_scope_type CHECK (
    scope_type IN ('global', 'locale', 'page_type', 'page_path', 'campaign')
  )
);

CREATE INDEX IF NOT EXISTS idx_tracking_rule_scope
  ON tracking_rule(scope_type, scope_value, enabled, priority);

CREATE INDEX IF NOT EXISTS idx_tracking_rule_integration
  ON tracking_rule(integration_id, enabled);

CREATE TABLE IF NOT EXISTS audit_log (
  id bigserial PRIMARY KEY,
  actor text NOT NULL DEFAULT 'system',
  action text NOT NULL,
  entity_table text NOT NULL,
  entity_id bigint,
  before_json jsonb,
  after_json jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_log_entity
  ON audit_log(entity_table, entity_id, created_at DESC);
