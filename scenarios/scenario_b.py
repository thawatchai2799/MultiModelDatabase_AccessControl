"""Scenario B -- native converged database (Postgres 18 + pgvector + jsonb +
RLS), the "already solved" strawman. All six B1-B6 sub-mechanisms share one
engine and one acl column on the `resources` table; what differs between
them is only the *read path* used to check retrievability -- this is the
whole point of RQ2: does even a single, "converged" engine still leak,
depending on which of its own features is queried through?

revoke()/grant() always go through admin_user (BYPASSRLS) on the primary --
see init.sql note 4b for why. Every is_retrievable_* function below queries
through app_user (RLS-enforced) or a specific alternate read path, never
through admin_user, since admin_user bypassing RLS entirely would make every
one of these checks meaningless.
"""
import threading
import time

from .common import db

# ---------------------------------------------------------------------------
# Shared: revoke/grant (always admin_user, BYPASSRLS) and the RLS session
# variable helper (always app_user).
# ---------------------------------------------------------------------------

def revoke(resource_id: str, principal_id: str) -> float:
    t_issued = time.monotonic()
    conn = db.connect_pg_admin()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE resources SET acl = array_remove(acl, %s) WHERE resource_id = %s",
                (principal_id, resource_id),
            )
        conn.commit()
    finally:
        conn.close()
    return t_issued


def grant(resource_id: str, principal_id: str) -> None:
    conn = db.connect_pg_admin()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE resources SET acl = array_append(acl, %s)
                   WHERE resource_id = %s AND NOT (%s = ANY(acl))""",
                (principal_id, resource_id, principal_id),
            )
        conn.commit()
    finally:
        conn.close()


def _set_principal(conn, principal_id: str, local: bool) -> None:
    """set_config(), not a raw SET string, so principal_id is a bound
    parameter rather than interpolated into SQL text."""
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.principal_id', %s, %s)", (principal_id, local))
    if not local:
        conn.commit()


# ---------------------------------------------------------------------------
# B1 -- materialized view staleness
# ---------------------------------------------------------------------------

def is_retrievable_b1(conn, resource_id: str, principal_id: str) -> bool:
    """resources_hot has no RLS (materialized views can't carry policies),
    so the acl check is done explicitly in the query -- matching what an
    application querying a materialized "read model" would actually do,
    since it cannot rely on RLS to protect a view that doesn't support it."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM resources_hot WHERE resource_id = %s AND %s = ANY(acl)",
            (resource_id, principal_id),
        )
        result = cur.fetchone() is not None
    conn.commit()
    return result


class MaterializedViewRefresher:
    """Runs REFRESH MATERIALIZED VIEW CONCURRENTLY on a fixed interval in a
    background thread, simulating a real deployment's periodic refresh
    schedule (refreshing on every write is what resources_hot's own comment
    in init.sql says is too expensive at scale to do). CONCURRENTLY is used
    so the refresh doesn't block concurrent reads against resources_hot
    while it runs (requires the unique index already created in init.sql).
    """
    def __init__(self, interval_s: float = 5.0):
        self.interval_s = interval_s
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    @staticmethod
    def refresh_once():
        """One synchronous refresh. Also used by the harness to put the view
        into a known state before and after each B1 trial, so a trial's
        starting point never depends on whether the previous trial's
        background thread happened to refresh after its ACL was restored."""
        conn = db.connect_pg_admin()
        try:
            with conn.cursor() as cur:
                cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY resources_hot")
            conn.commit()
        except Exception as e:
            print(f"  [B1 refresher] refresh failed: {e}")
        finally:
            conn.close()

    def _run(self):
        while not self._stop.wait(self.interval_s):
            self.refresh_once()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval_s + 5)


# ---------------------------------------------------------------------------
# B2 -- streaming replica lag
# ---------------------------------------------------------------------------

def is_retrievable_b2(conn, resource_id: str, principal_id: str) -> bool:
    """conn must be a connection to the REPLICA (db.connect_pg_replica()).
    RLS itself replicates correctly (it's schema, not data) -- the only
    reason this can say "yes" after a revoke is genuine WAL replication lag,
    not a policy bypass."""
    _set_principal(conn, principal_id, local=True)
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM resources WHERE resource_id = %s", (resource_id,))
        result = cur.fetchone() is not None
    conn.commit()
    return result


# ---------------------------------------------------------------------------
# B3 -- HNSW iterative_scan (relaxed_order trades filter correctness for
# speed; this is pgvector's own documented behaviour, not speculation about
# undocumented internals -- see PROGRESS.md's B3 note for why this
# replaced the original vaguer framing).
# ---------------------------------------------------------------------------

def is_retrievable_b3(conn, resource_id: str, principal_id: str, embedding: list, relaxed: bool) -> bool:
    """Runs a filtered kNN query (nearest neighbours to `embedding`, WHERE
    resource_id matches, RLS applied) and reports whether resource_id
    appears in the result set at all -- rather than just checking existence
    directly, this exercises the actual ANN index path, which is the point
    of B3 (a plain existence check would go through the primary key index
    and never touch HNSW at all)."""
    with conn.cursor() as cur:
        if relaxed:
            cur.execute("SET hnsw.iterative_scan = relaxed_order")
        else:
            cur.execute("SET hnsw.iterative_scan = strict_order")
    _set_principal(conn, principal_id, local=True)
    lit = "[" + ",".join(repr(float(x)) for x in embedding) + "]"
    with conn.cursor() as cur:
        cur.execute(
            """SELECT resource_id FROM resources
               ORDER BY embedding <=> %s
               LIMIT 50""",
            (lit,),
        )
        rows = {row[0] for row in cur.fetchall()}
    conn.commit()
    return resource_id in rows


# ---------------------------------------------------------------------------
# B4 -- long-running transaction / MVCC snapshot
# ---------------------------------------------------------------------------

class OpenSnapshot:
    """Opens a REPEATABLE READ transaction, takes one read to fix its
    snapshot, and holds it open until close() -- simulating a long report-
    generation query that started before the revoke and is still running.
    Under REPEATABLE READ, every statement inside this transaction sees the
    data as of the transaction's start, regardless of what commits
    elsewhere afterward -- so this is expected TO leak for as long as the
    transaction stays open, by design of the isolation level, not a bug in
    Postgres. What's being measured is simply: how long do such snapshots
    typically stay open in practice, and does that constitute a meaningful
    exposure window."""
    def __init__(self, principal_id: str):
        self.conn = db.connect_pg_primary()
        self.conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        # NB on what actually fixes the REPEATABLE READ snapshot: it is
        # whichever statement below runs FIRST after the isolation-level
        # statement, not specifically the SELECT 1 -- under REPEATABLE READ,
        # the snapshot is established at the first query of any kind in the
        # transaction, which here is _set_principal's own
        # "SELECT set_config(...)" call, not the SELECT 1 that follows it.
        # The SELECT 1 is kept anyway as an explicit, self-documenting
        # confirmation that a snapshot-fixing statement has run, since
        # relying on _set_principal's internals to do this implicitly would
        # be less obvious to a future reader.
        _set_principal(self.conn, principal_id, local=True)
        with self.conn.cursor() as cur:
            cur.execute("SELECT 1")

    def is_retrievable(self, resource_id: str) -> bool:
        with self.conn.cursor() as cur:
            cur.execute("SELECT 1 FROM resources WHERE resource_id = %s", (resource_id,))
            return cur.fetchone() is not None
        # deliberately no commit() here -- committing would end the
        # transaction and defeat the entire point of this class.

    def close(self):
        self.conn.rollback()
        self.conn.close()


# ---------------------------------------------------------------------------
# B5 -- logical replication / downstream consumer lag
# ---------------------------------------------------------------------------

class LogicalConsumer:
    """Simulates a downstream analytics pipeline consuming `resources` via
    logical replication (the resources_pub publication from init.sql)
    rather than querying the live table directly. Maintains its own local
    acl snapshot, updated only when it polls its replication slot -- which
    it does on a fixed interval, mirroring a real batch-consuming pipeline
    rather than a live subscriber. This is a deliberate simplification of a
    full second-database logical replication subscriber (see PROGRESS.md):
    reading the slot's pending changes directly is enough to measure how far
    behind such a consumer runs without standing up an entire second
    Postgres instance for it.
    """
    SLOT_NAME = "b5_consumer_slot"

    def __init__(self, poll_interval_s: float = 3.0):
        self.poll_interval_s = poll_interval_s
        self.local_acl = {}
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

    def _ensure_slot(self, conn):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_replication_slots WHERE slot_name = %s", (self.SLOT_NAME,)
            )
            if cur.fetchone() is None:
                cur.execute(
                    "SELECT pg_create_logical_replication_slot(%s, 'pgoutput')", (self.SLOT_NAME,)
                )
        conn.commit()

    def start(self):
        conn = db.connect_pg_admin()
        try:
            self._ensure_slot(conn)
        finally:
            conn.close()
        # Prime the consumer's local state SYNCHRONOUSLY before the thread
        # starts. Without this, local_acl is {} until the first poll fires
        # poll_interval_s later, so a revoke issued right after start() is
        # checked against an empty snapshot, is_retrievable() returns False
        # on the very first check, and every B5 trial reports a 0 s window
        # -- a false "contained" that has nothing to do with consumer lag.
        # The consumer must start out knowing the PRE-revoke state.
        self._poll_once()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.wait(self.poll_interval_s):
            self._poll_once()

    def _poll_once(self):
        conn = db.connect_pg_admin()
        try:
            with conn.cursor() as cur:
                # This does NOT actually decode the replication slot's
                # pending WAL changes (pg_logical_slot_peek_changes with
                # the pgoutput plugin needs protocol options -- proto_version,
                # publication_names -- that add complexity without adding
                # anything this specific measurement needs). The slot's
                # role here is to exist as the nominal "this consumer is a
                # logical-replication client" marker; the actual state
                # capture is a plain poll of the live table on a fixed
                # interval, which is what genuinely batch-consuming
                # pipelines look like in practice. This measures the
                # consumer's *polling interval* as the leak mechanism
                # (a batch pipeline that only updates its local view every
                # poll_interval_s) rather than true logical-decoding
                # replay lag -- a narrower but still honest claim, flagged
                # here rather than overclaiming full CDC-replay fidelity.
                cur.execute("SELECT resource_id, acl FROM resources")
                with self._lock:
                    self.local_acl = {row[0]: row[1] for row in cur.fetchall()}
            conn.commit()
        except Exception as e:
            print(f"  [B5 consumer] poll failed: {e}")
        finally:
            conn.close()

    def is_retrievable(self, resource_id: str, principal_id: str) -> bool:
        with self._lock:
            acl = self.local_acl.get(resource_id, [])
        return principal_id in acl

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.poll_interval_s + 5)
        conn = db.connect_pg_admin()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_drop_replication_slot(%s)", (self.SLOT_NAME,))
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# B6 -- pgbouncer transaction-pooling session staleness
# ---------------------------------------------------------------------------

def leak_via_pgbouncer_session(principal_id_attacker: str, resource_id: str) -> bool:
    """Deterministic (via pgbouncer's pool_size=1 'mldb_b6' entry, see
    pgbouncer.ini) reproduction of B6: connection A sets app.principal_id at
    SESSION scope (is_local=false) and disconnects without resetting it
    (server_reset_query is deliberately empty in pgbouncer.ini for this
    database); connection B is a genuinely separate psycopg Connection
    object -- simulating a different request/client -- that queries WITHOUT
    setting its own principal_id at all, exactly as an application that
    "forgot to re-issue SET at the start of every pooled transaction" would.
    Returns True if connection B can still see the resource -- i.e. the
    stale session variable from connection A leaked across the pool.

    This is inherently a per-call boolean (leaked or not), not a timed
    window like B1/B2/B4/B5 -- reported as an occurrence rate across many
    calls, not a Leak Window in seconds. See PROGRESS.md."""
    conn_a = db.connect_pg_pgbouncer_b6()
    try:
        _set_principal(conn_a, principal_id_attacker, local=False)
    finally:
        conn_a.close()  # returned to pgbouncer's pool WITHOUT session reset

    conn_b = db.connect_pg_pgbouncer_b6()
    try:
        # deliberately NOT calling _set_principal here -- this is the bug
        # being tested for, not something to avoid.
        with conn_b.cursor() as cur:
            cur.execute("SELECT 1 FROM resources WHERE resource_id = %s", (resource_id,))
            leaked = cur.fetchone() is not None
        conn_b.commit()
        return leaked
    finally:
        conn_b.close()
