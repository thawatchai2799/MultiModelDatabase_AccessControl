# Artefact changelog

## v1.2.0-dev — Paired fault schedule, a transactional-outbox baseline, and the ablation table as a script

Prepared for the IEEE Access resubmission. Three of the five substantive
objections an editor-style review raised against the first submission
(baseline too weak; A and C not exposed to the same failures; rate claims
resting on 20 seeds at one arbitrary p) are answered here with measurements
rather than wording. Nothing that produced a number in the first
submission is changed: the stream schedule, `run_faulty_grid.sh`, and every
archived result file reproduce exactly as before, and
`experiments/test_ablation_table.py` asserts that they do.

### Why the first submission's comparison was unpaired, with evidence

Scenario A draws its injected failures from Python's `random.Random`, the
bridge from its own `mulberry32`. Same p, same seed value, different
streams. From `results/faulty20_none.jsonl`: on seed 3 A lost its `nosql`
write while the bridge's first `nosql` attempt succeeded; on seed 11 A lost
all three layers while the bridge lost none; on seed 10 the bridge
exhausted three attempts on `relational` while A's single `relational`
attempt succeeded. Section V-G said this, but it left the headline "A leaks
without bound, C recovers" open to the objection that C simply had an
easier draw. The new schedule removes the objection instead of qualifying
it.

### The paired schedule (`scenarios/common/faults.py`, `bridge/app/server.js`)

The decision for (seed, layer, attempt) is now available as a pure
function: `mulberry32` seeded from a fixed mix of the three values, with
the first output compared against p. Python and Node implement it
separately and `bridge/gen_paired_fault_test.py` lifts the JavaScript out
of `server.js`, runs it under Node over 171 (seed, layer, attempt) inputs,
and requires every value to be identical to the Python -- not close,
identical, since a single disagreement means the two sides are not paired.
The test also checks that the decision is independent of the order in which
the layers ask (the bridge's three retries interleave on timers), that
Scenario A's single attempt is attempt 0, and that the stream schedule
still produces the sequence it always did.

The bridge's `withRetry` now passes the attempt index to the function it
retries, which is the only change to a code path that produced a reported
number; `gen_retry_budget_test.py` still passes all fifteen checks.
`/fault-config` takes and reports `schedule`; `scenario_c.set_fault_config`
refuses to proceed if a paired schedule was requested and the bridge did not
report it back, because an older container accepts the field and ignores
it, which would produce a file labelled paired that is not.

Porting `mulberry32` to Python needed care in one place: `Math.imul` and
the `>>>` shifts operate on 32-bit values, with `t` signed between steps
and unsigned at the end. The port masks after each multiply and treats the
shifts as logical, which the cross-language test is what actually verifies.

### Scenario O, the transactional outbox (`scenarios/scenario_o.py`, `postgres/primary/outbox.sql`)

Section VII-A of the first submission named the outbox and CDC as the
designs a practitioner would reach for and said neither was measured. A
reviewer will say that the "uncoordinated stack leaks without bound"
result is therefore against a straw man. This adds the outbox as a fourth
architecture, built the way an application developer would build it
without a bridge: the relational ACL update and one outbox row per remote
layer commit in one transaction; a relay thread polls the outbox every
20 ms (`--outbox-poll-s`), applies each pending row with the same store
operations A and C use, marks it done, and on failure reschedules it with
the bridge's 50 ms doubling backoff, capped at 1 s, with no attempt limit.

Its failures are paired with everyone else's: the relay reads the attempt
count from the row and asks the injector for that explicit index, so a seed
in which A loses `nosql` is a seed in which the relay's first `nosql`
attempt fails and its second is what closes the layer. The relay's own
verdict (`outbox_status`: no pending row for the pair's latest revoke) is
recorded per trial under its own name, never as `bridge_status`, so the
self-report-versus-truth split can be computed for O without anything built
for the bridge mistaking an O run for a C run.

What the design should be expected to show, stated before the run so the
result cannot be fitted afterwards: O should contain every trial (the
retry is unbounded), which the bridge did not (19 of 20); it should do so
at a window governed by the relay cadence plus the backoff its dropped
attempts cost; its verdict should be correct, since it is the same
transaction log the relay works from; and what it will not have is a
record outside the database that holds the data, written by a role that
cannot edit it -- which the tamper experiment (§VI-C4) is about and this
scenario does not repeat. If O turns out not to contain every trial, that
is the result to report.

The relational write cannot be retried by the relay (it is the transaction
the outbox rows ride on), so it is retried inline with the same backoff,
attempts numbered from 0 like every other layer. Bounded at 50 attempts,
which at the capped backoff is longer than the trial timeout.

Housekeeping the structural guarantee needs: the relay is stopped in the
trial's `finally` before the ACL is restored (a relay still running would
re-apply the revoke), and any row still pending is marked `abandoned`
rather than deleted, so the table and the result file agree about what
happened. `test_scenario_o.py` makes `revoke()` raise mid-trial and checks
the relay was still stopped.

Tested against a real PostgreSQL, not a mock, because the relay's
correctness is in its SQL; MongoDB and Qdrant are replaced by in-memory
stand-ins since the relay only calls `db.revoke_nosql` / `db.revoke_vector`
on them. Twenty-nine checks, including a full `run_trial_o` through the
real concurrent poller in which the attempt count for every layer equals
the number the paired function predicts from `fault_seed(scale, seed)`.

`docker-compose.yml` mounts `outbox.sql` as `02-outbox.sql` so a fresh
volume gets the table; an existing cluster needs it applied once, which
`check_env.py` now checks (with a warning if leftover pending rows exist,
since a relay would apply them).

### The ablation table as a script (`experiments/ablation_table.py`)

Table VII's numbers and the Newcombe intervals in §VI-C3 were computed
outside the artefact. They are now computed by a script that reads the
result files, in ladder order, and reports per configuration and per p:
trials leaking without bound with Wilson intervals; drift window and
latency medians with bootstrap intervals; the self-report split (false
containment, false non-containment, and whether a reported failure named
the layer that actually leaked); the Newcombe difference against a
reference configuration; and, only when every configuration ran under the
paired schedule on the same seeds, a matched-pairs block -- the seeds on
which two configurations disagree in each direction, the paired difference,
and an exact McNemar test. Under the stream schedule the block is omitted
rather than computed on a false premise.

Exclusions follow `analyze.py` exactly (invalid setup, dead poller,
incomplete trial, host-stall anomaly as `poll.py` marks it), are counted
per reason, and never enter a denominator. A and O appear in every pass's
file; the script de-duplicates them per seed and, under the paired
schedule, warns if the same seed shows different failed layers in two
files, which would mean the passes are not comparable.

Reproduction check, from the archived files: A 13 of 19 [0.46, 0.85], +conc
11 of 19 [0.36, 0.77], +retry / +log / +ledger 1 of 20 [0.01, 0.24];
windows 0.261 / 0.101 / 0.100 / 0.123 / 2.623 s; retry-versus-concurrency
Newcombe 0.53 [+0.24, +0.72]. All match the submitted Table VII and
§VI-C3.

**One inconsistency found in the submitted paper by this check.** §VI-C3
gives the concurrency-versus-baseline Newcombe interval as [−0.17, +0.39].
That interval is what one gets from A = 14 of 20 against 11 of 19. Table VII
prints A as 13 of 19 (the same pass as the ledger column, with seed 10
excluded for a stuck poller); against that row the interval is
[−0.19, +0.38]. The two A counts are both real measurements from different
passes, and the conclusion (the interval contains zero) is unchanged either
way, but the sentence and the table it refers to are computed from
different A runs. To be corrected in the revision, whichever A the table
settles on. Under the paired schedule this cannot recur: A's which-layers-
failed outcome is the same in every pass by construction, and the script
checks it.

Also worth recording: Table VII's latency column used the bridge-reported
server-side total for C and the caller-observed latency for A. The script
reports the caller-observed figure for every configuration (comparable
across A, O and C) and the bridge total beside it for C, so the revision
can choose one convention and say which.

### The paired campaign driver (`experiments/run_paired_grid.sh`)

Sweeps p over {0.05, 0.10, 0.30} (the first submission had 0.30 alone,
which a reviewer called arbitrary), 50 seeds (Wilson on 1 of 20 is
[0.01, 0.24]; the intervals at 50 are what make the ladder's rungs
distinguishable), four bridge configurations in ladder order, with A and O
in every pass -- under the paired schedule their outcomes must be identical
across passes, a stronger comparability check than the rate-only one the
old driver had. Output files carry p and configuration in the name and
every record is verified to carry the labels it was run under before the
pass is accepted. Refuses to start unless the orderer is at the 2 s
BatchTimeout every other C result used, the bridge reports the paired
schedule back, and the outbox table is usable. `AO_PASSES=first` halves the
A time at the cost of the cross-pass check.

`run_faulty_grid.sh` is untouched.

### Revision template and filler (`docs/revision_v58_template.md`, `docs/fill_revision.py`)

Every passage of the manuscript that the paired campaign changes, keyed
to the v57 source paragraphs: Abstract, contribution 4, §5.3, §5.7,
§6.3.3 with the six-column Table 7, a new Table 8 for the p sweep, the
Figure 6 caption, §7.1, §8.3, §8.4, §9.1 and the Conclusion -- including
the five places that still say A and C are "comparable in rate, not
paired", which a search for only the two previously fixed strings would
have missed again (v26's lesson). Numbers are `{{placeholders}}` resolved
by the filler from `ablation_table.py`'s JSON; three passages whose
wording depends on what the data show (does the outbox contain every
trial; are there seeds on which only the concurrent bridge leaked) are
IF/ELSE/ENDIF blocks the filler resolves and reports. The filler refuses
to write while anything is unresolved. Dry-run on the v57 files relabelled
as paired: 495 values, 0 unresolved.

The first dry run found a bug in the filler's own IF parser: a bare IF
without ELSE let the pattern run on to the *next* block's ELSE, so the
outbox paragraph was being chosen by the wrong condition. Replaced by a
strict IF/ELSE/ENDIF grammar in which each block is closed by its own
ENDIF and a nested or unterminated block aborts the run.

**Bug fixed in `ablation_table.py`**, found by the same dry run:
`Path.with_suffix` on an output stem such as `paired_p0.30` treated `.30`
as the extension and wrote `paired_p0.md`; `run_paired_grid.sh` would have
produced three files named `paired_p0.*` overwriting one another. The
suffix is now appended, never substituted.

### Three review rounds after the first cut (recorded so the fixes are traceable)

Round 1 (edge-case tests here), round 2 (an independent reviewer that had
not seen the work read every changed file), round 3 (cross-file
consistency by script: every template placeholder resolvable, every file
path in README/SMOKE_TEST/CHANGELOG existing, test counts matching). Round
2 found twelve items; the ones that changed behaviour:

- **`outbox_status` answered from the wrong event.** It looked up the
  latest revoke for the (resource, principal) pair. The same pair is picked
  for a given (scale, seed) in every pass, and outbox rows are never
  deleted, so a trial whose own transaction never committed would have been
  answered by an earlier pass's finished event and scored as a false
  containment the outbox never claimed. Now scoped to the trial's own event
  id; an event with no rows reports "revoke not committed". Tested.
- **Pending rows left by an interrupted run** (Ctrl-C before the trial's
  own `abandon_event`) would be applied by the next trial's relay after the
  ACL restore, and a later trial on that pair would start already revoked
  and be excluded as an invalid setup. Every trial now abandons all
  pending rows before it starts and says how many. Tested.
- **The driver never exercised the cross-pass A/O check** it was documented
  to provide: it handed `ablation_table.py` one file for A and O, and the
  check only runs across several. With `AO_PASSES=all` the analysis now
  reads A and O from every pass's file.
- **Exclusions were counted per file, not per seed**, so with several files
  a seed excluded in one pass and valid in another was both counted and
  excluded, and a seed excluded in every pass was counted once per pass.
  Now a seed is excluded only if no file holds a valid trial for it, and
  counted once; reported per p rather than as a label-wide total. Tested.
- **RESUME counted a seed as done on its C trial alone**; an A or O trial
  that had errored left the seed "done" with no matched pair. Now every
  scenario the pass runs must have a complete, correctly labelled trial.
- **The driver's final analysis step could fail silently** (`| tail -1`
  without `-e`); it now logs to `analysis/<prefix>_p<p>.log` and says so.
- `fill_revision.py`: the three per-seed placeholders are now resolved from
  per-trial data that `ablation_table.py` exports (`leaked_seeds`,
  `trials`, `excluded_seeds`) instead of being written out as markers; a
  condition on a non-numeric field fails with a message instead of a
  traceback; a ladder with an unknown label is skipped in the rung
  comparison and a missing canonical configuration is a clear error.
- Comments corrected: `outbox.sql` (applied by the compose mount, not by
  init.sql; the harness abandons rows, never deletes); SMOKE_TEST Stage 4c
  (relational has no outbox row -- its attempts are `inline_attempts`);
  the driver refuses a `CONFIGS` entry outside the four rungs, and the
  "extra rungs" note below says so.
- Not changed, deliberately: `OutboxWorker.stop()` closes the relay's
  connections after a bounded join; if the join ever times out (a store
  call hung past its own client timeout) the relay's eventual write could
  land after the restore. The client timeouts make this a second-order
  case; noted rather than engineered around.

Test counts after the rounds: `test_scenario_o` 29 checks, `test_ablation_table` 30.

### First run on the VM (3-seed rehearsal, 4 October) and the driver's loop order

The rehearsal -- `SEEDS="1 2 3" P_VALUES="0.30"`, all four configurations,
on the same VM as every other result -- behaved as designed:

- The paired schedule held exactly. For every seed the attempt count the
  function predicts equals the attempts the relay made and the attempts
  the bridge made, in all four passes (seed 2: relational/nosql/vector
  need 2/2/2 attempts -- A lost all three, the no-retry bridge failed all
  three, the retrying bridge and the relay each succeeded on attempt 2 of
  each). A's failed layers were identical in all four passes.
- The outbox contained 3 of 3; the bridge with retry 3 of 3; no retry 0 of
  3; A 0 of 3. No relay error, no excluded trial, every record labelled.
- Not a result yet (three seeds), but worth watching at fifty: the
  ledger's `anchorEventMs` was 2.6, 4.2 and 5.9 s against the 2.6 s
  median of the first submission; the two slow ones were the first
  revokes after the Fabric network came up.

Two things went wrong, neither in the measurement:

- **The ssh session dropped and took the run with it** (SIGHUP to the
  shell, so the driver, tee and python all died while the terminal still
  showed the last output). `RESUME=1` recovered it in four minutes. The
  SMOKE_TEST instructions now say to run the campaign under `tmux`.
- **Seeding dominated the wall time**: 95 s per seed per pass against
  ~35 s of measurement, and the original loop order re-seeds every seed
  in every (p, config) pass -- twelve times. Measured per seed-pass time
  was 2.5 min, which is ~25 h for the full grid, not the 6-10 h the
  script header guessed. The driver's default order is now
  **seed-outer**: each seed is seeded once and every (config, p) cell is
  measured against it, with the bridge restarted once per (seed, config)
  instead of once per pass. The ACL is restored after every trial, the
  paired schedule fixes each cell's injected failures from (seed, layer,
  attempt) alone, and the rehearsal showed A and O producing identical
  outcomes across passes, so the order of cells on the same data does not
  touch the measurement. `AO_PASSES=first` is now the default (A and O
  once per (seed, p)); the estimate for 50 seeds x 3 p x 4 configs is
  about 4.5 h. `ORDER=config-outer` keeps the original loops.

Also in the driver: `DRY_RUN=1` prints the sequence of seed / bridge /
trial steps without a stack (used to check both orders and RESUME against
the rehearsal's own result files), and `HF_HUB_OFFLINE=1` is the default
because sentence-transformers contacts the Hugging Face Hub on every
model load unless told not to; the model is cached after the first
seeding on a machine.

### The v58 manuscript build (`docs/build_v58.py`, `docs/v58_edits.py`)

The scripts that built v57 did not survive the session that made them;
the v57 .docx did. v58 is therefore built by editing the IEEE v57 file in
place, which leaves everything already verified in it alone and touches
only what the paired campaign changes. `v58_edits.py` lists every change
as an exact-match replacement -- 40 paragraph edits, 8 of them in the
abstract -- and `build_v58.py` applies them with the v57 discipline: a
replacement that does not match exactly one paragraph aborts; every
number is a `{{placeholder}}` resolved from the `ablation_table.py` JSON;
an unresolved placeholder aborts; and before anything is written the
document is checked for stale phrases, unresolved markers, the 250-word
abstract limit, ascending first-use citation order, uncited references,
and the TABLE / FIGURE sequence. A `--roundtrip` mode opens and saves
with no edits and verifies the text identical (it is).

What the build does beyond text: inserts the O column into Table VII
(cloning the A cell in every row, resetting `tblGrid`); clones Table VII
into the new Table VIII (the p sweep) in a one-column section of its own
right after Figure 6, as the template holds every full-width table;
renumbers the former Tables VIII--X to IX--XI in captions and text (done
first, on v57 text, so the edits can use final numbers); rebuilds the
per-seed table (now XI) at 50 x 8 from the per-trial lists, with the
"injected layers" column computed from the paired schedule rather than
read off Scenario A, and aborting if A's outcome ever disagrees with the
schedule; replaces the Figure 6 image by its relationship id and rescales
its height to the new aspect; replaces [26], removes [45], inserts the
lottery paper after [50] and renumbers all 71 in-text citation tokens
(tables included).

Two things the dry builds caught: (1) the first renumbering pass assigned
`run.text` to every run whether changed or not, and python-docx's setter
drops the run's children -- the Figure 6 drawing vanished, the IEEE v27
masthead loss again. Every run write now goes through one function that
assigns only on change and refuses a run that holds a drawing. (2) The
v57 tables keep each cell as an empty unformatted run followed by the
text in an 8-pt run; writing into the first run rendered the new column
at body size. Cell writes now go to the run that carries the formatting.
A third, from LibreOffice's rendering: Table VIII appended inside Table
VII's section sat under Figure 6; it has its own section now. (The
submitted PDF is made in Word on the author's machine; LibreOffice here is
a proxy, and the Word rendering is what the author must check.)

**Abstract**: adding the outbox and the paired schedule cost 14 words
against a 250-word limit that v57 met exactly, so eight sentences were
tightened (e.g. "only as strong as the slowest of the three" -> "... the
slowest"; "Consolidation does not remove the problem" -> "Consolidation
does not help"; "a transaction-pooled connection" -> "a pooled
connection"; "at each scale" dropped after "150 of 150 calls"). 247 words
with two-digit counts in every placeholder. No claim was removed.

Decisions D1--D5 of the revision template, as built: Table VIII inserted
and the rest renumbered (D1); caller-observed latency in every column,
with Table VII's row label unchanged (D2); same VM, so no second testbed
(D3); Figure 6 with six bars from `make_paper_figures.py`, which now
reads the paired files when present and keeps the v57 path otherwise
(D4); Table XI kept in the paper at 50 rows (D5).

To build v58 once the campaign has finished:

    python3 experiments/ablation_table.py ... --out analysis/paired_p0.30   (the driver does this)
    python3 docs/make_paper_figures.py              # Figure 6 from results/paired_p0.30_*.jsonl
    python3 docs/build_v58.py --v57 MultiModelDatabase_AccessControl_IEEE_v57.docx \
        --p30 analysis/paired_p0.30.json --p10 analysis/paired_p0.10.json --p05 analysis/paired_p0.05.json \
        --fig6 docs/figures/fig7_ablation.png --out MultiModelDatabase_AccessControl_IEEE_v58.docx

### The paired campaign (4 October) and v58

Fifty seeds x three injection probabilities x four bridge configurations
on the same VM, seed-outer order, 257 minutes, no trial error, every
record label verified by the driver, one host stall (Scenario A, seed 3,
p = 0.30: a 388 s window, caught by the clock-anomaly guard and excluded).
Files: `results/paired_p{0.05,0.10,0.30}_{none-noretry,none,log,ledger}.jsonl`,
analyses in `analysis/paired_p*.{md,json}`.

Trials leaking without bound (of 50; A at 0.30 is of 49):

| p    | A      | O     | +conc  | +retry | +log  | +ledger |
|------|--------|-------|--------|--------|-------|---------|
| 0.05 | 6      | 0     | 6      | 0      | 0     | 0       |
| 0.10 | 16     | 0     | 16     | 0      | 0     | 0       |
| 0.30 | 35/49  | 0     | 36     | 6      | 6     | 6       |

The pairing held at every p: the seeds on which A leaked are exactly the
seeds on which the no-retry bridge leaked (36 at 0.30, the 36th being the
seed A lost to the stall), with no discordant pair anywhere. At 0.30 the
bridge's bounded retry left 6 of 50 open -- 0.12 [0.06, 0.24], against
the model's ~8% per revoke and the first submission's 1 of 20 -- and the
outbox's unbounded retry closed all 50, including those 6, between 0.56
and 1.90 s after the revoke. Windows at 0.30: A 0.224 s, O 0.538 s, +conc
0.162 s, +retry 0.124 s, +log 0.164 s, +ledger 2.872 s; the ledger costs
5.1 s of caller-observed latency over the table (5.321 s against 0.255 s;
the first submission measured 4.8 s).

**A confound the sweep exposed, in timings only.** With seed-outer order
each configuration's p = 0.05 cell is the first revoke after a bridge
restart, so its latency and window include connection warm-up (ledger
7.95 s and 5.6 s at 0.05 against 5.3 s and 2.8 s at 0.10 and 0.30; the
no-retry bridge 0.42 s against 0.16 s). Proportions are unaffected. The
paper reports timings from p = 0.30 only and Table VIII's caption says
so; a future driver should issue one unmeasured warm-up revoke after each
restart.

**v58 built** from the campaign with `docs/build_v58.py`: 40 paragraph
edits, Table VII at seven columns, Table VIII new, Tables IX--XI
renumbered, Table XI at 50 x 8, Figure 6 at six bars, references
reworked ([26] replaced, [45] removed, lottery [50]); abstract 247 words;
every check passed. 25 pages (v57: 24). The cover letter is updated to
v58 (fifty seeds, three probabilities, the six-way ablation).

Reading the built paragraphs found five things the placeholders had
resolved correctly but worded badly, fixed in `v58_edits.py` /
`build_v58.py` and rebuilt: Table VII's "trials the record could confirm
containment" row printed n of n instead of the confirmed count (44 of 50;
Figure 6's panel (c) had it right -- the v37 cross-check); "McNemar p =
< 0.001"; a zero-discordant comparison that read "disagreed on 0 seeds --
0 on which ..." now has its own sentence; the injected-seed count in
Table VII's caption came from A's valid trials (35) rather than the
schedule (36), and is now computed from the schedule with a check that A
leaked on every valid injected trial; and the O cell of "leak detected
and attributed" read "0 of 0". Figure 6's six-bar labels overlapped as
the two "19/20" labels did at v40; they are smaller and staggered.

Not changed in v58, for the author to decide: Table I (testbed) is
unchanged since the campaign ran on the same VM; the response letter to
the SMC Section decision is unnecessary (the resubmission is as a Regular
Manuscript); the lottery reference needs its DOI from IEEE Xplore and
[26] its LNCS volume confirmed from Springer; §IX-B must cite the Zenodo
version DOI of v1.2.0 once it is tagged and archived; and the PDF for
submission must be made from the .docx in Word, as v57's was.

### Nine review rounds over the v58 work (4 October)

Each round took a different angle and changed only what it found wrong;
rounds 1--3 (independent recomputation of every number in Tables VII,
VIII and XI and in Figure 6 from the raw jsonl) found nothing.

- **Round 4 -- the rest of the paper.** Sections VI-B and VI-C and
  Tables III, V and VI still described the twenty-seed campaign (14 of 20,
  261 ms, 1 of 20, 2.62 s). Twenty-two further edits in `v58_edits.py`
  put them on the fifty-seed data; `build_v58.py` gained a `cell` edit
  kind (table by header, row by first cell and -- where two rows share a
  label, as Table III's two "A / C" rows do -- by the old text) and the
  `closed_rate`/`win_ms` values. Checked: the six ledger-bridge leaks all
  returned HTTP 207 and named the right layer; the 44-of-50 recovery is
  0.88 [0.76, 0.94].
- **Round 5 -- references.** 56 entries numbered 1--56, every one cited,
  first use in increasing order, [26] and [50] as agreed, biographies
  byte-identical to v57.
- **Round 6 -- document structure.** Headers and footers identical to
  v57 in every section, 9 drawings (7 figures, 2 photographs) with every
  image relationship resolving, only Figure 6's image changed, 11 tables
  with grids matching their cells, no placeholder left, one-column
  sections only where Figure 6 and Table VIII sit. Two builds from the
  same inputs produce identical text.
- **Round 7 -- a reviewer's read, by an agent that had not seen the
  build.** 46 findings; the genuine errors were fixed, the pre-existing
  style points left for the authors. Fixed: (BLOCKER, ours) v57 cited
  the deleted reference [45] in the out-of-scope paragraph and the
  renumbering would have pointed that citation at Androulaki et al. --
  the citation is removed with the reference and `rework_references`
  now aborts on any citation to a deleted entry; (BLOCKER, v57's) the
  five guard bullets of Section V-D5 were missing from the IEEE
  manuscript -- the paragraph ended in a colon -- and are restored from
  the authors' non-IEEE v57 (new `insert_bullets_after` edit kind, the
  stall sentence generalised to both campaigns); "three scenarios" and
  "two of the three" (now four, three of them injected); "one trial"
  detected-but-not-contained (now six, from the data, spelled out);
  "the same in all four of its columns" (three: the no-retry column
  leaked 36); the VIII-B bullet that said the injected regime gives "a
  qualitative difference rather than a rate", which contradicted V-G
  and VIII-D once fifty seeds were run; Table II's recovery definition
  tied to the bridge's three-attempt budget while the abstract applies
  the term to the outbox; the ablation description "adds one mechanism
  to the one before it", wrong for the third column (it adds to A, not
  O); "poller guard" / "poller error" / "clock anomaly" unified on the
  guard that actually fired; "each affected trial" for one trial; Table
  III's caption said O was measured "in the same cells" (one cell); the
  Figure 4 caption's dangling "three seeds per point". Left as the
  authors wrote them, for their decision: "six read paths ... serve
  revoked data" (B2 and B3 relaxed closed within resolution), "bounded
  and attributable" in IV-C and X, the duplicated run-to-run paragraph in
  V-H and IX, RQ labels never defined, the single-author acknowledgment,
  the long dashed sentence in Section X, "[4]--[5]", mixed -ise/-ize.
- **Round 8 -- code review, by an agent that had not written it.**
  Statistics confirmed against the definitions (Wilson, Newcombe method
  10, exact McNemar, bootstrap, the paired block's handling of the
  excluded seed); 30/30 and 171-value tests pass; reports rebuilt from
  all four pass files are byte-identical. Fixed: the bullet template for
  `insert_bullets_after` was located by a lead that the host-stall edit
  rewrites (any three-run bullet now serves); `build_v58.py` refuses an
  exclusion reason other than the clock-anomaly guard rather than
  narrating it as a stall; Figure 6's latency medians now exclude guarded
  trials as Table VII does (`caller_latencies`; the PNG is byte-identical
  because seed 3's latency sat at the median either side). Noted, not
  changed: `fault_seed(10000, ...)` is fixed to the campaign's scale (the
  A-leaked-equals-injected check would catch another), the degenerate
  Wald interval at b = c = 0 is printed but unused.
- **Round 9 -- rebuild and retest.** `docs/verify_v58.py` added: it
  recomputes Tables V, VI, VII, VIII and XI, the injected layers (from
  A's lost attempts, the bridge's attempt counts and the outbox's inline
  and relay attempts, which must agree seed by seed), the latency
  differences and every "k of n" / interval token in the prose from the
  per-layer jsonl, sharing no code with `ablation_table.py`; 0
  mismatches. Twelve of the thirteen suites pass here (the integration
  test needs the compose stack); chaincode tests pass. v58 is 26 pages
  in the LibreOffice proxy.

### v59 (4 October, evening)

An editor-style reading of v58 scored it 84% and expected reviewers to
ask whether the paper claims a new mechanism. It does not, and now says
so in one sentence after "This paper makes five contributions" and one at
the end of Section X (`v58_edits.py`, block "v59"); no number changed,
`verify_v58.py` 0 mismatches, abstract 248 words. The same reading
reported "Odrant" in Table I of the PDF; the .docx reads "Qdrant"
everywhere (checked), so it is a rendering artefact of the LibreOffice
proxy PDF, and the submission PDF must come from Word. The cover letter
is shortened by four lines so that it fits one page in Word (v58's ran
one line over) and now carries the same contribution sentence.

### v60 (4 October, 22:25) -- the archived release

Release v1.2.0 (commit 4f0f69f) is archived by Zenodo as
https://doi.org/10.5281/zenodo.23139029; Section IX-B now names it in
place of v1.1.0 / 22097730 (`v58_edits.py`, block "v60"). The only other
change from v59 is that one sentence; `verify_v58.py` 0 mismatches. The
repository head is one commit past the release: this CHANGELOG entry and
the v60 edit block.

### v61 (4 October, 22:40) -- reference details confirmed at source

Three reference entries completed from the publishers' own pages, each
confirmed by the author against the PDF or landing page: [26] gains its
Springer DOI 10.1007/978-3-642-34883-9_22 (LNCS 7646, pp. 275-287
confirmed from the chapter footer; the unconfirmed ordinal "5th" is
dropped); [50] gains the IEEE DOI 10.1109/ECTI-NCON.2019.8692241 and
the venue as IEEE Xplore names it; [21] gains vol. 4, no. 1, Art. no.
11, pp. 1-26, Feb. 2026 from the ACM reference format and "Honeybee"
as ACM spells it. [6] stays as it is: the Smart Cities submission is
still under review. Six changed lines against v60, all in the
reference list; `verify_v58.py` 0 mismatches.

### v62 (4 October, 22:50) -- the excluded trial, stated where it is used

A second editor-style reading of v61 raised four points. Two were not
defects: Table XI's "+ concurrency" column holds exactly 36 "unbounded"
cells (counted from the .docx), matching Table VII, and "Odrant" is the
proxy PDF's rendering of an 8-pt "Qdrant" (the PDF text layer says
Qdrant). Two were fair: the abstract's "35 of 49 trials" now reads "35
of 49 valid trials" (248 words), and the Table VIII caption now says over
which seeds the pairs are counted (49 for every comparison with Scenario
A at p = 0.30, 50 at the other two p), from `pair.*.n_pairs`. The
template's "10.1109/ACCESS.2026.DOI" and "xxxx 00, 0000" on page 1 are
IEEE Access's own placeholders and stay. Four changed lines against v61;
`verify_v58.py` 0 mismatches.

### Not done, and why

- **Network delay, packet loss, process crash** (the "single host"
  objection). Feasible with `tc netem` on the compose network and
  `docker kill` mid-revoke, and worth doing, but it is a different failure
  model from the dropped-write one every existing result uses and should be
  designed as its own regime with its own outcome definitions rather than
  folded into this change. Proposed as the next step.
- **CDC.** The outbox is the durable design a developer would build; CDC
  needs Debezium or a hand-written pgoutput consumer and would test that
  tool's latency more than the pattern. Named, not measured, as before.
- **Bounded retry with more attempts.** `RETRY_ATTEMPTS=5` or `10` would
  show where bounded retry meets the outbox's unbounded one. The bridge
  already supports any budget; the driver does not yet (it refuses a
  `CONFIGS` entry other than the four rungs), and `fill_revision.py` knows
  only the six canonical labels. Both are small extensions if the p sweep
  leaves the question open.

### Files

Added: `docs/revision_v58_template.md`, `docs/fill_revision.py`, `docs/build_v58.py`, `docs/v58_edits.py`, `docs/verify_v58.py`, `scenarios/scenario_o.py`, `postgres/primary/outbox.sql`,
`experiments/ablation_table.py`, `experiments/test_ablation_table.py`,
`experiments/test_scenario_o.py`, `experiments/run_paired_grid.sh`,
`bridge/gen_paired_fault_test.py`, `CHANGELOG.md`.

Changed: `scenarios/common/faults.py` (schedule; `mulberry32` port;
`should_fail_attempt`), `bridge/app/server.js` (schedule; attempt index
through `withRetry`; `/fault-config` reports it), `scenarios/scenario_c.py`
(`set_fault_config(schedule=)` with the stale-bridge refusal),
`experiments/run_experiment.py` (`--fault-schedule`, `--outbox-poll-s`,
scenario `o`, `fault_schedule` label on every A/C/O record),
`experiments/analyze.py` (groups scenario `o` like `a`/`c`; three lines),
`experiments/check_env.py` (outbox and paired-schedule checks),
`experiments/test_run_experiment_integration.py` (its mock of
`set_fault_config` takes the new argument), `docker-compose.yml` (mounts
`outbox.sql`), `README.md`, `SMOKE_TEST.md`.

Test status at this commit, in this environment (no Docker, no Fabric): 12
of the 13 suites were run and pass -- the six existing harness suites, the
three existing bridge suites (`gen_propagate_test` exercises the changed
`withRetry` signature), `gen_paired_fault_test` (171 of 171 values
identical), `test_scenario_o` (29 checks against PostgreSQL 16), and
`test_ablation_table` (30 checks, including the Table VII reproduction).
The chaincode suite was not run here (its npm dependencies are not
installed); the chaincode is unchanged. Nothing has yet run end to end on
the VM against the real stores and the bridge container -- Stage 4c and the
3-seed rehearsal in SMOKE_TEST.md are the first things to do there.
