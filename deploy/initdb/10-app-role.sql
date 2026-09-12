-- Fresh-container initialization only; no real credentials in this file.
-- The runtime app owns its database but is not a PostgreSQL superuser.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'newcrown_app') THEN
    EXECUTE format('CREATE ROLE newcrown_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L',
                   btrim(pg_read_file('/run/secrets/db_password'), E' \t\r\n'));
  END IF;
END $$;
ALTER DATABASE newcrown OWNER TO newcrown_app;
GRANT USAGE, CREATE ON SCHEMA public TO newcrown_app;
