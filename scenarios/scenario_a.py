"""Scenario A -- naive polyglot: three separate stores, no coordination.

revoke() fires all three store writes directly, exactly as an application
developer would if they simply called revoke_in_postgres(), revoke_in_mongo(),
revoke_in_qdrant() one after another (or concurrently) without any shared
ledger or retry discipline. This is deliberately the *worst case* baseline:
no retry, no mutex, whatever ordering asyncio.gather() happens to produce.
That absence of the coordination Scenario C adds is the entire point of the
comparison -- adding retry/mutex logic here would blur the exact thing being
measured.
"""
import time

from .common import db


def revoke(resource_id: str, principal_id: str, conns: dict,
           faults=None, async_vector=None) -> float:
    """Issues revoke against all three stores with no coordination.
    Returns the monotonic timestamp at which the revoke was issued (before
    any store call), for the caller to pass to the poller.

    faults: optional scenarios.common.faults.FaultInjector -- regime
    "faulty": a store call that the injector fails is simply NOT made (one
    attempt, no retry -- the naive app's discipline; the bridge under the
    same injector retries, decision 2).
    async_vector: optional scenarios.common.faults.AsyncVectorWorker --
    regime "async": the vector revoke is queued for the worker's next flush
    instead of being written here.
    """
    t_issued = time.monotonic()
    # Concurrency here is *unmanaged* on purpose -- a real naive integration
    # might call these in any order, sequentially or in parallel, and get no
    # guarantee either way. Sequential calls are used here only because
    # Python's DB clients in this harness are synchronous; this is still "no
    # coordination", just not concurrent, which if anything is a slightly
    # *more* naive (slower-to-close) baseline than a naive-but-concurrent
    # implementation would be -- worth noting in the paper as a
    # conservative choice, not something hidden.
    def attempt(layer, fn):
        if faults is not None and faults.should_fail(layer):
            print(f"  [scenario_a] {layer} revoke: injected transient failure (no retry)")
            return
        try:
            fn()
        except Exception as e:
            print(f"  [scenario_a] {layer} revoke failed: {e}")

    attempt("relational", lambda: db.revoke_relational(conns["pg"], resource_id, principal_id))
    attempt("nosql", lambda: db.revoke_nosql(conns["mongo"], resource_id, principal_id))
    if async_vector is not None:
        async_vector.enqueue_revoke(resource_id, principal_id)
    else:
        attempt("vector", lambda: db.revoke_vector(conns["qdrant"], resource_id, principal_id))
    return t_issued


def make_check_fns(resource_id: str, principal_id: str, conns: dict) -> dict:
    """One ground-truth check_fn per layer, for
    poll.measure_leak_windows_concurrent. Each must use a DIFFERENT client
    object: they run on separate threads (decision 10)."""
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
    # QdrantClient has no explicit close needed for the REST client used here.
