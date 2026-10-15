BEGIN;

-- 引导式闭环编排：闭环实例、运行拓扑、引导任务与外部资产绑定。
-- 这里保存控制面与观测面；任何平台密钥仍只允许进入 secret store。

CREATE TABLE IF NOT EXISTS marketing_loop (
  id bigserial PRIMARY KEY,
  loop_key uuid NOT NULL UNIQUE,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE RESTRICT,
  provider_id bigint NOT NULL REFERENCES marketing_provider(id) ON DELETE RESTRICT,
  name varchar(200) NOT NULL,
  binding_key varchar(160) NOT NULL UNIQUE,
  route_code varchar(40) NOT NULL,
  environment varchar(16) NOT NULL,
  scope_code varchar(80) NOT NULL,
  sequence_no integer NOT NULL,
  template_version varchar(32) NOT NULL DEFAULT 'meta-web-lead-v1',
  status varchar(24) NOT NULL DEFAULT 'draft',
  current_task_key varchar(80) NOT NULL DEFAULT 'dataset_setup',
  desired_revision integer NOT NULL DEFAULT 1,
  observed_revision integer NOT NULL DEFAULT 0,
  config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_by_id integer NOT NULL REFERENCES auth_user(id) ON DELETE RESTRICT,
  updated_by_id integer NOT NULL REFERENCES auth_user(id) ON DELETE RESTRICT,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT marketing_loop_name_check CHECK (length(btrim(name)) BETWEEN 2 AND 200),
  CONSTRAINT marketing_loop_binding_key_check CHECK (
    binding_key ~ '^[a-z][A-Za-z0-9]{7,159}$'
  ),
  CONSTRAINT marketing_loop_route_check CHECK (
    route_code IN ('website_form', 'instant_form', 'whatsapp_click')
  ),
  CONSTRAINT marketing_loop_environment_check CHECK (
    environment IN ('production', 'staging', 'test', 'development')
  ),
  CONSTRAINT marketing_loop_scope_check CHECK (
    scope_code ~ '^[A-Z][A-Za-z0-9]{1,79}$'
  ),
  CONSTRAINT marketing_loop_sequence_check CHECK (sequence_no BETWEEN 1 AND 999),
  CONSTRAINT marketing_loop_status_check CHECK (
    status IN ('draft', 'configuring', 'ready', 'running', 'paused', 'degraded', 'archived')
  ),
  CONSTRAINT marketing_loop_revision_check CHECK (
    desired_revision >= 1 AND observed_revision >= 0
  ),
  CONSTRAINT uq_marketing_loop_identity UNIQUE (
    site_id, provider_id, route_code, environment, scope_code, sequence_no
  )
);

CREATE INDEX IF NOT EXISTS idx_marketing_loop_route
  ON marketing_loop(site_id, provider_id, route_code, status, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_marketing_loop_current_task
  ON marketing_loop(site_id, current_task_key, status, id);

CREATE TABLE IF NOT EXISTS marketing_loop_node (
  id bigserial PRIMARY KEY,
  loop_id bigint NOT NULL REFERENCES marketing_loop(id) ON DELETE CASCADE,
  node_key varchar(80) NOT NULL,
  node_type varchar(40) NOT NULL,
  title varchar(160) NOT NULL,
  actor varchar(24) NOT NULL,
  sequence_no integer NOT NULL,
  status varchar(24) NOT NULL DEFAULT 'pending',
  summary text NOT NULL DEFAULT '',
  desired_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  observed_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  evidence_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  last_checked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT marketing_loop_node_key_check CHECK (node_key ~ '^[a-z][a-z0-9_]{1,79}$'),
  CONSTRAINT marketing_loop_node_type_check CHECK (
    node_type IN ('meta_ad', 'website', 'web_form', 'crm', 'capi', 'meta_dataset', 'optimizer')
  ),
  CONSTRAINT marketing_loop_node_actor_check CHECK (
    actor IN ('meta', 'website', 'crm', 'operator', 'system', 'shared')
  ),
  CONSTRAINT marketing_loop_node_sequence_check CHECK (sequence_no BETWEEN 1 AND 99),
  CONSTRAINT marketing_loop_node_status_check CHECK (
    status IN ('pending', 'blocked', 'waiting_user', 'checking', 'configured', 'healthy', 'warning', 'failed', 'paused')
  ),
  UNIQUE(loop_id, node_key)
);

CREATE INDEX IF NOT EXISTS idx_marketing_loop_node_state
  ON marketing_loop_node(loop_id, sequence_no, status, id);

CREATE TABLE IF NOT EXISTS marketing_loop_edge (
  id bigserial PRIMARY KEY,
  loop_id bigint NOT NULL REFERENCES marketing_loop(id) ON DELETE CASCADE,
  edge_key varchar(80) NOT NULL,
  source_node_id bigint NOT NULL REFERENCES marketing_loop_node(id) ON DELETE CASCADE,
  target_node_id bigint NOT NULL REFERENCES marketing_loop_node(id) ON DELETE CASCADE,
  signal_code varchar(80) NOT NULL,
  status varchar(24) NOT NULL DEFAULT 'pending',
  observed_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT marketing_loop_edge_key_check CHECK (edge_key ~ '^[a-z][a-z0-9_]{1,79}$'),
  CONSTRAINT marketing_loop_edge_signal_check CHECK (signal_code ~ '^[a-z][a-z0-9_]{1,79}$'),
  CONSTRAINT marketing_loop_edge_distinct_check CHECK (source_node_id <> target_node_id),
  CONSTRAINT marketing_loop_edge_status_check CHECK (
    status IN ('pending', 'checking', 'healthy', 'warning', 'failed', 'paused')
  ),
  UNIQUE(loop_id, edge_key)
);

CREATE INDEX IF NOT EXISTS idx_marketing_loop_edge_state
  ON marketing_loop_edge(loop_id, status, id);

CREATE TABLE IF NOT EXISTS marketing_loop_task (
  id bigserial PRIMARY KEY,
  loop_id bigint NOT NULL REFERENCES marketing_loop(id) ON DELETE CASCADE,
  node_id bigint REFERENCES marketing_loop_node(id) ON DELETE SET NULL,
  task_key varchar(80) NOT NULL,
  title varchar(200) NOT NULL,
  owner_type varchar(24) NOT NULL,
  sequence_no integer NOT NULL,
  status varchar(24) NOT NULL DEFAULT 'blocked',
  input_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  result_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  evidence_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT marketing_loop_task_key_check CHECK (task_key ~ '^[a-z][a-z0-9_]{1,79}$'),
  CONSTRAINT marketing_loop_task_owner_check CHECK (
    owner_type IN ('meta', 'website', 'crm', 'operator', 'system', 'shared')
  ),
  CONSTRAINT marketing_loop_task_sequence_check CHECK (sequence_no BETWEEN 1 AND 99),
  CONSTRAINT marketing_loop_task_status_check CHECK (
    status IN ('blocked', 'ready', 'in_progress', 'waiting', 'done', 'failed', 'skipped')
  ),
  CONSTRAINT marketing_loop_task_completed_check CHECK (
    status <> 'done' OR completed_at IS NOT NULL
  ),
  UNIQUE(loop_id, task_key)
);

CREATE INDEX IF NOT EXISTS idx_marketing_loop_task_queue
  ON marketing_loop_task(loop_id, sequence_no, status, id);

CREATE TABLE IF NOT EXISTS marketing_loop_asset_binding (
  id bigserial PRIMARY KEY,
  loop_id bigint NOT NULL REFERENCES marketing_loop(id) ON DELETE CASCADE,
  node_id bigint REFERENCES marketing_loop_node(id) ON DELETE SET NULL,
  provider_id bigint NOT NULL REFERENCES marketing_provider(id) ON DELETE RESTRICT,
  role varchar(40) NOT NULL,
  asset_type varchar(40) NOT NULL,
  external_id varchar(255) NOT NULL DEFAULT '',
  expected_name varchar(255) NOT NULL DEFAULT '',
  observed_name varchar(255) NOT NULL DEFAULT '',
  reuse_mode varchar(16) NOT NULL DEFAULT 'undecided',
  status varchar(24) NOT NULL DEFAULT 'planned',
  metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  last_verified_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT marketing_loop_asset_role_check CHECK (role ~ '^[a-z][a-z0-9_]{1,39}$'),
  CONSTRAINT marketing_loop_asset_type_check CHECK (asset_type ~ '^[a-z][a-z0-9_]{1,39}$'),
  CONSTRAINT marketing_loop_asset_reuse_check CHECK (
    reuse_mode IN ('undecided', 'new', 'existing', 'shared')
  ),
  CONSTRAINT marketing_loop_asset_status_check CHECK (
    status IN ('planned', 'recorded', 'checking', 'verified', 'warning', 'failed', 'retired')
  ),
  UNIQUE(loop_id, role)
);

CREATE INDEX IF NOT EXISTS idx_marketing_loop_asset_external
  ON marketing_loop_asset_binding(provider_id, asset_type, external_id)
  WHERE external_id <> '';
CREATE UNIQUE INDEX IF NOT EXISTS uq_marketing_loop_new_asset_id
  ON marketing_loop_asset_binding(provider_id, asset_type, external_id)
  WHERE reuse_mode = 'new' AND external_id <> '';

CREATE OR REPLACE FUNCTION validate_marketing_loop_child_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  source_loop_id bigint;
  target_loop_id bigint;
  node_loop_id bigint;
  loop_provider_id bigint;
BEGIN
  IF TG_TABLE_NAME = 'marketing_loop_edge' THEN
    SELECT loop_id INTO source_loop_id FROM marketing_loop_node WHERE id = NEW.source_node_id;
    SELECT loop_id INTO target_loop_id FROM marketing_loop_node WHERE id = NEW.target_node_id;
    IF source_loop_id IS DISTINCT FROM NEW.loop_id OR target_loop_id IS DISTINCT FROM NEW.loop_id THEN
      RAISE EXCEPTION 'loop edge nodes must belong to the same loop';
    END IF;
  ELSIF TG_TABLE_NAME = 'marketing_loop_task' AND NEW.node_id IS NOT NULL THEN
    SELECT loop_id INTO node_loop_id FROM marketing_loop_node WHERE id = NEW.node_id;
    IF node_loop_id IS DISTINCT FROM NEW.loop_id THEN
      RAISE EXCEPTION 'loop task node must belong to the same loop';
    END IF;
  ELSIF TG_TABLE_NAME = 'marketing_loop_asset_binding' THEN
    SELECT provider_id INTO loop_provider_id FROM marketing_loop WHERE id = NEW.loop_id;
    IF loop_provider_id IS DISTINCT FROM NEW.provider_id THEN
      RAISE EXCEPTION 'loop asset provider must match loop provider';
    END IF;
    IF NEW.node_id IS NOT NULL THEN
      SELECT loop_id INTO node_loop_id FROM marketing_loop_node WHERE id = NEW.node_id;
      IF node_loop_id IS DISTINCT FROM NEW.loop_id THEN
        RAISE EXCEPTION 'loop asset node must belong to the same loop';
      END IF;
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_marketing_loop_edge_scope ON marketing_loop_edge;
CREATE TRIGGER trg_marketing_loop_edge_scope
BEFORE INSERT OR UPDATE ON marketing_loop_edge
FOR EACH ROW EXECUTE FUNCTION validate_marketing_loop_child_scope();

DROP TRIGGER IF EXISTS trg_marketing_loop_task_scope ON marketing_loop_task;
CREATE TRIGGER trg_marketing_loop_task_scope
BEFORE INSERT OR UPDATE ON marketing_loop_task
FOR EACH ROW EXECUTE FUNCTION validate_marketing_loop_child_scope();

DROP TRIGGER IF EXISTS trg_marketing_loop_asset_scope ON marketing_loop_asset_binding;
CREATE TRIGGER trg_marketing_loop_asset_scope
BEFORE INSERT OR UPDATE ON marketing_loop_asset_binding
FOR EACH ROW EXECUTE FUNCTION validate_marketing_loop_child_scope();

CREATE OR REPLACE FUNCTION prevent_marketing_loop_identity_rewrite()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.loop_key IS DISTINCT FROM OLD.loop_key
     OR NEW.site_id IS DISTINCT FROM OLD.site_id
     OR NEW.provider_id IS DISTINCT FROM OLD.provider_id
     OR NEW.binding_key IS DISTINCT FROM OLD.binding_key
     OR NEW.route_code IS DISTINCT FROM OLD.route_code
     OR NEW.environment IS DISTINCT FROM OLD.environment
     OR NEW.scope_code IS DISTINCT FROM OLD.scope_code
     OR NEW.sequence_no IS DISTINCT FROM OLD.sequence_no
     OR NEW.template_version IS DISTINCT FROM OLD.template_version THEN
    RAISE EXCEPTION 'marketing loop identity is immutable';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_marketing_loop_identity_immutable ON marketing_loop;
CREATE TRIGGER trg_marketing_loop_identity_immutable
BEFORE UPDATE ON marketing_loop
FOR EACH ROW EXECUTE FUNCTION prevent_marketing_loop_identity_rewrite();

COMMIT;
