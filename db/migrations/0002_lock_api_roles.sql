-- 0002_lock_api_roles: keep mock tables off Supabase's public REST/GraphQL API.
-- The app connects as the database owner over Postgres, so it is unaffected.
-- Row level security with no policies blocks the anon and authenticated roles;
-- revoking grants removes them as a second layer. Safe on plain Postgres (CI),
-- where those roles do not exist.
DO $$
DECLARE
    t text;
BEGIN
    FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = current_schema() LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA %I FROM anon, authenticated', current_schema());
        EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA %I FROM anon, authenticated', current_schema());
        EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I REVOKE ALL ON TABLES FROM anon, authenticated', current_schema());
    END IF;
END
$$;
