#!/usr/bin/env python3
"""Logic test for the append-only audit-log backend.

Lifts bridge/app/audit-log.js verbatim, swaps only its `pg` import for a mock
pool, and runs the real code -- the same technique as gen_propagate_test.py,
and for the same reason: a reimplementation of the logic in the test would
pass while the shipped code was broken.

The point of these checks is that the log backend must give the SAME
containment verdicts as the chaincode for the same sequence of events. If it
did not, the comparison in the paper would be measuring two different
questions rather than two costs for one answer.

Run from mldb/:  python3 bridge/gen_audit_log_test.py
"""
import pathlib
import subprocess
import sys

SRC = pathlib.Path(__file__).resolve().parent / "app" / "audit-log.js"
OUT = pathlib.Path("/tmp/audit_log_test.mjs")

MOCK = """
// ---- mock pg pool: an in-memory table with the same constraints ----------
const rows = [];
let nextId = 1;
const queries = [];

function fakeQuery(sql, params = []) {
  queries.push(sql.trim().split('\\n')[0].trim());
  const s = sql.replace(/\\s+/g, ' ').trim();
  if (s.startsWith('SELECT to_regclass')) return { rows: [{ present: true }], rowCount: 1 };
  if (s.startsWith('SELECT has_table_privilege')) return { rows: [{ ins: true, sel: true }], rowCount: 1 };
  if (s.startsWith('INSERT INTO access_audit_log')) {
    const [event_id, resource_id, principal_id, action, ts, layer = null] = params;
    const layer_key = layer ?? '@event';
    if (rows.some(r => r.event_id === event_id && r.layer_key === layer_key)) {
      return { rows: [], rowCount: 0 };          // ON CONFLICT DO NOTHING
    }
    rows.push({ id: nextId++, event_id, resource_id, principal_id, action, ts, layer, layer_key });
    return { rows: [{ id: nextId - 1 }], rowCount: 1 };
  }
  if (s.startsWith('SELECT resource_id, principal_id, action')) {
    const out = rows.filter(r => r.event_id === params[0] && r.layer === null);
    return { rows: out, rowCount: out.length };
  }
  if (s.startsWith('SELECT event_id, action, ts')) {
    const out = rows
      .filter(r => r.resource_id === params[0] && r.principal_id === params[1] && r.layer === null)
      .sort((a, b) => (a.ts === b.ts ? (a.event_id > b.event_id ? 1 : -1) : (a.ts > b.ts ? 1 : -1)))
      .reverse().slice(0, 1)
      .map(r => ({ event_id: r.event_id, action: r.action, ts: r.ts }));
    return { rows: out, rowCount: out.length };
  }
  if (s.startsWith('SELECT layer, ts')) {
    const out = rows.filter(r => r.event_id === params[0] && r.layer !== null)
      .map(r => ({ layer: r.layer, ts: r.ts }));
    return { rows: out, rowCount: out.length };
  }
  if (s.startsWith('SELECT event_id, principal_id')) {
    const out = rows.filter(r => r.resource_id === params[0] && r.layer === null);
    return { rows: out, rowCount: out.length };
  }
  throw new Error('unexpected SQL: ' + s.slice(0, 80));
}
const pkg = { Pool: class { async query(s, p) { return fakeQuery(s, p); } async end() {} } };
"""

TESTS = r"""
const log = makeAuditLog({ host: 'x', port: 5432, database: 'd', user: 'u', password: 'p' });
let failures = 0;
const check = (name, fn) => {
  try { fn(); console.log(`  ok  ${name}`); }
  catch (e) { failures++; console.log(`  FAIL ${name}: ${e.message}`); }
};
const assert = (c, m) => { if (!c) throw new Error(m); };

console.log('1) the same verdicts the chaincode gives');
await log.check();
await log.anchorAccessEvent('e1', 'r1', 'u1', 'revoke', 100);
let st = await log.isContained('r1', 'u1');
check('revoke anchored, nothing propagated -> not contained, all layers missing', () => {
  assert(st.contained === false, JSON.stringify(st));
  assert(st.missingLayers.sort().join() === 'nosql,relational,vector', JSON.stringify(st.missingLayers));
});
for (const l of ['relational', 'nosql']) await log.recordPropagation('e1', l, 200);
st = await log.isContained('r1', 'u1');
check('two of three -> still not contained, names the missing one', () => {
  assert(st.contained === false && st.missingLayers.join() === 'vector', JSON.stringify(st));
});
await log.recordPropagation('e1', 'vector', 200);
st = await log.isContained('r1', 'u1');
check('all three -> contained', () => assert(st.contained === true, JSON.stringify(st)));

console.log('2) event ordering and negative cases');
await log.anchorAccessEvent('e2', 'r1', 'u1', 'grant', 300);
st = await log.isContained('r1', 'u1');
check('a later grant makes it not contained again', () => {
  assert(st.contained === false && /grant/.test(st.reason), JSON.stringify(st));
});
st = await log.isContained('nobody', 'nothing');
check('no events on record', () => assert(/no access event/.test(st.reason), JSON.stringify(st)));

for (const [name, fn] of [
  ['duplicate event id is rejected', () => log.anchorAccessEvent('e1', 'r1', 'u1', 'revoke', 1)],
  ['unknown action is rejected', () => log.anchorAccessEvent('e9', 'r', 'u', 'delete', 1)],
  ['unknown layer is rejected', () => log.recordPropagation('e1', 'graph', 1)],
  ['propagation for an unknown event is rejected', () => log.recordPropagation('ghost', 'nosql', 1)],
]) {
  let threw = false;
  try { await fn(); } catch { threw = true; }
  check(name, () => assert(threw, 'should have thrown'));
}

console.log('3) write shape');
const before = rows.filter(r => r.event_id === 'e1' && r.layer === 'nosql').length;
await log.recordPropagation('e1', 'nosql', 999);
check('idempotent per (event, layer)', () => {
  assert(rows.filter(r => r.event_id === 'e1' && r.layer === 'nosql').length === before,
    'a second write for the same layer added a row');
});
check('never issues UPDATE or DELETE: the table is append-only by use as well as by grant', () => {
  const bad = queries.filter(q => /^(UPDATE|DELETE|TRUNCATE)/i.test(q));
  assert(bad.length === 0, 'found: ' + bad.join('; '));
});

console.log(failures === 0 ? 'ALL AUDIT-LOG TESTS PASSED' : `${failures} FAILURE(S)`);
process.exit(failures === 0 ? 0 : 1);

"""


REQUIRED_OPS = ("anchorAccessEvent", "recordPropagation", "isContained", "getAccessHistory")


def check_interface_parity():
    """server.js calls the audit backend through `audit.<op>` without knowing
    which backend it is, so both must expose the same operations. Checked on
    the source text so this test needs neither Fabric nor a database."""
    import re
    fabric_src = (SRC.parent / "fabric-client.js").read_text()
    log_src = SRC.read_text()
    fabric_ops = set(re.findall(r"^export async function (\w+)", fabric_src, re.M))
    log_ops = set(re.findall(r"^    (?:async )?(\w+)\(", log_src, re.M))
    missing = [op for op in REQUIRED_OPS if op not in fabric_ops or op not in log_ops]
    if missing:
        sys.exit(f"backend interface drift: {missing} not present in both fabric-client.js and audit-log.js")
    print("  ok  both audit backends expose:", ", ".join(REQUIRED_OPS))


def main():
    check_interface_parity()
    src = SRC.read_text()
    assert "import pkg from 'pg';" in src, "audit-log.js no longer imports pg the expected way"
    src = src.replace("import pkg from 'pg';", MOCK)
    src = src.replace("export function makeAuditLog", "function makeAuditLog")
    OUT.write_text(src + TESTS)
    r = subprocess.run(["node", str(OUT)], capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()
