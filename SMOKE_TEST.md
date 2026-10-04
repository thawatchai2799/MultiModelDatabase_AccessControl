# Smoke test on the VM — run in this order, stop at the first failure

Goal: first-ever end-to-end run. Nothing here has run against real Docker
or Fabric before, so expect failures; that is the point. When something
fails, send back: the stage number, the full command, and the complete
output (not a summary). Do not skip ahead past a failed stage — later
stages depend on earlier ones and their errors would be misleading.

Stages are ordered so that A and B (no Fabric needed) are proven before
Fabric and the bridge are touched. If Fabric turns out to be the hard part,
the A/B results are already real.

## Stage 0 — host prerequisites (once)

```bash
docker --version && docker compose version     # Docker Engine + compose v2
node --version                                 # 20.x expected (bridge + chaincode + Fabric client)
python3 --version                              # 3.12 expected
sudo apt install -y jq curl                    # network-up.sh needs both
nproc; free -g                                 # expect 10 / ~28
```

## Stage 1 — Python environment

```bash
cd ~/mldb            # wherever the zip was extracted (the dir containing docker-compose.yml)
python3 -m venv .venv && source .venv/bin/activate
pip install -r experiments/requirements.txt
```

`requirements.txt` pins psycopg 3.2.3 / pymongo 4.9.2 / qdrant-client 1.12.1,
but PROGRESS.md says the code's API usage was verified against 3.3.4 /
4.17 / 1.19.0. If pip succeeds, run `python experiments/check_env.py --no-bridge`
once the databases are up (Stage 3) and note the versions it prints — if
something fails only on the older pins, upgrading those three is the first
thing to try.

The sentence-transformers install pulls torch (large, several GB). It is
needed only by seed.py (embeddings). First seed run also downloads the
all-MiniLM-L6-v2 model (~90 MB) — the VM needs internet for that one time.

## Stage 2 — Docker stack (databases, pgbouncer, bridge *without* Fabric)

```bash
cp .env.example .env
# edit .env: set FABRIC_SAMPLES to the real absolute path (Stage 5); leave the rest
docker compose up -d postgres-primary postgres-replica pgbouncer mongodb qdrant
docker compose ps                    # all five should reach "healthy" / "running"
docker compose logs postgres-primary | grep -i -E "error|fatal" | head
docker compose logs postgres-replica | tail -20      # should show "started streaming WAL"
docker compose logs pgbouncer | tail -10
```

Do NOT start `bridge` yet.

## Stage 3 — connectivity check (no bridge)

```bash
python experiments/check_env.py --no-bridge
```

Every line must be PASS. This checks: both Postgres roles and their
BYPASSRLS flag, all three tables/matview from init.sql, pgvector present,
the replica actually in recovery, wal_level=logical + publication (B5),
both pgbouncer pool entries (B6), Mongo, Qdrant. Send the full output if
anything FAILs — each line carries the raw exception.

## Stage 4 — seed at 1K and run A + B (no Fabric, no bridge)

```bash
python experiments/seed.py --scenario a --n 1000 --seed 1
python experiments/seed.py --scenario b --n 1000 --seed 1
python experiments/run_experiment.py --scenarios a,b1,b2,b3,b4,b5,b6 --scales 1000 --seeds 1 --out results/smoke_ab.jsonl
python experiments/analyze.py results/smoke_ab.jsonl --out analysis/smoke_ab
cat analysis/smoke_ab/summary.md
```

What to look at (all of it is also in summary.md). Since progress-13 the
poller runs per layer on its own thread and is already watching before the
revoke is issued, so A's per-layer windows are measured (tens of ms) rather
than censored, and a 0 has a censor bound of about one poll interval.

- Any `WARNING: ... NOT retrievable before the revoke` line means the trial
  was set up wrong (not seeded / ACL already revoked) -- that record is
  excluded by analyze.py as invalid, not reported as 0 s.
- Any `TRIAL FAILED:` line in the run output → send it.
- Any `[restore] WARNING` line → send it (a failed grant-back taints later trials).
- Scenario A: three per-layer rows; windows of a few ms to tens of ms are
  plausible; a 30 s timeout on a layer means the revoke never landed there.
- B1 window ≈ somewhere in [0, 5 s] (refresh interval); B2 small; B3 strict
  ≈ 0; B4 should **time out** (held-open snapshot leaks until closed — that
  is the expected result, not a failure); B5 ≈ [0, 3 s]; B6 should show a
  leak rate of (close to) 50/50: the pool_size=1 pool makes the stale
  session variable deterministic. B1 / B5 windows should now be spread
  over (0, interval], not pinned at the full interval.
- Budget: this stage is ~1–3 minutes plus the one 30 s B4 timeout.

## Stage 4b — Scenario A regimes (decision 11; A only, no bridge needed)

```bash
python experiments/run_experiment.py --scenarios a --scales 1000 --seeds 1,2,3 --regime faulty --fault-p 0.10 --out results/smoke_a_faulty.jsonl
python experiments/run_experiment.py --scenarios a --scales 1000 --seeds 1,2,3 --regime async --async-interval-s 2.0 --out results/smoke_a_async.jsonl
python experiments/analyze.py results/smoke13b_ab.jsonl results/smoke_a_faulty.jsonl results/smoke_a_async.jsonl --out analysis/smoke_regimes
cat analysis/smoke_regimes/summary.md
```

Expect: A[async] vector windows spread over (0, 2 s]; A[faulty] trials
with an `injected transient failure` line end in a 30 s timeout on that
layer (that IS the result: a naive revoke that never landed). With p=0.10
about 27% of trials hit at least one layer, so most will still look
healthy -- that is expected at 3 seeds. Once the bridge is up (Stage 6),
run `--scenarios a,c --regime faulty` so both get the same p.

## Stage 4c — Scenario O, the transactional outbox (v1.2.0; no bridge needed)

On an existing cluster the outbox table must be created once (a fresh
volume gets it from docker-compose automatically):

```bash
docker exec -i mldb-postgres-primary psql -U mldb -d mldb < postgres/primary/outbox.sql
python experiments/check_env.py --no-bridge          # "pg primary: revoke_outbox (O)" must PASS
python experiments/run_experiment.py --scenarios a,o --scales 1000 --seeds 1,2,3 \
    --regime faulty --fault-p 0.30 --fault-schedule paired --out results/smoke_ao_paired.jsonl
python experiments/ablation_table.py A=results/smoke_ao_paired.jsonl O=results/smoke_ao_paired.jsonl
```

Expect: for every seed, the layers A reports as `injected transient
failure` are exactly the layers O had to retry -- `outbox_worker.layers.<l>.attempts` > 1
for nosql and vector, `outbox_revoke.inline_attempts` > 1 for relational
(which has no outbox row: it is the transaction the rows ride on). That is
the paired schedule working. O should close
every layer (unbounded retry); each retried layer's window is at least the
50 ms backoff. The table's matched-pairs block should list the seeds A lost
under "ref only".

## Stage 5 — Fabric

Run `node fabric/chaincode/test-chaincode.mjs` first: it checks the
chaincode's logic and its MVCC key layout without needing Fabric at all, so
a broken contract is caught before a three-minute network bring-up.


Reuse an existing fabric-samples checkout if there is one and it
is still on a machine you can copy from — same Fabric version as the E13
numbers, one less variable. Otherwise install per Hyperledger's current
docs (`install-fabric.sh docker samples binary`); I am not writing the
download URL from memory. Then:

```bash
# set FABRIC_SAMPLES=/home/<you>/fabric-samples in ~/mldb/.env (absolute path, no $HOME)
cd ~/mldb/fabric && FABRIC_SAMPLES=/home/<you>/fabric-samples ./network-up.sh
```

`--batch-timeout 500ms` rewrites the orderer's BatchTimeout for the sweep;
the value used is recorded in `.fabric-config.json` and copied into every
Scenario C record automatically.

It runs start to finish without prompting, and tears down any existing
network on the way (pass `--confirm` if you want to be asked first). The
last line on success is
`=== network-up.sh finished successfully (all 6 checkpoints) ===` — if you
do not see it, the run stopped early.

If a previous attempt got as far as starting chaincode containers, the
script now removes them itself before tearing the network down.

`network-up.sh` prints 6 checkpoints. Report which checkpoint fails and
the full output. The known-uncertain parts, in order of likelihood:
checkpoint 4 (CCaaS chaincode package/approve/commit — the deployCC-vs-CCaaS
question flagged in PROGRESS.md decision 1), and the final
host.docker.internal diagnostic (whether a container can reach the peer on
the host network).

## Stage 6 — bridge with Fabric, then Scenario C

```bash
cd ~/mldb
docker compose up -d --build bridge    # --build: server.js gained /fault-config in progress-14
docker compose logs -f bridge        # wait for "[bridge] listening on :8080, Fabric enabled"; Ctrl-C
python experiments/check_env.py      # now including the bridge -> audit backend /status check (reports ledger / log / none)
python experiments/seed.py --scenario a --n 1000 --seed 1     # C shares A's stores; re-seed to a clean state
python experiments/run_experiment.py --scenarios a,c --scales 1000 --seeds 1 --out results/smoke_ac.jsonl
python experiments/analyze.py results/smoke_ac.jsonl --out analysis/smoke_ac
cat analysis/smoke_ac/summary.md
```

Look at the false-containment section: on a healthy run expect
`true containment` = 1, everything else 0, `revoke rejected` = 0. If
`revoke rejected` = 1, the bridge could not anchor to Fabric (check
`docker compose logs bridge`).

If Fabric is blocking progress, C can be run unanchored to at least
validate the propagation path: set `FABRIC_ENABLED: "false"` on the bridge
in docker-compose.yml, `docker compose up -d --force-recreate bridge`.
analyze.py will then report bridge status as unavailable (501) — expected.

## Stage 7 — only after 4 and 6 are clean: a real-shape mini grid

```bash
python experiments/seed.py --scenario a --n 10000 --seed 1
python experiments/seed.py --scenario b --n 10000 --seed 1
python experiments/run_experiment.py --scenarios a,c --scales 10000 --seeds 1,2 --out results/grid_ac_10k.jsonl
python experiments/run_experiment.py --scenarios b1,b2,b3,b4,b5,b6 --scales 10000 --seeds 1,2 --out results/grid_b_10k.jsonl
python experiments/analyze.py results/grid_*.jsonl --out analysis/grid_10k
```

For the v1.2.0 paired campaign, once Stages 4c and 6 are clean
(`check_env.py` must also PASS "bridge paired fault schedule", or rebuild
the bridge with `docker compose up -d --build bridge`):

```bash
sudo apt install -y tmux && tmux new -s paired     # an ssh drop must not kill a 5-hour run
cd ~/mldb && source .venv/bin/activate
SEEDS="1 2 3" P_VALUES="0.30" PREFIX=rehearsal bash experiments/run_paired_grid.sh 2>&1 | tee results/rehearsal.log   # ~15 min
bash experiments/run_paired_grid.sh 2>&1 | tee results/paired.log   # the full grid: 50 seeds x 3 p x 4 configs, ~4.5 h
```

Detach from tmux with `Ctrl-b d`; reattach with `tmux attach -t paired`. If
the run is interrupted anyway, `RESUME=1 bash experiments/run_paired_grid.sh`
continues from the last complete cell. `DRY_RUN=1` prints the step
sequence without running anything.

Seeding note: `seed.py --seed N` must be run for every seed value used in
`--seeds`, since resource ids embed the seed (`res-<seed>-<i>`); by default
seed.py truncates, so seed with `--no-truncate` for the 2nd+ seed of the
same scale, and re-seed fresh (truncating) when changing scale.

## Two traps worth knowing

- `--out` refuses to reuse an existing file. That is deliberate: results
  from two builds merged into one file analyse as a single group whose
  medians belong to neither. Use a fresh filename per run.
- Read `docker logs peer0.org1.example.com | grep -c MVCC_READ_CONFLICT`
  AFTER the run, not before: `network-up.sh` recreates the peer, so its log
  starts empty and a zero before the run means nothing.

## What to send back

1. Stage number.
2. Exact command.
3. Complete output / traceback (paste, don't paraphrase).
4. For Docker problems: `docker compose ps` and the relevant `docker compose logs <service> | tail -50`.
5. The `results/*.jsonl` file if the run produced one — even a partial one is useful.
