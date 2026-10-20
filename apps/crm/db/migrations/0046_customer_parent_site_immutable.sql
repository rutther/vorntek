BEGIN;

-- Tenant ownership is immutable because there is no supported cross-site move
-- workflow for companies or import batches. Parent reassignment could strand
-- already-validated customer-pool child rows in another site.
CREATE OR REPLACE FUNCTION prevent_customer_parent_site_reassignment()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.site_id IS DISTINCT FROM OLD.site_id THEN
    RAISE EXCEPTION '% site ownership is immutable', TG_TABLE_NAME
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS crm_company_site_immutable ON crm_company;
CREATE TRIGGER crm_company_site_immutable
BEFORE UPDATE OF site_id ON crm_company
FOR EACH ROW EXECUTE FUNCTION prevent_customer_parent_site_reassignment();

DROP TRIGGER IF EXISTS crm_customer_import_batch_site_immutable ON crm_customer_import_batch;
CREATE TRIGGER crm_customer_import_batch_site_immutable
BEFORE UPDATE OF site_id ON crm_customer_import_batch
FOR EACH ROW EXECUTE FUNCTION prevent_customer_parent_site_reassignment();

COMMIT;
