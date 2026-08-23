'use strict';
const { Contract } = require('fabric-contract-api');

/**
 * AccessLedger chaincode — the ledger-anchored record of every grant/revoke
 * decision made about a resource, and of when propagation to each data-store
 * layer was confirmed. It stores ONLY the event record, never the resource's
 * own content.
 *
 * This is a direct structural adaptation of the CityAnchor chaincode used in
 * our earlier intrusion-detection chaincode (AnchorAlert/AnchorModel -> AnchorAccessEvent;
 * GetAlert/GetModel -> GetAccessEvent), kept deliberately close to that proven
 * pattern: append-only key-per-event state, submitter MSP recorded from the
 * client identity (never trusted from the payload), one fact per put, no
 * hidden state. Endorsement policy is set at deploy time (MAJORITY of
 * consortium orgs), same as before.
 *
 *   AnchorAccessEvent(eventId, resourceId, principalId, action, ts)
 *       action: "grant" | "revoke"
 *   RecordPropagation(eventId, layer, ts)
 *       layer: "relational" | "nosql" | "vector"
 *
 * KEY LAYOUT (this is load-bearing, do not "simplify" it back):
 * A propagation record is written to its OWN composite key,
 * propIdx~eventId~layer, and NEVER by updating the event record in place.
 * The first Scenario C run on the VM did the latter, so the bridge's three
 * concurrent RecordPropagation calls all read-modify-wrote the same eventId
 * key; Fabric's state validator invalidated the two losers of every block
 * with MVCC_READ_CONFLICT (15 conflicts over 5 trials, exactly 3 per trial),
 * the bridge's retry re-submitted them into later blocks, and a revoke that
 * had already closed every store in 2.7 s took 9.5 s to return -- with the
 * retry budget of decision 2 exhausted (attempts = 3 of 3) on self-inflicted
 * contention rather than being available for real failures.
 *
 * With per-layer keys the three transactions read the event key (reads do
 * not conflict with reads) and write three disjoint keys, so they commit in
 * one block. RecordPropagation must therefore NOT range-query the
 * propagation keys either: a range query in a submitted transaction is
 * recorded in the read set for phantom detection, and three concurrent
 * writers each adding a key inside that range would re-create the conflict
 * by a different route. The range query lives only in the read-only paths
 * (GetAccessEvent / GetAccessHistory / IsContained), which are evaluated,
 * never submitted, and so are not validated.
 *
 * The external shape is unchanged: every event returned by GetAccessEvent /
 * GetAccessHistory / IsContained still carries propagatedTo, assembled from
 * those keys on read.
 *   GetAccessEvent(eventId)
 *   GetAccessHistory(resourceId)   -- range query over all events for a resource
 *   IsContained(resourceId, principalId)
 *       -- true iff the most recent event for (resourceId, principalId) is a
 *          revoke AND propagation has been recorded for all three layers
 */
class AccessLedger extends Contract {

  async AnchorAccessEvent(ctx, eventId, resourceId, principalId, action, ts) {
    if (action !== 'grant' && action !== 'revoke') {
      throw new Error(`action must be "grant" or "revoke", got "${action}"`);
    }
    const exists = await ctx.stub.getState(eventId);
    if (exists && exists.length) throw new Error(`event ${eventId} already anchored`);
    const rec = {
      kind: 'ACCESS_EVENT',
      eventId, resourceId, principalId, action, ts,
      // NOT stored here: propagation records live under their own keys and
      // are attached on read (see the key layout note above). Keeping an
      // array here would require RecordPropagation to write this key.
      submitterMSP: ctx.clientIdentity.getMSPID(),
      txId: ctx.stub.getTxID(),
    };
    await ctx.stub.putState(eventId, Buffer.from(JSON.stringify(rec)));
    // Secondary index: resourceId~eventId -> eventId, so GetAccessHistory can
    // range-query without scanning the whole world state.
    const idxKey = ctx.stub.createCompositeKey('resourceIdx', [resourceId, eventId]);
    await ctx.stub.putState(idxKey, Buffer.from(eventId));
    return JSON.stringify(rec);
  }

  async RecordPropagation(ctx, eventId, layer, ts) {
    if (!['relational', 'nosql', 'vector'].includes(layer)) {
      throw new Error(`layer must be relational|nosql|vector, got "${layer}"`);
    }
    // Read-only existence check: puts eventId in the read set, which is
    // harmless because nothing writes eventId at this point.
    const b = await ctx.stub.getState(eventId);
    if (!b || !b.length) throw new Error(`event ${eventId} not found`);
    const rec = {
      kind: 'PROPAGATION',
      eventId, layer, ts,
      byMSP: ctx.clientIdentity.getMSPID(),
      txId: ctx.stub.getTxID(),
    };
    // One key per (event, layer). Writing it twice is idempotent, so no
    // read-before-write is needed to avoid duplicates -- which is the whole
    // point: this transaction's write set is a single key that no other
    // concurrent RecordPropagation touches.
    await ctx.stub.putState(this._propKey(ctx, eventId, layer), Buffer.from(JSON.stringify(rec)));
    return JSON.stringify(rec);
  }

  _propKey(ctx, eventId, layer) {
    return ctx.stub.createCompositeKey('propIdx', [eventId, layer]);
  }

  // Read-only. Only ever called from evaluated transactions (see the key
  // layout note at the top of this file).
  async _propagationsFor(ctx, eventId) {
    const iter = await ctx.stub.getStateByPartialCompositeKey('propIdx', [eventId]);
    const out = [];
    let res = await iter.next();
    while (!res.done) {
      const raw = res.value.value.toString('utf8');
      if (raw) {
        const p = JSON.parse(raw);
        out.push({ layer: p.layer, ts: p.ts, byMSP: p.byMSP });
      }
      res = await iter.next();
    }
    await iter.close();
    out.sort((a, b) => (a.layer > b.layer ? 1 : -1));
    return out;
  }

  async GetAccessEvent(ctx, eventId) {
    const b = await ctx.stub.getState(eventId);
    if (!b || !b.length) throw new Error('not found');
    const rec = JSON.parse(b.toString());
    rec.propagatedTo = await this._propagationsFor(ctx, eventId);
    return JSON.stringify(rec);
  }

  // Raw events, without their propagation records attached -- IsContained
  // only needs them for the single latest event, and attaching them to every
  // event costs one range query each.
  async _eventsFor(ctx, resourceId) {
    const iter = await ctx.stub.getStateByPartialCompositeKey('resourceIdx', [resourceId]);
    const events = [];
    let res = await iter.next();
    while (!res.done) {
      const eventId = res.value.value.toString('utf8');
      const b = await ctx.stub.getState(eventId);
      if (b && b.length) events.push(JSON.parse(b.toString()));
      res = await iter.next();
    }
    await iter.close();
    // Deterministic tiebreak on eventId: two events with the same ts would
    // otherwise order arbitrarily, and IsContained takes the LAST one.
    events.sort((a, b) => (a.ts === b.ts
      ? (a.eventId > b.eventId ? 1 : -1)
      : (a.ts > b.ts ? 1 : -1)));
    return events;
  }

  async GetAccessHistory(ctx, resourceId) {
    const events = await this._eventsFor(ctx, resourceId);
    for (const e of events) {
      e.propagatedTo = await this._propagationsFor(ctx, e.eventId);
    }
    return JSON.stringify(events);
  }

  async IsContained(ctx, resourceId, principalId) {
    const events = (await this._eventsFor(ctx, resourceId)).filter(e => e.principalId === principalId);
    if (events.length === 0) {
      return JSON.stringify({ contained: false, reason: 'no access event on record' });
    }
    const latest = events[events.length - 1];
    latest.propagatedTo = await this._propagationsFor(ctx, latest.eventId);
    if (latest.action !== 'revoke') {
      return JSON.stringify({ contained: false, reason: 'most recent event is a grant', latest });
    }
    const layers = latest.propagatedTo.map(p => p.layer);
    const required = ['relational', 'nosql', 'vector'];
    const missing = required.filter(l => !layers.includes(l));
    return JSON.stringify({ contained: missing.length === 0, missingLayers: missing, latest });
  }
}
module.exports.contracts = [AccessLedger];
