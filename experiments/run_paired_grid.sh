#!/usr/bin/env bash
# The paired campaign (v1.2.0): Scenarios A, O and C at 10K under injected
# failure, over a sweep of injection probabilities, for each bridge
# configuration -- with the PAIRED fault schedule, so that attempt k of a
# layer fails in every scenario or in none and the per-seed outcomes can be
# analysed as matched pairs (experiments/ablation_table.py).
#
# run_faulty_grid.sh is left exactly as it was: it produced the first
# submission's Table VII under the stream schedule, and those files must
# keep reproducing.
#
# For each p in P_VALUES, for each bridge configuration in CONFIGS:
#   results/paired_p<p>_<config>.jsonl
# where config is one of  none-noretry | none | log | ledger  (the ladder of
# Table VII). A and O run in EVERY pass, exactly as A did before: under the
# paired schedule their outcomes must be identical across passes, and
# ablation_table.py checks that they are -- a built-in test that the passes
# are comparable, stronger than the rate-only check the old driver had.
#
#   bash experiments/run_paired_grid.sh 2>&1 | tee results/paired.log
#
# Budget, measured in the 3-seed rehearsal: seeding 10K takes ~95 s, an A
# trial 31 s when it leaks (most do at p = 0.30), O ~2 s, C 3-10 s or 30 s
# when it leaks, a bridge restart ~10 s. With the default seed-outer order
# and AO_PASSES=first, 50 seeds x 3 p x 4 configs is about 4.5 hours.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || { echo "cannot cd to the mldb directory"; exit 1; }
MLDB_DIR="$PWD"

BRIDGE="${BRIDGE_URL:-http://localhost:8080}"
SCALE="${SCALE:-10000}"
SEEDS="${SEEDS:-$(seq -s ' ' 1 50)}"
P_VALUES="${P_VALUES:-0.05 0.10 0.30}"
# Ladder order. Each entry is <audit mode>[-noretry].
CONFIGS="${CONFIGS:-none-noretry none log ledger}"
OUTBOX_POLL_S="${OUTBOX_POLL_S:-0.02}"
PY="${PYTHON:-python}"
RESUME="${RESUME:-0}"
PREFIX="${PREFIX:-paired}"
# AO_PASSES=first (default): A and O run only in the first configuration's
# pass for each p; ablation_table.py reads them from that file. A leaking A
# trial costs the full 30 s timeout, so running A in every pass would add
# ~3 h to the full grid. AO_PASSES=all runs them in every pass, which lets
# ablation_table.py check that the same seed failed the same layers in every
# pass -- the rehearsal did this over 3 seeds x 4 passes and they agreed; the
# paired schedule makes it true by construction.
AO_PASSES="${AO_PASSES:-first}"
# ORDER=seed-outer (default): seed each seed once and run every (config, p)
# cell against it. ORDER=config-outer: the original p -> config -> seed
# loops, re-seeding in every pass (~5x slower; kept for reproducing the
# rehearsal exactly).
ORDER="${ORDER:-seed-outer}"
# DRY_RUN=1 prints the sequence of seed / bridge / trial steps without
# running anything, so the loop order can be checked without a stack.
DRY_RUN="${DRY_RUN:-0}"
# The embedding model is cached after the first seeding; sentence-transformers
# still contacts the Hugging Face Hub on every load unless told not to, and
# a hung Hub request stalled the rehearsal. Offline is the safe default; the
# first-ever seeding on a machine needs HF_HUB_OFFLINE=0 once.
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}" TRANSFORMERS_OFFLINE="${TRANSFORMERS_OFFLINE:-1}"

say()  { printf '\n=== %s ===\n' "$*"; }
note() { printf '    %s\n' "$*"; }
fail() { printf '\nFATAL: %s\n' "$*"; exit 1; }

restore_ledger() { [ "${DRY_RUN:-0}" = "1" ] && return 0; ( cd "$MLDB_DIR" && env -u AUDIT_MODE docker compose up -d bridge ) >/dev/null 2>&1 || true; }
trap restore_ledger EXIT

cfg_json() { curl -s --max-time 2 "$BRIDGE/$1" 2>/dev/null; }
jget() { python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get(sys.argv[1],""))' "$1" 2>/dev/null; }

set_mode() {
  local mode="$1" retry="$2"
  if [ "$mode" = "ledger" ]; then
    env -u AUDIT_MODE RETRY_ATTEMPTS="$retry" docker compose up -d bridge >/dev/null 2>&1
  else
    AUDIT_MODE="$mode" RETRY_ATTEMPTS="$retry" docker compose up -d bridge >/dev/null 2>&1
  fi
  for _ in $(seq 1 40); do
    local got gotr
    got="$(cfg_json anchor-config | jget auditMode || true)"
    if [ "$got" = "$mode" ]; then
      gotr="$(cfg_json anchor-config | jget retryAttempts || true)"
      [ "$gotr" = "$retry" ] || fail "bridge came up in audit mode '$got' but with retryAttempts=$gotr, expected $retry"
      note "bridge is in audit mode: $got, retry budget: $gotr"
      return 0
    fi
    sleep 1
  done
  echo "--- bridge log (last 15 lines) ---"; docker compose logs --tail 15 bridge 2>&1 | sed 's/^/    /'
  fail "bridge did not come up in audit mode '$mode' within 40 s"
}

# --- preconditions -----------------------------------------------------------
if [ "${DRY_RUN:-0}" = "1" ]; then
  note "DRY RUN: preconditions skipped"
else
bt="$(python3 -c 'import json; print(json.load(open(".fabric-config.json")).get("batch_timeout",""))' 2>/dev/null || true)"
[ "$bt" = "2s" ] || fail "orderer BatchTimeout is '$bt', not 2s -- re-run fabric/network-up.sh --batch-timeout 2s first, or the C results will not be comparable"

# The running bridge must know the paired schedule. An older container
# accepts the request and silently ignores the field; scenario_c refuses
# that at trial time, but better to refuse before the first seed.
set_mode ledger 3
sch="$(curl -s --max-time 3 -X POST -H 'content-type: application/json' -d '{"p":0,"seed":0,"schedule":"paired"}' "$BRIDGE/fault-config" | jget schedule || true)"
[ "$sch" = "paired" ] || fail "bridge does not report schedule=paired from /fault-config -- rebuild it: docker compose up -d --build bridge"
curl -s --max-time 3 -X POST -H 'content-type: application/json' -d '{"p":0,"seed":0}' "$BRIDGE/fault-config" >/dev/null

# The outbox table must exist for Scenario O.
$PY - <<'EOF' || fail "revoke_outbox table missing or not usable by app_user -- apply postgres/primary/outbox.sql (see its header)"
import sys; sys.path.insert(0, ".")
from scenarios.common import db
c = db.connect_pg_primary()
with c.cursor() as cur:
    cur.execute("SELECT to_regclass('public.revoke_outbox') IS NOT NULL, "
                "has_table_privilege('revoke_outbox', 'INSERT'), has_table_privilege('revoke_outbox', 'UPDATE')")
    ok = all(cur.fetchone())
c.close()
sys.exit(0 if ok else 1)
EOF
fi

mkdir -p results
t0=$(date +%s)
n_seeds=$(wc -w <<<"$SEEDS")
first_cfg="$(set -- $CONFIGS; echo "$1")"

# --- helpers --------------------------------------------------------------
cfg_mode()  { printf '%s' "${1%-noretry}"; }
cfg_retry() { case "$1" in *-noretry) printf 1 ;; *) printf 3 ;; esac; }
out_file()  { printf 'results/%s_p%s_%s.jsonl' "$PREFIX" "$1" "$2"; }   # p cfg
# Scenarios a pass runs: A and O only in the first configuration when
# AO_PASSES=first, otherwise in every configuration.
scen_for()  { if [ "$AO_PASSES" = "first" ] && [ "$1" != "$first_cfg" ]; then printf 'c'; else printf 'a,o,c'; fi; }

for cfg in $CONFIGS; do
  case "$(cfg_mode "$cfg")" in
    none|log|ledger) ;;
    *) fail "CONFIGS entry '$cfg' is not supported: use none-noretry | none | log | ledger (a different retry budget needs RETRY_ATTEMPTS support in this driver)" ;;
  esac
done

# Seeds already complete in a file: every scenario the pass runs must have a
# complete, correctly labelled, three-layer trial. An A or O trial that
# errored (an error record, no layers) leaves the seed un-done, so it is
# re-run rather than silently missing from the matched pairs.
done_seeds_in() {   # file mode retry p scen
  [ -e "$1" ] || { printf ''; return 0; }
  python3 -c '
import json,sys
want=sys.argv[2]; p=float(sys.argv[4]); need=sys.argv[5].split(","); layers={}
for l in open(sys.argv[1]):
    try: r=json.loads(l)
    except Exception: continue
    e=r.get("extra",{}); sc=r.get("scenario")
    if sc not in need or "error" in e: continue
    ok = abs(float(e.get("fault_p",-1))-p)<1e-9 and e.get("fault_schedule")=="paired"
    if sc=="c": ok = ok and e.get("audit_mode")==want and str(e.get("retry_attempts"))==sys.argv[3]
    if ok: layers.setdefault((r["seed"],sc), set()).add(r["layer"])
full={"relational","nosql","vector"}
seeds={s for s,_ in layers}
print(" ".join(str(s) for s in sorted(seeds) if all(layers.get((s,sc))==full for sc in need)))' "$1" "$2" "$3" "$4" "$5"
}

# Refuse to append to a pre-existing file unless RESUME=1, once per file.
declare -A FILE_OK
check_file() {   # file
  [ -n "${FILE_OK[$1]:-}" ] && return 0
  if [ -e "$1" ] && [ "$RESUME" != "1" ]; then
    fail "$1 already exists -- move it aside, or set RESUME=1 to skip the seeds it already holds"
  fi
  FILE_OK[$1]=1
}

seed_once() {   # seed
  if [ "$DRY_RUN" = "1" ]; then note "(dry) seed.py --seed $1"; return 0; fi
  $PY experiments/seed.py --scenario a --n "$SCALE" --seed "$1" || fail "seeding failed for seed $1"
}

run_trial() {   # p cfg seed scen
  local p="$1" cfg="$2" s="$3" scen="$4" out
  out="$(out_file "$p" "$cfg")"
  if [ "$DRY_RUN" = "1" ]; then note "(dry) run_experiment --scenarios $scen --seeds $s --fault-p $p -> $out"; return 0; fi
  $PY experiments/run_experiment.py --scenarios "$scen" --scales "$SCALE" --seeds "$s" \
      --regime faulty --fault-p "$p" --fault-schedule paired --outbox-poll-s "$OUTBOX_POLL_S" \
      --out "$out" --append \
    || fail "run_experiment failed for seed $s (p=$p $cfg)"
}

# Every measured record in a file must carry the labels it was run under.
verify_file() {   # p cfg
  local p="$1" cfg="$2" out mode retry chk bad n err skipped
  out="$(out_file "$p" "$cfg")"; mode="$(cfg_mode "$cfg")"; retry="$(cfg_retry "$cfg")"
  [ "$DRY_RUN" = "1" ] && { note "(dry) verify $out"; return 0; }
  chk="$(python3 -c '
import json,sys
want=sys.argv[2]; p=float(sys.argv[4]); n=0; bad=0; err=0; skipped=0
for l in open(sys.argv[1]):
    try: r=json.loads(l)
    except Exception: skipped+=1; continue
    e=r.get("extra",{})
    if "error" in e and r.get("layer") is None: err+=1; continue
    if r.get("scenario") not in ("a","o","c"): continue
    n+=1
    ok = e.get("fault_schedule")=="paired" and abs(float(e.get("fault_p",-1))-p)<1e-9
    if r["scenario"]=="c":
        ok = ok and e.get("audit_mode")==want and str(e.get("retry_attempts"))==sys.argv[3]
    bad += (not ok)
print(f"{bad} {n} {err} {skipped}")' "$out" "$mode" "$retry" "$p")"
  read -r bad n err skipped <<<"$chk"
  [ -n "$bad" ] || fail "$out: could not read the file to verify labels"
  [ "${skipped:-0}" = "0" ] || note "    ($skipped unparseable line(s) in $out; ablation_table.py skips them too)"
  [ "$bad" = "0" ] || fail "$out: $bad of $n records are NOT labelled p=$p schedule=paired (C: audit_mode=$mode retry=$retry)"
  note "$out: all $n measured records correctly labelled; $err trial(s) recorded as errors"
}

bridge_mode() {   # cfg
  if [ "$DRY_RUN" = "1" ]; then note "(dry) bridge -> $(cfg_mode "$1") retry $(cfg_retry "$1")"; return 0; fi
  set_mode "$(cfg_mode "$1")" "$(cfg_retry "$1")"
}

# --- the grid ---------------------------------------------------------------
if [ "$ORDER" = "config-outer" ]; then
  # Original order: p -> config -> seed. Every seed is re-seeded in every
  # pass (12 times at 3 p x 4 configs). Simple, but at ~95 s per seeding
  # this is 70% of the wall time; the rehearsal measured 2.5 min per
  # seed-pass, i.e. ~25 h for the full grid.
  for p in $P_VALUES; do
    for cfg in $CONFIGS; do
      out="$(out_file "$p" "$cfg")"; scen="$(scen_for "$cfg")"
      check_file "$out"
      done_seeds="$(done_seeds_in "$out" "$(cfg_mode "$cfg")" "$(cfg_retry "$cfg")" "$p" "$scen")"
      [ -z "$done_seeds" ] || note "RESUME: $out already complete for seeds: $done_seeds"
      say "p=$p CONFIG $cfg -> $out  ($n_seeds seeds, scale $SCALE, scenarios $scen)"
      bridge_mode "$cfg"
      i=0
      for s in $SEEDS; do
        i=$((i+1))
        case " $done_seeds " in *" $s "*) note "seed $s already complete -- skipped"; continue ;; esac
        printf '\n--- [p=%s %s] seed %s (%d/%d, +%dm) ---\n' "$p" "$cfg" "$s" "$i" "$n_seeds" $(( ($(date +%s)-t0)/60 ))
        seed_once "$s"
        run_trial "$p" "$cfg" "$s" "$scen"
      done
      verify_file "$p" "$cfg"
    done
  done
else
  # seed-outer (default): seed -> config -> p. Each seed's data is loaded
  # ONCE and every (config, p) cell is measured against it; the bridge is
  # restarted once per (seed, config) rather than once per (p, config, seed)
  # pass. The ACL is restored after every trial, so the stores are in the
  # seeded state at the start of each cell. Nothing about a cell's
  # measurement depends on which cells ran before it on the same data: the
  # paired schedule fixes every injected failure from (seed, layer, attempt)
  # alone, and the rehearsal showed A and O producing identical outcomes in
  # all four passes. Estimated ~4.5 h for 50 seeds at 3 p x 4 configs.
  for p in $P_VALUES; do for cfg in $CONFIGS; do check_file "$(out_file "$p" "$cfg")"; done; done
  i=0
  for s in $SEEDS; do
    i=$((i+1))
    # Which (p, cfg) cells still need this seed? Seed only if any does.
    todo=""
    for cfg in $CONFIGS; do
      scen="$(scen_for "$cfg")"
      for p in $P_VALUES; do
        ds="$(done_seeds_in "$(out_file "$p" "$cfg")" "$(cfg_mode "$cfg")" "$(cfg_retry "$cfg")" "$p" "$scen")"
        case " $ds " in *" $s "*) ;; *) todo="$todo $cfg:$p" ;; esac
      done
    done
    if [ -z "$todo" ]; then note "seed $s: every cell already complete -- skipped"; continue; fi
    printf '\n=== seed %s (%d/%d, +%dm): cells to run:%s ===\n' "$s" "$i" "$n_seeds" $(( ($(date +%s)-t0)/60 )) "$todo"
    seed_once "$s"
    for cfg in $CONFIGS; do
      scen="$(scen_for "$cfg")"
      cells=""
      for p in $P_VALUES; do case " $todo " in *" $cfg:$p "*) cells="$cells $p" ;; esac; done
      [ -n "$cells" ] || continue
      bridge_mode "$cfg"
      for p in $cells; do
        printf '\n--- [seed %s] %s p=%s (+%dm) ---\n' "$s" "$cfg" "$p" $(( ($(date +%s)-t0)/60 ))
        run_trial "$p" "$cfg" "$s" "$scen"
      done
    done
  done
  for p in $P_VALUES; do for cfg in $CONFIGS; do verify_file "$p" "$cfg"; done; done
fi

say "DONE in $(( ($(date +%s)-t0)/60 )) min"
mkdir -p analysis
for p in $P_VALUES; do
  first="$(set -- $CONFIGS; echo "$1")"
  if [ "$AO_PASSES" = "all" ]; then
    # A and O from EVERY pass: ablation_table.py de-duplicates them per seed
    # and warns if the same seed shows different failed layers in two
    # passes -- the cross-pass comparability check this mode exists for.
    ao=""
    for cfg in $CONFIGS; do ao="${ao:+$ao,}results/${PREFIX}_p${p}_${cfg}.jsonl"; done
  else
    ao="results/${PREFIX}_p${p}_${first}.jsonl"
  fi
  args=("A=$ao" "O=$ao")
  for cfg in $CONFIGS; do
    case "$cfg" in
      none-noretry) label="C no retry" ;; none) label="C retry" ;; log) label="C log" ;; ledger) label="C ledger" ;; *) label="C $cfg" ;;
    esac
    args+=("$label=results/${PREFIX}_p${p}_${cfg}.jsonl")
  done
  [ "$DRY_RUN" = "1" ] && { note "(dry) ablation_table.py ${args[*]}"; continue; }
  $PY experiments/ablation_table.py "${args[@]}" --out "analysis/${PREFIX}_p${p}" > "analysis/${PREFIX}_p${p}.log" 2>&1 \
    || note "ANALYSIS FAILED for p=$p -- see analysis/${PREFIX}_p${p}.log (the result files are intact; re-run ablation_table.py by hand)"
  tail -1 "analysis/${PREFIX}_p${p}.log"
done
note "bridge restored to ledger mode"
