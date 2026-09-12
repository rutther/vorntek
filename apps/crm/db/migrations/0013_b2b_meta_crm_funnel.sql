ALTER TABLE lead_form_definition
  ADD COLUMN IF NOT EXISTS contacted_event_name text NOT NULL DEFAULT 'contacted_lead';

ALTER TABLE lead_form_definition
  ALTER COLUMN qualified_event_name SET DEFAULT 'qualified_lead',
  ALTER COLUMN won_event_name SET DEFAULT 'converted';

UPDATE lead_form_definition
SET qualified_event_name = 'qualified_lead'
WHERE qualified_event_name IN ('QualifiedLead', 'LeadQualified');

UPDATE lead_form_definition
SET won_event_name = 'converted'
WHERE won_event_name = 'Purchase';

UPDATE marketing_integration AS integration
SET
  config_json = jsonb_set(
    COALESCE(integration.config_json, '{}'::jsonb),
    '{initial_crm_event_name}',
    to_jsonb('initial_lead'::text),
    true
  ),
  updated_at = now()
FROM marketing_provider AS provider
WHERE integration.provider_id = provider.id
  AND provider.code = 'meta'
  AND integration.integration_type = 'leadgen'
  AND NOT (COALESCE(integration.config_json, '{}'::jsonb) ? 'initial_crm_event_name');
