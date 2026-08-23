"""Shared database clients for the experiment harness.

Two families of functions:

  1. connect_* -- raw connection/client factories, one per distinct network
     path a scenario or the ground-truth poller needs (primary, replica,
     pgbouncer, mongo, qdrant).

  2. Scenario A/C layer operations (grant_relational/revoke_relational/...,
     grant_nosql/..., grant_vector/...) -- these are a deliberate,
     line-for-line Python port of bridge/app/stores.js's three store
     clients, because the ground-truth poller must query each layer
     *exactly* the way a correctly-written application would (the same
     query shape the bridge itself uses), or the "ground truth" it reports
     is not actually testing the same thing as the bridge. Scenario A also
     calls these functions directly (no bridge in front), matching its
     definition as "naive polyglot, no coordination".

Scenario B's operations live in scenarios/scenario_b.py instead, since they
are structurally different (one engine, multiple read paths) rather than
"the same operation against three stores".
"""
import os

import psycopg
from pymongo import MongoClient
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from .ids import point_id_for

# ---------------------------------------------------------------------------
# Connection factories
# ---------------------------------------------------------------------------

PG_HOST = os.environ.get("POSTGRES_HOST", "localhost")
PG_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))
PG_REPLICA_PORT = int(os.environ.get("POSTGRES_REPLICA_PORT", "5433"))
PG_PGBOUNCER_PORT = int(os.environ.get("PGBOUNCER_PORT", "6432"))
PG_DB = os.environ.get("POSTGRES_DB", "mldb")
PG_APP_USER = os.environ.get("POSTGRES_APP_USER", "app_user")
PG_APP_PASSWORD = os.environ.get("POSTGRES_APP_PASSWORD", "app_user_dev_only_change_me")
PG_ADMIN_USER = os.environ.get("POSTGRES_ADMIN_USER", "admin_user")
PG_ADMIN_PASSWORD = os.environ.get("POSTGRES_ADMIN_PASSWORD", "admin_user_dev_only_change_me")

MONGO_URI = os.environ.get(
    "MONGO_URI", "mongodb://app_user:app_user_dev_only_change_me@localhost:27017/mldb"
)
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
BRIDGE_URL = os.environ.get("BRIDGE_URL", "http://localhost:8080")

EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "384"))

# Every client gets an explicit, bounded timeout. Without one, a single call
# can block forever: on the first faulty-regime VM run a Qdrant scroll from a
# poller thread hung for minutes (a trial that should have taken 30 s took
# 196 s), which both stalls the run and leaves a connection in use while the
# main thread is closing it. A hung call must surface as an error the poller
# records and moves on from, not as an unbounded wait.
PG_CONNECT_TIMEOUT_S = int(os.environ.get("PG_CONNECT_TIMEOUT_S", "10"))
MONGO_TIMEOUT_MS = int(os.environ.get("MONGO_TIMEOUT_MS", "5000"))
QDRANT_TIMEOUT_S = int(os.environ.get("QDRANT_TIMEOUT_S", "10"))


def connect_pg_primary() -> psycopg.Connection:
    return psycopg.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_APP_USER, password=PG_APP_PASSWORD, connect_timeout=PG_CONNECT_TIMEOUT_S
    )


def connect_pg_replica() -> psycopg.Connection:
    return psycopg.connect(
        host=PG_HOST, port=PG_REPLICA_PORT, dbname=PG_DB, user=PG_APP_USER, password=PG_APP_PASSWORD, connect_timeout=PG_CONNECT_TIMEOUT_S
    )


def connect_pg_pgbouncer() -> psycopg.Connection:
    return psycopg.connect(
        host=PG_HOST, port=PG_PGBOUNCER_PORT, dbname=PG_DB, user=PG_APP_USER, password=PG_APP_PASSWORD, connect_timeout=PG_CONNECT_TIMEOUT_S
    )


def connect_pg_pgbouncer_b6() -> psycopg.Connection:
    """Through pgbouncer's dedicated pool_size=1 'mldb_b6' database entry
    (see pgbouncer.ini) -- guarantees backend-connection reuse across
    separate psycopg Connection objects, which is what makes Scenario B6's
    session-staleness test deterministic instead of probabilistic."""
    return psycopg.connect(
        host=PG_HOST, port=PG_PGBOUNCER_PORT, dbname="mldb_b6", user=PG_APP_USER, password=PG_APP_PASSWORD, connect_timeout=PG_CONNECT_TIMEOUT_S
    )


def connect_pg_admin() -> psycopg.Connection:
    """BYPASSRLS role for administrative grant/revoke -- see init.sql note 4b
    for why RLS-restricted app_user cannot be the one issuing revokes."""
    return psycopg.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_ADMIN_USER, password=PG_ADMIN_PASSWORD, connect_timeout=PG_CONNECT_TIMEOUT_S
    )


def connect_mongo() -> MongoClient:
    return MongoClient(
        MONGO_URI,
        serverSelectionTimeoutMS=MONGO_TIMEOUT_MS,
        connectTimeoutMS=MONGO_TIMEOUT_MS,
        socketTimeoutMS=MONGO_TIMEOUT_MS,
    )


def connect_qdrant() -> QdrantClient:
    return QdrantClient(url=QDRANT_URL, timeout=QDRANT_TIMEOUT_S)


def ensure_qdrant_collection(client: QdrantClient, name: str = "resources") -> None:
    existing = [c.name for c in client.get_collections().collections]
    if name in existing:
        return
    client.create_collection(
        collection_name=name,
        vectors_config=qmodels.VectorParams(size=EMBEDDING_DIM, distance=qmodels.Distance.COSINE),
    )
    client.create_payload_index(name, field_name="resource_id", field_schema="keyword")
    client.create_payload_index(name, field_name="acl", field_schema="keyword")


# ---------------------------------------------------------------------------
# Scenario A / C layer operations (relational_plain, nosql, vector)
# Ported line-for-line from bridge/app/stores.js -- keep both in sync if
# either changes.
# ---------------------------------------------------------------------------

def grant_relational(conn: psycopg.Connection, resource_id: str, principal_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE resources_layer_relational
               SET acl = array_append(acl, %s)
               WHERE resource_id = %s AND NOT (%s = ANY(acl))""",
            (principal_id, resource_id, principal_id),
        )
    conn.commit()


def revoke_relational(conn: psycopg.Connection, resource_id: str, principal_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE resources_layer_relational
               SET acl = array_remove(acl, %s)
               WHERE resource_id = %s""",
            (principal_id, resource_id),
        )
    conn.commit()


def is_retrievable_relational(conn: psycopg.Connection, resource_id: str, principal_id: str) -> bool:
    # commit() after the SELECT closes the implicit transaction immediately.
    # Without this, a connection reused across a tight polling loop (as the
    # ground-truth poller does -- up to ~3000 polls per revoke-then-poll
    # trial at 10ms intervals over a 30s timeout) would hold one long-open
    # transaction for the whole poll duration: a well-known Postgres
    # anti-pattern (interferes with VACUUM, causes bloat). Correctness is
    # not affected either way -- READ COMMITTED takes a fresh per-statement
    # snapshot regardless -- but resource hygiene during a 20+ hour
    # experiment run is not something to leave to chance.
    with conn.cursor() as cur:
        cur.execute(
            """SELECT 1 FROM resources_layer_relational
               WHERE resource_id = %s AND %s = ANY(acl)""",
            (resource_id, principal_id),
        )
        result = cur.fetchone() is not None
    conn.commit()
    return result


def grant_nosql(mongo: MongoClient, resource_id: str, principal_id: str) -> None:
    mongo.get_database().resources.update_one(
        {"resource_id": resource_id}, {"$addToSet": {"acl": principal_id}}
    )


def revoke_nosql(mongo: MongoClient, resource_id: str, principal_id: str) -> None:
    mongo.get_database().resources.update_one(
        {"resource_id": resource_id}, {"$pull": {"acl": principal_id}}
    )


def is_retrievable_nosql(mongo: MongoClient, resource_id: str, principal_id: str) -> bool:
    doc = mongo.get_database().resources.find_one(
        {"resource_id": resource_id, "acl": principal_id}, projection={"_id": 1}
    )
    return doc is not None


def _get_point_acl(qdrant: QdrantClient, collection: str, resource_id: str):
    points, _ = qdrant.scroll(
        collection_name=collection,
        scroll_filter=qmodels.Filter(
            must=[qmodels.FieldCondition(key="resource_id", match=qmodels.MatchValue(value=resource_id))]
        ),
        limit=1, with_payload=True, with_vectors=False,
    )
    if not points:
        return None
    return points[0].id, (points[0].payload.get("acl") or [])


def grant_vector(qdrant: QdrantClient, resource_id: str, principal_id: str, collection: str = "resources") -> None:
    found = _get_point_acl(qdrant, collection, resource_id)
    if found is None:
        return
    point_id, acl = found
    if principal_id not in acl:
        qdrant.set_payload(collection_name=collection, points=[point_id], payload={"acl": acl + [principal_id]})


def revoke_vector(qdrant: QdrantClient, resource_id: str, principal_id: str, collection: str = "resources") -> None:
    found = _get_point_acl(qdrant, collection, resource_id)
    if found is None:
        return
    point_id, acl = found
    qdrant.set_payload(
        collection_name=collection, points=[point_id],
        payload={"acl": [p for p in acl if p != principal_id]},
    )


def is_retrievable_vector(qdrant: QdrantClient, resource_id: str, principal_id: str, collection: str = "resources") -> bool:
    points, _ = qdrant.scroll(
        collection_name=collection,
        scroll_filter=qmodels.Filter(must=[
            qmodels.FieldCondition(key="resource_id", match=qmodels.MatchValue(value=resource_id)),
            qmodels.FieldCondition(key="acl", match=qmodels.MatchValue(value=principal_id)),
        ]),
        limit=1, with_payload=False, with_vectors=False,
    )
    return len(points) > 0
