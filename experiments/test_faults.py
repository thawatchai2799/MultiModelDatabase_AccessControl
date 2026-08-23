# Unit test for scenarios/common/faults.py and scenario_a.revoke() regimes (no DB).
# Run from mldb/:  python3 experiments/test_faults.py
import sys, types, time
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import scenarios, scenarios.common
m = types.ModuleType("scenarios.common.db"); sys.modules["scenarios.common.db"] = m; scenarios.common.db = m
calls = []
m.revoke_relational = lambda c, r, p: calls.append("relational")
m.revoke_nosql = lambda c, r, p: calls.append("nosql")
m.revoke_vector = lambda c, r, p: calls.append(("vector", c))
m.connect_qdrant = lambda: "WORKER-CLIENT"
import importlib
faults = importlib.import_module("scenarios.common.faults"); sa = importlib.import_module("scenarios.scenario_a")
conns = {"pg": "PG", "mongo": "MO", "qdrant": "CALLER-CLIENT"}
sa.revoke("r", "p", conns); assert calls == ["relational", "nosql", ("vector", "CALLER-CLIENT")], calls
calls.clear(); f = faults.FaultInjector(1.0, seed=1); sa.revoke("r", "p", conns, faults=f)
assert calls == [] and f.failed_layers() == ["relational", "nosql", "vector"], (calls, f.decisions)
calls.clear(); f = faults.FaultInjector(0.0, seed=1); sa.revoke("r", "p", conns, faults=f); assert len(calls) == 3 and f.failed_layers() == []
a = faults.FaultInjector(0.3, seed=7); b = faults.FaultInjector(0.3, seed=7)
assert [a.should_fail("x") for _ in range(50)] == [b.should_fail("x") for _ in range(50)], "not deterministic"
f = faults.FaultInjector(0.3, seed=3); n = sum(f.should_fail("x") for _ in range(20000)); assert 0.28 < n / 20000 < 0.32
calls.clear(); w = faults.AsyncVectorWorker(0.2); w.start()
t0 = time.monotonic(); sa.revoke("r", "p", conns, async_vector=w)
assert calls == ["relational", "nosql"], calls
while not any(isinstance(c, tuple) for c in calls): time.sleep(0.005)
lag = time.monotonic() - t0; assert 0.15 < lag < 2.0, lag   # upper bound loose: host jitter
assert ("vector", "WORKER-CLIENT") in calls and w.applied and w.flushes >= 1
w.stop(); assert not w._thread.is_alive()
w2 = faults.AsyncVectorWorker(5.0); w2.start(); w2.enqueue_revoke("r2", "p2"); w2.stop()
assert w2.applied == [] and w2._q.qsize() == 1, "stop() must not apply queued revokes unless drain=True"
print(f"faults/scenario_a OK (async lag {lag:.3f}s)")
