CREATE TABLE IF NOT EXISTS admin_secret_store (
  id bigserial PRIMARY KEY,
  secret_key text NOT NULL UNIQUE,
  secret_cipher bytea NOT NULL,
  last4 text NOT NULL DEFAULT '',
  updated_by text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_admin_secret_store_updated_at
  ON admin_secret_store(updated_at DESC);
