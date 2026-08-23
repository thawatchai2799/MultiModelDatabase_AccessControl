// A plain append-only table in PostgreSQL, exposing the same four operations
// as the Fabric client (anchorAccessEvent, recordPropagation, isContained,
// getAccessHistory) so that server.js can be pointed at either without
// knowing which it is talking to.
//
// This exists to answer the question the ledger ablation raises: once we have
// shown that the retry does all the containment work and the ledger only buys
// the ability to answer afterwards, a reviewer will ask why that answer needs
// a blockchain rather than a log table. This backend is that log table, so
// the comparison can be measured instead of argued.
//
// What it deliberately does NOT provide, and what the paper must say:
//   - Append-only here is enforced by privilege, not by structure. app_user
//     holds INSERT and SELECT and nothing else, so the application cannot
//     rewrite history; anyone with the admin role, the database superuser, or
//     filesystem access to the data directory still can, and would leave no
//     evidence in the table itself.
//   - There is no independent attestation of who wrote a row. The ledger
//     takes the submitter's identity from the transaction's credentials,
//     verified by the endorsing organisations; here the row says whatever the
//     writer put in it.
// The point of the comparison is precisely that these two properties are what
// the extra seconds buy, not speed and not containment.
import pkg from 'pg';
const { Pool } = pkg;

const LAYERS = ['relational', 'nosql', 'vector'];

export function makeAuditLog(cfg) {
  const pool = new Pool({
    host: cfg.host, port: cfg.port, database: cfg.database,
    user: cfg.user, password: cfg.password, max: 5,
  });

  return {
    kind: 'log',

    // Fails loudly at startup rather than on the first revoke: a missing
    // table would otherwise surface mid-run as a failed trial.
    async check() {
      const r = await pool.query(
        "SELECT to_regclass('public.access_audit_log') IS NOT NULL AS present");
      if (!r.rows[0].present) {
        throw new Error(
          'access_audit_log table is missing. Create it once as the database superuser:\n' +
          '  docker exec -i mldb-postgres-primary psql -U mldb -d mldb < postgres/primary/audit_log.sql');
      }
      // The table existing is not enough: app_user must be able to INSERT,
      // or every revoke fails at the first anchor. Ask the catalogue rather
      // than writing a probe row into an audit table.
      const priv = await pool.query(
        "SELECT has_table_privilege('access_audit_log', 'INSERT') AS ins, " +
        "       has_table_privilege('access_audit_log', 'SELECT') AS sel");
      if (!priv.rows[0].ins || !priv.rows[0].sel) {
        throw new Error(
          'access_audit_log exists but this user lacks INSERT/SELECT on it. Re-run the grants:\n' +
          '  docker exec -i mldb-postgres-primary psql -U mldb -d mldb < postgres/primary/audit_log.sql');
      }
      return true;
    },

    async anchorAccessEvent(eventId, resourceId, principalId, action, ts) {
      if (action !== 'grant' && action !== 'revoke') {
        throw new Error(`action must be "grant" or "revoke", got "${action}"`);
      }
      const r = await pool.query(
        `INSERT INTO access_audit_log (event_id, resource_id, principal_id, action, ts, layer)
         VALUES ($1, $2, $3, $4, $5, NULL)
         ON CONFLICT (event_id, layer_key) DO NOTHING
         RETURNING id`,
        [eventId, resourceId, principalId, action, String(ts)]);
      if (r.rowCount === 0) throw new Error(`event ${eventId} already recorded`);
      return { eventId, resourceId, principalId, action, ts: String(ts) };
    },

    async recordPropagation(eventId, layer, ts) {
      if (!LAYERS.includes(layer)) {
        throw new Error(`layer must be relational|nosql|vector, got "${layer}"`);
      }
      const ev = await pool.query(
        'SELECT resource_id, principal_id, action FROM access_audit_log WHERE event_id = $1 AND layer IS NULL',
        [eventId]);
      if (ev.rowCount === 0) throw new Error(`event ${eventId} not found`);
      const { resource_id, principal_id, action } = ev.rows[0];
      // One row per (event, layer), mirroring the chaincode's composite key
      // so that concurrent propagation writes do not contend either.
      await pool.query(
        `INSERT INTO access_audit_log (event_id, resource_id, principal_id, action, ts, layer)
         VALUES ($1, $2, $3, $4, $5, $6)
         ON CONFLICT (event_id, layer_key) DO NOTHING`,
        [eventId, resource_id, principal_id, action, String(ts), layer]);
      return { eventId, layer, ts: String(ts) };
    },

    // Same verdict the chaincode gives: the most recent event for this pair
    // must be a revoke, and a propagation row must exist for every layer.
    async isContained(resourceId, principalId) {
      const latest = await pool.query(
        `SELECT event_id, action, ts FROM access_audit_log
         WHERE resource_id = $1 AND principal_id = $2 AND layer IS NULL
         ORDER BY ts DESC, event_id DESC LIMIT 1`,
        [resourceId, principalId]);
      if (latest.rowCount === 0) {
        return { contained: false, reason: 'no access event on record' };
      }
      const ev = latest.rows[0];
      if (ev.action !== 'revoke') {
        return { contained: false, reason: 'most recent event is a grant', latest: ev };
      }
      const props = await pool.query(
        'SELECT layer, ts FROM access_audit_log WHERE event_id = $1 AND layer IS NOT NULL',
        [ev.event_id]);
      const seen = props.rows.map(r => r.layer);
      const missing = LAYERS.filter(l => !seen.includes(l));
      return {
        contained: missing.length === 0,
        missingLayers: missing,
        latest: { ...ev, propagatedTo: props.rows },
      };
    },

    async getAccessHistory(resourceId) {
      const r = await pool.query(
        `SELECT event_id, principal_id, action, ts FROM access_audit_log
         WHERE resource_id = $1 AND layer IS NULL ORDER BY ts, event_id`,
        [resourceId]);
      return r.rows;
    },

    async close() { await pool.end(); },
  };
}
