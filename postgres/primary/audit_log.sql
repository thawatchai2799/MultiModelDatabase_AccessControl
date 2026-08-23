-- Append-only audit log: the third audit backend, used to measure what a
-- plain database table gives compared with the ledger (paper Section 6.3.3).
--
-- Run once as the database superuser on an already-initialised cluster:
--   docker exec -i mldb-postgres-primary psql -U mldb -d mldb < postgres/primary/audit_log.sql
-- On a fresh volume, init.sql applies it automatically.
--
-- "Append-only" here means app_user is granted INSERT and SELECT and nothing
-- else, so the application cannot rewrite history. It does not mean the table
-- is immutable: the superuser, the admin role, or anyone with filesystem
-- access to the data directory can still alter it, and the table would carry
-- no trace of that. That difference is the point of the comparison, not an
-- oversight, and Section 8 says so.

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
