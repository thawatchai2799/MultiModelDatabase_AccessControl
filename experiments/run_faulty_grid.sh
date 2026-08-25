#!/usr/bin/env bash
# The 20-seed faulty grid: Scenarios A and C at 10K under injected failure
# (p = 0.30), for each of the three audit backends. Replaces the five-seed
# runs behind Tables 4, 5 and 7 with enough trials to quote rates rather than
# patterns.
#
# Each seed is seeded fresh (truncating) and measured immediately, so the
# store holds exactly one seed's records at a time -- the discipline from
# PROGRESS.md 5m. A runs in every pass even though it does not touch the
# bridge: identical A results across the three passes are the built-in
# check that the passes are comparable.
#
# Every pass verifies the bridge's audit mode before its first seed, and the
# bridge is returned to ledger mode however the script ends.
#
#   bash experiments/run_faulty_grid.sh 2>&1 | tee results/faulty20.log
#
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || { echo "cannot cd to the mldb directory"; exit 1; }
MLDB_DIR="$PWD"

BRIDGE="${BRIDGE_URL:-http://localhost:8080}"
SCALE="${SCALE:-10000}"
SEEDS="${SEEDS:-$(seq -s ' ' 1 20)}"
FAULT_P="${FAULT_P:-0.30}"
MODES="${MODES:-ledger none log}"
# Ablation column that isolates concurrency from retry. RETRY=1 runs the
# bridge with the retry disabled; the mode name in the output file becomes
# "<mode>-noretry" so a run can never be confused with a default one.
RETRY="${RETRY:-3}"
PY="${PYTHON:-python}"
# RESUME=1: continue a pass whose file already exists, skipping seeds that
# have a complete, labelled Scenario C trial in it. Off by default, because
# appending to a file this run did not create is how two builds' results
# were once merged into one group (PROGRESS.md 5e).
RESUME="${RESUME:-0}"

say()  { printf '\n=== %s ===\n' "$*"; }
note() { printf '    %s\n' "$*"; }
fail() { printf '\nFATAL: %s\n' "$*"; exit 1; }

restore_ledger() { ( cd "$MLDB_DIR" && env -u AUDIT_MODE docker compose up -d bridge ) >/dev/null 2>&1 || true; }
trap restore_ledger EXIT

set_mode() {
  local mode="$1"
  if [ "$mode" = "ledger" ]; then
    env -u AUDIT_MODE RETRY_ATTEMPTS="$RETRY" docker compose up -d bridge >/dev/null 2>&1
  else
    AUDIT_MODE="$mode" RETRY_ATTEMPTS="$RETRY" docker compose up -d bridge >/dev/null 2>&1
  fi
  for _ in $(seq 1 40); do
    local got
    got="$(curl -s --max-time 2 "$BRIDGE/anchor-config" 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("auditMode",""))' 2>/dev/null || true)"
    if [ "$got" = "$mode" ]; then
      # Verify the retry budget too: a mode that came up correctly with the
      # wrong budget would produce a whole pass that looks valid and is not.
      local gotr
      gotr="$(curl -s --max-time 2 "$BRIDGE/anchor-config" 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("retryAttempts",""))' 2>/dev/null || true)"
      [ "$gotr" = "$RETRY" ] || fail "bridge came up in audit mode '$got' but with retryAttempts=$gotr, expected $RETRY"
      note "bridge is in audit mode: $got, retry budget: $gotr"
      return 0
    fi
    sleep 1
  done
  echo "--- bridge log (last 15 lines) ---"; docker compose logs --tail 15 bridge 2>&1 | sed 's/^/    /'
  fail "bridge did not come up in audit mode '$mode' within 40 s"
}

# Refuse to start if the ledger is not at the block time every other result
# in the paper was measured at.
bt="$(python3 -c 'import json; print(json.load(open(".fabric-config.json")).get("batch_timeout",""))' 2>/dev/null || true)"
[ "$bt" = "2s" ] || fail "orderer BatchTimeout is '$bt', not 2s -- re-run fabric/network-up.sh --batch-timeout 2s first, or the C results will not be comparable"

mkdir -p results
t0=$(date +%s)
n_seeds=$(wc -w <<<"$SEEDS")
for mode in $MODES; do
  suffix="$mode"
  [ "$RETRY" = "3" ] || suffix="${mode}-noretry"
  out="results/faulty20_${suffix}.jsonl"
  done_seeds=""
  if [ -e "$out" ]; then
    [ "$RESUME" = "1" ] || fail "$out already exists -- move it aside, or set RESUME=1 to skip the seeds it already holds"
    # A seed counts as done only if all three layers of its C trial are
    # present and labelled with this mode; anything less is re-run.
    done_seeds="$(python3 -c '
import json,sys
want=sys.argv[2]; layers={}
for l in open(sys.argv[1]):
    try: r=json.loads(l)
    except Exception: continue
    if (r.get("scenario")=="c" and "error" not in r.get("extra",{})
            and r["extra"].get("audit_mode")==want
            and str(r["extra"].get("retry_attempts"))==sys.argv[3]):
        layers.setdefault(r["seed"], set()).add(r["layer"])
print(" ".join(str(s) for s,L in sorted(layers.items()) if L=={"relational","nosql","vector"}))' "$out" "$mode" "$RETRY")"
    # Refuse up front if the file already holds records from another mode:
    # the end-of-pass label check would catch it, but only after twenty
    # minutes of runs had been appended to the wrong file.
    foreign="$(python3 -c '
import json,sys
want=sys.argv[2]; seen=set()
for l in open(sys.argv[1]):
    try: r=json.loads(l)
    except Exception: continue
    if r.get("scenario")=="c" and "error" not in r.get("extra",{}):
        m=r["extra"].get("audit_mode"); t=str(r["extra"].get("retry_attempts"))
        if m!=want: seen.add(f"audit_mode={m}")
        if t!=sys.argv[3]: seen.add(f"retry_attempts={t}")
print(",".join(sorted(seen)))' "$out" "$mode" "$RETRY")"
    [ -z "$foreign" ] || fail "$out holds Scenario C records with $foreign -- this is the wrong file for a $mode pass at retry budget $RETRY"
    note "RESUME: $out already has complete trials for seeds: ${done_seeds:-none}"
  fi
  say "MODE $mode -> $out  ($n_seeds seeds, scale $SCALE, p=$FAULT_P)"
  set_mode "$mode"
  i=0
  for s in $SEEDS; do
    i=$((i+1))
    case " $done_seeds " in *" $s "*) note "seed $s already complete -- skipped"; continue ;; esac
    printf '\n--- [%s] seed %s (%d/%d, +%dm) ---\n' "$mode" "$s" "$i" "$n_seeds" $(( ($(date +%s)-t0)/60 ))
    $PY experiments/seed.py --scenario a --n "$SCALE" --seed "$s" \
      || fail "seeding failed for seed $s (mode $mode)"
    $PY experiments/run_experiment.py --scenarios a,c --scales "$SCALE" --seeds "$s" \
        --regime faulty --fault-p "$FAULT_P" --out "$out" --append \
      || fail "run_experiment failed for seed $s (mode $mode)"
  done
  # Every record in this file must carry the mode it was run under.
  # A trial that failed is written with extra={"error": ...} and no label;
  # that is a legitimate outcome analyze.py reports separately, not a
  # mislabelling, and it must not throw away a twenty-minute pass.
  chk="$(python3 -c '
import json,sys
want=sys.argv[2]; n=0; bad=0; err=0
skipped=0
for l in open(sys.argv[1]):
    try: r=json.loads(l)
    except Exception: skipped+=1; continue     # a line cut short by a crash; analyze.py skips these too
    if r.get("scenario")!="c": continue
    if "error" in r.get("extra",{}): err+=1; continue
    n+=1
    bad += (r["extra"].get("audit_mode")!=want
            or str(r["extra"].get("retry_attempts")) != sys.argv[3])
print(f"{bad} {n} {err} {skipped}")' "$out" "$mode" "$RETRY")"
  read -r bad n err skipped <<<"$chk"
  [ -n "$bad" ] || fail "$out: could not read the file to verify labels"
  [ "${skipped:-0}" = "0" ] || note "    ($skipped unparseable line(s) in $out -- a write cut short; analyze.py will skip them too)"
  [ "$bad" = "0" ] || fail "$out: $bad of $n Scenario C records are NOT labelled audit_mode=$mode"
  note "$out: all $n measured Scenario C records labelled audit_mode=$mode; $err trial(s) recorded as errors"
  [ "$err" = "0" ] || note "    (errors are kept in the file and reported by analyze.py; check them before citing this pass)"
done

say "DONE in $(( ($(date +%s)-t0)/60 )) min"
for mode in $MODES; do
  $PY experiments/analyze.py "results/faulty20_${mode}.jsonl" --out "analysis/faulty20_${mode}" --no-figures 2>&1 | tail -1
done
note "bridge restored to ledger mode"
