# Logic/timing test for measure_leak_windows_concurrent (no DB).
# Run from mldb/:  python3 experiments/test_poll_concurrent.py
import sys, time, threading
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from experiments.poll import measure_leak_windows_concurrent


def assert_window(m, true_close_s, label, extra_slack_s=0.05):
    """The poller reports a BRACKET: leak_window_s is the last instant the
    layer was proven still open and leak_window_upper_s the first instant it
    was proven closed. The property to assert is that the true close time
    falls inside that bracket -- not that the reported number matches a
    fixed constant, which is wrong on a fast host and on a starved one
    alike (an earlier version of this file failed on the VM by 5 us).
    """
    lo = m.leak_window_s
    hi = m.leak_window_upper_s
    assert hi is not None, f"{label}: containment was never proven"
    assert lo <= hi, f"{label}: bracket inverted ({lo} > {hi})"
    assert lo - extra_slack_s <= true_close_s <= hi + extra_slack_s, (
        f"{label}: true close {true_close_s:.3f}s outside the proven bracket "
        f"[{lo:.4f}, {hi:.4f}] (width {(hi - lo) * 1000:.0f} ms)")

def make_layer(close_delay_after_revoke):
    """Retrievable until `close_delay` seconds after revoke() is called."""
    st = {"t_rev": None}
    def revoke(): st["t_rev"] = time.monotonic()
    def check():
        return st["t_rev"] is None or (time.monotonic() - st["t_rev"]) < close_delay_after_revoke
    return revoke, check

# --- 1) three layers closing at 0.00 (during revoke), 0.12, 0.30 s; revoke itself takes 0.05 s
L = {"relational": make_layer(0.0), "nosql": make_layer(0.12), "vector": make_layer(0.30)}
def revoke_all():
    L["relational"][0](); time.sleep(0.05); L["nosql"][0](); L["vector"][0](); return "ok"
t0, ret, lat, res = measure_leak_windows_concurrent({k: v[1] for k, v in L.items()}, revoke_all,
                                                   poll_interval_s=0.01, confirm_checks=3, timeout_s=2.0)
# Absolute time bounds anywhere in this file are loose: on a loaded VM the
# host can starve a thread for hundreds of ms (observed). Window values are
# checked with assert_window(), which is scaled to the observed check gap.
assert ret == "ok" and 0.05 <= lat < 2.0, lat
for k, m in res.items():
    assert m.retrievable_before_revoke and m.confirmed_contained and m.error is None, (k, m)
# relational closed immediately at t0 -> window ~0 with a censor bound of about one poll interval, NOT the revoke latency
r = res["relational"]; assert r.leak_window_s < 0.05 and r.first_check_after_issue_s is not None and r.first_check_after_issue_s < 1.0, r
# nosql revoked at t0+0.05, closes 0.12 later -> ~0.17 ; vector ~0.35
n = res["nosql"];  assert_window(n, 0.05 + 0.12, "nosql")     # revoked at t0+0.05, closes 0.12 later
v = res["vector"]; assert_window(v, 0.05 + 0.30, "vector")
assert r.leak_window_s < n.leak_window_s < v.leak_window_s, "layers must be ordered by when they closed"
print(f"1) timing ok: rel={r.leak_window_s:.3f} (bound {r.first_check_after_issue_s:.3f}) nosql={n.leak_window_s:.3f} vec={v.leak_window_s:.3f} lat={lat:.3f}")

# --- 2) never closes -> timeout, not confirmed. Asserted on semantics, not on
# an exact duration: on a loaded VM the poller can be starved for hundreds of
# ms (observed), which delays the last check and stretches the window. What
# must hold is that it timed out, the window is at least the timeout (a lower
# bound), and the whole call still returned within the hard stop.
t0 = time.monotonic()
_, _, _, res = measure_leak_windows_concurrent({"x": lambda: True}, lambda: None, poll_interval_s=0.01, confirm_checks=3, timeout_s=0.3)
elapsed = time.monotonic() - t0
m = res["x"]
assert not m.confirmed_contained, m
assert m.leak_window_s >= 0.28, m.leak_window_s
assert elapsed < 0.3 + 10.0, elapsed        # hard stop = timeout + 2 x grace
assert m.checks_performed >= 1, m
print(f"2) timeout ok (window {m.leak_window_s:.3f}s, {m.checks_performed} checks, "
      f"max gap {0 if m.max_gap_after_issue_s is None else m.max_gap_after_issue_s * 1000:.0f} ms)")

# --- 3) never retrievable even before revoke -> flagged, window 0, confirmed
t0, _, _, res = measure_leak_windows_concurrent({"x": lambda: False}, lambda: None, poll_interval_s=0.01, confirm_checks=3, timeout_s=1.0)
m = res["x"]; assert m.retrievable_before_revoke is False and m.leak_window_s == 0.0 and m.confirmed_contained, m
print("3) not-retrievable-before-revoke flagged")

# --- 4) check_fn raises -> error captured, other layers unaffected, no hang
def boom(): raise RuntimeError("db down")
L2 = make_layer(0.05)
t0, _, _, res = measure_leak_windows_concurrent({"bad": boom, "good": L2[1]}, L2[0], poll_interval_s=0.01, confirm_checks=3, timeout_s=1.0)
assert "db down" in res["bad"].error and res["good"].confirmed_contained and res["good"].error is None
print("4) exception isolated")

# --- 5) transient blip is not containment: False once then True for a while, then closes
st = {"t_rev": None, "n": 0}
def rev5(): st["t_rev"] = time.monotonic()
def chk5():
    st["n"] += 1
    if st["t_rev"] is None: return True
    dt = time.monotonic() - st["t_rev"]
    if 0.02 < dt < 0.03: return False      # one-poll blip
    return dt < 0.15
t0, _, _, res = measure_leak_windows_concurrent({"x": chk5}, rev5, poll_interval_s=0.01, confirm_checks=3, timeout_s=1.0)
assert_window(res["x"], 0.15, "blip")
print(f"5) blip ignored: {res['x'].leak_window_s:.3f}")

# --- 6) each poller runs on its own thread (thread identity differs per layer, and from main)
seen = {}
def mk(name):
    def f():
        seen.setdefault(name, set()).add(threading.get_ident()); return False
    return f
measure_leak_windows_concurrent({"a": mk("a"), "b": mk("b")}, lambda: None, poll_interval_s=0.01, confirm_checks=2, timeout_s=0.5)
assert len(seen["a"]) == 1 and len(seen["b"]) == 1 and seen["a"] != seen["b"] and threading.get_ident() not in seen["a"] | seen["b"]
print("6) per-layer threads ok")

# --- 7) before_revoke_fn runs after warm-up and before t_issued
order = []
st7 = {"t": None}
def before7(): order.append("before"); time.sleep(0.15)
def rev7(): order.append("revoke"); st7["t"] = time.monotonic()
def chk7(): return st7["t"] is None or (time.monotonic() - st7["t"]) < 0.10
t0 = time.monotonic()
t_iss, _, _, res = measure_leak_windows_concurrent({"x": chk7}, rev7, poll_interval_s=0.01,
                                                  confirm_checks=3, timeout_s=2.0, before_revoke_fn=before7)
assert order == ["before", "revoke"], order
assert t_iss - t0 >= 0.15, "the hook's time must be spent before t_issued"
assert_window(res["x"], 0.10, "before_revoke_fn")
print("7) before_revoke_fn ordering ok")

# --- 8) a check that raises is counted, not answered: intermittent errors must
# neither manufacture containment nor a leak; an all-failing poller is flagged.
st8 = {"t": None, "n": 0}
def chk8():
    st8["n"] += 1
    if st8["n"] % 3 == 0: raise TimeoutError("store timed out")
    return st8["t"] is None or (time.monotonic() - st8["t"]) < 0.10
_, _, _, res = measure_leak_windows_concurrent({"x": chk8}, lambda: st8.__setitem__("t", time.monotonic()),
                                               poll_interval_s=0.01, confirm_checks=3, timeout_s=2.0)
m = res["x"]
assert m.confirmed_contained and m.check_errors > 0 and m.error is None, m
assert_window(m, 0.10, "check-errors")
t0 = time.monotonic()
def always_bad(): raise ConnectionError("store down")
_, _, _, res = measure_leak_windows_concurrent({"x": always_bad}, lambda: None,
                                               poll_interval_s=0.01, confirm_checks=3, timeout_s=0.5)
m = res["x"]
assert m.error and "every check failed" in m.error and not m.retrievable_before_revoke, m
assert time.monotonic() - t0 < 12, "an all-failing poller must still finish inside the hard stop"
print("8) check errors counted, never answered")

# --- 9) starved host: every check takes 50-200 ms (what the VM effectively
# does). Windows must still be ordered and never under-report, and the
# starvation must be visible in max_gap_after_issue_s.
import random as _random
rng9 = _random.Random(4)
st9 = {"t": None}
def rev9(): time.sleep(0.05); st9["t"] = time.monotonic()
def mk9(delay):
    def f():
        time.sleep(rng9.uniform(0.05, 0.2))
        return st9["t"] is None or (time.monotonic() - st9["t"]) < delay
    return f
_, _, _, res = measure_leak_windows_concurrent({"a": mk9(0.0), "b": mk9(0.5), "c": mk9(1.0)}, rev9,
                                               poll_interval_s=0.01, confirm_checks=3, timeout_s=5.0)
assert all(m.confirmed_contained and m.error is None for m in res.values()), res
assert res["a"].leak_window_s < res["b"].leak_window_s < res["c"].leak_window_s, res
# Each window is the last check that still saw the layer open, so with checks
# ~180 ms apart it can sit up to one gap BELOW the true close time -- that is
# the resolution limit this test exists to demonstrate, not an error. What
# must hold is |window - true close| <= one max gap.
for layer, true_close in (("b", 0.5), ("c", 1.0)):
    assert_window(res[layer], true_close, f"starved-{layer}")
assert all(m.max_gap_after_issue_s > 0.05 for m in res.values()), "starvation must show up in max_gap"
print("9) starved-host simulation ok")
print("ALL POLL TESTS PASSED")
