"""Deterministic resourceId -> Qdrant point-ID mapping.

Must produce byte-for-byte the same UUID string as bridge/app/stores.js's
pointIdFor() for a given resourceId, since Scenario C's harness writes
points via this module directly (during seeding) and the bridge then reads/
writes the *same* points by resourceId lookup. A mismatch here would make
Scenario C look broken for a reason that has nothing to do with what the
paper is actually measuring.
"""
import hashlib


def point_id_for(resource_id: str) -> str:
    h = hashlib.sha1(resource_id.encode("utf-8")).hexdigest()
    parts = [
        h[0:8],
        h[8:12],
        "5" + h[13:16],
        format((int(h[16], 16) & 0x3) | 0x8, "x") + h[17:20],
        h[20:32],
    ]
    return "-".join(parts)


if __name__ == "__main__":
    # Self-check against the known-good outputs already verified for the
    # JS implementation (see the earlier node -e test run for stores.js).
    known = {
        "res-0001": "c7417afe-e102-55af-a203-038594877e6e",
        "res-0002": "67c2aed0-ab02-534d-95ca-d24872c03f45",
        "res-99999": "202e4cf8-64f1-5900-868b-fc591c11af87",
        "a": "86f7e437-faa5-57fc-a15d-1ddcb9eaeaea",
    }
    for resource_id, expected in known.items():
        got = point_id_for(resource_id)
        status = "OK" if got == expected else "MISMATCH"
        print(f"{status}: {resource_id} -> {got} (expected {expected})")
