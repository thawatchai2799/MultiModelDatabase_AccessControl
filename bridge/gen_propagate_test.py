# Generates and runs a node test that exercises the REAL withRetry /
# fault injector / keyed mutex / propagate() code lifted verbatim out of
# app/server.js, with stub stores and a stub Fabric client. Run from mldb/:
#   python3 bridge/gen_propagate_test.py && node /tmp/bridge_propagate_test.mjs
import pathlib, subprocess, sys
src = (pathlib.Path(__file__).resolve().parent / 'app' / 'server.js').read_text()

# Take everything from MAX_ATTEMPTS up to the first express route, i.e. the
# retry policy, the fault injector, the keyed mutex and propagate() -- verbatim.
start = src.index('const MAX_ATTEMPTS')
end = src.index("app.post('/revoke'")
verbatim = src[start:end]

stubs = '''// --- stubs for what propagate() closes over (stores / fabric / flag) ---
let FABRIC_ENABLED = true;
// server.js gates anchoring on AUDIT_ON and calls the backend through
// `audit`; the harness mirrors that so the extracted propagate() runs
// unmodified. Tests toggle FABRIC_ENABLED, so AUDIT_ON follows it.
Object.defineProperty(globalThis, 'AUDIT_ON', { get: () => FABRIC_ENABLED });
// anchorLayer() is lifted from server.js and calls the backend as `audit`,
// so it must resolve at module scope, not inside the test IIFE. Bound lazily
// because `fabric` is declared further down. These tests exercise
// AUDIT_MODE=ledger, where audit is the Fabric client.
Object.defineProperty(globalThis, 'audit', { get: () => fabric });
const anchored = [];
const fabric = { recordPropagation: async (e, l, t) => { anchored.push(l); } };
const writes = [];
const stores = ['relational', 'nosql', 'vector'].map(layer => ({
  layer,
  async revoke(rid, pid) { writes.push([layer, rid, pid]); },
  async grant(rid, pid) { writes.push(['grant:' + layer, rid, pid]); },
}));

'''

body = r'''
(async () => {
  // 1) healthy: all three written and anchored
  setFaultConfig(0, 1);
  let res = await propagate('revoke', 'r1', 'p1', 'e1');
  if (res.length !== 3 || !res.every(r => r.ok && r.anchored) || writes.length !== 3) throw new Error('healthy: ' + JSON.stringify(res));
  if (anchored.length !== 3) throw new Error('anchors: ' + anchored.length);

  // 2) p=1: nothing written, 3 attempts each, nothing anchored
  writes.length = 0; anchored.length = 0; setFaultConfig(1, 1);
  res = await propagate('revoke', 'r2', 'p2', 'e2');
  if (writes.length !== 0) throw new Error('p=1 must not write');
  if (!res.every(r => !r.ok && r.attempts === 3 && r.anchored === false)) throw new Error('p=1 result: ' + JSON.stringify(res));
  if (anchored.length !== 0) throw new Error('must not anchor a failed layer');
  if (fault.injected !== 9 || fault.attempts !== 9) throw new Error('counters: ' + fault.injected + '/' + fault.attempts);

  // 3) partial: anchored set must equal the ok set
  writes.length = 0; anchored.length = 0; setFaultConfig(0.5, 11);
  res = await propagate('revoke', 'r3', 'p3', 'e3');
  const okLayers = res.filter(r => r.ok).map(r => r.layer);
  if (okLayers.length !== writes.length) throw new Error('ok count != writes');
  if (JSON.stringify(okLayers.slice().sort()) !== JSON.stringify(anchored.slice().sort())) throw new Error('anchored != ok');

  // 4) determinism for a given seed
  const run = async (seed) => { setFaultConfig(0.5, seed); const r = await propagate('revoke', 'r4', 'p4', 'e4'); return r.map(x => x.ok).join(','); };
  if (await run(99) !== await run(99)) throw new Error('not deterministic');

  // 5) mutex still serialises same-resource propagations under injection
  setFaultConfig(0, 5);
  const order = [];
  const slow = ['relational', 'nosql', 'vector'].map(layer => ({
    layer, async revoke(rid, pid) { order.push('start:' + pid); await sleep(20); order.push('end:' + pid); },
  }));
  const saved = stores.splice(0, 3, ...slow);
  await Promise.all([propagate('revoke', 'same', 'A', 'e5'), propagate('revoke', 'same', 'B', 'e6')]);
  stores.splice(0, 3, ...saved);
  const first = order[0].split(':')[1];
  const lastEndOfFirst = order.lastIndexOf('end:' + first);
  const firstStartOfOther = order.findIndex(o => o.startsWith('start:') && o.split(':')[1] !== first);
  if (firstStartOfOther < lastEndOfFirst) throw new Error('mutex broken: ' + order.join(' '));

  // 6) anchor failure after a successful write is still reported distinctly
  setFaultConfig(0, 1); writes.length = 0;
  const realRecord = fabric.recordPropagation;
  fabric.recordPropagation = async () => { throw new Error('fabric down'); };
  res = await propagate('revoke', 'r6', 'p6', 'e7');
  fabric.recordPropagation = realRecord;
  if (!res.every(r => r.ok && r.anchored === false && r.anchorError)) throw new Error('anchor-failure reporting: ' + JSON.stringify(res));

  // 7) grant path is injected with the same semantics
  setFaultConfig(1, 2); writes.length = 0;
  res = await propagate('grant', 'r7', 'p7', 'e8');
  if (writes.length !== 0 || res.some(r => r.ok)) throw new Error('grant injection wrong');

  // 8) FABRIC_ENABLED=false: writes happen, nothing anchored, ok stays true
  setFaultConfig(0, 1); writes.length = 0; anchored.length = 0; FABRIC_ENABLED = false;
  res = await propagate('revoke', 'r8', 'p8', 'e9');
  FABRIC_ENABLED = true;
  if (writes.length !== 3 || anchored.length !== 0 || !res.every(r => r.ok && r.anchored === false)) throw new Error('fabric-disabled path: ' + JSON.stringify(res));

  // 9) timing fields present and consistent
  setFaultConfig(0, 1); FABRIC_ENABLED = true;
  const slow9 = ['relational', 'nosql', 'vector'].map(layer => ({
    layer, async revoke(rid, pid) { await sleep(30); } }));
  const saved2 = stores.splice(0, 3, ...slow9);
  const realRec = fabric.recordPropagation;
  fabric.recordPropagation = async () => { await sleep(20); };
  res = await propagate('revoke', 'r9', 'p9', 'e10');
  stores.splice(0, 3, ...saved2); fabric.recordPropagation = realRec;
  for (const r of res) {
    if (!(r.storeMs >= 25)) throw new Error('storeMs missing/too small: ' + JSON.stringify(r));
    if (!(r.anchorPropMs >= 15)) throw new Error('anchorPropMs missing/too small: ' + JSON.stringify(r));
    if (r.anchorPropAttempts !== 1) throw new Error('anchorPropAttempts wrong: ' + JSON.stringify(r));
    if (r.anchored !== true) throw new Error('anchored should be true: ' + JSON.stringify(r));
  }
  // ---- async propagation anchoring -------------------------------------
  const settle = async (ms = 60) => { await sleep(ms); };

  // 10) async: the reply does not wait for anchoring, and the anchoring
  //     still happens afterwards.
  setFaultConfig(0, 1); FABRIC_ENABLED = true; anchorCfg.async = true;
  anchored.length = 0; writes.length = 0;
  const slowAnchor = fabric.recordPropagation;
  fabric.recordPropagation = async (e, l) => { await sleep(80); anchored.push(l); };
  let t0 = Date.now();
  res = await propagate('revoke', 'r10', 'p10', 'e-async');
  const replyMs = Date.now() - t0;
  if (replyMs >= 80) throw new Error('async reply waited for anchoring: ' + replyMs + 'ms');
  if (writes.length !== 3) throw new Error('stores must still be written before replying');
  if (!res.every(r => r.ok && r.anchored === null && r.anchorMode === 'async')) throw new Error('async result shape: ' + JSON.stringify(res));
  let st = anchorStateFor('e-async');
  if (st.pending !== 3) throw new Error('expected 3 pending anchors, got ' + JSON.stringify(st));
  await settle(300);
  st = anchorStateFor('e-async');
  if (!(st.pending === 0 && st.done === 3 && st.failed.length === 0)) throw new Error('anchors did not complete: ' + JSON.stringify(st));
  if (anchored.length !== 3) throw new Error('anchor calls: ' + anchored.length);
  fabric.recordPropagation = slowAnchor;

  // 11) async: a background anchoring failure is recorded, not thrown, and
  //     does not stop the other layers.
  anchorCfg.async = true;
  const realRec2 = fabric.recordPropagation;
  fabric.recordPropagation = async (e, l) => { if (l === 'nosql') throw new Error('fabric down'); };
  res = await propagate('revoke', 'r11', 'p11', 'e-async-fail');
  await settle(400);
  st = anchorStateFor('e-async-fail');
  if (!(st.pending === 0 && st.done === 2 && st.failed.length === 1 && st.failed[0].layer === 'nosql')) {
    throw new Error('failure not recorded per layer: ' + JSON.stringify(st));
  }
  fabric.recordPropagation = realRec2;

  // 12) async: a store write that fails is still reported as not anchored,
  //     and no anchor is scheduled for it.
  setFaultConfig(1, 4); anchorCfg.async = true;
  res = await propagate('revoke', 'r12', 'p12', 'e-async-nostore');
  if (!res.every(r => !r.ok && r.anchored === false)) throw new Error('failed stores must not be anchored: ' + JSON.stringify(res));
  if (anchorStateFor('e-async-nostore').pending !== 0) throw new Error('scheduled an anchor for a failed store');

  // 13) the per-resource lock still serialises store writes in async mode.
  setFaultConfig(0, 5); anchorCfg.async = true;
  const order2 = [];
  const slow13 = ['relational', 'nosql', 'vector'].map(layer => ({
    layer, async revoke(rid, pid) { order2.push('start:' + pid); await sleep(20); order2.push('end:' + pid); } }));
  const saved13 = stores.splice(0, 3, ...slow13);
  await Promise.all([propagate('revoke', 'same2', 'A', 'e13a'), propagate('revoke', 'same2', 'B', 'e13b')]);
  stores.splice(0, 3, ...saved13);
  const first2 = order2[0].split(':')[1];
  if (order2.findIndex(o => o.startsWith('start:') && o.split(':')[1] !== first2) < order2.lastIndexOf('end:' + first2)) {
    throw new Error('mutex broken in async mode: ' + order2.join(' '));
  }

  // 14) switching back to sync restores the waiting behaviour exactly.
  anchorCfg.async = false; setFaultConfig(0, 1);
  fabric.recordPropagation = async (e, l) => { await sleep(60); anchored.push(l); };
  t0 = Date.now();
  res = await propagate('revoke', 'r14', 'p14', 'e14');
  if (Date.now() - t0 < 60) throw new Error('sync mode must wait for anchoring');
  if (!res.every(r => r.anchored === true && r.anchorMode === 'sync' && r.anchorPropAttempts === 1)) {
    throw new Error('sync result shape changed: ' + JSON.stringify(res));
  }
  fabric.recordPropagation = realRecord;

  console.log('bridge propagate+injection OK (14 checks)');
})();
'''

out = '/tmp/bridge_propagate_test.mjs'
open(out, 'w').write(stubs + verbatim + body)
sys.exit(subprocess.run(['node', out]).returncode)
