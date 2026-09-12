UPDATE marketing_integration AS integration
SET
  config_json = jsonb_set(
    COALESCE(integration.config_json, '{}'::jsonb),
    '{queue_initial_crm_event}',
    'true'::jsonb,
    true
  ),
  updated_at = now()
FROM marketing_provider AS provider
WHERE integration.provider_id = provider.id
  AND provider.code = 'meta'
  AND integration.integration_type = 'leadgen';
