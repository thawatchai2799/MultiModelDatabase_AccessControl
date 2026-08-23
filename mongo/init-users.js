// Runs once on first container start via /docker-entrypoint-initdb.d/.
//
// MongoDB has no native row/document-level security equivalent to Postgres
// RLS or Qdrant's payload filter -- access control on the `resources`
// collection is enforced entirely by the querying application always
// including an `acl` filter clause. That asymmetry is deliberate and
// realistic: it is exactly the mechanism A1/A3 exploit if a revoke has not
// yet reached this collection's `acl` array when a (correctly, filter-
// including) query runs.
db = db.getSiblingDB('mldb');

db.createUser({
  user: 'app_user',
  pwd: 'app_user_dev_only_change_me',
  roles: [{ role: 'readWrite', db: 'mldb' }],
});

db.createCollection('resources');
db.resources.createIndex({ resource_id: 1 }, { unique: true });
// Compound index matching the exact filtered-query shape the ground-truth
// poller and the app always use: { resource_id, acl: principalId }.
db.resources.createIndex({ resource_id: 1, acl: 1 });
