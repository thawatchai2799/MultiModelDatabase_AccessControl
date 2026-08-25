#!/bin/bash
# Brings up the Hyperledger Fabric test-network (2 orgs, Raft ordering, same
# topology as our earlier intrusion-detection work) and deploys the
# AccessLedger chaincode as a service (CCaaS).
#
# NOTE on why CCaaS and not the simpler `deployCC ... -ccl javascript`: the
# prior project's fabric/chaincode/Dockerfile is built specifically for CCaaS
# deployment ("avoids the legacy /build endpoint that recent Docker Engine
# releases no longer expose to the peer"), but fabric/README.md's own
# instructions still describe the older deployCC path. Those two artifacts
# disagree with each other and there is no record of which one actually
# produced the paper's E13 numbers. This script uses CCaaS, matching the
# Dockerfile (the more modern, more robust-to-newer-Docker approach, and the
# one this project's chaincode files are already set up for) -- flagged here
# in case that turns out to be the wrong call and deployCC is what's needed
# instead.
#
# Usage:  ./network-up.sh [--yes]     (--yes skips the teardown confirmation)
# Checkpoints print at every stage; if any fails, the exact error and which
# checkpoint it failed at is what to send back for a fix.
set -euo pipefail

CHAINCODE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/chaincode" && pwd)"
FABRIC_SAMPLES="${FABRIC_SAMPLES:-$HOME/fabric-samples}"
CHANNEL=mychannel
CCNAME=accessledger
# deployCCAAS builds and looks for exactly "${CC_NAME}_ccaas_image:latest";
# derive it rather than hard-coding, so renaming CCNAME cannot desynchronise
# the two.
CCAAS_IMAGE="${CCNAME}_ccaas_image:latest"

# Runs start to finish without stopping for input. The teardown of an
# existing network is destructive, so it is announced loudly, but it is not
# gated behind a prompt: this script is re-run constantly during development,
# and a prompt in the middle of it was itself the cause of a silently
# aborted run (see PROGRESS.md 5d). Pass --confirm to get the old
# ask-before-destroying behaviour.
ASSUME_YES=1
BATCH_TIMEOUT=""          # empty = leave fabric-samples' own value alone
while [ "$#" -gt 0 ]; do
  case "$1" in
    --yes|-y) ASSUME_YES=1 ;;
    --confirm) ASSUME_YES=0 ;;
    --batch-timeout) shift; BATCH_TIMEOUT="${1:-}" ;;
    --batch-timeout=*) BATCH_TIMEOUT="${1#*=}" ;;
    *) echo "Unknown argument: $1"
       echo "usage: ./network-up.sh [--confirm] [--batch-timeout 500ms]"; exit 1 ;;
  esac
  shift
done
if [ -n "$BATCH_TIMEOUT" ] && ! printf '%s' "$BATCH_TIMEOUT" | grep -Eq '^[0-9]+(\.[0-9]+)?(ms|s|m)$'; then
  echo "FATAL: --batch-timeout must look like 500ms / 2s / 1m, got '$BATCH_TIMEOUT'"; exit 1
fi

# --- progress reporting ---------------------------------------------------
# Several steps here are silent for 30-60 s (image pulls, network bring-up,
# deployCCAAS, and every Fabric commit waiting on a block). Without a clock
# on the output it is impossible to tell "working" from "hung", which has
# already cost real time on the VM. Every checkpoint is stamped with the
# elapsed time since the script started, and the steps that produce no
# output of their own get a dot every 5 s from a background heartbeat.
SCRIPT_T0=$(date +%s)
elapsed() { local d=$(( $(date +%s) - SCRIPT_T0 )); printf '%02d:%02d' $(( d / 60 )) $(( d % 60 )); }
step() { echo ""; echo "=== [$1] $2   (+$(elapsed))"; }

HEARTBEAT_PID=""
heartbeat_start() {
  [ -n "$HEARTBEAT_PID" ] && return 0
  ( while true; do sleep 5; printf '    ... still working (+%s)\n' "$(elapsed)"; done ) &
  HEARTBEAT_PID=$!
}
heartbeat_stop() {
  [ -z "$HEARTBEAT_PID" ] && return 0
  kill "$HEARTBEAT_PID" 2>/dev/null || true
  wait "$HEARTBEAT_PID" 2>/dev/null || true
  HEARTBEAT_PID=""
}
# Never leave the heartbeat running if the script dies part-way.
trap 'heartbeat_stop' EXIT
# ...and an interrupt must actually stop the script. A handler that only
# cleans up returns control to the next line, so Ctrl-C during the network
# bring-up would have continued on to deploy chaincode onto a half-built
# network.
trap 'heartbeat_stop; echo ""; echo "Interrupted at +$(elapsed) -- the Fabric network may be half-built; re-run this script."; exit 130' INT
trap 'heartbeat_stop; echo ""; echo "Terminated at +$(elapsed)."; exit 143' TERM

# Where this project lives (this script is at <repo>/fabric/network-up.sh).
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

step "0/6" "Prerequisite check"
for cmd in docker node curl jq; do
  command -v "$cmd" >/dev/null || { echo "FATAL: $cmd not found on PATH"; exit 1; }
done
docker version >/dev/null || { echo "FATAL: docker daemon not reachable"; exit 1; }
echo "OK: docker, node, curl, jq present; docker daemon reachable"

step "1/6" "Fabric samples + binaries"
if [ ! -x "$FABRIC_SAMPLES/test-network/network.sh" ]; then
  echo "fabric-samples not found at $FABRIC_SAMPLES -- installing (fabric v2.5.9, matching the prior E13 run)"
  mkdir -p "$(dirname "$FABRIC_SAMPLES")"
  cd "$(dirname "$FABRIC_SAMPLES")"
  curl -sSLO https://raw.githubusercontent.com/hyperledger/fabric/main/scripts/install-fabric.sh
  chmod +x install-fabric.sh
  ./install-fabric.sh --fabric-version 2.5.9 docker samples binary
fi
[ -x "$FABRIC_SAMPLES/test-network/network.sh" ] || { echo "FATAL: network.sh still not found after install"; exit 1; }
echo "OK: $FABRIC_SAMPLES/test-network/network.sh present"

step "2/6" "Bring up network (2 orgs + Raft orderer + CAs)"
cd "$FABRIC_SAMPLES/test-network"
if docker ps --format '{{.Names}}' | grep -q 'peer0.org\|orderer'; then
  echo "WARNING: a Fabric network is already running. Continuing will run"
  echo "  './network.sh down', destroying ALL ledger data anchored so far --"
  echo "  including any access events from an experiment run in progress."
  if [ "${ASSUME_YES}" = "1" ]; then
    echo "  Tearing down and rebuilding (non-interactive; pass --confirm to be asked)."
  elif [ ! -t 0 ]; then
    # stdin is not a terminal, so `read` would consume EOF and silently
    # abort. That is exactly what happened on the VM when the script was
    # invoked as `./network-up.sh | tail -25`: it aborted instantly, the OLD
    # chaincode kept running, and the next measurement looked like a
    # regression rather than an aborted run. Refuse loudly instead.
    echo "FATAL: --confirm was given but stdin is not a terminal, so the"
    echo "  confirmation cannot be answered. Drop --confirm, or run without a pipe."
    exit 1
  else
    read -r -p "  Type 'yes' to tear down and rebuild, anything else to abort: " CONFIRM
    [ "$CONFIRM" = "yes" ] || { echo "Aborted -- network left as-is."; exit 1; }
  fi
fi
# --- orderer BatchTimeout -------------------------------------------------
# C's Leak Window is dominated by how long a block takes to close, because
# decision 5 anchors the revoke event BEFORE touching any store. Sweeping
# this value is how that claim gets tested rather than asserted, so the
# script edits it here and records what it used (see .fabric-config.json
# below) instead of leaving it to whoever remembers which run was which.
CONFIGTX="$FABRIC_SAMPLES/test-network/configtx/configtx.yaml"
CONFIGTX_ORIG="$CONFIGTX.mldb-orig"
[ -f "$CONFIGTX_ORIG" ] || cp "$CONFIGTX" "$CONFIGTX_ORIG"
cp "$CONFIGTX_ORIG" "$CONFIGTX"            # always start from the pristine file
if [ -n "$BATCH_TIMEOUT" ]; then
  sed -i -E "s/^([[:space:]]*)BatchTimeout:[[:space:]]*.*/\1BatchTimeout: $BATCH_TIMEOUT/" "$CONFIGTX"
  if ! grep -Eq "^[[:space:]]*BatchTimeout:[[:space:]]*$BATCH_TIMEOUT\$" "$CONFIGTX"; then
    echo "FATAL: could not set BatchTimeout in $CONFIGTX"; exit 1
  fi
fi
EFFECTIVE_BATCH_TIMEOUT="$(grep -E "^[[:space:]]*BatchTimeout:" "$CONFIGTX" | head -1 | awk '{print $2}')"
echo "Orderer BatchTimeout: $EFFECTIVE_BATCH_TIMEOUT"

./network.sh down >/dev/null 2>&1 || true   # idempotent: clean slate every run
# deployCCAAS starts the per-peer chaincode containers with `docker run --rm`,
# outside compose, so network.sh down does not remove them; a leftover one
# holds the container name and the next deploy fails.
docker rm -f "peer0org1_${CCNAME}_ccaas" "peer0org2_${CCNAME}_ccaas" >/dev/null 2>&1 || true
heartbeat_start
./network.sh up createChannel -c "$CHANNEL" -ca
heartbeat_stop
RUNNING=$(docker ps --format '{{.Names}}' | grep -c 'peer0.org\|orderer\|ca_' || true)
[ "$RUNNING" -ge 3 ] || { echo "FATAL: expected peers/orderer/CAs not running (got $RUNNING containers)"; exit 1; }
echo "OK: network up, $RUNNING peer/orderer/CA containers running"
# Recorded only now that the network is actually running with this value:
# run_experiment.py copies it into every Scenario C record, so a file left
# behind by a failed bring-up would mislabel a whole sweep point.
printf '{"batch_timeout": "%s", "written_at": "%s"}\n' \
  "$EFFECTIVE_BATCH_TIMEOUT" "$(date -Iseconds)" > "$REPO_ROOT/.fabric-config.json"

step "3/6" "Build the CCaaS chaincode image"
# Building here is not strictly required (deployCCAAS builds the same image
# in step 4, and will hit the layer cache), but it fails fast and loudly on a
# broken Dockerfile instead of failing inside network.sh's own output.
docker build -t "$CCAAS_IMAGE" "$CHAINCODE_DIR"
echo "OK: built $CCAAS_IMAGE"

step "4/6" "Deploy accessledger as Chaincode-as-a-Service"
export PATH="${PWD}/../bin:$PATH"
export FABRIC_CFG_PATH="$PWD/../config/"
# deployCCAAS builds the image from $CHAINCODE_DIR/Dockerfile itself, packages
# with the external builder, installs on both peers, approves for both orgs,
# commits, and starts the per-peer chaincode containers.
#
# NO -cci HERE, deliberately. -cci is CC_INIT_FCN (the name of an init
# function to invoke after commit), NOT the image name -- an earlier version
# of this script passed "$CCAAS_IMAGE" to it, and the first real VM run got
# through install/approve/commit and then failed at the very end with:
#   "chaincode 'accessledger' does not require initialization but called as
#    init"
# because deployCCAAS dutifully invoked a function by that name. This
# chaincode has no Init; leaving -cci unset (default "NA") skips the invoke.
# The image name is not configurable here: deployCCAAS derives it as
# ${CC_NAME}_ccaas_image:latest, which is what step 3 builds.
heartbeat_start
./network.sh deployCCAAS -ccn "$CCNAME" -ccp "$CHAINCODE_DIR"
heartbeat_stop

CC_CONTAINERS=$(docker ps --format '{{.Names}}' | grep -c "$CCNAME" || true)
if [ "$CC_CONTAINERS" -lt 1 ]; then
  echo "WARNING: no running container matched '$CCNAME' -- deployCCAAS may have used a"
  echo "  different naming convention, or the chaincode container needs to be started"
  echo "  manually. Check: docker ps -a --format '{{.Names}}\t{{.Status}}' | grep -i cc"
fi

# The peer dials the chaincode container by name on the fabric_test network
# (CCaaS). deployCCAAS starts those containers and returns immediately, so an
# invoke fired straight afterwards can hit "connection refused" while the
# Node process is still booting -- observed on the first successful deploy.
# Wait until each one actually accepts a TCP connection, and if one never
# does, print its logs instead of failing with only the peer's gRPC error.
cat > /tmp/mldb-ccaas-probe.mjs <<'PROBE_EOF'
import { connect } from 'node:net';
const [host, port] = [process.argv[2], Number(process.argv[3])];
const code = await new Promise((resolve) => {
  const sock = connect({ host, port, timeout: 2000 });
  sock.on('connect', () => { sock.destroy(); resolve(0); });
  sock.on('timeout', () => { sock.destroy(); resolve(1); });
  sock.on('error', () => resolve(1));
});
process.exit(code);
PROBE_EOF

wait_for_ccaas() {
  local name="$1" attempt
  for attempt in $(seq 1 30); do
    if docker run --rm -i=false -t=false --network fabric_test \
         -v /tmp/mldb-ccaas-probe.mjs:/probe.mjs:ro \
         node:20-alpine node /probe.mjs "$name" 9999 >/dev/null 2>&1; then
      echo "OK: $name is accepting connections on 9999 (attempt $attempt)"
      return 0
    fi
    printf '    waiting for %s on 9999 (attempt %d/30, +%s)\r' "$name" "$attempt" "$(elapsed)"
    sleep 2
  done
  echo ""
  echo "FATAL: $name never accepted a connection on 9999. Its logs:"
  docker logs "$name" 2>&1 | tail -40 || echo "  (container is gone -- it exited; it was started with --rm)"
  echo "  Container state:"
  docker ps -a --filter "name=$name" --format '  {{.Names}}\t{{.Status}}' || true
  return 1
}

for cc in "peer0org1_${CCNAME}_ccaas" "peer0org2_${CCNAME}_ccaas"; do
  wait_for_ccaas "$cc" || exit 1
done

step "5/6" "Checkpoint invoke: AnchorAccessEvent"
# fabric-samples' envVar.sh reads $OVERRIDE_ORG (and friends) without a
# default, which aborts under this script's `set -u` ("OVERRIDE_ORG: unbound
# variable" on the first real VM run). Its own scripts run without -u, so
# relax it just for the sourcing and the setGlobals call, then restore.
set +u
source ./scripts/envVar.sh
setGlobals 1
set -u
heartbeat_start
peer chaincode invoke -o localhost:7050 --ordererTLSHostnameOverride orderer.example.com \
  --tls --cafile "${PWD}/organizations/ordererOrganizations/example.com/tlsca/tlsca.example.com-cert.pem" \
  -C "$CHANNEL" -n "$CCNAME" \
  --peerAddresses localhost:7051 --tlsRootCertFiles "${PWD}/organizations/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls/ca.crt" \
  --peerAddresses localhost:9051 --tlsRootCertFiles "${PWD}/organizations/peerOrganizations/org2.example.com/peers/peer0.org2.example.com/tls/ca.crt" \
  -c '{"function":"AnchorAccessEvent","Args":["evt-checkpoint","res-checkpoint","user-checkpoint","grant","1"]}'
heartbeat_stop
echo "OK: checkpoint transaction submitted"

step "6/6" "Diagnostic: connectivity path the bridge container will actually use"
# The bridge does NOT run on the host like this checkpoint script does -- it
# runs inside its own container and reaches the peer via
# host.docker.internal:7051 (see docker-compose.yml). That is a different
# network path than localhost:7051 above and has never been exercised before
# this project, so it is checked here explicitly, before any experiment
# depends on it, rather than being discovered as a failure 20 hours into a
# run.
echo "Peer port bindings (must show 0.0.0.0, not 127.0.0.1, for host.docker.internal to reach them):"
docker port peer0.org1.example.com 2>&1 | grep 7051 || echo "  (could not read port binding -- check manually: docker port peer0.org1.example.com)"

cat > /tmp/mldb-fabric-connectivity-check.mjs <<'EOF'
// Run from INSIDE a throwaway container on the same Docker host, simulating
// exactly the network path bridge/app/fabric-client.js uses. Written in
// plain Node (guaranteed present in the node:20-alpine image this runs in)
// rather than a shell /dev/tcp trick -- busybox ash on Alpine does not
// reliably support that redirection, which is the exact class of bug this
// project already found and fixed once (the Qdrant healthcheck). Lesson
// applied here too rather than repeated.
import { connect } from 'node:net';
import { lookup } from 'node:dns/promises';

const HOST = 'host.docker.internal';
const PORT = 7051;

try {
  const { address } = await lookup(HOST);
  console.log(`Resolved ${HOST} -> ${address}`);
} catch (e) {
  console.log(`FAILED to resolve ${HOST}: ${e.message}`);
  process.exit(1);
}

await new Promise((resolve) => {
  const sock = connect({ host: HOST, port: PORT, timeout: 3000 });
  sock.on('connect', () => { console.log(`TCP connect to ${HOST}:${PORT}: OK`); sock.destroy(); resolve(); });
  sock.on('timeout', () => { console.log(`TCP connect to ${HOST}:${PORT}: TIMEOUT`); sock.destroy(); resolve(); });
  sock.on('error', (e) => { console.log(`TCP connect to ${HOST}:${PORT}: FAILED (${e.message})`); resolve(); });
});
EOF
echo "Running connectivity probe from a throwaway container (add-host mirrors docker-compose.yml's extra_hosts)..."
docker run --rm -i=false -t=false --add-host=host.docker.internal:host-gateway \
  -v /tmp/mldb-fabric-connectivity-check.mjs:/check.mjs:ro \
  node:20-alpine node /check.mjs || echo "WARNING: probe container failed to run -- check Docker networking manually"

echo ""
echo "=== Network is up. Next: cd .. && docker compose up -d (from the mldb project root) ==="
echo "To tear down later: cd $FABRIC_SAMPLES/test-network && ./network.sh down"
echo ""
echo "IMPORTANT: if the bridge container was already running before this script ran,"
echo "  restart it now: docker compose restart bridge"
echo "  network.sh up regenerates ALL Fabric crypto material from scratch every time."
echo "  The bridge's Fabric Gateway connection (fabric-client.js) is built once at"
echo "  startup and cached for the life of the process -- an already-running bridge"
echo "  is holding an identity built from material that no longer exists on disk"
echo "  after this script runs, and will fail its next Fabric call silently until"
echo "  restarted."
echo ""
echo ""
echo "=== network-up.sh finished successfully (all 6 checkpoints) in $(elapsed) ==="
echo "    orderer BatchTimeout = $EFFECTIVE_BATCH_TIMEOUT (recorded in $REPO_ROOT/.fabric-config.json;"
echo "    run_experiment.py copies it into every Scenario C record)"
