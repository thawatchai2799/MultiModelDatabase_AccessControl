#!/usr/bin/env bash
# Tamper-resistance experiment for the two audit backends (paper §6.3.3).
#
# Question: after a revoke has been fully propagated and its record says
# "contained", can the highest-privilege administrator of each system make
# that record say otherwise -- and would anything show that they did?
#
# Both sides get the same treatment: one committed revoke, a read of the
# containment verdict, an attempt by the system's own administrator to remove
# one propagation record, and a second read. The script REPORTS what happened
# rather than asserting what should happen; a result that contradicts the
# paper's expectation is a finding, not a failure of the script.
#
# What this does NOT test, deliberately: editing a peer's state database on
# disk. That would show one peer disagreeing with the other rather than the
# ledger changing, and doing it to a running network risks the rest of the
# campaign. Section 8 says so.
#
# Prerequisites: the stack up, the ledger up, the audit table created
# (postgres/primary/audit_log.sql), and .env with FABRIC_SAMPLES set.
# Runs from the mldb/ directory. Leaves the bridge in ledger mode afterwards.
#
#   bash experiments/tamper_test.sh 2>&1 | tee results/tamper_test.log
#
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || { echo "cannot cd to the mldb directory"; exit 1; }
MLDB_DIR="$PWD"       # the EXIT trap must find docker-compose.yml even if we die inside fabric-samples

BRIDGE="${BRIDGE_URL:-http://localhost:8080}"
PG="docker exec mldb-postgres-primary psql -v ON_ERROR_STOP=1 -qtA -d mldb"
OUT="results/tamper_test.json"
mkdir -p results
declare -A R          # findings, written out as JSON at the end

# Whatever happens -- including a fatal in Part A -- the bridge is put back
# into ledger mode, so a later Scenario C run cannot silently inherit the
# log-table override.
restore_ledger() { ( cd "$MLDB_DIR" && env -u AUDIT_MODE docker compose up -d bridge ) >/dev/null 2>&1 || true; }
trap restore_ledger EXIT

say()  { printf '\n=== %s ===\n' "$*"; }
note() { printf '    %s\n' "$*"; }
fail() { printf '\nFATAL: %s\n' "$*"; exit 1; }

json_get() {    # json_get '<json>' key   -> JSON value, or null if the input is not JSON
  python3 -c '
import json, sys
try:
    d = json.loads(sys.argv[1])
except Exception:
    d = {}
print(json.dumps(d.get(sys.argv[2]) if isinstance(d, dict) else None))' "$1" "$2"
}

# --------------------------------------------------------------------------
# The bridge is recreated between the two halves. Wait until it reports the
# mode we asked for, rather than sleeping and hoping.
# --------------------------------------------------------------------------
set_mode() {
  local mode="$1"
  if [ "$mode" = "ledger" ]; then
    # env -u: an AUDIT_MODE exported by the calling shell must not leak in
    env -u AUDIT_MODE docker compose up -d bridge >/dev/null 2>&1
  else
    AUDIT_MODE="$mode" docker compose up -d bridge >/dev/null 2>&1
  fi
  for _ in $(seq 1 40); do
    local got
    got="$(curl -s --max-time 2 "$BRIDGE/anchor-config" 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("auditMode",""))' 2>/dev/null || true)"
    [ "$got" = "$mode" ] && { note "bridge is in audit mode: $got"; return 0; }
    sleep 1
  done
  echo "--- bridge log (last 15 lines) ---"; docker compose logs --tail 15 bridge 2>&1 | sed 's/^/    /'
  fail "bridge did not come up in audit mode '$mode' within 40 s -- see the log above (a missing audit table is the usual cause in log mode)"
}

revoke() {     # revoke <resourceId> <principalId>  -> prints the eventId
  local resp
  resp="$(curl -s --max-time 120 -X POST "$BRIDGE/revoke" -H 'Content-Type: application/json' \
          -d "{\"resourceId\":\"$1\",\"principalId\":\"$2\"}")"
  [ -n "$resp" ] || fail "no response from the bridge at $BRIDGE/revoke (timeout or connection refused)"
  local fully; fully="$(json_get "$resp" fullyPropagated)"
  [ "$fully" = "true" ] || fail "revoke did not fully propagate: $resp"
  json_get "$resp" eventId | tr -d '"'
}

status() {     # status <resourceId> <principalId> -> contained:<bool> missing:<list>
  local resp; resp="$(curl -s --max-time 30 "$BRIDGE/status/$1/$2")"
  printf 'contained=%s missing=%s' "$(json_get "$resp" contained)" "$(json_get "$resp" missingLayers)"
}

# The revoked pair is synthetic. The store writes are no-ops on an id that
# is not present (array_remove / $pull / filter of nothing), so the
# experiment touches only the audit record, which is the thing under test.
RID="res-tamper-$(date +%s)"
PID="user-tamper-001"

# ==========================================================================
say "PART A -- append-only PostgreSQL table"
# ==========================================================================
set_mode log
EV_A="$(revoke "$RID" "$PID")"
note "revoke anchored as event $EV_A"
R[table_before]="$(status "$RID" "$PID")";              note "before: ${R[table_before]}"

say "A1: the application user tries to delete a propagation row"
# trust auth for local connections inside the container: no password needed
if $PG -U app_user -c "DELETE FROM access_audit_log WHERE event_id='$EV_A' AND layer='vector';" 2>/tmp/tamper_err; then
  R[table_app_delete]="SUCCEEDED"
else
  R[table_app_delete]="denied: $(tr -d '\n' </tmp/tamper_err | sed 's/^ERROR:  //')"
fi
note "app_user DELETE: ${R[table_app_delete]}"
R[table_after_app]="$(status "$RID" "$PID")";           note "after app_user: ${R[table_after_app]}"

say "A2: the database superuser tries the same"
if $PG -U mldb -c "DELETE FROM access_audit_log WHERE event_id='$EV_A' AND layer='vector';" 2>/tmp/tamper_err; then
  R[table_super_delete]="SUCCEEDED"
else
  R[table_super_delete]="denied: $(tr -d '\n' </tmp/tamper_err)"
fi
note "superuser DELETE: ${R[table_super_delete]}"
R[table_after_super]="$(status "$RID" "$PID")";         note "after superuser: ${R[table_after_super]}"

say "A3: is there any trace in the table itself?"
R[table_rows_for_event]="$($PG -U mldb -c "SELECT count(*) FROM access_audit_log WHERE event_id='$EV_A';")"
note "rows remaining for the event: ${R[table_rows_for_event]} (an intact record has 4: the event + 3 layers)"
note "the table has no tombstone, no version, no history: a deleted row is simply absent"

# ==========================================================================
say "PART B -- Hyperledger Fabric ledger"
# ==========================================================================
set_mode ledger
EV_B="$(revoke "$RID" "$PID")"
note "revoke anchored as event $EV_B"
R[ledger_before]="$(status "$RID" "$PID")";             note "before: ${R[ledger_before]}"

# Peer CLI as the Org1 administrator -- the most privileged single actor in
# the network. Same environment network-up.sh uses for its checkpoint.
FABRIC_SAMPLES="$(grep -E '^FABRIC_SAMPLES=' .env | cut -d= -f2-)"
[ -d "$FABRIC_SAMPLES/test-network" ] || fail "FABRIC_SAMPLES in .env does not point at fabric-samples: '$FABRIC_SAMPLES'"
CHANNEL="$(grep -E '^FABRIC_CHANNEL=' .env | cut -d= -f2-)";   CHANNEL="${CHANNEL:-mychannel}"
CCNAME="$(grep -E '^FABRIC_CHAINCODE=' .env | cut -d= -f2-)";  CCNAME="${CCNAME:-accessledger}"
pushd "$FABRIC_SAMPLES/test-network" >/dev/null || fail "cannot enter $FABRIC_SAMPLES/test-network"
export PATH="${PWD}/../bin:$PATH"
export FABRIC_CFG_PATH="$PWD/../config/"
set +u; source ./scripts/envVar.sh; setGlobals 1; set -u
ORDERER_CA="${PWD}/organizations/ordererOrganizations/example.com/tlsca/tlsca.example.com-cert.pem"
ORG1_TLS="${PWD}/organizations/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls/ca.crt"

say "B1: the committed chaincode definition"
# The text form shows which organisations approved the definition; it does
# not print the endorsement policy itself. The policy is demonstrated
# empirically by B3 rather than read here.
R[ledger_definition]="$(peer lifecycle chaincode querycommitted -C "$CHANNEL" -n "$CCNAME" 2>&1 | tr -d '\n')"
note "${R[ledger_definition]}"

say "B2: Org1 admin invokes a delete operation"
# There is none. This records what the chaincode answers when asked.
out="$(peer chaincode invoke -o localhost:7050 --ordererTLSHostnameOverride orderer.example.com \
        --tls --cafile "$ORDERER_CA" -C "$CHANNEL" -n "$CCNAME" \
        --peerAddresses localhost:7051 --tlsRootCertFiles "$ORG1_TLS" \
        -c "{\"function\":\"DeletePropagation\",\"Args\":[\"$EV_B\",\"vector\"]}" 2>&1 || true)"
R[ledger_delete_op]="$(printf '%s' "$out" | grep -oE 'Error:.*' | head -1 | cut -c1-200)"
# No Fabric error line means either the call succeeded (a delete operation
# exists -- which would be a finding) or the CLI itself did not run. Record
# the raw tail and let the reader judge rather than guess for them.
[ -n "${R[ledger_delete_op]}" ] || R[ledger_delete_op]="no Fabric error line captured; raw output: $(printf '%s' "$out" | tail -1 | cut -c1-150)"
note "${R[ledger_delete_op]}"

say "B3: Org1 admin writes a record endorsed by Org1 alone"
# A legitimate operation, but with only one organisation's endorsement. If
# the policy requires both, the orderer accepts the transaction and the
# committers then mark it invalid; --waitForEvent reports that verdict.
out="$(peer chaincode invoke -o localhost:7050 --ordererTLSHostnameOverride orderer.example.com \
        --tls --cafile "$ORDERER_CA" -C "$CHANNEL" -n "$CCNAME" \
        --peerAddresses localhost:7051 --tlsRootCertFiles "$ORG1_TLS" \
        --waitForEvent --waitForEventTimeout 60s \
        -c "{\"function\":\"AnchorAccessEvent\",\"Args\":[\"evt-tamper-$(date +%s)\",\"$RID\",\"$PID\",\"grant\",\"$(date +%s%3N)\"]}" 2>&1 || true)"
R[ledger_single_org_write]="$(printf '%s' "$out" | grep -oE 'committed with status \([A-Z_]+\)|ENDORSEMENT_POLICY_FAILURE|Error:.*' | head -1 | cut -c1-160)"
[ -n "${R[ledger_single_org_write]}" ] || R[ledger_single_org_write]="no status line captured: $(printf '%s' "$out" | tail -2 | tr '\n' ' ')"
note "${R[ledger_single_org_write]}"
popd >/dev/null || fail "popd failed"

R[ledger_after]="$(status "$RID" "$PID")";              note "after both attempts: ${R[ledger_after]}"

# ==========================================================================
say "RESULT"
# ==========================================================================
python3 - "$OUT" "${!R[@]}" <<'PYEOF' -- "${R[@]}"
import json, sys
out = sys.argv[1]
sep = sys.argv.index('--')
keys, vals = sys.argv[2:sep], sys.argv[sep+1:]
d = dict(zip(keys, vals))
json.dump(d, open(out, 'w'), indent=2)
w = max(len(k) for k in d)
for k in ["table_before", "table_app_delete", "table_after_app", "table_super_delete",
          "table_after_super", "table_rows_for_event", "ledger_before", "ledger_definition",
          "ledger_delete_op", "ledger_single_org_write", "ledger_after"]:
    print(f"  {k:<{w}}  {d.get(k, '')[:110]}")
print(f"\nwritten to {out}")
PYEOF
