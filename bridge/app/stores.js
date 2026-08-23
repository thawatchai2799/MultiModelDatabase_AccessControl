// One client per data layer, all exposing the same three operations, so the
// bridge's propagation logic (server.js) never needs to know which store
// it's talking to:
//   grant(resourceId, principalId)   -- add principal to the resource's acl
//   revoke(resourceId, principalId)  -- remove principal from the acl
//   isRetrievable(resourceId, principalId) -- ground-truth check: would a
//       correctly-written, acl-filtering query against *this store alone*
//       still return the resource for this principal right now?
//
// isRetrievable is used by the bridge only for its own internal retry
// decisions; the experiment harness's independent poller (Python,
// experiments/poll.py) performs its own direct queries against each store
// for the actual Leak Window measurement, deliberately bypassing the bridge
// entirely, so the bridge is never both the actor and the referee.
import pkg from 'pg';
const { Pool } = pkg;
import { MongoClient } from 'mongodb';
import { QdrantClient } from '@qdrant/js-client-rest';
import { createHash } from 'crypto';

export function makePostgresStore(cfg) {
  const pool = new Pool({
    host: cfg.host, port: cfg.port, database: cfg.database,
    user: cfg.user, password: cfg.password, max: 10,
  });
  return {
    layer: 'relational',
    async grant(resourceId, principalId) {
      await pool.query(
        `UPDATE resources_layer_relational
         SET acl = array_append(acl, $2)
         WHERE resource_id = $1 AND NOT ($2 = ANY(acl))`,
        [resourceId, principalId]
      );
    },
    async revoke(resourceId, principalId) {
      await pool.query(
        `UPDATE resources_layer_relational
         SET acl = array_remove(acl, $2)
         WHERE resource_id = $1`,
        [resourceId, principalId]
      );
    },
    async isRetrievable(resourceId, principalId) {
      const r = await pool.query(
        `SELECT 1 FROM resources_layer_relational
         WHERE resource_id = $1 AND $2 = ANY(acl)`,
        [resourceId, principalId]
      );
      return r.rowCount > 0;
    },
    async close() { await pool.end(); },
  };
}

export function makeMongoStore(cfg) {
  const client = new MongoClient(cfg.uri);
  let db = null;
  async function ensure() {
    if (!db) { await client.connect(); db = client.db(); }
    return db;
  }
  return {
    layer: 'nosql',
    async grant(resourceId, principalId) {
      const d = await ensure();
      await d.collection('resources').updateOne(
        { resource_id: resourceId },
        { $addToSet: { acl: principalId } }
      );
    },
    async revoke(resourceId, principalId) {
      const d = await ensure();
      await d.collection('resources').updateOne(
        { resource_id: resourceId },
        { $pull: { acl: principalId } }
      );
    },
    async isRetrievable(resourceId, principalId) {
      const d = await ensure();
      const doc = await d.collection('resources').findOne(
        { resource_id: resourceId, acl: principalId },
        { projection: { _id: 1 } }
      );
      return doc !== null;
    },
    async close() { await client.close(); },
  };
}

export function makeQdrantStore(cfg) {
  const client = new QdrantClient({ url: cfg.url });
  const COLLECTION = 'resources';
  return {
    layer: 'vector',
    async grant(resourceId, principalId) {
      const point = await getPointAcl(client, COLLECTION, resourceId);
      if (point && !point.acl.includes(principalId)) {
        await client.setPayload(COLLECTION, {
          points: [pointIdFor(resourceId)],
          payload: { acl: [...point.acl, principalId] },
        });
      }
    },
    async revoke(resourceId, principalId) {
      const point = await getPointAcl(client, COLLECTION, resourceId);
      if (point) {
        await client.setPayload(COLLECTION, {
          points: [pointIdFor(resourceId)],
          payload: { acl: point.acl.filter(p => p !== principalId) },
        });
      }
    },
    async isRetrievable(resourceId, principalId) {
      // Mirrors a real filtered semantic-search call: a payload filter is
      // applied server-side, but the filter is evaluated against whatever
      // the *current* payload says -- which is exactly what can still say
      // "yes" if a revoke hasn't landed here yet.
      const res = await client.scroll(COLLECTION, {
        filter: {
          must: [
            { key: 'resource_id', match: { value: resourceId } },
            { key: 'acl', match: { value: principalId } },
          ],
        },
        limit: 1,
        with_payload: false,
        with_vector: false,
      });
      return res.points.length > 0;
    },
    async close() {},
  };
}

// Qdrant point IDs must be uint64 or UUID; resource IDs in this experiment
// are short synthetic strings, so they are mapped through a stable UUIDv5-
// style deterministic hash rather than requiring UUIDs end-to-end -- see
// scenarios/common/ids.py for the matching Python-side implementation used
// when the harness seeds data directly.
function pointIdFor(resourceId) {
  const h = createHash('sha1').update(resourceId).digest('hex');
  return [
    h.slice(0, 8), h.slice(8, 12), '5' + h.slice(13, 16),
    ((parseInt(h[16], 16) & 0x3) | 0x8).toString(16) + h.slice(17, 20), h.slice(20, 32),
  ].join('-');
}

async function getPointAcl(client, collection, resourceId) {
  const res = await client.scroll(collection, {
    filter: { must: [{ key: 'resource_id', match: { value: resourceId } }] },
    limit: 1, with_payload: true, with_vector: false,
  });
  if (res.points.length === 0) return null;
  return { id: res.points[0].id, acl: res.points[0].payload.acl || [] };
}
