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

Two schedules (v1.2.0):

  "stream" -- the original: one seeded stream per process, consumed in call
      order. Scenario A (Python, random.Random) and the bridge (Node,
      mulberry32) therefore draw DIFFERENT failures even for the same seed:
      the same failure *rate*, not the same failures. Every result reported
      in the paper's first submission used this schedule, so it stays the
      default and is untouched.

  "paired" -- the decision for (seed, layer, attempt) is a pure function of
      those three values, computed identically here and in
      bridge/app/server.js (mulberry32 seeded from a mix of the three; see
      paired_fault_u). Attempt k of a layer fails in every configuration or
      in none: Scenario A's single attempt is attempt 0, and the bridge's
      first attempt is the same attempt 0, so a seed in which A loses the
      nosql write is a seed in which the bridge's first nosql write also
      fails and only its retry can save it. A vs C then differ in the revoke
      discipline alone, and the per-seed outcomes can be analysed as
      matched pairs (experiments/ablation_table.py). Call order is
      irrelevant, which matters in the bridge where the three layers'
      retries interleave on timers.
"""
import queue
import random
import threading
import time

from . import db

LAYER_INDEX = {"relational": 0, "nosql": 1, "vector": 2}
SCHEDULES = ("stream", "paired")


def _imul(a: int, b: int) -> int:
    """Math.imul: low 32 bits of the product, as a signed 32-bit integer."""
    r = (a * b) & 0xFFFFFFFF
    return r - 0x100000000 if r >= 0x80000000 else r


def _u32(x: int) -> int:
    return x & 0xFFFFFFFF


def mulberry32_first(seed: int) -> float:
    """The first output of mulberry32(seed), matching server.js exactly.
    Operations mirror the JS, where `t` is a signed 32-bit value between
    steps and the final `>>> 0` makes it unsigned."""
    a = _u32(_u32(seed) + 0x6D2B79F5)
    t = a
    t = _imul(t ^ (t >> 15), t | 1)                        # t ^ (t >>> 15): a is unsigned, so >> is >>>
    t = _u32(t)
    t ^= _u32(t + _imul(t ^ (t >> 7), t | 61))
    t = _u32(t)
    return _u32(t ^ (t >> 14)) / 4294967296


def paired_fault_u(seed: int, layer: str, attempt: int) -> float:
    """Uniform in [0, 1) for one (seed, layer, attempt). The mix constants
    are the same in server.js; a change to either side must be made to both
    and re-verified with bridge/gen_paired_fault_test.py."""
    li = LAYER_INDEX[layer]
    s = _u32(seed) ^ _u32((li + 1) * 0x9E3779B1) ^ _u32((attempt + 1) * 0x85EBCA77)
    return mulberry32_first(s)


class FaultInjector:
    def __init__(self, p: float, seed: int, schedule: str = "stream"):
        if not 0.0 <= p <= 1.0:
            raise ValueError(f"p must be in [0, 1], got {p}")
        if schedule not in SCHEDULES:
            raise ValueError(f"schedule must be one of {SCHEDULES}, got {schedule!r}")
        self.p = p
        self.seed = seed
        self.schedule = schedule
        self._rng = random.Random(seed)
        self._attempts = {}   # layer -> attempts made so far (paired schedule)
        self.decisions = []   # (layer, failed) in call order

    def should_fail(self, layer: str) -> bool:
        if self.schedule == "paired":
            k = self._attempts.get(layer, 0)
            self._attempts[layer] = k + 1
            failed = paired_fault_u(self.seed, layer, k) < self.p
        else:
            failed = self._rng.random() < self.p
        self.decisions.append((layer, failed))
        return failed

    def should_fail_attempt(self, layer: str, attempt: int) -> bool:
        """Decision for an explicit attempt index. Scenario O's relay keeps
        the attempt count in the outbox row, so it passes it in rather than
        relying on this object's own per-layer counter (which the relay's
        thread and the revoke thread would otherwise share). Under the
        stream schedule the index is ignored and the stream is consumed in
        call order, as before."""
        if self.schedule == "paired":
            failed = paired_fault_u(self.seed, layer, attempt) < self.p
        else:
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
