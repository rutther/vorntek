-- Preserve immutable Pixel ownership. The legacy 0018 bootstrap may still
-- assign non-Pixel global integrations, but must never rebind a Pixel (even
-- the disabled 0022 placeholder). Real Pixels are created with an explicit
-- site through the existing integration workflow, not claimed by site insert.
-- This single DDL statement does not update/delete any integration rows or
-- enable routes; protect_pixel_identity() and its trigger remain unchanged.
CREATE OR REPLACE FUNCTION bind_legacy_marketing_integrations_to_default_site()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.code = 'siteos_demo' THEN
    UPDATE marketing_integration
    SET site_id = NEW.id,
        updated_at = now()
    WHERE site_id IS NULL
      AND integration_type <> 'pixel';
  END IF;
  RETURN NEW;
END;
$$;
