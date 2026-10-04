#!/usr/bin/env python3
"""The paired fault schedule is only a paired schedule if the Python harness
(Scenario A, Scenario O) and the Node bridge (Scenario C) compute the SAME
decision for every (seed, layer, attempt). This lifts mulberry32,
pairedFaultU and maybeInjectFault out of server.js verbatim, runs them under
Node over a grid of inputs, and compares every value against
scenarios/common/faults.py. A single mismatch means the two sides are not
paired and the matched-pairs analysis would be invalid.

Run from the repository root:  python3 bridge/gen_paired_fault_test.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SERVER = ROOT / "bridge" / "app" / "server.js"

from gen_retry_budget_test import lift  # noqa: E402  (same lifter, same file conventions)
from scenarios.common import faults  # noqa: E402

SEEDS = [0, 1, 2, 7, 12345, 0x7FFFFFFF, (17 * 17_000_003 + 10_000) & 0x7FFFFFFF,
         (20 * 17_000_003 + 100_000) & 0x7FFFFFFF, 4294967295]
LAYERS = ["relational", "nosql", "vector"]
ATTEMPTS = list(range(0, 6))


def node_values():
    src = SERVER.read_text(encoding="utf-8")
    harness = "\n".join([
        lift("mulberry32", src),
        lift("LAYER_INDEX", src),
        lift("pairedFaultU", src),
        f"""
const seeds = {json.dumps(SEEDS)};
const layers = {json.dumps(LAYERS)};
const attempts = {json.dumps(ATTEMPTS)};
const out = [];
for (const s of seeds) for (const l of layers) for (const k of attempts) {{
  out.push([s, l, k, pairedFaultU(s, l, k)]);
}}
// and the raw generator on a few seeds, for the port itself
const raw = seeds.map(s => [s, mulberry32(s)()]);
console.log(JSON.stringify({{ out, raw }}));
""",
    ])
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False) as fh:
        fh.write(harness)
        path = fh.name
    r = subprocess.run(["node", path], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"node failed: {r.stderr}")
    return json.loads(r.stdout)


def main():
    got = node_values()
    bad = 0
    for s, u in got["raw"]:
        mine = faults.mulberry32_first(s)
        if mine != u:
            bad += 1
            print(f"MISMATCH mulberry32_first({s}): py={mine!r} js={u!r}")
    for s, l, k, u in got["out"]:
        mine = faults.paired_fault_u(s, l, k)
        if mine != u:
            bad += 1
            print(f"MISMATCH paired_fault_u({s}, {l}, {k}): py={mine!r} js={u!r}")
    n = len(got["out"]) + len(got["raw"])

    # Behavioural checks on the Python side alone.
    f1 = faults.FaultInjector(0.3, seed=7, schedule="paired")
    f2 = faults.FaultInjector(0.3, seed=7, schedule="paired")
    # Order-independence: whatever order the layers are asked in, attempt k
    # of a layer gets the same answer.
    a = [f1.should_fail(l) for l in ("relational", "nosql", "vector")]
    b = [f2.should_fail(l) for l in ("vector", "nosql", "relational")][::-1]
    if a != b:
        bad += 1
        print(f"paired schedule is order-dependent: {a} vs {b}")
    # A single attempt in A equals attempt 0 in C by construction:
    for s in SEEDS:
        for l in LAYERS:
            fa = faults.FaultInjector(0.3, seed=s, schedule="paired")
            if fa.should_fail(l) != (faults.paired_fault_u(s, l, 0) < 0.3):
                bad += 1
                print(f"A's single attempt is not attempt 0 for seed={s} layer={l}")
    # Rate sanity over many seeds (not a proof, a smoke check).
    hits = sum(faults.paired_fault_u(s, "nosql", 0) < 0.3 for s in range(20000))
    if not 0.28 < hits / 20000 < 0.32:
        bad += 1
        print(f"paired rate off: {hits / 20000}")
    # The stream schedule is untouched: same sequence as before this change.
    old = faults.FaultInjector(0.3, seed=7)
    ref = __import__("random").Random(7)
    if [old.should_fail("x") for _ in range(50)] != [ref.random() < 0.3 for _ in range(50)]:
        bad += 1
        print("stream schedule changed -- previously reported results would not reproduce")

    if bad:
        raise SystemExit(f"FAIL: {bad} mismatch(es)")
    print(f"paired fault schedule OK: {n} values identical in Python and Node; "
          f"order-independent; A attempt == C attempt 0; stream schedule unchanged")


if __name__ == "__main__":
    main()
