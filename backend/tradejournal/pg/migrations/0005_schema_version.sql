-- 0005: let the request role ask which migration the database is at, and nothing more.
--
-- A new image must not take traffic against an older schema (zero-downtime deploys, doc 02 section 7). The readiness
-- check (/readyz) therefore compares the newest migration file in the image with the newest one applied. The request
-- role has no grant on schema_migrations, and must not get one, so it asks through this one function. It runs as the
-- owner, with a fixed search path, and returns only the name of the latest migration.

CREATE FUNCTION app_schema_version() RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public
AS $$ SELECT max(version) FROM public.schema_migrations $$;

REVOKE ALL ON FUNCTION app_schema_version() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_schema_version() TO app_rw;
