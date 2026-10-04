"""Scenario C -- the proposed ledger-anchored cross-layer bridge.

revoke() is a single HTTP call to the bridge's /revoke endpoint (see
bridge/app/server.js); the bridge itself does the concurrent propagation,
retry, mutex, and Fabric anchoring. This module deliberately does none of
that work -- if it did, it would no longer be testing the bridge, it would
be re-implementing it in Python and testing that instead.

Ground-truth checking uses the *same* db.py functions as Scenario A
(is_retrievable_relational/nosql/vector against the *same* stores and
schema) since Scenario C uses identical underlying stores to Scenario A --
the only difference between A and C is whether a bridge sits in front of
the revoke path, not what the stores themselves are. This is what makes the
A vs C comparison a fair, single-variable comparison.
"""
import time

import requests

from .common import db

DEFAULT_TIMEOUT_S = 15


def revoke(resource_id: str, principal_id: str, bridge_url: str) -> tuple[float, dict]:
    """Issues one revoke call to the bridge. Returns (t_issued, response_json).
    t_issued is captured *before* the HTTP call, matching Scenario A's
    definition of "issued" as the point the caller decided to revoke, not
    the point any individual store's write happened to land."""
    t_issued = time.monotonic()
    try:
        resp = requests.post(
            f"{bridge_url}/revoke",
            json={"resourceId": resource_id, "principalId": principal_id},
            timeout=DEFAULT_TIMEOUT_S,
        )
        body = resp.json()
    except requests.RequestException as e:
        body = {"error": str(e)}
    return t_issued, body


def grant(resource_id: str, principal_id: str, bridge_url: str) -> dict:
    resp = requests.post(
        f"{bridge_url}/grant",
        json={"resourceId": resource_id, "principalId": principal_id},
        timeout=DEFAULT_TIMEOUT_S,
    )
    return resp.json()


def bridge_reported_status(resource_id: str, principal_id: str, bridge_url: str) -> dict | None:
    """The bridge's own opinion (via Fabric IsContained), fetched purely for
    computing the false-containment / false-non-containment split against
    the independent ground-truth poll -- never used as the actual Leak
    Window measurement itself (see decision 5 in PROGRESS.md)."""
    try:
        resp = requests.get(f"{bridge_url}/status/{resource_id}/{principal_id}", timeout=DEFAULT_TIMEOUT_S)
        if resp.status_code == 200:
            return resp.json()
        return {"error": f"status {resp.status_code}", "body": resp.text}
    except requests.RequestException as e:
        return {"error": str(e)}


def set_fault_config(bridge_url: str, p: float, seed: int, schedule: str = "stream") -> dict:
    """Configures the bridge's per-attempt store-write fault injection
    (regime "faulty"; p=0 restores the healthy regime). Raises on any
    non-200 so a run can never silently proceed with the wrong regime.
    schedule: "stream" (original, default) or "paired" (v1.2.0, the decision
    for (seed, layer, attempt) shared with the Python harness)."""
    resp = requests.post(f"{bridge_url}/fault-config", json={"p": p, "seed": seed, "schedule": schedule},
                         timeout=DEFAULT_TIMEOUT_S)
    if resp.status_code == 404:
        raise RuntimeError(
            "bridge has no /fault-config endpoint -- the running container predates the regime support. "
            "Rebuild it: docker compose up -d --build bridge")
    if resp.status_code != 200:
        raise RuntimeError(f"bridge /fault-config returned {resp.status_code}: {resp.text[:200]}")
    body = resp.json()
    if abs(float(body.get("p", -1)) - p) > 1e-9:
        raise RuntimeError(f"bridge did not apply fault p={p}: {body}")
    # A bridge built before v1.2.0 accepts the request and ignores the
    # schedule; it would then run the stream schedule while the file says
    # paired. Refuse rather than mislabel.
    if schedule != "stream" and body.get("schedule") != schedule:
        raise RuntimeError(
            f"bridge did not apply fault schedule={schedule!r} (it reports {body.get('schedule')!r}) -- "
            "the running container predates the paired schedule. Rebuild it: docker compose up -d --build bridge")
    return body


def get_fault_config(bridge_url: str) -> dict:
    resp = requests.get(f"{bridge_url}/fault-config", timeout=DEFAULT_TIMEOUT_S)
    resp.raise_for_status()
    return resp.json()


def set_anchor_config(bridge_url: str, use_async: bool) -> dict:
    """Switches the bridge between waiting for propagation anchoring
    (default) and anchoring it in the background. Raises on anything but a
    confirmed 200 so a run can never proceed in the wrong mode."""
    resp = requests.post(f"{bridge_url}/anchor-config", json={"async": use_async}, timeout=DEFAULT_TIMEOUT_S)
    if resp.status_code == 404:
        raise RuntimeError(
            "bridge has no /anchor-config endpoint -- the running container predates async anchoring. "
            "Rebuild it: docker compose up -d --build bridge")
    if resp.status_code != 200:
        raise RuntimeError(f"bridge /anchor-config returned {resp.status_code}: {resp.text[:200]}")
    body = resp.json()
    if bool(body.get("async")) is not bool(use_async):
        raise RuntimeError(f"bridge did not apply anchor async={use_async}: {body}")
    return body


def get_anchor_config(bridge_url: str) -> dict:
    """Anchoring mode and, importantly, whether the bridge has Fabric enabled
    at all, and which audit backend it uses (ledger / log / none). Recorded
    per trial so an ablation labels itself in the result file instead of
    relying on whoever ran it to remember."""
    try:
        resp = requests.get(f"{bridge_url}/anchor-config", timeout=DEFAULT_TIMEOUT_S)
        return resp.json() if resp.status_code == 200 else {"error": f"status {resp.status_code}"}
    except requests.RequestException as e:
        return {"error": str(e)}


def anchor_status(bridge_url: str, event_id: str) -> dict | None:
    """Per-event background-anchoring state. Read right after /status so that
    "IsContained says not contained" can be attributed to anchoring still in
    flight rather than to an anchoring failure -- in async mode those are
    different findings and must not be conflated."""
    if not event_id:
        return None
    try:
        resp = requests.get(f"{bridge_url}/anchor-status/{event_id}", timeout=DEFAULT_TIMEOUT_S)
        return resp.json() if resp.status_code == 200 else {"error": f"status {resp.status_code}"}
    except requests.RequestException as e:
        return {"error": str(e)}


def make_check_fns(resource_id: str, principal_id: str, conns: dict) -> dict:
    """Identical to scenario_a's -- same stores, same schema."""
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
