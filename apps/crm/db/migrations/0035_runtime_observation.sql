-- Run inside a transaction. Add manual observations without weakening existing
-- enum constraints or rewriting any business/configuration rows.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';
ALTER TABLE marketing_integration_check
  DROP CONSTRAINT marketing_integration_check_type_check,
  ADD CONSTRAINT marketing_integration_check_type_check CHECK (
    check_type IN ('configuration','connection','test_event','webhook','delivery','runtime_observation')
  ),
  DROP CONSTRAINT marketing_integration_check_evidence_check,
  ADD CONSTRAINT marketing_integration_check_evidence_check CHECK (
    evidence_level IN ('local','provider_response','provider_processed','manual')
  );
