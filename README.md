# Measuring authorization drift across relational, NoSQL and vector stores

Research artefact accompanying the study *A Ledger-Anchored Cross-Layer
Access-Control Architecture for Multi-Model Databases: Authorization-Drift
Detection Across Relational, NoSQL and Vector Stores*, by Preecha Noiumkar
and Thawatchai Chomsiri, Department of Information Technology, Research
Center of Information Technology for the Future, Faculty of Informatics,
Mahasarakham University.

This repository contains everything needed to reproduce the study: container
definitions for all four data services, the Fabric chaincode, the revocation
bridge, the measurement harness, the analysis pipeline, **the raw result
files behind every table and figure**, and the test suites.

Released under the [MIT License](LICENSE).

---

## What is measured

When one record lives in a relational store, a document store and a vector
store at once, a revoke has to reach all three. The **Leak Window** is the
interval from a revoke being issued to the last instant the record is still
retrievable through any layer. It is measured by a poller that queries the
stores directly, with its own connections, and never consults the system
under test.

Three architectures are compared:

- **A** — an uncoordinated polyglot stack: three store writes, no retry, no
  shared record of what happened.
- **B** — a converged PostgreSQL instance with pgvector and row-level
  security, read through six configurations (a materialized view, a
  streaming replica, approximate vector search under two orderings, a
  long-lived snapshot, a batch consumer, and a transaction-pooled
  connection).
- **C** — a bridge that records every revoke to an audit backend before
  touching any store, then propagates concurrently with bounded retry.
- **O** (v1.2.0) — a transactional outbox: the relational write and one
  outbox row per remote layer commit in one transaction, and a relay drains
  the outbox with unbounded retry. The durable-recovery baseline the first
  version of the study named but did not measure.

Scenario C is run against three audit backends — a Hyperledger Fabric
ledger, an append-only PostgreSQL table, and none at all — so that the
contribution of each mechanism can be separated from the others.

## Repository layout

```
bridge/              the revocation bridge (Node.js)
  app/server.js        /revoke, /status, fault injection, anchoring modes
  app/fabric-client.js Hyperledger Fabric audit backend
  app/audit-log.js     append-only PostgreSQL audit backend
  gen_*.py             tests that lift the real code and run it against mocks
fabric/              chaincode and network-up.sh (brings up the test network)
postgres/            init.sql, replica configuration, audit_log.sql
scenarios/           the three scenarios and the store clients
experiments/         harness, poller, analysis pipeline, tests, drivers
docs/                figure generators, the generated figures, and the v58
                     manuscript build (build_v58.py, v58_edits.py) with its
                     independent checker (verify_v58.py)
results/             raw result files (JSONL), one record per trial per layer
```

## Reproducing the results

Step-by-step instructions are in [SMOKE_TEST.md](SMOKE_TEST.md). In brief:

```bash
cp .env.example .env            # set FABRIC_SAMPLES to an absolute path
docker compose up -d --build    # PostgreSQL + replica, MongoDB, Qdrant, pgbouncer, bridge
cd fabric && ./network-up.sh --batch-timeout 2s && cd ..
docker compose restart bridge
python experiments/check_env.py         # 14 environment checks
python experiments/seed.py --scenario a --n 10000 --seed 1
python experiments/run_experiment.py --scenarios a,c --scales 10000 --seeds 1
python experiments/analyze.py results/<file>.jsonl --out analysis/<name>
```

Two environment constraints are not preferences:

- **MongoDB must be 7.0**, not 8.x. MongoDB 8.x refuses to start on Linux
  kernel 6.19 and newer (SERVER-121912), and the failure appears as a plain
  container exit rather than as a version error.
- **The Fabric binaries are 2.5.9 while the sample scripts come from the
  fabric-samples `main` branch**, because the v2.5.9 tag of that repository
  is no longer published. The two are used together deliberately.

`fabric/network-up.sh` verifies both, and ten further preconditions, before
anything is measured.

Three multi-run drivers are provided:

```bash
bash experiments/run_faulty_grid.sh    # 20 seeds x 3 audit backends, faulty regime (stream schedule)
RETRY=1 MODES="none" bash experiments/run_faulty_grid.sh   # concurrency-only ablation column
bash experiments/run_paired_grid.sh    # v1.2.0: A, O and C, p in {0.05, 0.10, 0.30}, 50 seeds, PAIRED schedule (~4.5 h; run under tmux)
bash experiments/tamper_test.sh        # administrator tampering, both audit backends
```

### The paired fault schedule (v1.2.0)

Under the original ("stream") schedule, Scenario A and the bridge draw
their injected failures from separate generators: the same failure *rate*,
not the same failures. `--fault-schedule paired` makes the decision for
(seed, layer, attempt) a pure function of those three values, computed
identically in the Python harness and the Node bridge
(`bridge/gen_paired_fault_test.py` verifies the two agree value for value).
Attempt k of a layer then fails in every scenario or in none — A's single
attempt is attempt 0, the relay's and the bridge's first attempt is the same
attempt 0 — so A, O and C differ in revoke discipline alone, and
`experiments/ablation_table.py` reports the per-seed outcomes as matched
pairs (discordant counts, exact McNemar) alongside the Wilson and Newcombe
intervals. The stream schedule remains the default; every file listed
below that predates v1.2.0 reproduces unchanged.

## Which result file produced which table or figure

| File | Content |
|---|---|
| `ac_healthy.jsonl` | Scenarios A and C, 1K records, 5 seeds, healthy regime |
| `ac_10k.jsonl`, `ac_100k.jsonl` | the same grid at 10K and 100K |
| `ac_faulty_v30.jsonl`, `ac_faulty_10k.jsonl` | faulty regime, p = 0.30, at 1K and 10K, 5 seeds |
| `faulty20_ledger.jsonl`, `faulty20_none.jsonl`, `faulty20_log.jsonl` | the 20-seed faulty grid, one file per audit backend |
| `faulty20_none-noretry.jsonl` | the same grid with the bridge's retry disabled (`RETRY_ATTEMPTS=1`), isolating concurrent propagation from bounded retry |
| `a_async16.jsonl` | Scenario A with an asynchronous vector worker |
| `a_poll50ms.jsonl` | the observer-effect check, at a 50 ms polling interval |
| `smoke13b_ab.jsonl`, `b_10k.jsonl` | Scenario B mechanisms B1–B6 |
| `c_bt500ms.jsonl`, `c_v29.jsonl`, `c_bt5s.jsonl` | the orderer BatchTimeout sweep |
| `c_bt5s_sync.jsonl`, `c_bt5s_async.jsonl`, `c_bt500ms_async.jsonl` | synchronous versus asynchronous anchoring |
| `ablation_ledger_on.jsonl`, `ablation_ledger_off.jsonl`, `ablation_logtable.jsonl` | the first five-seed ablation |
| `tamper_test.json` | the administrator-tampering experiment |
| `paired_p<p>_<config>.jsonl` (v1.2.0) | A, O and C under the paired schedule at p ∈ {0.05, 0.10, 0.30}, 50 seeds, one file per bridge configuration (`none-noretry`, `none`, `log`, `ledger`); A and O are in the `none-noretry` file. Behind Tables VII, VIII and XI and Figure 6 of the revised paper |

The ablation table (Table VII) is computed from the result files by
`experiments/ablation_table.py`; run without arguments for its usage. On the
`faulty20_*` files it reproduces the first submission's numbers exactly
(`experiments/test_ablation_table.py` checks this).

The revised manuscript is produced from the v57 Word file and the
`analysis/paired_p*.json` reports by `docs/build_v58.py` (every number in
it is a placeholder resolved from the reports; see `docs/v58_edits.py`),
and `docs/verify_v58.py` then recomputes Tables V–VIII and XI and the
prose counts from the raw `results/paired_p*.jsonl` files, sharing no
code with `ablation_table.py`:

```bash
python docs/verify_v58.py --docx MultiModelDatabase_AccessControl_IEEE_v58.docx --results results
```

Every figure is regenerated from these files by:

```bash
python docs/make_paper_figures.py        # data figures
python docs/make_schematic_figures.py    # architecture and chaincode key layout
```

Each is written as PDF (vector, for typesetting) and PNG at 600 dpi, sized
for a two-column layout.

## Record format

Each line of a result file is one trial-layer observation:

```json
{"scenario": "c", "scale": 10000, "seed": 3, "layer": "vector",
 "t_issued": 1787409206.3, "leak_window_s": 2.58,
 "confirmed_contained": true, "checks_performed": 271,
 "extra": {"revoke_latency_s": 4.94, "audit_mode": "ledger",
           "fabric_batch_timeout": "2s", "retrievable_before_revoke": true}}
```

Three conventions matter when reading these files:

- `leak_window_s` is the **lower bound** of an empirically bracketed
  interval: the last instant the layer was observed still readable. The true
  window lies between it and the first unsuccessful poll.
- `confirmed_contained: false` means the layer had not closed when
  observation stopped. Such a trial is an unbounded leak and carries no
  measured window; it is excluded from window statistics and counted
  separately.
- `extra` records the conditions the trial ran under — audit backend, block
  time, fault probability, whether the record was retrievable before the
  revoke — so a result file identifies its own configuration rather than
  relying on how it was filed.

## Tests

Thirteen suites exercise the harness, the chaincode and the bridge. All but
one need neither Docker, a network nor a running ledger:

```bash
for t in test_poll_concurrent test_faults test_analyze test_seed_guard \
         test_run_experiment_integration test_scenario_b5_priming test_ablation_table; do
  python experiments/$t.py; done
python bridge/gen_propagate_test.py
python bridge/gen_audit_log_test.py
python bridge/gen_retry_budget_test.py
python bridge/gen_paired_fault_test.py     # Python and Node agree on every paired decision
node fabric/chaincode/test-chaincode.mjs
# Scenario O's relay is tested against a real PostgreSQL (any reachable
# cluster; it creates and drops its own database, mldb_test_o):
POSTGRES_PORT=5432 POSTGRES_USER=mldb POSTGRES_PASSWORD=... python experiments/test_scenario_o.py
```

## Citing

Please cite the study. If you use the artefact itself, cite the archived
release in addition.
