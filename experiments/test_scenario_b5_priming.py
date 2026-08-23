# Regression test for B5: LogicalConsumer must know the PRE-revoke state at start()
# and must still lag after the live table changes, until its next poll.
# Run from mldb/:  python3 experiments/test_scenario_b5_priming.py
import sys, types, time
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import scenarios, scenarios.common
m = types.ModuleType("scenarios.common.db"); sys.modules["scenarios.common.db"] = m; scenarios.common.db = m
live = {"res-1": ["p1", "p2"]}
class Cur:
    def __enter__(s): return s
    def __exit__(s, *a): pass
    def execute(s, q, p=None): s.q = q
    def fetchone(s): return (1,) if "pg_replication_slots" in s.q else None
    def fetchall(s): return [(k, list(v)) for k, v in live.items()]
class Conn:
    def cursor(s): return Cur()
    def commit(s): pass
    def close(s): pass
m.connect_pg_admin = lambda: Conn()
import importlib; sb = importlib.import_module("scenarios.scenario_b")
c = sb.LogicalConsumer(poll_interval_s=0.2)
c.start()
assert c.is_retrievable("res-1", "p1") is True, "primed state must reflect DB before revoke"
live["res-1"] = ["p2"]  # the revoke lands in the live table
t0 = time.monotonic()
assert c.is_retrievable("res-1", "p1") is True, "consumer must still lag right after revoke"
while c.is_retrievable("res-1", "p1"):
    time.sleep(0.005)
lag = time.monotonic() - t0; c.stop()
assert 0.15 < lag < 2.0, lag   # upper bound loose: host jitter
print(f"B5 primed + lagged {lag:.3f}s (interval 0.2) OK")
