import express from 'express';
import { randomUUID } from 'crypto';
import { makePostgresStore, makeMongoStore, makeQdrantStore } from './stores.js';
import * as fabric from './fabric-client.js';
import { makeAuditLog } from './audit-log.js';

const app = express();
app.use(express.json());

const FABRIC_ENABLED = process.env.FABRIC_ENABLED !== 'false';
// Which backend records the audit trail:
//   'ledger' -- Hyperledger Fabric (the proposed design, and the default)
//   'log'    -- an append-only PostgreSQL table, for the comparison in the
//               paper's ablation: same verdict, no tamper-evidence
//   'none'   -- no audit trail at all; retry and propagation only
// FABRIC_ENABLED=false still selects 'none', so existing runs are unaffected.
const AUDIT_MODE = process.env.AUDIT_MODE || (FABRIC_ENABLED ? 'ledger' : 'none');
if (!['ledger', 'log', 'none'].includes(AUDIT_MODE)) {
  console.error(`[bridge] AUDIT_MODE must be ledger|log|none, got "${AUDIT_MODE}"`);
  process.exit(1);
}
const AUDIT_ON = AUDIT_MODE !== 'none';

// The audit backend exposes the same operations whichever it is, so the
// propagation logic below never branches on which one is in use.
const auditLog = AUDIT_MODE === 'log' ? makeAuditLog({
  host: process.env.POSTGRES_HOST, port: Number(process.env.POSTGRES_PORT || 5432),
  database: process.env.POSTGRES_DB, user: process.env.POSTGRES_APP_USER,
  password: process.env.POSTGRES_APP_PASSWORD,
}) : null;
const audit = AUDIT_MODE === 'log' ? auditLog : fabric;

const stores = [
  makePostgresStore({
    host: process.env.POSTGRES_HOST, port: Number(process.env.POSTGRES_PORT),
    database: process.env.POSTGRES_DB, user: process.env.POSTGRES_APP_USER,
    password: process.env.POSTGRES_APP_PASSWORD,
  }),
  makeMongoStore({ uri: process.env.MONGO_URI }),
  makeQdrantStore({ url: process.env.QDRANT_URL }),
];

// Retry policy for propagation failures: MAX_ATTEMPTS attempts (3 by
// default, and 3 for every result reported in the paper), with exponential
// backoff starting at 50ms (50ms, 100ms, 200ms). If all the attempts fail
// for a given layer, that layer is recorded as a failed propagation and
// counts toward the false-containment rate if the bridge is ever asked
// whether the resource is contained while that layer is still unresolved.
// The attempt count is configurable so that the ablation can run the bridge
// with retry switched off (RETRY_ATTEMPTS=1) and thereby separate what the
// bounded retry contributes from what concurrent propagation contributes.
// The default is 3, and every result reported before this option existed was
// produced with the default, so no previously reported number is affected.
const MAX_ATTEMPTS = (() => {
  const raw = process.env.RETRY_ATTEMPTS;
  if (raw === undefined || raw === '') return 3;
  const n = Number(raw);
  if (!Number.isInteger(n) || n < 1) {
    console.error(`FATAL: RETRY_ATTEMPTS must be an integer >= 1, got ${JSON.stringify(raw)}`);
    process.exit(1);
  }
  return n;
})();
const BASE_BACKOFF_MS = 50;

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function withRetry(fn) {
  let lastErr;
  for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
    try {
      await fn();
      return { ok: true, attempts: attempt + 1 };
    } catch (err) {
      lastErr = err;
      if (attempt < MAX_ATTEMPTS - 1) await sleep(BASE_BACKOFF_MS * 2 ** attempt);
    }
  }
  return { ok: false, attempts: MAX_ATTEMPTS, error: String(lastErr && lastErr.message || lastErr) };
}

// Keyed async mutex, one lock queue per resourceId. Needed because Qdrant's
// client has no atomic "remove this one principal from the acl array"
// operation the way Postgres (array_remove in an UPDATE) and MongoDB ($pull)
// do -- the Qdrant store client (stores.js) does read-payload, modify array,
// write-payload, and two concurrent calls for the *same* resourceId (even
// for different principals, since they share one point's payload) can race
// and lose an update. Serialising per resourceId here, in front of all three
// stores uniformly, closes that gap without pretending Qdrant itself is
// atomic -- a real deployment has exactly the same weaker guarantee, so this
// is the bridge compensating for it, not the vector layer somehow gaining a
// property it does not actually have.
// ---------------------------------------------------------------------------
// Fault injection (experiment regime "faulty", PROGRESS.md decision 11).
// Each STORE WRITE ATTEMPT fails independently with probability p -- the
// same per-attempt semantics the Python harness applies to Scenario A's
// single, un-retried attempt (scenarios/common/faults.py). Here the
// attempt is made inside withRetry, so a layer stays un-revoked only if all
// MAX_ATTEMPTS attempts fail, with probability p^MAX_ATTEMPTS -- p^3 at the
// default budget, and p itself when the ablation sets RETRY_ATTEMPTS=1.
// Fabric anchoring calls are NOT
// injected: the regime models flaky data stores, not a flaky ledger.
// Configured per run via POST /fault-config {p, seed}; p=0 (default) is
// the healthy regime. Seeded (mulberry32) so a run is reproducible.
// ---------------------------------------------------------------------------
function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const fault = { p: 0, seed: 0, rng: mulberry32(0), injected: 0, attempts: 0 };
function setFaultConfig(p, seed) {
  fault.p = p; fault.seed = seed >>> 0; fault.rng = mulberry32(fault.seed);
  fault.injected = 0; fault.attempts = 0;
}
function maybeInjectFault(layer) {
  fault.attempts += 1;
  if (fault.p > 0 && fault.rng() < fault.p) {
    fault.injected += 1;
    throw new Error(`injected transient failure (${layer})`);
  }
}

// ---------------------------------------------------------------------------
// Propagation-anchoring mode (experiment variant, PROGRESS.md 5j).
// Anchoring each propagation record happens AFTER the store write has already
// closed the leak, yet in the default (synchronous) mode the caller waits for
// it: measured at BatchTimeout 5 s, the revoke returned in 10.7 s while every
// layer was inaccessible by 5.6 s, because the propagation records wait for a
// second block. Setting async mode replies as soon as the stores are written
// and anchors in the background.
//
// What async does NOT change: the revoke event itself is still anchored
// fail-closed before any store is touched (decision 5). What it costs: a 200
// no longer means "propagation is on the ledger", and anchors still in flight
// are lost if the process dies. Both are measured rather than argued -- the
// per-event anchor state below is what the harness reads to tell "not yet
// anchored" apart from "anchoring failed".
// ---------------------------------------------------------------------------
const anchorCfg = { async: false };
const anchorState = new Map();   // eventId -> { pending, done, failed: [...] }

function anchorStateFor(eventId) {
  return anchorState.get(eventId) || { pending: 0, done: 0, failed: [] };
}

async function anchorLayer(eventId, layer) {
  const t = Date.now();
  const outcome = await withRetry(() => audit.recordPropagation(eventId, layer, Date.now()));
  return {
    anchorPropMs: Date.now() - t,
    anchorPropAttempts: outcome.attempts,
    anchored: outcome.ok,
    ...(outcome.ok ? {} : { anchorError: outcome.error }),
  };
}

const resourceLocks = new Map();
function withResourceLock(key, fn) {
  const tail = resourceLocks.get(key) || Promise.resolve();
  const result = tail.then(fn, fn);
  resourceLocks.set(key, result.catch(() => {}));
  return result;
}

async function propagate(action, resourceId, principalId, eventId) {
  // Propagate to all three layers concurrently -- the point of the bridge
  // is to close the window, not to add a new one by doing this serially.
  // The whole propagation step is wrapped in the per-resourceId lock so a
  // second grant/revoke for the same resource (even for a different
  // principal) cannot interleave its store writes with this one.
  // Only the STORE WRITES need the per-resource lock (Qdrant's payload update
  // is read-modify-write). Anchoring writes one composite key per
  // (event, layer) and contends with nothing, so it is deliberately outside.
  const results = await withResourceLock(resourceId, async () => Promise.all(stores.map(async store => {
    // Timings are reported per layer so the experiment can separate what
    // actually closes the leak (the store write) from the bookkeeping that
    // follows it (anchoring the propagation). On the first Scenario C run
    // the caller-observed revoke latency was 9.4 s while every layer was
    // measurably closed by 2.6 s -- without this breakdown that gap is
    // invisible and the whole 9.4 s would be misread as the bridge's cost
    // to contain the data.
    const tStore = Date.now();
    const outcome = await withRetry(async () => {
      maybeInjectFault(store.layer);          // per attempt, before the real write
      await store[action](resourceId, principalId);
    });
    return { layer: store.layer, ...outcome, storeMs: Date.now() - tStore };
  })));

  const toAnchor = AUDIT_ON ? results.filter(r => r.ok) : [];
  if (toAnchor.length === 0) {
    return results.map(r => ({ ...r, anchored: false, anchorMode: anchorCfg.async ? 'async' : 'sync' }));
  }

  if (!anchorCfg.async) {
    // Default: the caller waits, so a 200 means the propagation is on the
    // ledger. A Fabric anchoring failure after a successful store write is
    // reported distinctly (propagated but not yet anchored) rather than
    // folded into either success or failure -- exactly the gap the
    // false-containment metric exists to catch.
    const anchored = await Promise.all(results.map(async r =>
      (r.ok ? { ...r, ...(await anchorLayer(eventId, r.layer)) }
            : { ...r, anchored: false })));
    return anchored.map(r => ({ ...r, anchorMode: 'sync' }));
  }

  // Async: reply now, anchor in the background. Recorded per event so the
  // harness can distinguish "not anchored yet" from "anchoring failed";
  // never rejects, so a background failure cannot take the process down.
  anchorState.set(eventId, { pending: toAnchor.length, done: 0, failed: [] });
  for (const r of toAnchor) {
    (async () => {
      let res;
      try {
        res = await anchorLayer(eventId, r.layer);
      } catch (err) {
        res = { anchored: false, anchorError: String(err && err.message || err) };
      }
      const st = anchorState.get(eventId);
      if (!st) return;
      st.pending -= 1;
      if (res.anchored) st.done += 1;
      else st.failed.push({ layer: r.layer, error: res.anchorError });
      if (st.pending === 0) st.finishedAt = Date.now();
    })();
  }
  return results.map(r => ({ ...r, anchored: r.ok ? null : false, anchorMode: 'async' }));
}

app.post('/revoke', async (req, res) => {
  const { resourceId, principalId } = req.body;
  if (!resourceId || !principalId) {
    return res.status(400).json({ error: 'resourceId and principalId are required' });
  }
  const eventId = `evt-${Date.now()}-${randomUUID()}`;
  const issuedAt = Date.now();

  const tStart = Date.now();
  let anchorEventMs = 0;
  let anchorEventAttempts = 0;
  if (AUDIT_ON) {
    const tAnchor = Date.now();
    const anchorOutcome = await withRetry(() => audit.anchorAccessEvent(eventId, resourceId, principalId, 'revoke', issuedAt));
    anchorEventMs = Date.now() - tAnchor;
    anchorEventAttempts = anchorOutcome.attempts;
    if (!anchorOutcome.ok) {
      return res.status(502).json({ error: 'failed to anchor revoke event', detail: anchorOutcome.error,
                                    anchorEventMs, anchorEventAttempts });
    }
  }

  const tPropagate = Date.now();
  const propagationResults = await propagate('revoke', resourceId, principalId, eventId);
  const propagateMs = Date.now() - tPropagate;
  const allOk = propagationResults.every(r => r.ok);
  res.status(allOk ? 200 : 207).json({
    eventId, resourceId, principalId, action: 'revoke', issuedAt,
    propagation: propagationResults,
    fullyPropagated: allOk,
    // anchorEventMs: fail-closed anchoring of the event itself, BEFORE any
    // store is touched (decision 5) -- this is what the Leak Window pays for.
    // propagateMs: store writes plus the anchoring of each propagation; the
    // store part closes the leak, the anchor part happens after it.
    timing: { anchorEventMs, anchorEventAttempts, propagateMs, totalMs: Date.now() - tStart,
              fabricEnabled: AUDIT_MODE === 'ledger', auditMode: AUDIT_MODE },
  });
});

app.post('/grant', async (req, res) => {
  const { resourceId, principalId } = req.body;
  if (!resourceId || !principalId) {
    return res.status(400).json({ error: 'resourceId and principalId are required' });
  }
  const eventId = `evt-${Date.now()}-${randomUUID()}`;
  const issuedAt = Date.now();

  const tStart = Date.now();
  let anchorEventMs = 0;
  let anchorEventAttempts = 0;
  if (AUDIT_ON) {
    const tAnchor = Date.now();
    const anchorOutcome = await withRetry(() => audit.anchorAccessEvent(eventId, resourceId, principalId, 'grant', issuedAt));
    anchorEventMs = Date.now() - tAnchor;
    anchorEventAttempts = anchorOutcome.attempts;
    if (!anchorOutcome.ok) {
      return res.status(502).json({ error: 'failed to anchor grant event', detail: anchorOutcome.error,
                                    anchorEventMs, anchorEventAttempts });
    }
  }

  const tPropagate = Date.now();
  const propagationResults = await propagate('grant', resourceId, principalId, eventId);
  const propagateMs = Date.now() - tPropagate;
  const allOk = propagationResults.every(r => r.ok);
  res.status(allOk ? 200 : 207).json({
    eventId, resourceId, principalId, action: 'grant', issuedAt,
    propagation: propagationResults,
    fullyPropagated: allOk,
    // anchorEventMs: fail-closed anchoring of the event itself, BEFORE any
    // store is touched (decision 5) -- this is what the Leak Window pays for.
    // propagateMs: store writes plus the anchoring of each propagation; the
    // store part closes the leak, the anchor part happens after it.
    timing: { anchorEventMs, anchorEventAttempts, propagateMs, totalMs: Date.now() - tStart,
              fabricEnabled: AUDIT_MODE === 'ledger', auditMode: AUDIT_MODE },
  });
});

// The bridge's own opinion of containment status, per the ledger. This is
// deliberately NOT used as ground truth anywhere in the experiment -- it is
// only compared against the harness's independent direct-query poll to
// compute the false-containment rate (the bridge claiming "contained" while
// a layer can still actually be queried successfully).
app.get('/status/:resourceId/:principalId', async (req, res) => {
  if (!AUDIT_ON) return res.status(501).json({ error: 'audit trail disabled (AUDIT_MODE=none)' });
  try {
    const status = await audit.isContained(req.params.resourceId, req.params.principalId);
    res.json(status);
  } catch (err) {
    res.status(502).json({ error: String(err.message || err) });
  }
});

app.get('/health', (_req, res) => res.json({ ok: true }));

app.post('/fault-config', (req, res) => {
  const p = Number(req.body.p);
  const seed = Number(req.body.seed ?? 0);
  if (!(p >= 0 && p <= 1) || !Number.isFinite(seed)) {
    return res.status(400).json({ error: 'p must be in [0,1], seed must be a number' });
  }
  setFaultConfig(p, seed);
  res.json({ p: fault.p, seed: fault.seed });
});
app.post('/anchor-config', (req, res) => {
  const wantAsync = req.body.async;
  if (typeof wantAsync !== 'boolean') {
    return res.status(400).json({ error: 'body must be {"async": true|false}' });
  }
  anchorCfg.async = wantAsync;
  res.json({ async: anchorCfg.async });
});
// fabricEnabled is reported here so a run can label itself: the ablation
// that disables the ledger must not be mistakable, in a result file, for an
// ordinary Scenario C run.
app.get('/anchor-config', (_req, res) =>
  res.json({ async: anchorCfg.async, tracked: anchorState.size,
             fabricEnabled: AUDIT_MODE === 'ledger', auditMode: AUDIT_MODE,
             retryAttempts: MAX_ATTEMPTS }));

// Whether the background anchoring for one event has finished. The harness
// reads this next to /status so that "IsContained says no" can be attributed
// to anchoring still in flight rather than to an anchoring failure.
app.get('/anchor-status/:eventId', (req, res) => res.json(anchorStateFor(req.params.eventId)));

app.get('/fault-config', (_req, res) =>
  res.json({ p: fault.p, seed: fault.seed, injected: fault.injected, attempts: fault.attempts }));

// Since the qdrant service has no Docker-level healthcheck gate (see
// docker-compose.yml for why), the bridge waits for Qdrant itself to answer
// before it starts accepting traffic, rather than assuming the container
// being "started" means the Qdrant process inside it is actually ready.
async function waitForQdrant(url, { retries = 30, delayMs = 1000 } = {}) {
  for (let attempt = 1; attempt <= retries; attempt++) {
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(2000) });
      if (res.ok) return;
    } catch { /* not ready yet */ }
    console.log(`[bridge] waiting for Qdrant at ${url} (${attempt}/${retries})...`);
    await sleep(delayMs);
  }
  throw new Error(`Qdrant at ${url} did not become ready after ${retries} attempts`);
}

const port = Number(process.env.PORT || 8080);
let server;
try {
  await waitForQdrant(process.env.QDRANT_URL || 'http://qdrant:6333');
  if (auditLog) {
    // Fail at startup, not on the first revoke: a missing table would
    // otherwise surface as a failed trial in the middle of a run.
    await auditLog.check().catch(err => {
      console.error(`[bridge] AUDIT_MODE=log but the audit table is unusable:\n${err.message}`);
      process.exit(1);
    });
  }
  server = app.listen(port, () => console.log(`[bridge] listening on :${port}, audit=${AUDIT_MODE}`));
} catch (err) {
  console.error('[bridge] startup failed:', err.message);
  process.exit(1);
}

async function shutdown() {
  console.log('[bridge] shutting down...');
  server.close();
  await Promise.all(stores.map(s => s.close().catch(() => {})));
  if (AUDIT_MODE === 'ledger') await fabric.closeFabric().catch(() => {});
  if (auditLog) await auditLog.close().catch(() => {});
  process.exit(0);
}
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
