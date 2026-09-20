ALTER TABLE content_access_grant
  DROP CONSTRAINT IF EXISTS content_access_grant_capability_check;

ALTER TABLE content_access_grant
  ADD CONSTRAINT content_access_grant_capability_check CHECK (
    capability IN (
      'content.read',
      'content.write',
      'content.set_published',
      'content.locale.manage',
      'assets.read',
      'assets.write',
      'assets.import_local',
      'releases.read',
      'releases.preview_build',
      'releases.candidate_build',
      'releases.candidate_select',
      'releases.deploy'
    )
  );

CREATE UNIQUE INDEX IF NOT EXISTS uq_website_selection_scope_binding
  ON website_release_selection(site_id, id, release_id, version);

ALTER TABLE website_release_selection
  ADD CONSTRAINT website_release_selection_release_fk
  FOREIGN KEY (release_id) REFERENCES release(id) ON DELETE RESTRICT;

CREATE TABLE IF NOT EXISTS website_deployment_operation (
  id bigserial PRIMARY KEY,
  request_token uuid NOT NULL UNIQUE,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE RESTRICT,
  selection_id bigint NOT NULL
    REFERENCES website_release_selection(id) ON DELETE RESTRICT,
  release_id bigint NOT NULL REFERENCES release(id) ON DELETE RESTRICT,
  version text NOT NULL,
  previous_version text NOT NULL DEFAULT '',
  deployed_by text NOT NULL,
  reason text NOT NULL,
  status text NOT NULL DEFAULT 'prepared',
  error_code text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  CONSTRAINT website_deploy_operation_selection_fk
    FOREIGN KEY (site_id, selection_id, release_id, version)
    REFERENCES website_release_selection(site_id, id, release_id, version)
    ON DELETE RESTRICT,
  CONSTRAINT website_deploy_operation_version_check CHECK (
    version ~ '^[a-f0-9]{64}$'
  ),
  CONSTRAINT website_deploy_operation_previous_check CHECK (
    previous_version = '' OR previous_version ~ '^[a-f0-9]{64}$'
  ),
  CONSTRAINT website_deploy_operation_actor_check CHECK (
    char_length(btrim(deployed_by)) BETWEEN 1 AND 150
  ),
  CONSTRAINT website_deploy_operation_reason_check CHECK (
    char_length(btrim(reason)) BETWEEN 8 AND 500
  ),
  CONSTRAINT website_deploy_operation_status_check CHECK (
    status IN ('prepared', 'activated', 'failed')
  ),
  CONSTRAINT website_deploy_operation_completion_check CHECK (
    (status = 'prepared' AND completed_at IS NULL AND error_code = '')
    OR (status = 'activated' AND completed_at IS NOT NULL AND error_code = '')
    OR (
      status = 'failed'
      AND completed_at IS NOT NULL
      AND error_code ~ '^[a-z][a-z0-9_]{0,99}$'
    )
  )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_website_deploy_prepared_site
  ON website_deployment_operation(site_id)
  WHERE status = 'prepared';

CREATE INDEX IF NOT EXISTS idx_website_deploy_operation_site
  ON website_deployment_operation(site_id, id DESC);

CREATE TABLE IF NOT EXISTS website_release_deployment (
  id bigserial PRIMARY KEY,
  operation_id bigint NOT NULL UNIQUE
    REFERENCES website_deployment_operation(id) ON DELETE RESTRICT,
  request_token uuid NOT NULL UNIQUE,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE RESTRICT,
  selection_id bigint NOT NULL
    REFERENCES website_release_selection(id) ON DELETE RESTRICT,
  release_id bigint NOT NULL REFERENCES release(id) ON DELETE RESTRICT,
  version text NOT NULL,
  previous_version text NOT NULL DEFAULT '',
  deployed_by text NOT NULL,
  reason text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT website_deployment_selection_fk
    FOREIGN KEY (site_id, selection_id, release_id, version)
    REFERENCES website_release_selection(site_id, id, release_id, version)
    ON DELETE RESTRICT,
  CONSTRAINT website_deployment_version_check CHECK (
    version ~ '^[a-f0-9]{64}$'
  ),
  CONSTRAINT website_deployment_previous_check CHECK (
    previous_version = '' OR previous_version ~ '^[a-f0-9]{64}$'
  ),
  CONSTRAINT website_deployment_actor_check CHECK (
    char_length(btrim(deployed_by)) BETWEEN 1 AND 150
  ),
  CONSTRAINT website_deployment_reason_check CHECK (
    char_length(btrim(reason)) BETWEEN 8 AND 500
  )
);

CREATE INDEX IF NOT EXISTS idx_website_deployment_site
  ON website_release_deployment(site_id, id DESC);

CREATE OR REPLACE FUNCTION validate_website_release_deployment_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM website_deployment_operation operation
    WHERE operation.id = NEW.operation_id
      AND operation.status = 'prepared'
      AND operation.request_token = NEW.request_token
      AND operation.site_id = NEW.site_id
      AND operation.selection_id = NEW.selection_id
      AND operation.release_id = NEW.release_id
      AND operation.version = NEW.version
      AND operation.previous_version = NEW.previous_version
      AND operation.deployed_by = NEW.deployed_by
      AND operation.reason = NEW.reason
  ) THEN
    RAISE EXCEPTION 'website_release_deployment does not match prepared operation';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS website_release_deployment_validate_insert
  ON website_release_deployment;

CREATE TRIGGER website_release_deployment_validate_insert
BEFORE INSERT ON website_release_deployment
FOR EACH ROW
EXECUTE FUNCTION validate_website_release_deployment_insert();

CREATE OR REPLACE FUNCTION protect_website_deployment_operation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'website_deployment_operation cannot be deleted';
  END IF;
  IF OLD.status <> 'prepared'
     OR NEW.status NOT IN ('activated', 'failed')
     OR NEW.id IS DISTINCT FROM OLD.id
     OR NEW.request_token IS DISTINCT FROM OLD.request_token
     OR NEW.site_id IS DISTINCT FROM OLD.site_id
     OR NEW.selection_id IS DISTINCT FROM OLD.selection_id
     OR NEW.release_id IS DISTINCT FROM OLD.release_id
     OR NEW.version IS DISTINCT FROM OLD.version
     OR NEW.previous_version IS DISTINCT FROM OLD.previous_version
     OR NEW.deployed_by IS DISTINCT FROM OLD.deployed_by
     OR NEW.reason IS DISTINCT FROM OLD.reason
     OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'website_deployment_operation transition refused';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS website_deployment_operation_protect
  ON website_deployment_operation;

CREATE TRIGGER website_deployment_operation_protect
BEFORE UPDATE OR DELETE ON website_deployment_operation
FOR EACH ROW
EXECUTE FUNCTION protect_website_deployment_operation();

CREATE OR REPLACE FUNCTION prevent_website_release_deployment_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'website_release_deployment is append-only';
END;
$$;

DROP TRIGGER IF EXISTS website_release_deployment_prevent_update_delete
  ON website_release_deployment;

CREATE TRIGGER website_release_deployment_prevent_update_delete
BEFORE UPDATE OR DELETE ON website_release_deployment
FOR EACH ROW
EXECUTE FUNCTION prevent_website_release_deployment_mutation();
