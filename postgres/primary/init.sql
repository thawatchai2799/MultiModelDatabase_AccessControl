-- Runs once, automatically, on first container start (mounted into
-- /docker-entrypoint-initdb.d/ per the postgres image convention).

-- 1. Replication role for the streaming replica (Scenario B2).
CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD 'replicator_dev_only_change_me';

-- 2. Extensions needed for Scenario B ("native converged": relational +
--    vector + document-ish data in one engine).
CREATE EXTENSION IF NOT EXISTS vector;

-- 3. The resource table. jsonb payload stands in for the NoSQL layer inside
--    the converged engine; embedding is pgvector; owner_id/acl drive RLS.
CREATE TABLE IF NOT EXISTS resources (
    resource_id   TEXT PRIMARY KEY,
    payload       JSONB NOT NULL,
    embedding     VECTOR(384) NOT NULL,
    -- acl is the ground-truth access list for this resource, maintained by
    -- whichever scenario's revoke path is under test.
    acl           TEXT[] NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- HNSW index for ANN search (this is the index type B3 targets: the
-- approximate-search seam where a filter can be applied after candidate
-- retrieval rather than before it, depending on query plan).
CREATE INDEX IF NOT EXISTS resources_embedding_hnsw
    ON resources USING hnsw (embedding vector_cosine_ops);

-- Materialized view for B1 (materialized-view staleness): a "hot resources"
-- view that a real deployment would refresh periodically rather than on
-- every write, since REFRESH MATERIALIZED VIEW is expensive at scale.
CREATE MATERIALIZED VIEW IF NOT EXISTS resources_hot AS
    SELECT resource_id, payload, embedding, acl FROM resources;
CREATE UNIQUE INDEX IF NOT EXISTS resources_hot_pk ON resources_hot (resource_id);

-- 4. Row-Level Security: a principal may see a row only if their session
--    variable app.principal_id appears in that row's acl. This is the
--    "already solved" mechanism Scenario B is supposed to represent.
ALTER TABLE resources ENABLE ROW LEVEL SECURITY;
ALTER TABLE resources FORCE ROW LEVEL SECURITY;

CREATE POLICY resources_acl_policy ON resources
    USING (current_setting('app.principal_id', true) = ANY(acl));

-- Note: resources_hot is a materialized view, not a table -- RLS policies
-- cannot be attached to it directly. Its staleness *relative to* resources'
-- live RLS state is exactly what Scenario B1 measures, so this is by design,
-- not an oversight: a query against resources_hot has no ACL filter applied
-- at all until the next manual/scheduled refresh.

-- 4b. Without a FOR clause, resources_acl_policy above applies to every
--     command including UPDATE -- which would mean the harness's own
--     admin/system process issuing a revoke would need its own principal_id
--     to already be a member of the resource's acl just to UPDATE it, which
--     is not how access-control administration works in a real deployment
--     (the party revoking access is not, in general, a party the access
--     applied to). A dedicated admin role with BYPASSRLS mirrors the usual
--     real-world separation: an admin/system service account manages ACLs,
--     while ordinary application sessions (app_user, RLS-enforced) can only
--     read what their own principal_id is entitled to.

-- 5. A non-superuser app role, so RLS is actually enforced (superusers and
--    table owners bypass RLS by default, which would silently make every
--    Scenario B test pass regardless of what we're trying to measure).
-- NB: password_encryption is set to md5 for this role specifically so that
-- pgbouncer (Scenario B6) can authenticate it via a plain MD5 userlist.txt
-- entry without needing SCRAM passthrough support -- a simplification that
-- is fine for this local, non-internet-facing experiment and removes a
-- version-dependent pgbouncer/Postgres SCRAM-relay failure mode that would
-- add operational risk without adding anything to what B6 measures.
SET password_encryption = 'md5';
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_user') THEN
        CREATE ROLE app_user WITH LOGIN PASSWORD 'app_user_dev_only_change_me';
    END IF;
END
$$;
RESET password_encryption;
GRANT SELECT, INSERT, UPDATE, DELETE ON resources TO app_user;
GRANT SELECT ON resources_hot TO app_user;

-- 6. A second, plain table for Scenarios A and C's "relational layer".
--    Deliberately has NO RLS and NO native enforcement of any kind -- in
--    those two scenarios, every layer (this table, MongoDB, Qdrant) relies
--    on the querying application to include an acl filter itself, matching
--    how MongoDB and Qdrant are used throughout. Scenario B is the only one
--    that exercises native enforcement (the RLS-protected `resources` table
--    above); mixing the two mechanisms into one table would blur exactly
--    the comparison the paper is making.
CREATE TABLE IF NOT EXISTS resources_layer_relational (
    resource_id   TEXT PRIMARY KEY,
    payload       JSONB NOT NULL,
    acl           TEXT[] NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS resources_layer_relational_acl_gin
    ON resources_layer_relational USING gin (acl);
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON resources_layer_relational TO app_user;  -- TRUNCATE: seed.py (no RLS on this table)

-- 7. Logical replication publication for Scenario B5 ("downstream analytics
--    pipeline" staleness): a consumer that reads resources via logical
--    decoding rather than a live query lags behind by however long it takes
--    to poll/apply its replication slot, which is a different and slower
--    mechanism than B2's physical streaming replication.
CREATE PUBLICATION resources_pub FOR TABLE resources;

-- 8. Admin role for grant/revoke administration (see note 4b). BYPASSRLS is
--    a real, intentionally narrow privilege escalation -- exactly the kind
--    of role a production access-control system would have, used here only
--    for the harness's own administrative revoke/grant calls, never for
--    reads (those always go through app_user, RLS-enforced). REPLICATION is
--    also needed here: Scenario B5's pg_create_logical_replication_slot()
--    requires the calling role to have the REPLICATION attribute (or be
--    superuser) -- without it, B5's slot creation fails outright.
SET password_encryption = 'md5';
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'admin_user') THEN
        CREATE ROLE admin_user WITH LOGIN PASSWORD 'admin_user_dev_only_change_me' BYPASSRLS REPLICATION;
    END IF;
END
$$;
RESET password_encryption;
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON resources TO admin_user;  -- TRUNCATE: seed.py seeds 'resources' as admin_user (RLS would reject app_user's INSERTs)
-- Materialized views are read-only through normal DML (only REFRESH
-- MATERIALIZED VIEW can change their contents) -- granting INSERT/UPDATE/
-- DELETE here would be accepted syntactically but could never be exercised,
-- so only SELECT is granted, matching what's actually usable.
GRANT SELECT ON resources_hot TO admin_user;
-- REFRESH MATERIALIZED VIEW has no separate grantable privilege in Postgres
-- -- only the owner (or a superuser) may run it. Transferring ownership to
-- admin_user is what lets Scenario B1's periodic-refresh simulation run as
-- admin_user rather than needing the Postgres superuser credentials in the
-- Python harness.
ALTER MATERIALIZED VIEW resources_hot OWNER TO admin_user;

-- ---------------------------------------------------------------------------
-- Append-only audit log (see postgres/primary/audit_log.sql for the rationale
-- and for the one-command migration on an already-initialised cluster).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS access_audit_log (
    id            bigserial PRIMARY KEY,
    event_id      text        NOT NULL,
    resource_id   text        NOT NULL,
    principal_id  text        NOT NULL,
    action        text        NOT NULL CHECK (action IN ('grant', 'revoke')),
    ts            text        NOT NULL,
    layer         text        CHECK (layer IN ('relational', 'nosql', 'vector')),
    -- One row per (event, layer), and one event row with layer NULL. A unique
    -- index cannot treat NULLs as equal, so a generated key stands in for the
    -- event row; this mirrors the chaincode's composite key, where the three
    -- concurrent propagation writes touch three distinct keys and so never
    -- contend with one another.
    layer_key     text GENERATED ALWAYS AS (COALESCE(layer, '@event')) STORED,
    recorded_at   timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS access_audit_log_event_layer
    ON access_audit_log (event_id, layer_key);
CREATE INDEX IF NOT EXISTS access_audit_log_lookup
    ON access_audit_log (resource_id, principal_id, ts DESC);

-- INSERT and SELECT only: no UPDATE, no DELETE, no TRUNCATE.
GRANT SELECT, INSERT ON access_audit_log TO app_user;
GRANT USAGE, SELECT ON SEQUENCE access_audit_log_id_seq TO app_user;
