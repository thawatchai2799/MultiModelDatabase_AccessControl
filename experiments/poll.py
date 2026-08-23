"""Ground-truth Leak Window measurement.

Deliberately reusable across all three scenarios via a plain callable
(check_fn() -> bool), so the exact same measurement logic -- same polling
cadence, same clock, same stop condition -- is used whether the resource in
question lives in Scenario A/C's three separate stores or Scenario B's one
converged engine read through six different paths. Only what check_fn does
differs per scenario; how it's measured does not, which is what makes the
comparison across scenarios fair.

time.monotonic() is used throughout, never time.time() / wall-clock, so
measurements are immune to any NTP adjustment or VM clock-sync correction
during a run (see the VirtualBox clock-drift discussion earlier in this
project) -- see README.md's reproducibility notes for the full rationale
once written.
"""
import time
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# DEPRECATED as of PROGRESS.md decision 10. Nothing calls this any more; use
# measure_leak_windows_concurrent() below.
#
# It polls only AFTER the revoke call has returned, which is what made
# Scenario A unmeasurable on the first real VM run: all three stores had
# already closed by the time the first check ran, every window came back as a
# censored zero, and the failure was invisible in the output. It is kept only
# so the older result files (smoke_ab.jsonl, smoke13_ab.jsonl) can still be
# related to the code that produced them. Calling it again would silently
# reintroduce that bug.
# ---------------------------------------------------------------------------

@dataclass
class LeakMeasurement:
    layer: str
    t_revoke_issued: float          # monotonic seconds
    t_last_retrievable: float | None  # None if never observed retrievable after revoke
    leak_window_s: float
    checks_performed: int
    confirmed_contained: bool       # False if timeout was hit before containment confirmed
    samples: list = field(default_factory=list)  # (t_offset_s, retrievable) for diagnostics


def measure_leak_window(   # DEPRECATED -- see the banner below; do not call
    check_fn,
    t_revoke_issued: float,
    layer: str = "",
    poll_interval_s: float = 0.01,
    confirm_checks: int = 5,
    timeout_s: float = 30.0,
    record_samples: bool = False,
) -> LeakMeasurement:
    """Poll check_fn() (a zero-arg callable returning bool) starting
    immediately, until it returns False for `confirm_checks` consecutive
    polls in a row (guards against one transient blip being mistaken for
    real containment -- a single False right after a network hiccup is not
    evidence the layer is actually closed), or until timeout_s elapses.

    t_revoke_issued must be a time.monotonic() timestamp taken by the
    caller at the moment the revoke was issued (before this function is
    called), so the window includes whatever time the revoke call itself
    took to reach this point -- not just the polling loop's own duration.
    """
    t_last_retrievable = None
    consecutive_false = 0
    checks = 0
    samples = []

    deadline = t_revoke_issued + timeout_s
    while time.monotonic() < deadline:
        now = time.monotonic()
        retrievable = bool(check_fn())
        checks += 1
        if record_samples:
            samples.append((now - t_revoke_issued, retrievable))

        if retrievable:
            t_last_retrievable = now
            consecutive_false = 0
        else:
            consecutive_false += 1
            if consecutive_false >= confirm_checks:
                break

        time.sleep(poll_interval_s)

    confirmed = consecutive_false >= confirm_checks
    if t_last_retrievable is None:
        # Never observed retrievable, not even on the very first check --
        # the layer was already closed by (or before) t_revoke_issued.
        leak_window = 0.0
    else:
        leak_window = t_last_retrievable - t_revoke_issued

    return LeakMeasurement(
        layer=layer,
        t_revoke_issued=t_revoke_issued,
        t_last_retrievable=t_last_retrievable,
        leak_window_s=max(0.0, leak_window),
        checks_performed=checks,
        confirmed_contained=confirmed,
        samples=samples,
    )


# ---------------------------------------------------------------------------
# Concurrent, pre-revoke polling (added after the first real VM run)
# ---------------------------------------------------------------------------
#
# measure_leak_window() above starts polling only AFTER the revoke call has
# returned. On the first VM run that made Scenario A unmeasurable: the three
# store revokes completed in ~31 ms, the poller started after that, and every
# layer was already closed at its first check (window 0, censored). The metric
# is defined from revoke-ISSUED, so the poller has to be watching before the
# revoke is issued. This version runs one poller thread per layer, each with
# its own check_fn (and therefore its own DB connection -- psycopg3
# connections must never be shared across threads; PROGRESS.md decision 8),
# confirms every layer is retrievable BEFORE the revoke, then lets the caller
# issue the revoke while polling is already live.

import os
import sys
import threading

_POLL_DEBUG = os.environ.get("MLDB_POLL_DEBUG") == "1"


@dataclass
class ConcurrentLeakMeasurement:
    layer: str
    t_revoke_issued: float
    t_last_retrievable: float | None     # monotonic; may predate t_revoke_issued
    leak_window_s: float                 # LOWER bound: last instant the layer was PROVEN still open
    leak_window_upper_s: float | None    # UPPER bound: first instant it was PROVEN closed (None = never)
    checks_performed: int                # checks at or after t_revoke_issued
    max_gap_after_issue_s: float | None   # largest interval between consecutive post-issue checks
    confirmed_contained: bool
    retrievable_before_revoke: bool      # False => the trial's setup was wrong
    first_check_after_issue_s: float | None  # offset of the first post-issue check (censor bound for a 0)
    check_errors: int = 0               # checks that raised (counted, not treated as an answer)
    last_check_error: str | None = None
    error: str | None = None             # fatal: the poller thread died, or a hard-stop/clock flag
    samples: list = field(default_factory=list)


class _LayerPoller(threading.Thread):
    def __init__(self, layer, check_fn, issued, poll_interval_s, confirm_checks, timeout_s, record_samples):
        super().__init__(daemon=True, name=f"poller-{layer}")
        self.layer = layer
        self.check_fn = check_fn
        self.issued = issued              # dict: {"t": float|None}, set by the caller
        self.poll_interval_s = poll_interval_s
        self.confirm_checks = confirm_checks
        self.timeout_s = timeout_s
        self.record_samples = record_samples
        self.ready = threading.Event()    # set after the first pre-revoke check
        self.stop = threading.Event()     # set by the caller to force the loop to end
        self.retrievable_before_revoke = False
        self.t_last_retrievable = None
        self.checks_after_issue = 0
        self.confirmed = False
        self.first_check_after_issue = None
        self.last_check_after_issue = None
        self.max_gap_after_issue = None
        self.t_first_closed = None     # end of the first "not retrievable" answer since the last True
        self.check_errors = 0
        self.last_check_error = None
        self.error = None
        self.samples = []

    def run(self):
        consecutive_false = 0
        last_debug = time.monotonic()
        try:
            while not self.stop.is_set():
                now = time.monotonic()
                try:
                    retrievable = bool(self.check_fn())
                except Exception as e:
                    # A failed check is NOT an answer. Treating it as "not
                    # retrievable" would manufacture containment; treating it
                    # as "retrievable" would manufacture a leak. Count it,
                    # keep polling, and let the report say how many there
                    # were. Only a poller that never got a single answer is
                    # discarded (analyze.py).
                    self.check_errors += 1
                    self.last_check_error = f"{type(e).__name__}: {e}"
                    if self.issued["t"] is None:
                        self.ready.set()   # do not deadlock the caller on a failing pre-revoke check
                    if self.issued["t"] is not None and now >= self.issued["t"] + self.timeout_s:
                        return
                    time.sleep(self.poll_interval_s)
                    continue
                if _POLL_DEBUG and time.monotonic() - last_debug >= 2.0:
                    last_debug = time.monotonic()
                    ti = self.issued["t"]
                    print(f"  [poll-debug] {self.layer}: +{(last_debug - ti) if ti else float('nan'):.1f}s "
                          f"checks_after_issue={self.checks_after_issue} last_check_took={last_debug - now:.3f}s "
                          f"retrievable={retrievable}", file=sys.stderr, flush=True)
                t_issued = self.issued["t"]
                after_issue = t_issued is not None and now >= t_issued
                if self.record_samples:
                    self.samples.append((None if t_issued is None else now - t_issued, retrievable))
                # Timestamp taken AFTER the call returns: a True answer proves
                # the layer was still open at that instant -- later than, and
                # so a tighter (still conservative) bound than, the instant
                # the call started. A False answer only proves the layer was
                # closed by the time the call returned.
                t_answer = time.monotonic()
                if retrievable:
                    self.t_last_retrievable = t_answer
                    self.t_first_closed = None      # not closed after all
                elif self.t_first_closed is None:
                    self.t_first_closed = t_answer
                if not after_issue:
                    # Pre-revoke phase: only establish that the layer is live.
                    if retrievable:
                        self.retrievable_before_revoke = True
                    self.ready.set()
                    consecutive_false = 0
                else:
                    self.checks_after_issue += 1
                    if self.first_check_after_issue is None:
                        self.first_check_after_issue = now
                    else:
                        gap = now - self.last_check_after_issue
                        if self.max_gap_after_issue is None or gap > self.max_gap_after_issue:
                            self.max_gap_after_issue = gap
                    self.last_check_after_issue = now
                    if retrievable:
                        consecutive_false = 0
                    else:
                        consecutive_false += 1
                        if consecutive_false >= self.confirm_checks:
                            self.confirmed = True
                            return
                    if now >= t_issued + self.timeout_s:
                        return
                time.sleep(self.poll_interval_s)
        except Exception as e:  # never let a thread die silently
            self.error = f"{type(e).__name__}: {e}"
            self.ready.set()


def measure_leak_windows_concurrent(
    check_fns: dict,
    revoke_fn,
    poll_interval_s: float = 0.01,
    confirm_checks: int = 5,
    timeout_s: float = 30.0,
    ready_timeout_s: float = 10.0,
    record_samples: bool = False,
    before_revoke_fn=None,
):
    """check_fns: {layer: zero-arg callable -> bool}. Each callable must use
    its OWN connection (it runs on its own thread). revoke_fn: zero-arg
    callable that issues the revoke; its return value is passed back.

    before_revoke_fn: optional zero-arg callable run AFTER the pollers are
    warmed up and ready but BEFORE t_issued is taken. Anything whose timing
    must be measured relative to the revoke -- Scenario A's async batch
    worker (start it, then wait a random phase of its flush cycle) --
    belongs here. Doing it before this function instead lets the pollers'
    warm-up time silently eat into that interval, which is exactly what
    under-measured the async vector window on the first VM run.

    Sequence: start one poller per layer -> wait until every poller has
    completed at least one pre-revoke check (ready_timeout_s) ->
    before_revoke_fn() -> take t_issued and call revoke_fn() -> publish
    t_issued to the pollers -> join them. Returns (t_issued, revoke_return, revoke_latency_s,
    {layer: ConcurrentLeakMeasurement}).

    leak_window_s is a LOWER bound (the last instant the layer was proven
    still open, relative to the revoke); leak_window_upper_s is the matching
    UPPER bound (the first instant it was proven closed). The true window
    lies in [leak_window_s, leak_window_upper_s]; their difference is the
    measurement resolution for that trial and widens when the host starves
    the poller. leak_window_upper_s is None if containment was never
    observed (timeout).

    A layer whose first post-issue check is already "not retrievable" gets
    leak_window_s == 0; the true window is somewhere in
    [0, first_check_after_issue_s] -- at most about one poll interval,
    instead of the whole revoke duration as with post-revoke polling.
    """
    issued = {"t": None}
    pollers = {
        layer: _LayerPoller(layer, fn, issued, poll_interval_s, confirm_checks, timeout_s, record_samples)
        for layer, fn in check_fns.items()
    }
    for p in pollers.values():
        p.start()
    deadline = time.monotonic() + ready_timeout_s
    for p in pollers.values():
        remaining = deadline - time.monotonic()
        if not p.ready.wait(timeout=max(0.0, remaining)):
            p.error = p.error or f"poller not ready within {ready_timeout_s}s"

    if before_revoke_fn is not None:
        before_revoke_fn()

    t_issued = time.monotonic()
    issued["t"] = t_issued            # pollers read this on their next check
    revoke_return = revoke_fn()
    revoke_latency_s = time.monotonic() - t_issued

    # Hard stop enforced from HERE, not only inside the threads: every poller
    # must be done by t_issued + timeout_s (+ a grace period for one slow
    # check). On the first concurrent-poller VM run a B4 poller ran for 108 s
    # against a 30 s timeout for a reason not yet understood; whatever it
    # was, the experiment must not depend on the thread's own loop noticing.
    grace_s = 5.0
    for p in pollers.values():
        remaining = (t_issued + timeout_s + grace_s) - time.monotonic()
        p.join(timeout=max(0.0, remaining))
        if p.is_alive():
            p.stop.set()
            p.join(timeout=grace_s)
        if p.is_alive():
            # Stuck in a blocking client call. The caller will go on to close
            # that layer's connection from its own thread, which is the one
            # concurrent-use case psycopg3 cannot tolerate -- make it loud.
            p.error = (p.error or "") + f" [poller still alive {timeout_s + 2 * grace_s:.0f}s after issue -- stuck in a client call]"
            print(f"  [poll] WARNING: {p.layer} poller did not finish; its connection may still be in use")
    t_end = time.monotonic()

    results = {}
    for layer, p in pollers.items():
        if p.check_errors and p.checks_after_issue == 0 and p.t_last_retrievable is None:
            p.error = (p.error or "") + f" [every check failed: {p.last_check_error}]"
        if p.t_last_retrievable is None:
            window = 0.0
        else:
            window = max(0.0, p.t_last_retrievable - t_issued)
        if window > timeout_s + grace_s:
            # Physically impossible for this poller: it stops at timeout.
            # The only way to get here is a clock jump (e.g. the VM was
            # paused and CLOCK_MONOTONIC caught up on resume) -- seen once on
            # the VM as a "108 s" B4 window against a 30 s timeout, with a
            # check count that matched exactly 30 s of polling. Flag, don't
            # report it as a measurement.
            p.error = (p.error or "") + f" [clock anomaly: window {window:.1f}s exceeds timeout {timeout_s:.0f}s -- VM stall?]"
            p.confirmed = False
        if _POLL_DEBUG:
            print(f"  [poll-debug] {layer}: done at +{t_end - t_issued:.2f}s, checks_after_issue={p.checks_after_issue}, "
                  f"confirmed={p.confirmed}, window={window:.3f}", file=sys.stderr, flush=True)
        results[layer] = ConcurrentLeakMeasurement(
            layer=layer,
            t_revoke_issued=t_issued,
            t_last_retrievable=p.t_last_retrievable,
            leak_window_s=window,
            leak_window_upper_s=(None if p.t_first_closed is None else max(0.0, p.t_first_closed - t_issued)),
            checks_performed=p.checks_after_issue,
            max_gap_after_issue_s=p.max_gap_after_issue,
            confirmed_contained=p.confirmed,
            retrievable_before_revoke=p.retrievable_before_revoke,
            first_check_after_issue_s=(None if p.first_check_after_issue is None else p.first_check_after_issue - t_issued),
            check_errors=p.check_errors,
            last_check_error=p.last_check_error,
            error=p.error,
            samples=p.samples,
        )
    return t_issued, revoke_return, revoke_latency_s, results
