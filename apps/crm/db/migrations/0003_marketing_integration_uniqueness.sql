CREATE UNIQUE INDEX IF NOT EXISTS idx_marketing_integration_unique_public
  ON marketing_integration(provider_id, integration_type, public_id);
