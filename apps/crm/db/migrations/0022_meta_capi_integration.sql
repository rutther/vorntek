INSERT INTO marketing_integration (
  site_id,
  provider_id,
  name,
  integration_type,
  public_id,
  secret_ref,
  consent_category,
  enabled,
  config_json
)
SELECT
  site.id,
  provider.id,
  'Meta Conversions API',
  'pixel',
  'pending-dataset-id',
  NULL,
  'marketing',
  false,
  '{}'::jsonb
FROM marketing_provider AS provider
LEFT JOIN site ON site.code = 'siteos_demo'
WHERE provider.code = 'meta'
  AND NOT EXISTS (
    SELECT 1
    FROM marketing_integration AS existing
    WHERE existing.provider_id = provider.id
      AND existing.integration_type = 'pixel'
  )
ON CONFLICT DO NOTHING;
