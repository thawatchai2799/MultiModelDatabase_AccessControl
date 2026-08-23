"""Injection used to put Scenario A into its non-healthy regimes
(PROGRESS.md decision 11). Two mechanisms, deliberately simple and
reportable:

1. Transient store-call failure (regime "faulty"): each store write
   attempt fails independently with probability p. The naive app makes ONE
   attempt per store and does not retry, so a failed attempt is a revoke
   that never lands in that layer (until some later housekeeping). The
   bridge (Scenario C) is subjected to the SAME per-attempt p inside
   bridge/app/server.js (see /fault-config); it retries up to 3 times
   (decision 2), so a layer stays un-revoked only with probability p^3.
   Per-attempt semantics in both is what makes the comparison fair: the
   environment is identical, only the revoke discipline differs.

2. Asynchronous vector propagation (regime "async"): the vector layer is
   not written synchronously by the revoke path at all. Revokes are queued
   and a background worker flushes the queue every interval_s -- the usual
   shape of an embedding/indexing pipeline, where the vector store is fed
   by a batch job rather than by the request path. Only Scenario A has
   this regime: the bridge's whole point is that it replaces such a
   pipeline with synchronous, retried, anchored propagation.

Fault decisions are drawn from a seeded RNG so a run is reproducible, and
every decision is logged so the results record which layers were hit.
"""
import queue
import random
import threading
import time

from . import db


class FaultInjector:
    def __init__(self, p: float, seed: int):
        if not 0.0 <= p <= 1.0:
            raise ValueError(f"p must be in [0, 1], got {p}")
        self.p = p
        self._rng = random.Random(seed)
        self.decisions = []   # (layer, failed) in call order

    def should_fail(self, layer: str) -> bool:
        failed = self._rng.random() < self.p
        self.decisions.append((layer, failed))
        return failed

    def failed_layers(self) -> list:
        return [layer for layer, failed in self.decisions if failed]


class AsyncVectorWorker:
    """Background flusher for the vector layer. enqueue_revoke() returns
    immediately; the worker applies queued revokes every interval_s using
    its OWN Qdrant client (never the caller's). start() before use, stop()
    in a finally -- a worker left running would apply a later trial's
    revoke at an unrelated time."""

    def __init__(self, interval_s: float):
        if interval_s <= 0:
            raise ValueError("interval_s must be > 0")
        self.interval_s = interval_s
        self._q = queue.Queue()
        self._stop = threading.Event()
        self._thread = None
        self._client = None
        self.flushes = 0
        self.applied = []   # (resource_id, principal_id, t_applied)
        self.errors = []

    def start(self):
        self._client = db.connect_qdrant()
        self._thread = threading.Thread(target=self._run, daemon=True, name="async-vector-worker")
        self._thread.start()

    def enqueue_revoke(self, resource_id: str, principal_id: str) -> None:
        self._q.put((resource_id, principal_id))

    def _flush(self):
        items = []
        while True:
            try:
                items.append(self._q.get_nowait())
            except queue.Empty:
                break
        for resource_id, principal_id in items:
            try:
                db.revoke_vector(self._client, resource_id, principal_id)
                self.applied.append((resource_id, principal_id, time.monotonic()))
            except Exception as e:
                self.errors.append(f"{resource_id}/{principal_id}: {type(e).__name__}: {e}")
        self.flushes += 1

    def _run(self):
        while not self._stop.wait(self.interval_s):
            self._flush()

    def stop(self, drain: bool = False):
        """Stops the worker. drain=False (default) leaves anything still
        queued UNAPPLIED -- the harness restores the ACL afterwards anyway,
        and applying a stale revoke after the trial would be wrong."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval_s + 5.0)
        if drain:
            self._flush()
