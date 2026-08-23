// Logic test for the AccessLedger chaincode, run against a mock stub that
// records the read set, the write set and any range queries -- because the
// bug this key layout exists to prevent (MVCC_READ_CONFLICT between the
// bridge's three concurrent RecordPropagation calls) is a property of those
// sets, not of the return values. Run from mldb/:
//   node fabric/chaincode/test-chaincode.mjs
//
// No Fabric, no Docker, no network needed.
import Module from 'node:module';
import { createRequire } from 'node:module';

// Stub fabric-contract-api so this test runs anywhere -- no npm install, no
// network. The base Contract class contributes nothing to the logic under
// test (the chaincode's methods are called directly), and stubbing it keeps
// the test isolated from the library version installed in the image.
const originalLoad = Module._load;
Module._load = function (request, ...rest) {
  if (request === 'fabric-contract-api') return { Contract: class Contract {} };
  return originalLoad.call(this, request, ...rest);
};

const require = createRequire(import.meta.url);
const { contracts } = require('./index.js');
Module._load = originalLoad;
const AccessLedger = contracts[0];

const SEP = '\u0000';

class MockStub {
  constructor(world = new Map(), msp = 'Org1MSP', txId = 'tx-1') {
    this.world = world;          // key -> string
    this.msp = msp;
    this.txId = txId;
    this.reads = [];             // keys read (getState)
    this.writes = [];            // keys written (putState)
    this.rangeQueries = [];      // partial composite keys iterated
  }
  async getState(k) {
    this.reads.push(k);
    const v = this.world.get(k);
    return v === undefined ? Buffer.alloc(0) : Buffer.from(v);
  }
  async putState(k, v) {
    this.writes.push(k);
    this.world.set(k, v.toString());
  }
  createCompositeKey(objectType, attrs) {
    return SEP + objectType + SEP + attrs.join(SEP) + SEP;
  }
  async getStateByPartialCompositeKey(objectType, attrs) {
    const prefix = SEP + objectType + SEP + (attrs.length ? attrs.join(SEP) + SEP : '');
    this.rangeQueries.push(prefix);
    const hits = [...this.world.entries()]
      .filter(([k]) => k.startsWith(prefix))
      .sort(([a], [b]) => (a > b ? 1 : -1));
    let i = 0;
    return {
      async next() {
        if (i >= hits.length) return { done: true };
        const [key, value] = hits[i++];
        return { done: false, value: { key, value: Buffer.from(value) } };
      },
      async close() {},
    };
  }
  getTxID() { return this.txId; }
}

function ctxFor(stub) {
  return { stub, clientIdentity: { getMSPID: () => stub.msp } };
}

const cc = new AccessLedger();
let failures = 0;
function check(name, fn) {
  try { fn(); console.log(`  ok  ${name}`); }
  catch (e) { failures++; console.log(`  FAIL ${name}: ${e.message}`); }
}
function assert(cond, msg) { if (!cond) throw new Error(msg); }

// ---------------------------------------------------------------------------
console.log('1) anchor + three propagations, external shape preserved');
const world = new Map();
{
  const s = new MockStub(world);
  await cc.AnchorAccessEvent(ctxFor(s), 'evt-1', 'res-1', 'user-1', 'revoke', '100');
  check('anchor writes the event key and the resource index only', () => {
    assert(s.writes.length === 2, `wrote ${s.writes.length} keys: ${JSON.stringify(s.writes)}`);
    assert(s.writes.includes('evt-1'), 'event key not written');
    assert(s.writes.some(k => k.includes('resourceIdx')), 'resource index not written');
  });
  check('the stored event record does NOT carry a propagatedTo array', () => {
    const rec = JSON.parse(world.get('evt-1'));
    assert(rec.propagatedTo === undefined, 'propagatedTo must not be stored in the event record');
    assert(rec.submitterMSP === 'Org1MSP', 'submitter MSP must come from the client identity');
  });
}

// The three calls the bridge makes concurrently, each on its own stub, all
// starting from the same committed world state -- exactly how Fabric
// simulates transactions destined for the same block.
const snapshot = new Map(world);
const sims = ['relational', 'nosql', 'vector'].map((layer, i) => {
  const s = new MockStub(new Map(snapshot), 'Org1MSP', `tx-prop-${i}`);
  return { layer, stub: s };
});
for (const sim of sims) {
  await cc.RecordPropagation(ctxFor(sim.stub), 'evt-1', sim.layer, '200');
}

console.log('2) MVCC properties of concurrent RecordPropagation');
check('no propagation transaction writes the event key', () => {
  for (const { layer, stub } of sims) {
    assert(!stub.writes.includes('evt-1'), `${layer} wrote evt-1 -- this is the conflict`);
  }
});
check('each writes exactly one key, and the three are disjoint', () => {
  const sets = sims.map(s => s.stub.writes);
  for (const w of sets) assert(w.length === 1, `expected 1 write, got ${JSON.stringify(w)}`);
  const flat = sets.flat();
  assert(new Set(flat).size === 3, `write sets overlap: ${JSON.stringify(flat)}`);
});
check('no transaction writes a key another transaction read (the MVCC rule)', () => {
  for (const a of sims) {
    for (const b of sims) {
      if (a === b) continue;
      const clash = a.stub.writes.filter(k => b.stub.reads.includes(k));
      assert(clash.length === 0, `${a.layer} writes ${JSON.stringify(clash)} which ${b.layer} reads`);
    }
  }
});
check('no range query in a submitted transaction (phantom-read protection)', () => {
  for (const { layer, stub } of sims) {
    assert(stub.rangeQueries.length === 0,
      `${layer} range-queried ${JSON.stringify(stub.rangeQueries)} -- would re-create the conflict`);
  }
});

// Merge the three write sets, as committing one block would.
for (const { stub } of sims) for (const [k, v] of stub.world) world.set(k, v);

console.log('3) reads reassemble the same external shape');
{
  const s = new MockStub(world);
  const ev = JSON.parse(await cc.GetAccessEvent(ctxFor(s), 'evt-1'));
  check('GetAccessEvent attaches all three propagations', () => {
    assert(ev.propagatedTo.length === 3, JSON.stringify(ev.propagatedTo));
    assert(ev.propagatedTo.every(p => p.ts === '200' && p.byMSP === 'Org1MSP'), JSON.stringify(ev.propagatedTo));
    assert(ev.propagatedTo.map(p => p.layer).sort().join() === 'nosql,relational,vector', 'layers wrong');
  });
  const st = JSON.parse(await cc.IsContained(ctxFor(new MockStub(world)), 'res-1', 'user-1'));
  check('IsContained: contained once all three layers are recorded', () => {
    assert(st.contained === true, JSON.stringify(st));
    assert(st.missingLayers.length === 0, JSON.stringify(st.missingLayers));
    assert(st.latest.propagatedTo.length === 3, 'latest must carry propagatedTo for the caller');
  });
}

console.log('4) partial and negative cases');
{
  const w2 = new Map();
  const s = new MockStub(w2);
  await cc.AnchorAccessEvent(ctxFor(s), 'evt-2', 'res-2', 'user-2', 'revoke', '100');
  await cc.RecordPropagation(ctxFor(new MockStub(w2)), 'evt-2', 'relational', '200');
  const st = JSON.parse(await cc.IsContained(ctxFor(new MockStub(w2)), 'res-2', 'user-2'));
  check('IsContained: not contained, and names the missing layers', () => {
    assert(st.contained === false, JSON.stringify(st));
    assert(st.missingLayers.sort().join() === 'nosql,vector', JSON.stringify(st.missingLayers));
  });
}
{
  const w3 = new Map();
  await cc.AnchorAccessEvent(ctxFor(new MockStub(w3)), 'evt-3a', 'res-3', 'user-3', 'revoke', '100');
  await cc.AnchorAccessEvent(ctxFor(new MockStub(w3)), 'evt-3b', 'res-3', 'user-3', 'grant', '200');
  const st = JSON.parse(await cc.IsContained(ctxFor(new MockStub(w3)), 'res-3', 'user-3'));
  check('IsContained: latest event is a grant -> not contained', () => {
    assert(st.contained === false && /grant/.test(st.reason), JSON.stringify(st));
  });
}
{
  const st = JSON.parse(await cc.IsContained(ctxFor(new MockStub(new Map())), 'nope', 'nobody'));
  check('IsContained: no events on record', () => {
    assert(st.contained === false && /no access event/.test(st.reason), JSON.stringify(st));
  });
}
{
  const w4 = new Map();
  await cc.AnchorAccessEvent(ctxFor(new MockStub(w4)), 'evt-4', 'res-4', 'user-4', 'revoke', '100');
  await cc.RecordPropagation(ctxFor(new MockStub(w4)), 'evt-4', 'nosql', '200');
  await cc.RecordPropagation(ctxFor(new MockStub(w4)), 'evt-4', 'nosql', '300');   // repeat
  const ev = JSON.parse(await cc.GetAccessEvent(ctxFor(new MockStub(w4)), 'evt-4'));
  check('repeating a propagation for the same layer is idempotent', () => {
    assert(ev.propagatedTo.length === 1, JSON.stringify(ev.propagatedTo));
    assert(ev.propagatedTo[0].ts === '300', 'the later record should win');
  });
}
for (const [name, fn] of [
  ['RecordPropagation rejects an unknown layer', () =>
    cc.RecordPropagation(ctxFor(new MockStub(world)), 'evt-1', 'graph', '1')],
  ['RecordPropagation rejects an unknown event', () =>
    cc.RecordPropagation(ctxFor(new MockStub(new Map())), 'ghost', 'nosql', '1')],
  ['AnchorAccessEvent rejects an unknown action', () =>
    cc.AnchorAccessEvent(ctxFor(new MockStub(new Map())), 'e', 'r', 'p', 'delete', '1')],
  ['AnchorAccessEvent rejects a duplicate event id', () =>
    cc.AnchorAccessEvent(ctxFor(new MockStub(world)), 'evt-1', 'res-1', 'user-1', 'revoke', '1')],
]) {
  let threw = false;
  try { await fn(); } catch { threw = true; }
  check(name, () => assert(threw, 'should have thrown'));
}

console.log('5) history ordering and isolation between resources');
{
  const w5 = new Map();
  await cc.AnchorAccessEvent(ctxFor(new MockStub(w5)), 'e-late', 'res-5', 'u', 'revoke', '900');
  await cc.AnchorAccessEvent(ctxFor(new MockStub(w5)), 'e-early', 'res-5', 'u', 'grant', '100');
  await cc.AnchorAccessEvent(ctxFor(new MockStub(w5)), 'e-other', 'res-6', 'u', 'revoke', '500');
  const hist = JSON.parse(await cc.GetAccessHistory(ctxFor(new MockStub(w5)), 'res-5'));
  check('GetAccessHistory: only this resource, sorted by ts, propagatedTo attached', () => {
    assert(hist.length === 2, JSON.stringify(hist.map(e => e.eventId)));
    assert(hist[0].eventId === 'e-early' && hist[1].eventId === 'e-late', 'not sorted by ts');
    assert(hist.every(e => Array.isArray(e.propagatedTo)), 'propagatedTo missing');
  });
}

console.log('6) composite-key isolation and ordering edge cases');
{
  // A composite key ends each attribute with the separator, so evt-1's
  // partial key must not match evt-10's records. If it did, IsContained
  // would count another event's propagations as this one's.
  const w6 = new Map();
  await cc.AnchorAccessEvent(ctxFor(new MockStub(w6)), 'evt-1', 'res-7', 'u', 'revoke', '100');
  await cc.AnchorAccessEvent(ctxFor(new MockStub(w6)), 'evt-10', 'res-8', 'u', 'revoke', '100');
  for (const l of ['relational', 'nosql', 'vector']) {
    await cc.RecordPropagation(ctxFor(new MockStub(w6)), 'evt-10', l, '200');
  }
  const st1 = JSON.parse(await cc.IsContained(ctxFor(new MockStub(w6)), 'res-7', 'u'));
  const st10 = JSON.parse(await cc.IsContained(ctxFor(new MockStub(w6)), 'res-8', 'u'));
  check("evt-1 does not absorb evt-10's propagations", () => {
    assert(st1.contained === false, JSON.stringify(st1));
    assert(st1.missingLayers.length === 3, JSON.stringify(st1.missingLayers));
    assert(st10.contained === true, JSON.stringify(st10));
  });
}
{
  // Same ts: the tiebreak must make "latest" deterministic, otherwise
  // IsContained's verdict depends on world-state iteration order.
  const w7 = new Map();
  await cc.AnchorAccessEvent(ctxFor(new MockStub(w7)), 'evt-bbb', 'res-9', 'u', 'grant', '500');
  await cc.AnchorAccessEvent(ctxFor(new MockStub(w7)), 'evt-aaa', 'res-9', 'u', 'revoke', '500');
  const seen = new Set();
  for (let i = 0; i < 5; i++) {
    const st = JSON.parse(await cc.IsContained(ctxFor(new MockStub(w7)), 'res-9', 'u'));
    seen.add(st.latest.eventId);
  }
  check('equal timestamps resolve deterministically', () => {
    assert(seen.size === 1, `latest varied between calls: ${[...seen].join(', ')}`);
    assert(seen.has('evt-bbb'), `expected the eventId-greater one, got ${[...seen][0]}`);
  });
}

console.log(failures === 0 ? '\nALL CHAINCODE TESTS PASSED' : `\n${failures} FAILURE(S)`);
process.exit(failures === 0 ? 0 : 1);
