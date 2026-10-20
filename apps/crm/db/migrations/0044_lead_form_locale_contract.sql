BEGIN;

-- A public form code identifies one logical form per site.  Allow an exact
-- locale definition and one site-wide fallback to coexist without permitting
-- ambiguous duplicates in either scope.
ALTER TABLE lead_form_definition
  DROP CONSTRAINT IF EXISTS lead_form_definition_site_id_code_key;

CREATE UNIQUE INDEX IF NOT EXISTS uq_lead_form_definition_site_code_shared
  ON lead_form_definition(site_id, code)
  WHERE locale_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_lead_form_definition_site_code_locale
  ON lead_form_definition(site_id, code, locale_id)
  WHERE locale_id IS NOT NULL;

COMMIT;
