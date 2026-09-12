CREATE TABLE IF NOT EXISTS sales_team (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  code text NOT NULL,
  name text NOT NULL,
  enabled boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(site_id, code),
  CONSTRAINT sales_team_code_format CHECK (code ~ '^[a-z][a-z0-9_]*$')
);

CREATE TABLE IF NOT EXISTS sales_team_member (
  id bigserial PRIMARY KEY,
  team_id bigint NOT NULL REFERENCES sales_team(id) ON DELETE CASCADE,
  user_id integer NOT NULL REFERENCES auth_user(id) ON DELETE CASCADE,
  membership_role text NOT NULL DEFAULT 'member',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(team_id, user_id),
  CONSTRAINT sales_team_member_role_check CHECK (
    membership_role IN ('member', 'manager')
  )
);

ALTER TABLE lead_submission
  ADD COLUMN IF NOT EXISTS team_id bigint;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'lead_submission_team_fk'
      AND conrelid = 'lead_submission'::regclass
  ) THEN
    ALTER TABLE lead_submission
      ADD CONSTRAINT lead_submission_team_fk
      FOREIGN KEY (team_id) REFERENCES sales_team(id) ON DELETE SET NULL;
  END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_sales_team_member_user
  ON sales_team_member(user_id, team_id, membership_role);

CREATE INDEX IF NOT EXISTS idx_lead_submission_team
  ON lead_submission(site_id, team_id, stage, submitted_at DESC);

INSERT INTO sales_team(site_id, code, name)
SELECT id, 'default', '默认销售团队'
FROM site
ON CONFLICT (site_id, code) DO NOTHING;

UPDATE lead_submission AS submission
SET team_id = team.id
FROM sales_team AS team
WHERE submission.site_id = team.site_id
  AND team.code = 'default'
  AND submission.team_id IS NULL;
