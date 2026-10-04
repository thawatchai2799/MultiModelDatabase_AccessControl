-- Transactional outbox for Scenario O (v1.2.0): the durable-recovery
-- baseline the first submission named in Section VII-A but did not measure.
--
-- The pattern: the application's own relational write and one outbox row per
-- remote layer are committed in ONE PostgreSQL transaction, so a revoke that
-- has returned to its caller is guaranteed to be in the outbox. A relay
-- (scenarios/scenario_o.py OutboxWorker) then drains pending rows to MongoDB
-- and Qdrant with unbounded retry. What the pattern promises is eventual
-- propagation; what it does not promise is any bound on when, any record a
-- database administrator cannot rewrite, or any answer that comes from
-- outside the database holding the data. Those are the three properties the
-- comparison against Scenario C measures rather than argues.
--
-- Run once as the database superuser on an already-initialised cluster:
--   docker exec -i mldb-postgres-primary psql -U mldb -d mldb < postgres/primary/outbox.sql
-- On a fresh volume docker-compose mounts this file as 02-outbox.sql and the
-- image's entrypoint applies it after 01-init.sql (which creates app_user).

CREATE TABLE IF NOT EXISTS revoke_outbox (
    id              bigserial PRIMARY KEY,
    event_id        text        NOT NULL,
    resource_id     text        NOT NULL,
    principal_id    text        NOT NULL,
    action          text        NOT NULL CHECK (action IN ('grant', 'revoke')),
    layer           text        NOT NULL CHECK (layer IN ('nosql', 'vector')),
    status          text        NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'abandoned')),
    attempts        integer     NOT NULL DEFAULT 0,
    -- The relay's retry clock, in monotonic seconds supplied by the harness
    -- (never wall-clock: the whole study's timing rule). NULL means "now".
    next_attempt_at double precision,
    created_at      timestamptz NOT NULL DEFAULT now(),
    applied_at      timestamptz,
    last_error      text,
    UNIQUE (event_id, layer)
);

CREATE INDEX IF NOT EXISTS revoke_outbox_pending
    ON revoke_outbox (status, next_attempt_at) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS revoke_outbox_lookup
    ON revoke_outbox (resource_id, principal_id, id DESC);

-- The application owns this table outright: the relay UPDATEs rows as it
-- applies them and the harness UPDATEs leftover rows to 'abandoned' (it
-- never deletes, so the table and the result file agree). DELETE is granted
-- anyway because that is the point: an outbox is application state, and
-- nothing stops the application -- or its administrator -- from editing it.
GRANT SELECT, INSERT, UPDATE, DELETE ON revoke_outbox TO app_user;
GRANT USAGE, SELECT ON SEQUENCE revoke_outbox_id_seq TO app_user;
