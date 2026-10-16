-- Ownership ledger for optional synthetic examples; no customer or account data.
-- Existing SQL and existing business rows are intentionally unchanged.
CREATE TABLE site_example_bundle (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE RESTRICT,
  pack_key varchar(80) NOT NULL,
  source_digest varchar(64) NOT NULL,
  state varchar(16) NOT NULL DEFAULT 'active',
  manifest_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT site_example_bundle_identity UNIQUE (site_id, pack_key),
  CONSTRAINT site_example_bundle_key CHECK (pack_key ~ '^[a-z][a-z0-9_-]{0,79}$'),
  CONSTRAINT site_example_bundle_digest CHECK (source_digest ~ '^[a-f0-9]{64}$'),
  CONSTRAINT site_example_bundle_state CHECK (state IN ('active', 'reset')),
  CONSTRAINT site_example_bundle_manifest CHECK (jsonb_typeof(manifest_json) = 'object')
);
