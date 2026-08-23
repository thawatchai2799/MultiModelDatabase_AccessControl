#!/usr/bin/env python3
"""Seed N resources (with real embeddings) into the stores a given scenario
uses. Run once per (scenario, scale, seed) combination before that trial's
revoke-then-poll measurement.

Usage:
  python seed.py --scenario a --n 10000 --seed 1
  python seed.py --scenario b --n 10000 --seed 1
  python seed.py --scenario c --n 10000 --seed 1   # same stores as 'a'
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scenarios.common import db
from scenarios.common.data import make_resources
from scenarios.common.ids import point_id_for


def seed_relational_layer(resources, truncate: bool):
    conn = db.connect_pg_primary()
    with conn.cursor() as cur:
        if truncate:
            cur.execute("TRUNCATE resources_layer_relational")
        # executemany batches the round trips -- at N=100,000 the original
        # one-INSERT-per-loop-iteration form does 100,000 individual network
        # round trips per seed call, and this is called once per (scenario,
        # scale, seed) trial -- with 5 seeds x 3 scales x 2 scenarios (A, C)
        # alone that is a lot of repeated seeding inside a 24-hour budget.
        # executemany does not eliminate the round trips entirely under
        # psycopg3 without pipeline mode, but batches statement parsing and
        # is materially faster in practice than the equivalent Python loop.
        cur.executemany(
            """INSERT INTO resources_layer_relational (resource_id, payload, acl)
               VALUES (%s, %s, %s)
               ON CONFLICT (resource_id) DO UPDATE SET payload = EXCLUDED.payload, acl = EXCLUDED.acl""",
            [(r.resource_id, __import__("json").dumps(r.payload), r.acl) for r in resources],
        )
    conn.commit()
    conn.close()
    print(f"  relational (Scenario A/C plain table): {len(resources)} rows")


def seed_nosql_layer(resources, truncate: bool):
    from pymongo import UpdateOne
    mongo = db.connect_mongo()
    coll = mongo.get_database().resources
    if truncate:
        coll.delete_many({})
    # bulk_write batches all N upserts into one round trip instead of N --
    # same rationale as executemany above for Postgres.
    ops = [
        UpdateOne(
            {"resource_id": r.resource_id},
            {"$set": {"payload": r.payload, "acl": r.acl}},
            upsert=True,
        )
        for r in resources
    ]
    if ops:
        coll.bulk_write(ops, ordered=False)
    mongo.close()
    print(f"  nosql (MongoDB): {len(resources)} documents")


def seed_vector_layer(resources, truncate: bool):
    qdrant = db.connect_qdrant()
    if truncate:
        # Delete-then-recreate directly, without first calling
        # ensure_qdrant_collection (which would create it, only for it to be
        # immediately deleted again below) -- avoids a pointless
        # create/delete/create round trip on every truncating seed call.
        try:
            qdrant.delete_collection("resources")
        except Exception:
            pass  # collection may not exist yet on the very first run
    db.ensure_qdrant_collection(qdrant, "resources")
    from qdrant_client.http import models as qmodels
    points = [
        qmodels.PointStruct(
            id=point_id_for(r.resource_id),
            vector=r.embedding,
            payload={"resource_id": r.resource_id, "acl": r.acl, **r.payload},
        )
        for r in resources
    ]
    BATCH = 500
    for i in range(0, len(points), BATCH):
        qdrant.upsert(collection_name="resources", points=points[i:i + BATCH])
    print(f"  vector (Qdrant): {len(resources)} points")


def _pgvector_literal(embedding: list[float]) -> str:
    """pgvector's text input format is [v1,v2,...] -- built explicitly
    without spaces rather than relying on Python's str(list) (which inserts
    ', ' between elements) since that format is untested against pgvector's
    parser and easy to get wrong silently."""
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


def seed_converged_layer(resources, truncate: bool):
    """Scenario B: one table, native pgvector + jsonb + RLS-governed acl.

    Must run as admin_user (BYPASSRLS, owner of resources_hot): `resources`
    has FORCE ROW LEVEL SECURITY and its policy has no separate WITH CHECK,
    so the USING clause is applied to INSERTed rows too -- app_user, with
    no app.principal_id set, would have every row rejected. TRUNCATE and
    REFRESH MATERIALIZED VIEW likewise need admin_user (privilege / owner)."""
    conn = db.connect_pg_admin()
    with conn.cursor() as cur:
        if truncate:
            cur.execute("TRUNCATE resources")
            cur.execute("REFRESH MATERIALIZED VIEW resources_hot")
        cur.executemany(
            """INSERT INTO resources (resource_id, payload, embedding, acl)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (resource_id) DO UPDATE
               SET payload = EXCLUDED.payload, embedding = EXCLUDED.embedding, acl = EXCLUDED.acl""",
            [(r.resource_id, __import__("json").dumps(r.payload), _pgvector_literal(r.embedding), r.acl)
             for r in resources],
        )
    conn.commit()
    conn.close()
    print(f"  converged (Postgres 'resources' table, RLS-governed): {len(resources)} rows")
    print("  NOTE: resources_hot materialized view NOT refreshed here on purpose for non-truncate"
          " runs -- B1 depends on it being able to go stale relative to 'resources'.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=["a", "b", "c"], required=True)
    ap.add_argument("--n", type=int, required=True, help="number of resources")
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--acl-size", type=int, default=3)
    ap.add_argument("--no-truncate", action="store_true", help="append instead of clearing existing data first")
    args = ap.parse_args()

    t0 = time.monotonic()
    print(f"Generating {args.n} resources (seed={args.seed}, embedding model download required on first run)...")
    resources = make_resources(args.n, seed=args.seed, acl_size=args.acl_size, embed=True)
    print(f"  generated in {time.monotonic() - t0:.1f}s")

    truncate = not args.no_truncate
    if args.scenario in ("a", "c"):
        seed_relational_layer(resources, truncate)
        seed_nosql_layer(resources, truncate)
        seed_vector_layer(resources, truncate)
    elif args.scenario == "b":
        seed_converged_layer(resources, truncate)

    # Report what is ACTUALLY in the store, not what this call inserted.
    # With --no-truncate the two differ, and reporting only the latter is how
    # a "scale = 10,000" run ended up measured on a 50,000-row base: the
    # stores had accumulated five seeds and nothing said so.
    _report_scale(args.scenario, len(resources), truncate)
    print(f"Done in {time.monotonic() - t0:.1f}s total.")


UNITS = {"postgres": "rows", "mongodb": "documents", "qdrant": "points"}


def _report_scale(scenario: str, expected: int, truncate: bool) -> None:
    """Say what each store actually holds and, if that disagrees with what
    was just seeded, name the disagreement AND the cause. The direction
    matters: fewer than expected is an incomplete insert, more than expected
    is accumulation (deliberate under --no-truncate, a failed truncate
    otherwise). An earlier version reported only "not equal" and attributed
    every case to --no-truncate, which would have pointed at the wrong thing
    in exactly the situations worth catching."""
    counts = _store_counts(scenario)
    for store, total in counts.items():
        print(f"  {store} now holds {total if total is not None else '?'} {UNITS.get(store, 'items')}")

    uncounted = [k for k, v in counts.items() if v is None]
    if uncounted:
        # A guard that stays quiet when it could not look is worse than no
        # guard: the run reads as clean.
        print(f"  WARNING: could not count {', '.join(uncounted)}, so this scale check is INCOMPLETE -- "
              f"a mismatch there would not have been reported.")

    short = {k: v for k, v in counts.items() if v is not None and v < expected}
    over = {k: v for k, v in counts.items() if v is not None and v > expected}
    fmt = lambda d: ", ".join(f"{k}={v}" for k, v in d.items())
    if short:
        print(f"  WARNING: {fmt(short)} -- FEWER than the {expected} just seeded. An insert did not "
              f"complete; this store is missing data. Do not report this run.")
    if over and truncate:
        print(f"  WARNING: this run truncated, yet {fmt(over)} instead of {expected}. A truncate did not "
              f"take effect; the effective scale is NOT {expected}. Do not report this run until the "
              f"stores agree.")
    elif over:
        print(f"  NOTE: --no-truncate was used, so earlier seeds are still present ({fmt(over)}). The "
              f"effective scale is what each store holds above, not {expected}. Truncate per seed unless "
              f"accumulation is what you want.")


def _store_counts(scenario: str) -> dict:
    """How much each store this scenario writes to actually holds right now.

    All three are counted for a/c, not just Postgres: a truncate can fail in
    one store and succeed in the others (seed_vector_layer deliberately
    ignores a delete_collection error, since the collection legitimately
    does not exist on a first run), and a guard that watches one store out
    of three would miss exactly that. Best effort throughout -- counting
    must never fail a seeding run."""
    counts = {"postgres": _pg_row_count(scenario)}
    if scenario != "b":
        counts["mongodb"] = _mongo_count()
        counts["qdrant"] = _qdrant_count()
    return counts


def _mongo_count():
    try:
        mongo = db.connect_mongo()
        try:
            return mongo.get_database().resources.count_documents({})
        finally:
            mongo.close()
    except Exception as e:
        print(f"  (could not count MongoDB documents: {e})")
        return None


def _qdrant_count():
    try:
        return db.connect_qdrant().count(collection_name="resources", exact=True).count
    except Exception as e:
        print(f"  (could not count Qdrant points: {e})")
        return None


def _pg_row_count(scenario: str):
    """Rows currently in the table this scenario measures. Best effort: a
    failure here must never fail a seeding run."""
    # Role matters here, and it differs per table (init.sql):
    #   resources_layer_relational -- granted to app_user only, and has no
    #     RLS, so app_user counts it correctly. admin_user was never granted
    #     it, which is how the first 100K run logged "permission denied".
    #   resources -- FORCE ROW LEVEL SECURITY, so app_user with no
    #     app.principal_id set would count 0 rather than error. admin_user
    #     (BYPASSRLS) is the only role that returns the true count.
    if scenario == "b":
        table, connect = "resources", db.connect_pg_admin
    else:
        table, connect = "resources_layer_relational", db.connect_pg_primary
    try:
        conn = connect()
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT count(*) FROM {table}")
                return cur.fetchone()[0]
        finally:
            conn.close()
    except Exception as e:
        print(f"  (could not count rows in {table}: {e})")
        return None


if __name__ == "__main__":
    main()
