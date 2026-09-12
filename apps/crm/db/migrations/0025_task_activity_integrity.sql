DO $$
DECLARE
  invalid_count bigint;
BEGIN
  SELECT count(*) INTO invalid_count
  FROM crm_task
  WHERE
    (status IN ('open', 'in_progress') AND (completed_at IS NOT NULL OR btrim(outcome) <> ''))
    OR (status IN ('completed', 'canceled') AND (completed_at IS NULL OR btrim(outcome) = ''));
  IF invalid_count <> 0 THEN
    RAISE EXCEPTION 'crm_task contains % invalid state/outcome rows', invalid_count;
  END IF;

  SELECT count(*) INTO invalid_count
  FROM crm_task t
  WHERE
    (t.submission_id IS NULL AND t.company_id IS NULL AND t.contact_id IS NULL AND t.opportunity_id IS NULL)
    OR (t.submission_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM lead_submission x WHERE x.id = t.submission_id AND x.site_id = t.site_id
    ))
    OR (t.company_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM crm_company x WHERE x.id = t.company_id AND x.site_id = t.site_id
    ))
    OR (t.contact_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM crm_contact x WHERE x.id = t.contact_id AND x.site_id = t.site_id
    ))
    OR (t.opportunity_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM crm_opportunity x WHERE x.id = t.opportunity_id AND x.site_id = t.site_id
    ));
  IF invalid_count <> 0 THEN
    RAISE EXCEPTION 'crm_task contains % invalid target rows', invalid_count;
  END IF;

  SELECT count(*) INTO invalid_count
  FROM crm_activity a
  WHERE
    (a.submission_id IS NULL AND a.company_id IS NULL AND a.contact_id IS NULL AND a.opportunity_id IS NULL)
    OR (a.submission_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM lead_submission x WHERE x.id = a.submission_id AND x.site_id = a.site_id
    ))
    OR (a.company_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM crm_company x WHERE x.id = a.company_id AND x.site_id = a.site_id
    ))
    OR (a.contact_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM crm_contact x WHERE x.id = a.contact_id AND x.site_id = a.site_id
    ))
    OR (a.opportunity_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM crm_opportunity x WHERE x.id = a.opportunity_id AND x.site_id = a.site_id
    ));
  IF invalid_count <> 0 THEN
    RAISE EXCEPTION 'crm_activity contains % invalid target rows', invalid_count;
  END IF;

  SELECT count(*) INTO invalid_count
  FROM crm_task t
  LEFT JOIN lead_submission s ON s.id = t.submission_id
  LEFT JOIN crm_company c ON c.id = t.company_id
  LEFT JOIN crm_contact ct ON ct.id = t.contact_id
  LEFT JOIN crm_opportunity o ON o.id = t.opportunity_id
  WHERE
    (
      ((t.submission_id IS NOT NULL)::integer + (t.company_id IS NOT NULL)::integer
        + (t.contact_id IS NOT NULL)::integer + (t.opportunity_id IS NOT NULL)::integer) > 1
      AND COALESCE(s.team_id, c.team_id, ct.team_id, o.team_id) IS NULL
    )
    OR (t.submission_id IS NOT NULL AND s.team_id IS DISTINCT FROM COALESCE(s.team_id, c.team_id, ct.team_id, o.team_id))
    OR (t.company_id IS NOT NULL AND c.team_id IS DISTINCT FROM COALESCE(s.team_id, c.team_id, ct.team_id, o.team_id))
    OR (t.contact_id IS NOT NULL AND ct.team_id IS DISTINCT FROM COALESCE(s.team_id, c.team_id, ct.team_id, o.team_id))
    OR (t.opportunity_id IS NOT NULL AND o.team_id IS DISTINCT FROM COALESCE(s.team_id, c.team_id, ct.team_id, o.team_id))
    OR t.team_id IS DISTINCT FROM COALESCE(s.team_id, c.team_id, ct.team_id, o.team_id)
    OR (t.company_id IS NOT NULL AND t.contact_id IS NOT NULL AND ct.company_id IS DISTINCT FROM t.company_id)
    OR (t.company_id IS NOT NULL AND t.opportunity_id IS NOT NULL AND o.company_id IS DISTINCT FROM t.company_id)
    OR (t.contact_id IS NOT NULL AND t.opportunity_id IS NOT NULL AND (ct.company_id IS NULL OR ct.company_id IS DISTINCT FROM o.company_id))
    OR (t.submission_id IS NOT NULL AND t.opportunity_id IS NOT NULL AND o.source_submission_id IS DISTINCT FROM t.submission_id)
    OR (t.team_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM sales_team_member m
      JOIN sales_team st ON st.id = m.team_id
      WHERE m.team_id = t.team_id AND m.user_id = t.owner_user_id
        AND st.site_id = t.site_id AND st.enabled
    ));
  IF invalid_count <> 0 THEN
    RAISE EXCEPTION 'crm_task contains % invalid team/relationship rows', invalid_count;
  END IF;

  SELECT count(*) INTO invalid_count
  FROM crm_activity a
  LEFT JOIN lead_submission s ON s.id = a.submission_id
  LEFT JOIN crm_company c ON c.id = a.company_id
  LEFT JOIN crm_contact ct ON ct.id = a.contact_id
  LEFT JOIN crm_opportunity o ON o.id = a.opportunity_id
  WHERE
    (
      ((a.submission_id IS NOT NULL)::integer + (a.company_id IS NOT NULL)::integer
        + (a.contact_id IS NOT NULL)::integer + (a.opportunity_id IS NOT NULL)::integer) > 1
      AND COALESCE(s.team_id, c.team_id, ct.team_id, o.team_id) IS NULL
    )
    OR (a.submission_id IS NOT NULL AND s.team_id IS DISTINCT FROM COALESCE(s.team_id, c.team_id, ct.team_id, o.team_id))
    OR (a.company_id IS NOT NULL AND c.team_id IS DISTINCT FROM COALESCE(s.team_id, c.team_id, ct.team_id, o.team_id))
    OR (a.contact_id IS NOT NULL AND ct.team_id IS DISTINCT FROM COALESCE(s.team_id, c.team_id, ct.team_id, o.team_id))
    OR (a.opportunity_id IS NOT NULL AND o.team_id IS DISTINCT FROM COALESCE(s.team_id, c.team_id, ct.team_id, o.team_id))
    OR (a.company_id IS NOT NULL AND a.contact_id IS NOT NULL AND ct.company_id IS DISTINCT FROM a.company_id)
    OR (a.company_id IS NOT NULL AND a.opportunity_id IS NOT NULL AND o.company_id IS DISTINCT FROM a.company_id)
    OR (a.contact_id IS NOT NULL AND a.opportunity_id IS NOT NULL AND (ct.company_id IS NULL OR ct.company_id IS DISTINCT FROM o.company_id))
    OR (a.submission_id IS NOT NULL AND a.opportunity_id IS NOT NULL AND o.source_submission_id IS DISTINCT FROM a.submission_id);
  IF invalid_count <> 0 THEN
    RAISE EXCEPTION 'crm_activity contains % invalid team/relationship rows', invalid_count;
  END IF;
END;
$$;

ALTER TABLE crm_task DROP CONSTRAINT IF EXISTS crm_task_completion_check;
ALTER TABLE crm_task ADD CONSTRAINT crm_task_completion_check CHECK (
  (
    status IN ('open', 'in_progress')
    AND completed_at IS NULL
    AND btrim(outcome) = ''
  )
  OR (
    status IN ('completed', 'canceled')
    AND completed_at IS NOT NULL
    AND btrim(outcome) <> ''
  )
);

CREATE INDEX IF NOT EXISTS idx_crm_task_submission
  ON crm_task(submission_id, status, due_at, id)
  WHERE submission_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_crm_task_company
  ON crm_task(company_id, status, due_at, id)
  WHERE company_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_crm_task_contact
  ON crm_task(contact_id, status, due_at, id)
  WHERE contact_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_crm_task_attention
  ON crm_task(site_id, status, due_at, id);
CREATE INDEX IF NOT EXISTS idx_crm_activity_task_id
  ON crm_activity((metadata_json ->> 'task_id'), occurred_at DESC, id DESC)
  WHERE metadata_json ? 'task_id';

CREATE TABLE IF NOT EXISTS crm_mutation_receipt (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE RESTRICT,
  actor_user_id integer NOT NULL REFERENCES auth_user(id) ON DELETE RESTRICT,
  mutation_scope text NOT NULL,
  token_hash text NOT NULL,
  fingerprint_hash text NOT NULL,
  status text NOT NULL DEFAULT 'processing',
  entity_table text NOT NULL DEFAULT '',
  entity_id bigint,
  result_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  expires_at timestamptz NOT NULL DEFAULT (now() + interval '30 days'),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT crm_mutation_receipt_status_check CHECK (
    status IN ('processing', 'succeeded')
  ),
  CONSTRAINT crm_mutation_receipt_token_check CHECK (
    token_hash ~ '^[0-9a-f]{64}$' AND fingerprint_hash ~ '^[0-9a-f]{64}$'
  ),
  UNIQUE(site_id, actor_user_id, mutation_scope, token_hash)
);

ALTER TABLE crm_mutation_receipt
  ADD COLUMN IF NOT EXISTS expires_at timestamptz NOT NULL DEFAULT (now() + interval '30 days');

CREATE INDEX IF NOT EXISTS idx_crm_mutation_receipt_created
  ON crm_mutation_receipt(site_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_crm_mutation_receipt_expiry
  ON crm_mutation_receipt(expires_at, id);

CREATE OR REPLACE FUNCTION validate_crm_relationship_row()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  target_count integer := 0;
  resolved_team_id bigint := NULL;
  current_site_id bigint;
  current_team_id bigint;
  company_for_contact bigint;
  company_for_opportunity bigint;
  source_for_opportunity bigint;
BEGIN
  IF NEW.submission_id IS NOT NULL THEN
    SELECT site_id, team_id INTO current_site_id, current_team_id
    FROM lead_submission WHERE id = NEW.submission_id;
    IF NOT FOUND OR current_site_id <> NEW.site_id THEN
      RAISE EXCEPTION 'CRM submission target must belong to the record site';
    END IF;
    IF current_team_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM sales_team WHERE id = current_team_id AND site_id = NEW.site_id
    ) THEN
      RAISE EXCEPTION 'CRM submission target team must belong to the record site';
    END IF;
    target_count := target_count + 1;
    resolved_team_id := current_team_id;
  END IF;

  IF NEW.company_id IS NOT NULL THEN
    SELECT site_id, team_id INTO current_site_id, current_team_id
    FROM crm_company WHERE id = NEW.company_id;
    IF NOT FOUND OR current_site_id <> NEW.site_id THEN
      RAISE EXCEPTION 'CRM company target must belong to the record site';
    END IF;
    IF current_team_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM sales_team WHERE id = current_team_id AND site_id = NEW.site_id
    ) THEN
      RAISE EXCEPTION 'CRM company target team must belong to the record site';
    END IF;
    target_count := target_count + 1;
    IF target_count = 1 THEN resolved_team_id := current_team_id;
    ELSIF resolved_team_id IS NULL OR current_team_id IS NULL OR resolved_team_id <> current_team_id THEN
      RAISE EXCEPTION 'CRM targets must share one non-null sales team';
    END IF;
  END IF;

  IF NEW.contact_id IS NOT NULL THEN
    SELECT site_id, team_id, company_id
      INTO current_site_id, current_team_id, company_for_contact
    FROM crm_contact WHERE id = NEW.contact_id;
    IF NOT FOUND OR current_site_id <> NEW.site_id THEN
      RAISE EXCEPTION 'CRM contact target must belong to the record site';
    END IF;
    IF current_team_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM sales_team WHERE id = current_team_id AND site_id = NEW.site_id
    ) THEN
      RAISE EXCEPTION 'CRM contact target team must belong to the record site';
    END IF;
    target_count := target_count + 1;
    IF target_count = 1 THEN resolved_team_id := current_team_id;
    ELSIF resolved_team_id IS NULL OR current_team_id IS NULL OR resolved_team_id <> current_team_id THEN
      RAISE EXCEPTION 'CRM targets must share one non-null sales team';
    END IF;
    IF NEW.company_id IS NOT NULL AND company_for_contact IS DISTINCT FROM NEW.company_id THEN
      RAISE EXCEPTION 'CRM contact must belong to the linked company';
    END IF;
  END IF;

  IF NEW.opportunity_id IS NOT NULL THEN
    SELECT site_id, team_id, company_id, source_submission_id
      INTO current_site_id, current_team_id, company_for_opportunity, source_for_opportunity
    FROM crm_opportunity WHERE id = NEW.opportunity_id;
    IF NOT FOUND OR current_site_id <> NEW.site_id THEN
      RAISE EXCEPTION 'CRM opportunity target must belong to the record site';
    END IF;
    IF current_team_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM sales_team WHERE id = current_team_id AND site_id = NEW.site_id
    ) THEN
      RAISE EXCEPTION 'CRM opportunity target team must belong to the record site';
    END IF;
    target_count := target_count + 1;
    IF target_count = 1 THEN resolved_team_id := current_team_id;
    ELSIF resolved_team_id IS NULL OR current_team_id IS NULL OR resolved_team_id <> current_team_id THEN
      RAISE EXCEPTION 'CRM targets must share one non-null sales team';
    END IF;
    IF NEW.company_id IS NOT NULL AND company_for_opportunity IS DISTINCT FROM NEW.company_id THEN
      RAISE EXCEPTION 'CRM opportunity must belong to the linked company';
    END IF;
    IF NEW.contact_id IS NOT NULL AND company_for_contact IS DISTINCT FROM company_for_opportunity THEN
      RAISE EXCEPTION 'CRM contact and opportunity must belong to the same company';
    END IF;
    IF NEW.submission_id IS NOT NULL AND source_for_opportunity IS DISTINCT FROM NEW.submission_id THEN
      RAISE EXCEPTION 'CRM opportunity must originate from the linked submission';
    END IF;
  END IF;

  IF target_count = 0 THEN
    RAISE EXCEPTION 'CRM relationship row requires at least one target';
  END IF;

  IF TG_TABLE_NAME = 'crm_task' THEN
    IF NEW.team_id IS DISTINCT FROM resolved_team_id THEN
      RAISE EXCEPTION 'CRM task team must match its linked targets';
    END IF;
    IF NEW.team_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM sales_team_member m
      JOIN sales_team t ON t.id = m.team_id
      WHERE m.team_id = NEW.team_id
        AND m.user_id = NEW.owner_user_id
        AND t.site_id = NEW.site_id
        AND t.enabled
    ) THEN
      RAISE EXCEPTION 'CRM task owner must be an active member of its team';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS crm_task_validate_relationship ON crm_task;
CREATE TRIGGER crm_task_validate_relationship
BEFORE INSERT OR UPDATE OF site_id, submission_id, company_id, contact_id, opportunity_id, owner_user_id, team_id
ON crm_task
FOR EACH ROW EXECUTE FUNCTION validate_crm_relationship_row();

DROP TRIGGER IF EXISTS crm_activity_validate_relationship ON crm_activity;
CREATE TRIGGER crm_activity_validate_relationship
BEFORE INSERT ON crm_activity
FOR EACH ROW EXECUTE FUNCTION validate_crm_relationship_row();
