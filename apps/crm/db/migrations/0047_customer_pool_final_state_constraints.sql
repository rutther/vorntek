BEGIN;

-- The pool constraints are deferred so a service can update the company and
-- its pool row in either order inside one transaction.  A deferred row trigger
-- must validate the final persisted pair, not NEW from an earlier queued event:
-- a normal available -> owned transition otherwise compares the old no-owner
-- company snapshot with the final owned state and rejects a valid commit.
CREATE OR REPLACE FUNCTION validate_company_pool_state_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  current_state text;
  current_owner_user_id bigint;
  current_team_id bigint;
BEGIN
  SELECT pool.state, company.owner_user_id, company.team_id
    INTO current_state, current_owner_user_id, current_team_id
  FROM crm_company_pool_state AS pool
  JOIN crm_company AS company ON company.id = pool.company_id
  WHERE pool.company_id = NEW.company_id;

  IF current_state = 'available' AND (
    current_owner_user_id IS NOT NULL OR current_team_id IS NULL
  ) THEN
    RAISE EXCEPTION 'available pool company must have a team and no owner';
  END IF;
  IF current_state = 'owned' AND current_owner_user_id IS NULL THEN
    RAISE EXCEPTION 'owned pool company must have an owner';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION validate_company_pool_owner()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  current_state text;
  current_owner_user_id bigint;
  current_team_id bigint;
BEGIN
  SELECT pool.state, company.owner_user_id, company.team_id
    INTO current_state, current_owner_user_id, current_team_id
  FROM crm_company AS company
  LEFT JOIN crm_company_pool_state AS pool ON pool.company_id = company.id
  WHERE company.id = NEW.id;

  IF current_state = 'available' AND (
    current_owner_user_id IS NOT NULL OR current_team_id IS NULL
  ) THEN
    RAISE EXCEPTION 'available pool company must have a team and no owner';
  END IF;
  IF current_state = 'owned' AND current_owner_user_id IS NULL THEN
    RAISE EXCEPTION 'owned pool company must have an owner';
  END IF;
  RETURN NEW;
END;
$$;

COMMIT;
