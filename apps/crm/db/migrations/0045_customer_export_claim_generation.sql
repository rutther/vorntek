-- Bind export completion/failure to the exact worker claim generation.
ALTER TABLE crm_customer_export_job
  ADD COLUMN IF NOT EXISTS claim_token varchar(32) NOT NULL DEFAULT '';

-- A deployment may interrupt an old worker that predates claim generations.
-- Return only those unfinished rows to the queue; a new worker will claim them.
UPDATE crm_customer_export_job
SET status = 'pending', claim_token = '', completed_at = NULL, updated_at = now()
WHERE status = 'processing' AND claim_token = '';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conrelid = 'crm_customer_export_job'::regclass
      AND conname = 'crm_customer_export_job_claim_check'
  ) THEN
    ALTER TABLE crm_customer_export_job
      ADD CONSTRAINT crm_customer_export_job_claim_check CHECK (
        (status = 'processing' AND claim_token ~ '^[0-9a-f]{32}$')
        OR (status <> 'processing' AND claim_token = '')
      );
  END IF;
END $$;
