CREATE TABLE IF NOT EXISTS privacy_retention_policy (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL UNIQUE REFERENCES site(id) ON DELETE CASCADE,
  enabled boolean NOT NULL DEFAULT false,
  lead_pii_retention_days integer NOT NULL DEFAULT 0,
  inbound_payload_retention_days integer NOT NULL DEFAULT 90,
  outbox_payload_retention_days integer NOT NULL DEFAULT 180,
  privacy_request_retention_days integer NOT NULL DEFAULT 365,
  updated_by_user_id integer REFERENCES auth_user(id) ON DELETE SET NULL,
  config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT privacy_retention_policy_lead_days_check CHECK (
    lead_pii_retention_days BETWEEN 0 AND 36500
  ),
  CONSTRAINT privacy_retention_policy_inbound_days_check CHECK (
    inbound_payload_retention_days BETWEEN 1 AND 36500
  ),
  CONSTRAINT privacy_retention_policy_outbox_days_check CHECK (
    outbox_payload_retention_days BETWEEN 1 AND 36500
  ),
  CONSTRAINT privacy_retention_policy_request_days_check CHECK (
    privacy_request_retention_days BETWEEN 1 AND 36500
  )
);

INSERT INTO privacy_retention_policy(site_id)
SELECT id
FROM site
WHERE code = 'siteos_demo'
ON CONFLICT (site_id) DO NOTHING;

CREATE OR REPLACE FUNCTION ensure_site_privacy_retention_policy()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  INSERT INTO privacy_retention_policy(site_id)
  VALUES (NEW.id)
  ON CONFLICT (site_id) DO NOTHING;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS site_ensure_privacy_retention_policy ON site;
CREATE TRIGGER site_ensure_privacy_retention_policy
AFTER INSERT ON site
FOR EACH ROW
EXECUTE FUNCTION ensure_site_privacy_retention_policy();

CREATE INDEX IF NOT EXISTS idx_privacy_retention_policy_enabled
  ON privacy_retention_policy(enabled, site_id);
