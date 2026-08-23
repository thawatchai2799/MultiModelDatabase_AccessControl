# Tests for seed.py's post-seed scale guard, with mocked stores (no DB).
# The guard exists because a run that silently measured 50,000 rows while
# reporting "scale = 10,000" nearly reached the paper.
# Run from mldb/:  python3 experiments/test_seed_guard.py
import sys, types, io, contextlib
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import scenarios, scenarios.common
m = types.ModuleType("scenarios.common.db"); sys.modules["scenarios.common.db"] = m; scenarios.common.db = m

state = {"pg": 1000, "mongo": 1000, "qdrant": 1000, "pg_fail": None, "mongo_fail": None, "qdrant_fail": None}
calls = []

class Cur:
    def __enter__(s): return s
    def __exit__(s, *a): pass
    def execute(s, q): calls.append(q)
    def fetchone(s): return (state["pg"],)
class PgConn:
    def cursor(s): return Cur()
    def close(s): calls.append("pg-close")
def _pg(role):
    def f():
        calls.append(role)
        if state["pg_fail"]: raise state["pg_fail"]
        return PgConn()
    return f
m.connect_pg_primary = _pg("as app_user")
m.connect_pg_admin = _pg("as admin_user")

class MongoColl:
    def count_documents(s, flt): return state["mongo"]
class MongoDb:
    resources = MongoColl()
class Mongo:
    def get_database(s): return MongoDb()
    def close(s): calls.append("mongo-close")
def _mongo():
    if state["mongo_fail"]: raise state["mongo_fail"]
    return Mongo()
m.connect_mongo = _mongo

class QCount:
    def __init__(s, n): s.count = n
class Q:
    def count(s, collection_name, exact): return QCount(state["qdrant"])
def _qdrant():
    if state["qdrant_fail"]: raise state["qdrant_fail"]
    return Q()
m.connect_qdrant = _qdrant

import importlib
seed = importlib.import_module("experiments.seed")
ok = True
def check(name, cond, extra=""):
    global ok
    print(("  ok  " if cond else "  FAIL ") + name + ("" if cond else f": {extra}"))
    ok = ok and cond

print("1) the right role reads the right table")
calls.clear(); seed._store_counts("a")
check("a/c counts resources_layer_relational as app_user",
      calls[0] == "as app_user" and "resources_layer_relational" in calls[1], str(calls[:2]))
calls.clear(); seed._store_counts("b")
check("b counts resources as admin_user (BYPASSRLS; app_user would see 0, not an error)",
      calls[0] == "as admin_user" and "FROM resources" in calls[1], str(calls[:2]))

print("2) all three stores are counted for a/c, only Postgres for b")
check("a/c -> three stores", set(seed._store_counts("a")) == {"postgres", "mongodb", "qdrant"})
check("b -> Postgres only", set(seed._store_counts("b")) == {"postgres"})

print("3) a store that cannot be counted is reported, not fatal")
state["qdrant_fail"] = ConnectionError("qdrant down")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    c = seed._store_counts("a")
state["qdrant_fail"] = None
check("qdrant failure -> None and a message", c["qdrant"] is None and "could not count Qdrant" in buf.getvalue())
check("the other stores are still counted", c["postgres"] == 1000 and c["mongodb"] == 1000)

print("4) a truncate that silently failed in ONE store is caught")
# Postgres truncated, Qdrant did not (delete_collection swallowed) --
# exactly the failure the single-store version of this guard would miss.
state["qdrant"] = 5000
c = seed._store_counts("a")
mismatched = {k: v for k, v in c.items() if v is not None and v != 1000}
check("the disagreeing store is identified", mismatched == {"qdrant": 5000}, str(mismatched))
state["qdrant"] = 1000
print("5) the warning text matches the actual cause")
# Drive main() with the seeding functions stubbed out, so only the guard runs.
seed.make_resources = lambda n, seed=0, acl_size=3, embed=True: [object()] * n
seed.seed_relational_layer = lambda r, t: None
seed.seed_nosql_layer = lambda r, t: None
seed.seed_vector_layer = lambda r, t: None
seed.seed_converged_layer = lambda r, t: None

def run_main(argv):
    old = sys.argv
    sys.argv = ["seed.py"] + argv
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            seed.main()
    finally:
        sys.argv = old
    return buf.getvalue()

state["pg"] = state["mongo"] = state["qdrant"] = 1000
out = run_main(["--scenario", "a", "--n", "1000", "--seed", "1"])
check("stores agree -> no warning at all", "WARNING" not in out and "NOTE" not in out, out)

state["pg"] = state["mongo"] = state["qdrant"] = 5000
out = run_main(["--scenario", "a", "--n", "1000", "--seed", "1", "--no-truncate"])
check("--no-truncate mismatch -> NOTE, not WARNING", "NOTE: --no-truncate" in out and "WARNING" not in out, out)
check("NOTE names the real scale", "5000" in out, out)

out = run_main(["--scenario", "a", "--n", "1000", "--seed", "1"])
check("truncate requested but stores disagree -> WARNING, and it does NOT blame --no-truncate",
      "WARNING: this run truncated" in out and "--no-truncate was used" not in out, out)
check("WARNING says not to report the run", "Do not report this run" in out, out)

state["pg"] = 1000; state["mongo"] = 1000; state["qdrant"] = 5000
out = run_main(["--scenario", "a", "--n", "1000", "--seed", "1"])
check("only the disagreeing store is named", "qdrant=5000" in out and "postgres=" not in out, out)
state["pg"] = state["mongo"] = state["qdrant"] = 1000

print("6) direction of the mismatch, and an incomplete check")
state["pg"] = state["mongo"] = 1000; state["qdrant"] = 400
out = run_main(["--scenario", "a", "--n", "1000", "--seed", "1"])
check("fewer than seeded -> 'insert did not complete', not a truncate story",
      "FEWER than the 1000" in out and "insert did not complete" in out and "truncate did not take effect" not in out, out)

state["qdrant"] = 5000
out = run_main(["--scenario", "a", "--n", "1000", "--seed", "1"])
check("more than seeded, truncate requested -> truncate story", "truncate did not take effect" in out, out)
check("and no false 'fewer' claim alongside it", "FEWER than" not in out, out)

state["pg"] = state["mongo"] = state["qdrant"] = 1000
state["qdrant_fail"] = ConnectionError("qdrant down")
out = run_main(["--scenario", "a", "--n", "1000", "--seed", "1"])
state["qdrant_fail"] = None
check("a store that could not be counted makes the check INCOMPLETE, loudly",
      "scale check is INCOMPLETE" in out and "qdrant" in out, out)
check("...and the run is not otherwise reported as clean", "WARNING" in out, out)

out = run_main(["--scenario", "b", "--n", "1000", "--seed", "1"])
check("scenario b reports rows, not items", "postgres now holds 1000 rows" in out, out)
out = run_main(["--scenario", "a", "--n", "1000", "--seed", "1"])
check("units per store are named correctly",
      "mongodb now holds 1000 documents" in out and "qdrant now holds 1000 points" in out, out)

print("ALL SEED-GUARD TESTS PASSED" if ok else "FAILURES")
sys.exit(0 if ok else 1)
