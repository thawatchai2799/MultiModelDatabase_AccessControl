"""Scenario O -- transactional outbox (v1.2.0): the durable-recovery baseline
that the first submission named (Section VII-A) but did not measure.

The pattern, as an application developer would build it without a bridge:

  revoke():  in ONE PostgreSQL transaction, remove the principal from the
             relational ACL and insert one outbox row per remote layer
             (nosql, vector). Commit. Return. From this point the revoke is
             durable: a relay that dies is restarted and finds the rows.

  OutboxWorker: a relay thread that polls the outbox every poll_interval_s,
             applies each pending row to its store, marks it done, and on
             failure schedules the row again with exponential backoff --
             UNBOUNDED retry, which is the property the bounded-retry bridge
             lacks and the reason 1 of 20 trials in Table VII stayed open.

What is deliberately the same as Scenarios A and C: the stores, the schema,
the store operations (db.py), the ground-truth poller, and the fault
injection -- attempt k of a layer is the same decision here as in A (its
only attempt) and in the bridge (its k-th attempt) under the paired
schedule, so O differs from both in revoke discipline alone.

What the relay's own opinion is, and is not: outbox_status() reports
"contained" when no row for the pair's latest revoke is pending. That is
the application's bookkeeping, read from the same database that holds the
data, by the same role that can edit it. It is fetched for the
self-report-versus-truth split, exactly as the bridge's /status is, and is
never used as the measurement.

The relational write is attempted in the same transaction as the outbox
insert, so it cannot be retried by the relay; it is retried inline with the
same backoff, since an outbox transaction that fails to commit is a revoke
the caller has not been told is durable. Its attempts are numbered from 0
like every other layer's.
"""
import threading
import time
import uuid

from .common import db

REMOTE_LAYERS = ("nosql", "vector")
BASE_BACKOFF_S = 0.05      # 50 ms, doubling -- the bridge's schedule (decision 2) ...
MAX_BACKOFF_S = 1.0        # ... capped, because the relay's retry is unbounded
INLINE_MAX_ATTEMPTS = 50   # relational: bounded only by the trial's own timeout in practice


def _next_backoff(attempts: int) -> float:
    return min(BASE_BACKOFF_S * (2 ** attempts), MAX_BACKOFF_S)


def revoke(resource_id: str, principal_id: str, conns: dict, faults=None) -> tuple[float, dict]:
    """Issues the revoke as one transaction: relational ACL update + outbox
    rows. Returns (t_issued, info) where info records the event id, the
    inline attempts the relational write took, and whether the transaction
    committed. The remote layers are NOT touched here -- that is the relay's
    job, and the whole point of the pattern."""
    t_issued = time.monotonic()
    event_id = f"obx-{uuid.uuid4()}"
    conn = conns["pg"]
    attempts = 0
    committed = False
    last_error = None
    while attempts < INLINE_MAX_ATTEMPTS and not committed:
        k = attempts
        attempts += 1
        try:
            if faults is not None and faults.should_fail_attempt("relational", k):
                raise RuntimeError("injected transient failure (relational)")
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE resources_layer_relational
                           SET acl = array_remove(acl, %s)
                           WHERE resource_id = %s""",
                        (principal_id, resource_id),
                    )
                    cur.executemany(
                        """INSERT INTO revoke_outbox (event_id, resource_id, principal_id, action, layer)
                           VALUES (%s, %s, %s, 'revoke', %s)""",
                        [(event_id, resource_id, principal_id, layer) for layer in REMOTE_LAYERS],
                    )
            committed = True
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            print(f"  [scenario_o] relational+outbox transaction attempt {k} failed: {last_error}")
            time.sleep(_next_backoff(k))
    return t_issued, {"event_id": event_id, "inline_attempts": attempts, "committed": committed,
                      "last_error": last_error}


class OutboxWorker:
    """The relay. Own connections (never the caller's: the poller and the
    revoke path run on other threads, and a psycopg connection must not be
    shared across threads -- decision 8). start() before the revoke, stop()
    in a finally: a relay left running would apply a later trial's rows at
    an unrelated time, which is the harness artefact the structural
    guarantee in run_experiment.py exists to prevent."""

    def __init__(self, poll_interval_s: float, faults=None):
        if poll_interval_s <= 0:
            raise ValueError("poll_interval_s must be > 0")
        self.poll_interval_s = poll_interval_s
        self.faults = faults
        self._stop = threading.Event()
        self._thread = None
        self._pg = None
        self._mongo = None
        self._qdrant = None
        self.applied = []      # (event_id, layer, attempts_used, t_applied)
        self.failures = []     # (event_id, layer, attempt_index, error)
        self.polls = 0
        self.error = None      # a relay-thread death; the trial must record it

    def start(self):
        self._pg = db.connect_pg_primary()
        self._mongo = db.connect_mongo()
        self._qdrant = db.connect_qdrant()
        self._thread = threading.Thread(target=self._run, daemon=True, name="outbox-relay")
        self._thread.start()

    def _claim_pending(self):
        now = time.monotonic()
        with self._pg.cursor() as cur:
            cur.execute(
                """SELECT id, event_id, resource_id, principal_id, layer, attempts
                   FROM revoke_outbox
                   WHERE status = 'pending' AND (next_attempt_at IS NULL OR next_attempt_at <= %s)
                   ORDER BY id""",
                (now,),
            )
            rows = cur.fetchall()
        self._pg.commit()
        return rows

    def _apply_one(self, row):
        row_id, event_id, resource_id, principal_id, layer, attempts = row
        k = attempts
        try:
            if self.faults is not None and self.faults.should_fail_attempt(layer, k):
                raise RuntimeError(f"injected transient failure ({layer})")
            if layer == "nosql":
                db.revoke_nosql(self._mongo, resource_id, principal_id)
            elif layer == "vector":
                db.revoke_vector(self._qdrant, resource_id, principal_id)
            else:
                raise RuntimeError(f"unexpected outbox layer {layer!r}")
            with self._pg.cursor() as cur:
                cur.execute(
                    """UPDATE revoke_outbox SET status = 'done', attempts = %s, applied_at = now()
                       WHERE id = %s AND status = 'pending'""",
                    (k + 1, row_id),
                )
            self._pg.commit()
            self.applied.append((event_id, layer, k + 1, time.monotonic()))
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            self.failures.append((event_id, layer, k, err))
            with self._pg.cursor() as cur:
                cur.execute(
                    """UPDATE revoke_outbox SET attempts = %s, next_attempt_at = %s, last_error = %s
                       WHERE id = %s AND status = 'pending'""",
                    (k + 1, time.monotonic() + _next_backoff(k), err[:500], row_id),
                )
            self._pg.commit()

    def _run(self):
        try:
            while not self._stop.is_set():
                self.polls += 1
                for row in self._claim_pending():
                    if self._stop.is_set():
                        break
                    self._apply_one(row)
                self._stop.wait(self.poll_interval_s)
        except Exception as e:
            # Recorded, not raised: a dead relay is a finding about the
            # trial, and the main thread reads it after the poll.
            self.error = f"{type(e).__name__}: {e}"

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.poll_interval_s + 10.0)
        for client in (self._pg, self._mongo, self._qdrant):
            try:
                if client is not None:
                    client.close()
            except Exception:
                pass

    def stats(self) -> dict:
        per_layer = {}
        for _, layer, attempts, _ in self.applied:
            per_layer[layer] = {"applied": True, "attempts": attempts}
        for _, layer, k, err in self.failures:
            per_layer.setdefault(layer, {"applied": False, "attempts": 0})
            per_layer[layer]["attempts"] = max(per_layer[layer]["attempts"], k + 1)
            per_layer[layer]["last_error"] = err
        return {"polls": self.polls, "layers": per_layer, "relay_error": self.error}


def outbox_status(conn, resource_id: str, principal_id: str, event_id: str | None = None) -> dict:
    """The application's own opinion: is THIS trial's revoke fully relayed?
    Mirrors the shape of the bridge's /status so the self-report-versus-truth
    split can be computed the same way. This is read from the database that
    holds the data, by the role that can edit both -- which is exactly what
    it does not have in common with the ledger's verdict.

    event_id scopes the verdict to the trial's own revoke. Without it the
    latest revoke for the pair is used -- and because the same pair is
    picked for a given (scale, seed) in every pass, a trial whose own
    transaction never committed would otherwise be answered by an earlier
    pass's finished event and scored as a false containment it did not
    commit. The harness always passes the event id."""
    with conn.cursor() as cur:
        if event_id is None:
            cur.execute(
                """SELECT event_id FROM revoke_outbox
                   WHERE resource_id = %s AND principal_id = %s AND action = 'revoke'
                   ORDER BY id DESC LIMIT 1""",
                (resource_id, principal_id),
            )
            row = cur.fetchone()
            if row is None:
                conn.commit()
                return {"contained": False, "reason": "no revoke on record"}
            event_id = row[0]
        cur.execute(
            """SELECT layer, status, attempts FROM revoke_outbox WHERE event_id = %s ORDER BY layer""",
            (event_id,),
        )
        rows = cur.fetchall()
    conn.commit()
    if not rows:
        # The event id is known but no row exists: the transaction that
        # would have written them never committed. Nothing was accepted,
        # so nothing can be claimed contained.
        return {"contained": False, "reason": "revoke not committed (no outbox rows)", "eventId": event_id,
                "missingLayers": list(REMOTE_LAYERS), "rows": []}
    missing = [layer for layer, status, _ in rows if status != "done"]
    return {"contained": not missing, "missingLayers": missing, "eventId": event_id,
            "rows": [{"layer": l, "status": s, "attempts": a} for l, s, a in rows]}


def abandon_all_pending(conn) -> int:
    """Called at the START of every trial. A row left pending by an
    interrupted run (Ctrl-C before the trial's own abandon_event) would be
    applied by this trial's relay against a pair whose ACL has since been
    restored, and a later trial on that pair would then begin already
    revoked and be excluded as an invalid setup. Nothing pending can
    belong to the trial that is about to start, so abandoning everything
    is safe."""
    with conn.cursor() as cur:
        cur.execute("UPDATE revoke_outbox SET status = 'abandoned' WHERE status = 'pending'")
        n = cur.rowcount
    conn.commit()
    return n


def abandon_event(conn, event_id: str) -> int:
    """Harness housekeeping after a trial: any row still pending must not be
    applied by a later trial's relay. Marked rather than deleted so the
    result file and the table agree about what happened."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE revoke_outbox SET status = 'abandoned' WHERE event_id = %s AND status = 'pending'",
            (event_id,),
        )
        n = cur.rowcount
    conn.commit()
    return n


def make_check_fns(resource_id: str, principal_id: str, conns: dict) -> dict:
    """Identical to scenario_a's -- same stores, same schema."""
    return {
        "relational": lambda: db.is_retrievable_relational(conns["pg"], resource_id, principal_id),
        "nosql": lambda: db.is_retrievable_nosql(conns["mongo"], resource_id, principal_id),
        "vector": lambda: db.is_retrievable_vector(conns["qdrant"], resource_id, principal_id),
    }


def open_connections() -> dict:
    return {"pg": db.connect_pg_primary(), "mongo": db.connect_mongo(), "qdrant": db.connect_qdrant()}


def close_connections(conns: dict) -> None:
    conns["pg"].close()
    conns["mongo"].close()
