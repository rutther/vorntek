ALTER TABLE category
  ADD COLUMN IF NOT EXISTS parent_id bigint REFERENCES category(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_category_tree
  ON category(site_id, locale_id, parent_id, status, sort_order);
