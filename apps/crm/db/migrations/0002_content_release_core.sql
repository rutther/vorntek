CREATE TABLE IF NOT EXISTS site (
  id bigserial PRIMARY KEY,
  code text NOT NULL UNIQUE,
  name text NOT NULL,
  base_url text NOT NULL DEFAULT '',
  default_locale text NOT NULL DEFAULT 'en',
  enabled boolean NOT NULL DEFAULT true,
  config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT site_code_format CHECK (code ~ '^[a-z][a-z0-9_]*$')
);

CREATE TABLE IF NOT EXISTS site_locale (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  locale_code text NOT NULL,
  label text NOT NULL,
  direction text NOT NULL DEFAULT 'ltr',
  is_default boolean NOT NULL DEFAULT false,
  enabled boolean NOT NULL DEFAULT true,
  sort_order integer NOT NULL DEFAULT 100,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(site_id, locale_code),
  CONSTRAINT site_locale_code_format CHECK (locale_code ~ '^[a-z]{2}(-[A-Z]{2})?$'),
  CONSTRAINT site_locale_direction CHECK (direction IN ('ltr', 'rtl'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_site_locale_one_default
  ON site_locale(site_id)
  WHERE is_default = true;

CREATE TABLE IF NOT EXISTS page_route (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  locale_id bigint NOT NULL REFERENCES site_locale(id) ON DELETE RESTRICT,
  path text NOT NULL,
  route_type text NOT NULL,
  status text NOT NULL DEFAULT 'draft',
  page_title text NOT NULL,
  meta_description text NOT NULL DEFAULT '',
  canonical_url text NOT NULL DEFAULT '',
  og_title text NOT NULL DEFAULT '',
  og_description text NOT NULL DEFAULT '',
  og_image_url text NOT NULL DEFAULT '',
  robots text NOT NULL DEFAULT 'index,follow,max-image-preview:large',
  published_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(site_id, locale_id, path),
  CONSTRAINT page_route_path_format CHECK (path ~ '^/[a-z0-9/_-]*$'),
  CONSTRAINT page_route_type_check CHECK (route_type IN ('free_page', 'article_index', 'article', 'system')),
  CONSTRAINT page_route_status_check CHECK (status IN ('draft', 'published', 'archived'))
);

CREATE INDEX IF NOT EXISTS idx_page_route_published_lookup
  ON page_route(site_id, locale_id, route_type, status, path);

CREATE TABLE IF NOT EXISTS free_page (
  id bigserial PRIMARY KEY,
  route_id bigint NOT NULL UNIQUE REFERENCES page_route(id) ON DELETE CASCADE,
  template_key text NOT NULL,
  title text NOT NULL,
  summary text NOT NULL DEFAULT '',
  hero_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  body_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT free_page_template_key_format CHECK (template_key ~ '^[a-z][a-z0-9_]*$')
);

CREATE INDEX IF NOT EXISTS idx_free_page_route
  ON free_page(route_id);

CREATE TABLE IF NOT EXISTS category (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  locale_id bigint NOT NULL REFERENCES site_locale(id) ON DELETE RESTRICT,
  code text NOT NULL,
  name text NOT NULL,
  slug text NOT NULL,
  description text NOT NULL DEFAULT '',
  status text NOT NULL DEFAULT 'published',
  sort_order integer NOT NULL DEFAULT 100,
  seo_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(site_id, locale_id, slug),
  UNIQUE(site_id, locale_id, code),
  CONSTRAINT category_code_format CHECK (code ~ '^[a-z][a-z0-9_]*$'),
  CONSTRAINT category_slug_format CHECK (slug ~ '^[a-z0-9][a-z0-9_-]*$'),
  CONSTRAINT category_status_check CHECK (status IN ('draft', 'published', 'archived'))
);

CREATE INDEX IF NOT EXISTS idx_category_published
  ON category(site_id, locale_id, status, sort_order);

CREATE TABLE IF NOT EXISTS article (
  id bigserial PRIMARY KEY,
  route_id bigint NOT NULL UNIQUE REFERENCES page_route(id) ON DELETE CASCADE,
  category_id bigint REFERENCES category(id) ON DELETE SET NULL,
  title text NOT NULL,
  slug text NOT NULL,
  excerpt text NOT NULL DEFAULT '',
  body_markdown text NOT NULL DEFAULT '',
  body_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  cover_image_url text NOT NULL DEFAULT '',
  author_name text NOT NULL DEFAULT '',
  status text NOT NULL DEFAULT 'draft',
  published_at timestamptz,
  reading_minutes integer NOT NULL DEFAULT 1,
  seo_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT article_slug_format CHECK (slug ~ '^[a-z0-9][a-z0-9_-]*$'),
  CONSTRAINT article_status_check CHECK (status IN ('draft', 'published', 'archived')),
  CONSTRAINT article_reading_minutes_positive CHECK (reading_minutes > 0)
);

CREATE INDEX IF NOT EXISTS idx_article_category_status
  ON article(category_id, status, published_at DESC);

CREATE INDEX IF NOT EXISTS idx_article_published_at
  ON article(status, published_at DESC);

CREATE TABLE IF NOT EXISTS tag (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  locale_id bigint NOT NULL REFERENCES site_locale(id) ON DELETE RESTRICT,
  code text NOT NULL,
  name text NOT NULL,
  slug text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(site_id, locale_id, code),
  UNIQUE(site_id, locale_id, slug),
  CONSTRAINT tag_code_format CHECK (code ~ '^[a-z][a-z0-9_]*$'),
  CONSTRAINT tag_slug_format CHECK (slug ~ '^[a-z0-9][a-z0-9_-]*$')
);

CREATE TABLE IF NOT EXISTS article_tag (
  id bigserial PRIMARY KEY,
  article_id bigint NOT NULL REFERENCES article(id) ON DELETE CASCADE,
  tag_id bigint NOT NULL REFERENCES tag(id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(article_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_article_tag_tag
  ON article_tag(tag_id, article_id);

CREATE TABLE IF NOT EXISTS navigation_menu (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  locale_id bigint NOT NULL REFERENCES site_locale(id) ON DELETE RESTRICT,
  code text NOT NULL,
  name text NOT NULL,
  region text NOT NULL,
  enabled boolean NOT NULL DEFAULT true,
  sort_order integer NOT NULL DEFAULT 100,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(site_id, locale_id, code),
  CONSTRAINT navigation_menu_code_format CHECK (code ~ '^[a-z][a-z0-9_]*$'),
  CONSTRAINT navigation_menu_region_check CHECK (region IN ('header', 'footer', 'sidebar'))
);

CREATE INDEX IF NOT EXISTS idx_navigation_menu_region
  ON navigation_menu(site_id, locale_id, region, enabled, sort_order);

CREATE TABLE IF NOT EXISTS navigation_item (
  id bigserial PRIMARY KEY,
  menu_id bigint NOT NULL REFERENCES navigation_menu(id) ON DELETE CASCADE,
  parent_id bigint REFERENCES navigation_item(id) ON DELETE CASCADE,
  route_id bigint REFERENCES page_route(id) ON DELETE SET NULL,
  label text NOT NULL,
  href text NOT NULL DEFAULT '',
  sort_order integer NOT NULL DEFAULT 100,
  enabled boolean NOT NULL DEFAULT true,
  config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT navigation_item_target_check CHECK (route_id IS NOT NULL OR href <> '')
);

CREATE INDEX IF NOT EXISTS idx_navigation_item_menu
  ON navigation_item(menu_id, parent_id, enabled, sort_order);

CREATE UNIQUE INDEX IF NOT EXISTS idx_navigation_item_unique_target
  ON navigation_item(
    menu_id,
    COALESCE(parent_id, 0),
    COALESCE(route_id, 0),
    href,
    label
  );

CREATE TABLE IF NOT EXISTS component_definition (
  id bigserial PRIMARY KEY,
  code text NOT NULL UNIQUE,
  name text NOT NULL,
  component_type text NOT NULL,
  description text NOT NULL DEFAULT '',
  enabled boolean NOT NULL DEFAULT true,
  config_schema jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT component_definition_code_format CHECK (code ~ '^[a-z][a-z0-9_]*$'),
  CONSTRAINT component_definition_type_check CHECK (
    component_type IN ('global', 'layout', 'content', 'marketing', 'tool')
  )
);

CREATE TABLE IF NOT EXISTS component_binding (
  id bigserial PRIMARY KEY,
  component_id bigint NOT NULL REFERENCES component_definition(id) ON DELETE CASCADE,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  locale_id bigint REFERENCES site_locale(id) ON DELETE CASCADE,
  scope_type text NOT NULL,
  scope_value text NOT NULL DEFAULT '*',
  region text NOT NULL,
  priority integer NOT NULL DEFAULT 100,
  enabled boolean NOT NULL DEFAULT true,
  config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT component_binding_scope_type_check CHECK (
    scope_type IN ('global', 'locale', 'page_type', 'page_path', 'category', 'article')
  ),
  CONSTRAINT component_binding_region_check CHECK (
    region IN ('header', 'footer', 'sidebar', 'content_top', 'content_bottom')
  )
);

CREATE INDEX IF NOT EXISTS idx_component_binding_scope
  ON component_binding(site_id, locale_id, scope_type, scope_value, enabled, priority);

CREATE UNIQUE INDEX IF NOT EXISTS idx_component_binding_unique_scope
  ON component_binding(
    component_id,
    site_id,
    COALESCE(locale_id, 0),
    scope_type,
    scope_value,
    region
  );

CREATE TABLE IF NOT EXISTS cta (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  code text NOT NULL,
  name text NOT NULL,
  label text NOT NULL,
  target_url text NOT NULL,
  target_type text NOT NULL DEFAULT 'internal',
  business_goal text NOT NULL DEFAULT 'lead',
  event_id bigint REFERENCES canonical_event(id) ON DELETE SET NULL,
  enabled boolean NOT NULL DEFAULT true,
  config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(site_id, code),
  CONSTRAINT cta_code_format CHECK (code ~ '^[a-z][a-z0-9_]*$'),
  CONSTRAINT cta_target_type_check CHECK (target_type IN ('internal', 'external', 'form', 'download', 'phone', 'email')),
  CONSTRAINT cta_business_goal_format CHECK (business_goal ~ '^[a-z][a-z0-9_]*$')
);

CREATE INDEX IF NOT EXISTS idx_cta_site_enabled
  ON cta(site_id, enabled, business_goal);

CREATE TABLE IF NOT EXISTS cta_binding (
  id bigserial PRIMARY KEY,
  cta_id bigint NOT NULL REFERENCES cta(id) ON DELETE CASCADE,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  locale_id bigint REFERENCES site_locale(id) ON DELETE CASCADE,
  scope_type text NOT NULL,
  scope_value text NOT NULL DEFAULT '*',
  position text NOT NULL DEFAULT 'content_bottom',
  priority integer NOT NULL DEFAULT 100,
  enabled boolean NOT NULL DEFAULT true,
  config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT cta_binding_scope_type_check CHECK (
    scope_type IN ('global', 'locale', 'page_type', 'page_path', 'category', 'article')
  ),
  CONSTRAINT cta_binding_position_check CHECK (
    position IN ('hero', 'inline', 'sidebar', 'content_top', 'content_bottom', 'footer')
  )
);

CREATE INDEX IF NOT EXISTS idx_cta_binding_scope
  ON cta_binding(site_id, locale_id, scope_type, scope_value, enabled, priority);

CREATE UNIQUE INDEX IF NOT EXISTS idx_cta_binding_unique_scope
  ON cta_binding(
    cta_id,
    site_id,
    COALESCE(locale_id, 0),
    scope_type,
    scope_value,
    position
  );

CREATE TABLE IF NOT EXISTS release (
  id bigserial PRIMARY KEY,
  site_id bigint NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  release_key text NOT NULL UNIQUE,
  status text NOT NULL DEFAULT 'draft',
  snapshot_manifest jsonb NOT NULL DEFAULT '{}'::jsonb,
  artifact_path text NOT NULL DEFAULT '',
  created_by text NOT NULL DEFAULT 'system',
  notes text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  exported_at timestamptz,
  built_at timestamptz,
  published_at timestamptz,
  CONSTRAINT release_key_format CHECK (release_key ~ '^[a-zA-Z0-9._:-]+$'),
  CONSTRAINT release_status_check CHECK (status IN ('draft', 'exported', 'built', 'live', 'failed', 'retired'))
);

CREATE INDEX IF NOT EXISTS idx_release_site_status
  ON release(site_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS release_build (
  id bigserial PRIMARY KEY,
  release_id bigint NOT NULL REFERENCES release(id) ON DELETE CASCADE,
  build_key text NOT NULL,
  status text NOT NULL DEFAULT 'queued',
  artifact_path text NOT NULL DEFAULT '',
  log_excerpt text NOT NULL DEFAULT '',
  config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(release_id, build_key),
  CONSTRAINT release_build_key_format CHECK (build_key ~ '^[a-zA-Z0-9._:-]+$'),
  CONSTRAINT release_build_status_check CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled'))
);

CREATE INDEX IF NOT EXISTS idx_release_build_status
  ON release_build(status, created_at DESC);
