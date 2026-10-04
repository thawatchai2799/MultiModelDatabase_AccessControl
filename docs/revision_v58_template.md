# Revision template for v58 — text changes that depend on the paired campaign

Every `{{...}}` is filled by `docs/fill_revision.py` from
`analysis/paired_p0.30.json`, `analysis/paired_p0.10.json` and
`analysis/paired_p0.05.json` (written by `experiments/ablation_table.py`,
which `run_paired_grid.sh` runs at the end). Nothing numeric is typed by
hand; the filler refuses to write the output while any placeholder is
unresolved. Placeholder grammar:

    {{<p>.<cfg>.<field>}}      p   = p30 | p10 | p05
                               cfg = A | O | Cnr | Cr | Clog | Cled
                                     (A, outbox, C no retry, C retry, C log, C ledger)
                               field = n | k | rate | lo | hi | win | winlo | winhi | lat | btot
                                       | sr_fc | sr_fnc | sr_att | sr_attable
    {{<p>.diff.<cfg>}}         Newcombe difference vs A:  "+0.63 [+0.34, +0.80]"
    {{<p>.pair.<cfg>.<f>}}     matched pairs vs A: refonly | cmponly | both | neither | pdiff | p
    {{<p>.rung.<cfg1>-<cfg2>.<f>}}  consecutive rungs, same fields
    {{<p>.A.layers}}           e.g. "nosql in 9, vector in 7, relational in 6"

Where a passage has two forms depending on what the data show, both are
given as a block `**IF** \`cond\`:` … `**ELSE**:` … `**ENDIF**` (ELSE
optional); `fill_revision.py` keeps the branch whose condition holds, drops
the other, and prints which it chose.

The paragraphs below are keyed to the single-column source
(`MultiModelDatabase_AccessControl_v57.docx`, paragraph numbers from the
extracted text) so the build script can locate each replacement; the IEEE
section labels are given beside them.

---

## 0. Decisions to make before filling (not data-dependent)

- **D1. Table numbering.** The ablation gains a column (O) and a second
  table for the p sweep. Proposed: Table 7 stays the p = 0.30 ladder, now
  six columns; new **Table 7b** (or Table 8 with everything after it
  renumbered) carries the three-p comparison of the unbounded-leak
  proportion only. The template assumes "Table 7" and "Table 8" with
  renumbering; change the labels in one place in the build script.
- **D2. Latency convention.** Table 7 v57 used the bridge-reported total
  for C and the caller-observed latency for A. The script reports both.
  Proposed: caller-observed for every column (comparable across A, O and
  C), with the bridge total mentioned once in §6.3.1 where the breakdown
  is discussed. The template uses `lat` (caller-observed) throughout.
- **D3. Testbed.** If the campaign ran on the same VM, delete §8.3-NEW
  below. If on another machine, keep it and update Table 1.
- **D4. Figure 6** gains a sixth bar (O) — in `make_paper_figures.py`,
  `fig_ablation` reads `paired_p0.30_*.jsonl`; the figure's in-chart labels
  must be regenerated from the data, never edited (lesson of v57 round 3).
- **D5. Table 10** (per-seed listing) becomes 50 rows × 6 columns. Either
  keep it as a full-page table or move it to the artefact and keep a
  summary line; the template assumes it stays.
- **D6. Two citation changes agreed on 4 October (not data-dependent).**
  (a) Reference [26] (hybrid tree-rule firewall, IEEE TCC) is *replaced in
  place* by T. Chomsiri, X. He, and P. Nanda, "Limitation of listed-rule
  firewall and the design of tree-rule firewall," in *Proc. 5th Int. Conf.
  Internet and Distributed Computing Systems (IDCS)*, LNCS vol. 7646,
  Berlin, Germany: Springer, 2012, pp. 275–287 — confirm volume and DOI
  from Springer before building. §II-B's third paragraph is rewritten so
  that the sentence claims only what the 2012 paper shows (listed-rule
  anomalies; tree-rule removes them by construction), not the hybrid's
  high-speed property:

  > Access control at the network perimeter has met a version of the same tension one layer down. A listed-rule firewall can carry rules that are shadowed by or redundant with earlier ones, so that the policy as written and the policy as enforced quietly diverge without anything in the system reporting it; the tree-rule firewall was designed to remove those anomalies by construction, so that a policy's effective behaviour can be read off its structure rather than assumed [26]. That trade — paying something in how a policy is expressed to keep its behaviour accountable — recurs in this work in a different form: the design in Section IV accepts additional revocation latency in exchange for a record of each decision that can be queried afterwards. The question here is not which packets are admitted, but whether a withdrawn authorisation has actually taken effect in every store that holds the record.

  (b) A new reference after [50], cited once as a clause in §II-E:
  P. Saichua, S. Khunthi, and T. Chomsiri, "Design of blockchain lottery
  for Thai government," in *Proc. 4th Int. Conf. Digital Arts, Media and
  Technology and 2nd ECTI Northern Section Conf. Electrical, Electronics,
  Computer and Telecommunications Engineering (ECTI DAMT-NCON)*, Nan,
  Thailand, 2019, pp. 9–12 — venue and dates confirmed from
  icdamt.up.ac.th/2019 (30 Jan–2 Feb 2019, Nan); add the DOI from IEEE
  Xplore. §II-E, end of the
  first paragraph, becomes:

  > The pattern recurs wherever several parties must agree on the history of an asset without a single trusted intermediary — agricultural product trading [50], or a public lottery whose draw records must be verifiable by the public rather than trusted to the operator [51] — and the property those settings pay for is the one Section VI-C4 measures here: a record that survives the people who run the system.

  (c) Reference [45] (Pansa and Chomsiri, dynamic password authentication)
  is removed. It supported only a contrast ("strengthening authentication …
  does nothing about a principal whose authorisation has since been
  withdrawn") that needs no citation. §II-D, second paragraph, becomes:

  > It is worth separating the two halves of the problem. Establishing that a principal is who they claim to be is an authentication question, and strengthening it — for instance by making the credential itself change between sessions — does nothing about a principal who was legitimately authenticated and whose authorisation has since been withdrawn. This paper assumes authentication is solved and measures what happens afterwards.

  Net effect on numbering: [45] removed, the lottery paper inserted after
  [50]; [46]–[50] become [45]–[49], the lottery is [50], and the former
  [51]–[56] keep their numbers. The build's citation-order check must pass.
  Self-citations become five of fifty-six: [3], [6], [26], [49] (NFT
  survey, formerly [50]), [50] (lottery) — one fewer than v57's six.

---

## 1. Abstract (¶7)

Replace the sentence

> The uncoordinated stack closes within tens of milliseconds when nothing fails, but when store writes are dropped it leaks without bound and in silence, in 14 of 20 trials at 10,000 records.

with

> The uncoordinated stack closes within tens of milliseconds when nothing fails, but when store writes are dropped it leaks without bound and in silence, in {{p30.A.k}} of {{p30.A.n}} trials at 10,000 records and p = 0.30.

Replace the ablation sentence

> An ablation separates what each mechanism buys: concurrency shortens the window but not the loss rate, bounded retry recovers 19 of 20 trials and the poller confirms containment in the same 19, an append-only table makes the outcome answerable, and the ledger adds only an audit record resisting alteration by the tested administrator, for 4.8 s more.

with

> An ablation under one failure schedule shared by every configuration separates what each mechanism buys: concurrency shortens the window but not the loss rate, bounded retry recovers {{p30.Cr.k_closed}} of {{p30.Cr.n}} trials, a transactional outbox with unbounded retry recovers {{p30.O.k_closed}} of {{p30.O.n}}, an append-only table makes the outcome answerable, and the ledger adds only an audit record resisting alteration by the tested administrator, for {{ledger_extra_s}} s more.

(`k_closed` = n − k. `ledger_extra_s` = Cled.lat − Clog.lat, computed by
the filler, one decimal.) Word count must be re-checked against the
250-word limit after filling; the new sentence is 11 words longer than the
old one, so trim elsewhere — candidates: "and the poller confirms
containment in the same 19" is already gone; "at each scale" after "150 of
150 calls" can go if needed.

---

## 2. Introduction, contribution 4 (¶19)

Replace

> Running the bridge with no record at all, with an append-only database table in place of the ledger, and with the ledger, under one injected-failure schedule, shows that the bounded retry does nearly all of the containment work, that a plain table makes the outcome as answerable as the ledger does at a fraction of the cost, and that what the ledger adds is an audit record which survived the deletion attempt we made against both backends as the system's own administrator.

with

> Running the uncoordinated stack, a transactional outbox, and the bridge with no record at all, with an append-only database table in place of the ledger, and with the ledger — all under one injected-failure schedule in which the same attempt fails in every configuration or in none, so that the configurations differ in revoke discipline alone — shows that retry does nearly all of the containment work whether bounded or not, that a plain table makes the outcome as answerable as the ledger does at a fraction of the cost, and that what the ledger adds is an audit record which survived the deletion attempt we made against both backends as the system's own administrator.

Also in ¶18, replace "recovered in 19 of 20 trials" with "recovered in
{{p30.Cr.k_closed}} of {{p30.Cr.n}} trials".

---

## 3. §5.3 Scenarios and regimes (¶117, ¶120)

¶117, replace the first sentence:

> Three scenarios are compared.

with

> Four scenarios are compared.

and append after "Scenario C routes the revoke through the proposed bridge.":

> Scenario O is a transactional outbox, the durable design a practitioner without a bridge would build: the relational write and one outbox row per remote layer are committed in a single PostgreSQL transaction, and a relay drains the outbox to the document and vector stores with unbounded retry — 50 ms doubling backoff, capped at 1 s, no attempt limit — polling every 20 ms. Its stores, schema, store operations and ground-truth poller are those of Scenarios A and C. Its own view of a revoke's state is the outbox itself: a revoke is reported contained when no row for it is pending. That report is read from the same database that holds the data, by the same role that can edit both, which is the property it does not share with the ledger and the reason it is in the comparison.

¶120 (Faulty), replace

> Each individual store-write attempt fails with probability p, drawn from a seeded generator so that a run is reproducible. Scenario A makes one attempt per store and does not retry, as an application without retry logic behaves; Scenario C retries up to three times with exponential backoff under the identical injection probability. The environment is thus the same for both and only the revoke discipline differs. Unless stated otherwise p = 0.30.

with

> Each individual store-write attempt fails with probability p. Whether the k-th attempt on a given layer fails is a pure function of the seed, the layer and k, computed identically in the Python harness and in the bridge, so that the same attempt fails in every scenario or in none. Scenario A makes one attempt per store and does not retry, as an application without retry logic behaves; that attempt is attempt 0. Scenario O's relay and Scenario C's bridge make the same attempt 0 and, if it fails, go on to attempts 1, 2, … — the bridge stopping after three, the relay not stopping. A seed in which Scenario A loses its write to one store is therefore a seed in which every other configuration's first write to that store also fails, and only its retry discipline decides what happens next. The failure schedule is thus paired across configurations trial by trial, not merely matched in rate, and Section 6.3.3 analyses the per-seed outcomes as matched pairs. The campaign is run at p = 0.05, 0.10 and 0.30; the ladder of Section 6.3.3 is reported at 0.30 and the proportions at all three.

---

## 4. §5.7 Repetitions (¶153, ¶154)

¶153, replace "Twenty seeds are used for the faulty regime at 10,000 records" with "Fifty seeds are used for the faulty regime at 10,000 records, at each of three injection probabilities".

¶154, replace the last sentence

> And the fault draws for Scenario A and for the bridge come from separate generators seeded from the same (scale, seed) pair, so the two scenarios face the same failure probability but not the same failure positions — they are comparable in rate, not paired trial by trial.

with

> The fault draws are paired across scenarios by construction (Section 5.3): attempt k on a layer fails for a given seed in every configuration or in none. This replaces the design of an earlier version of this study, in which Scenario A and the bridge drew from separate generators at the same probability and were comparable in rate but not trial by trial; the agreement between the two sides' draws is verified by a test that runs the bridge's generator under Node and compares every value against the harness's.

---

## 5. §6.3.3 Ablation (¶201–¶211) — rewritten

¶201, replace with:

> Scenario C bundles three mechanisms — bounded retry, concurrent propagation and a record of each decision — and the results above do not by themselves say which of them produces the improvement, nor whether the record needs to be a ledger at all, nor how the bridge compares with the durable design a practitioner would otherwise build. Six configurations were therefore run under one injected-failure schedule, paired trial by trial across all six (Section 5.3), at 10,000 records and fifty seeds: the uncoordinated stack; the transactional outbox; the bridge with concurrent propagation but the retry disabled; with the retry restored and no record of any kind; recording to an append-only PostgreSQL table; and with the ledger. From the third column on, each configuration adds exactly one mechanism to the one before it. Figure 6 and Table 7 give the comparison at p = 0.30; Table 8 gives the unbounded-leak proportion at all three p.

Table 7 caption, replace with:

> TABLE 7. Ablation at 10,000 records under injected failure (p = 0.30, fifty seeds per column, paired failure schedule). From the third column on, each column adds one mechanism to the one on its left. Intervals are Wilson 95%; differences between columns are given in the text as Newcombe 95% intervals and as matched-pair counts. Because the schedule is paired, the trials with an injected failure are the same seeds in every column — {{p30.A.k}} seeds, with {{p30.A.layers}} — verified seed by seed. Revoke latency is caller-observed for every column. † Scenario A's window is over the {{p30.A.n_closed}} trials in which no attempt failed; the other {{p30.A.k}} did not close. Table 10 lists every trial.

Table 7 body (six columns, row labels unchanged from v57 plus "self-report" rows):

| | A | O | +conc | +retry | +log | +ledger |
|---|---|---|---|---|---|---|
| trials leaking without bound | {{p30.A.k}} of {{p30.A.n}} [{{p30.A.lo}}, {{p30.A.hi}}] | {{p30.O.k}} of {{p30.O.n}} [{{p30.O.lo}}, {{p30.O.hi}}] | {{p30.Cnr.k}} of {{p30.Cnr.n}} [{{p30.Cnr.lo}}, {{p30.Cnr.hi}}] | {{p30.Cr.k}} of {{p30.Cr.n}} [{{p30.Cr.lo}}, {{p30.Cr.hi}}] | {{p30.Clog.k}} of {{p30.Clog.n}} [{{p30.Clog.lo}}, {{p30.Clog.hi}}] | {{p30.Cled.k}} of {{p30.Cled.n}} [{{p30.Cled.lo}}, {{p30.Cled.hi}}] |
| window, trials that closed (median) | {{p30.A.win}} s† | {{p30.O.win}} s | {{p30.Cnr.win}} s | {{p30.Cr.win}} s | {{p30.Clog.win}} s | {{p30.Cled.win}} s |
| revoke latency (median) | {{p30.A.lat}} s | {{p30.O.lat}} s | {{p30.Cnr.lat}} s | {{p30.Cr.lat}} s | {{p30.Clog.lat}} s | {{p30.Cled.lat}} s |
| self-report: false containment | n/a | {{p30.O.sr_fc}} | n/a | n/a | {{p30.Clog.sr_fc}} | {{p30.Cled.sr_fc}} |
| self-report: failures attributed to the right layer | n/a | {{p30.O.sr_att}} of {{p30.O.sr_attable}} | n/a | n/a | {{p30.Clog.sr_att}} of {{p30.Clog.sr_attable}} | {{p30.Cled.sr_att}} of {{p30.Cled.sr_attable}} |
| record survives the administrator (§6.3.4) | — | no (same role, same database) | — | — | no | yes |

New Table 8:

> TABLE 8. Trials leaking without bound at three injection probabilities (10,000 records, fifty seeds per cell, paired schedule). Wilson 95% intervals. The difference against Scenario A is a Newcombe 95% interval; "pairs" gives the seeds on which only A leaked / only this configuration leaked, with the exact McNemar p-value on the discordant pairs.

| p | A | O | +conc | +retry | +log | +ledger |
|---|---|---|---|---|---|---|
| 0.05 | {{p05.A.k}} of {{p05.A.n}} | {{p05.O.k}} of {{p05.O.n}}; pairs {{p05.pair.O.refonly}}/{{p05.pair.O.cmponly}}, p = {{p05.pair.O.p}} | {{p05.Cnr.k}} of {{p05.Cnr.n}}; pairs {{p05.pair.Cnr.refonly}}/{{p05.pair.Cnr.cmponly}}, p = {{p05.pair.Cnr.p}} | {{p05.Cr.k}} of {{p05.Cr.n}}; pairs {{p05.pair.Cr.refonly}}/{{p05.pair.Cr.cmponly}}, p = {{p05.pair.Cr.p}} | {{p05.Clog.k}} of {{p05.Clog.n}} | {{p05.Cled.k}} of {{p05.Cled.n}} |
| 0.10 | {{p10.A.k}} of {{p10.A.n}} | {{p10.O.k}} of {{p10.O.n}}; pairs {{p10.pair.O.refonly}}/{{p10.pair.O.cmponly}}, p = {{p10.pair.O.p}} | {{p10.Cnr.k}} of {{p10.Cnr.n}}; pairs {{p10.pair.Cnr.refonly}}/{{p10.pair.Cnr.cmponly}}, p = {{p10.pair.Cnr.p}} | {{p10.Cr.k}} of {{p10.Cr.n}}; pairs {{p10.pair.Cr.refonly}}/{{p10.pair.Cr.cmponly}}, p = {{p10.pair.Cr.p}} | {{p10.Clog.k}} of {{p10.Clog.n}} | {{p10.Cled.k}} of {{p10.Cled.n}} |
| 0.30 | {{p30.A.k}} of {{p30.A.n}} | {{p30.O.k}} of {{p30.O.n}}; pairs {{p30.pair.O.refonly}}/{{p30.pair.O.cmponly}}, p = {{p30.pair.O.p}} | {{p30.Cnr.k}} of {{p30.Cnr.n}}; pairs {{p30.pair.Cnr.refonly}}/{{p30.pair.Cnr.cmponly}}, p = {{p30.pair.Cnr.p}} | {{p30.Cr.k}} of {{p30.Cr.n}}; pairs {{p30.pair.Cr.refonly}}/{{p30.pair.Cr.cmponly}}, p = {{p30.pair.Cr.p}} | {{p30.Clog.k}} of {{p30.Clog.n}} | {{p30.Cled.k}} of {{p30.Cled.n}} |

Figure 6 caption, replace with:

> FIGURE 6. Ablation at p = 0.30. The outbox and the bridge face the same injected failures as the uncoordinated stack, seed for seed. Concurrent propagation shortens the window and leaves containment where it found it; bounded retry closes the leak in {{p30.Cr.k_closed}} of {{p30.Cr.n}} trials and the outbox's unbounded retry in {{p30.O.k_closed}} of {{p30.O.n}}; a record of any kind makes the outcome answerable; and the ledger alone makes that record resistant to the people who operate the database.

¶205, keep.

¶206 (concurrency), replace with:

> Concurrency shortens the window and does nothing for containment. Writing the three stores in parallel rather than in sequence, with the retry still disabled, cut the median window of the trials that closed from {{p30.A.win}} s to {{p30.Cnr.win}} s and the revoke latency from {{p30.A.lat}} s to {{p30.Cnr.lat}} s. Containment did not follow: {{p30.Cnr.k}} of {{p30.Cnr.n}} trials still leaked without bound, {{p30.Cnr.rate}} [{{p30.Cnr.lo}}, {{p30.Cnr.hi}}] against the baseline's {{p30.A.rate}} [{{p30.A.lo}}, {{p30.A.hi}}]; the Newcombe 95% interval for the difference is {{p30.diff.Cnr}}. Because the two face the same failures, the comparison can be made seed by seed: the two configurations disagreed on {{p30.pair.Cnr.discordant}} seeds — {{p30.pair.Cnr.refonly}} on which only A leaked and {{p30.pair.Cnr.cmponly}} on which only the concurrent bridge did — an exact McNemar p of {{p30.pair.Cnr.p}}. Concurrency changes how long a revoke takes to land, not whether it lands.

**IF** `p30.pair.Cnr.cmponly > 0`:

> The {{p30.pair.Cnr.cmponly}} seeds on which the concurrent bridge leaked and the sequential stack did not are seeds in which the same attempt failed in both; they differ in which layer the poller saw open last, and are listed in Table 10.

**ENDIF**

(This sentence exists because the unpaired v57 data could not distinguish
"C was unlucky" from "concurrency hurt"; with the paired schedule any such
seed has an explanation in the data and must be given one. If the filler
finds cmponly > 0, inspect those seeds before accepting the sentence.)

¶207 (retry), replace with:

> Retry does the containment work, and the bound on it is what the outbox removes. Adding the three-attempt budget to the same concurrent propagation, still with no record of any kind, took the unbounded-leak proportion from {{p30.Cnr.rate}} [{{p30.Cnr.lo}}, {{p30.Cnr.hi}}] to {{p30.Cr.rate}} [{{p30.Cr.lo}}, {{p30.Cr.hi}}] — a difference of {{p30.rung.Cnr-Cr.pdiff}} with a Newcombe 95% interval of {{p30.diff.rung.Cnr-Cr}}, which does not contain zero, and {{p30.rung.Cnr-Cr.refonly}} seeds recovered against {{p30.rung.Cnr-Cr.cmponly}} lost (McNemar p = {{p30.rung.Cnr-Cr.p}}). The bridge recovered in {{p30.Cr.k_closed}} of {{p30.Cr.n}} trials, every store write succeeding within the budget, and the independent poller confirmed containment in the same {{p30.Cr.k_closed}}. The window is unchanged at {{p30.Cr.win}} s and the latency rises only to {{p30.Cr.lat}} s.

**IF** `p30.O.k == 0`:

> The transactional outbox, whose relay retries without limit, contained every one of its {{p30.O.n}} trials, including the {{p30.Cr.k}} that exhausted the bridge's three attempts: on those seeds the relay's fourth or later attempt landed, and the layer closed {{p30.O.win_leaked_seeds}} s after the revoke. Its median window over all trials is {{p30.O.win}} s, governed by the relay cadence and the backoff its dropped attempts cost, and its latency, {{p30.O.lat}} s, is the cost of one local transaction. A deployment whose only requirement is that revokes must not be lost should build the outbox; it is cheaper than the bridge and its retry has no residue.

**ELSE**:

> The transactional outbox, whose relay retries without limit, leaked without bound in {{p30.O.k}} of {{p30.O.n}} trials, {{p30.O.rate}} [{{p30.O.lo}}, {{p30.O.hi}}]. [Inspect those seeds before writing this paragraph: an unbounded retry that fails to close a layer within 30 s means either a relay fault (recorded in `outbox_worker.relay_error`) or a backoff schedule that exceeded the observation limit, and the paragraph must say which.]

**ENDIF**

¶208 (table), replace numbers:

> An append-only table makes the outcome answerable, for {{log_extra_s}} s. Recording the same events to a PostgreSQL table with INSERT-only privileges gives the same containment verdict as the ledger in all {{p30.Clog.n}} trials — {{p30.Clog.k_closed}} confirmed contained, and the remaining {{p30.Clog.k}} correctly reported as not contained by both backends, naming the layer in {{p30.Clog.sr_att}} of {{p30.Clog.sr_attable}} — and raises latency from {{p30.Cr.lat}} s to {{p30.Clog.lat}} s. The outbox answers the same question from its own table, and in the trials evaluated its answer was also correct ({{p30.O.sr_fc}} false containments); what it cannot do is answer it from outside the database that holds the data.

¶209 (ledger), replace "for a further 4.8 s" with "for a further {{ledger_extra_s}} s" and "from 0.212 s to 4.987 s" with "from {{p30.Clog.lat}} s to {{p30.Cled.lat}} s".

¶210, keep, but replace the first sentence with:

> This is a more honest account of the design than the one the healthy and faulty results alone would support, and the outbox sharpens it.

¶211, replace with:

> One check confirms that the six columns are comparable rather than merely similar. Under the paired schedule the trials with an injected first attempt are the same {{p30.A.k}} seeds in every column, and Scenario A, which is run again in every pass, lost the same layers on the same seeds in all of them; the analysis script verifies this before producing the table and refuses if it does not hold. Within the six columns the only thing that differs is the revoke discipline.

---

## 6. §7.1 Discussion (¶230, ¶232, ¶233)

¶230, replace the ladder sentence numbers with placeholders (`{{p30.Cnr.win}}`, `{{p30.A.win}}`, `{{p30.Cr.k_closed}} of {{p30.Cr.n}}`, `{{ledger_extra_s}}`), and replace

> Most deployments should stop at the table: it closes the leak, it reports the residue, and it costs almost nothing.

with:

**IF** `p30.O.k == 0`:

> Most deployments should build the outbox and keep its table: it closes the leak without residue, it reports every outcome, and it costs one local transaction.

**ELSE**:

> Most deployments should stop at the table: it closes the leak, it reports the residue, and it costs almost nothing.

**ENDIF**

¶232, **delete** (the unpaired-comparison limitation no longer applies).

¶233, replace with:

> Two durable designs a practitioner will think of are the transactional outbox and a change-data-capture pipeline. The outbox is measured here, as Scenario O, and the comparison is in Table 7: it gives what a bounded in-request retry does not — a revoke that survives a process restart and a retry with no residue — at a cost below the bridge's, and its own table answers the same question the ledger does. What it does not give is an answer that survives the people who operate the database, and that is the only property for which the ledger's price is paid. A change-data-capture pipeline, which derives the propagation from the store's replication stream, was not measured; it would need the same Leak Window measurement applied to it before it could be placed on the ladder, and its latency would be that of the capture tool as much as of the pattern.

---

## 7. §8.3 External validity (¶257, ¶261) and §8.4 (¶263)

¶257 (host stalls): replace with the count from the new campaign; the
filler reports `{{anomalies}}` (total trials excluded as clock anomalies
across the three p) and the sentence should give the number and point to
Table 10.

¶261, replace with:

> Three retries do not eliminate unbounded leakage. Under the injection model used here, all three attempts for a layer fail with probability p³ per layer — about 8% per revoke at p = 0.30, 0.3% at 0.10 — and this occurred in {{p30.Cr.k}} of {{p30.Cr.n}} trials at 0.30 and {{p10.Cr.k}} of {{p10.Cr.n}} at 0.10. The outbox's unbounded retry removed it in the trials evaluated; a deployment that requires that guarantee should take the outbox's retry, with or without the bridge's record.

¶263, replace "uses twenty seeds per configuration" with "uses fifty seeds per configuration at each of three injection probabilities", replace the two proportions with `{{p30.A.rate}} [{{p30.A.lo}}, {{p30.A.hi}}]` and `{{p30.Cr.rate}} [{{p30.Cr.lo}}, {{p30.Cr.hi}}]`, and replace

> The two Wilson intervals do not overlap, which we report descriptively; no formal hypothesis test was performed, and nothing here should be read as a significance claim or generalised beyond the tested configuration.

with

> Because the failure schedule is paired, the comparison between configurations is also reported as matched pairs with an exact McNemar test on the discordant seeds (Table 8). The test is on the tested configurations at the tested probabilities; nothing here should be generalised beyond them.

**§8.3-NEW** (only if D3 says a second testbed was used), insert after ¶256:

> The paired campaign of Section 6.3.3 was run on a second machine (Table 1), after the first submission; every row of Tables 7 and 8 comes from that machine, and no timing in them is compared with a timing measured on the first. The proportions are not sensitive to the host; the windows and latencies are, and the two sets are kept apart for that reason.

---

## 8. §9.1 Per-seed results (¶271–¶274)

¶271: "twenty trials" → "fifty trials per configuration at each probability"; "the same seed in all three" → "the same seeds in all four bridge columns, and that Scenario O closed them".

Table 10 caption: "twenty-seed" → "fifty-seed"; the seed-10 sentences are
replaced by the filler's `{{excluded_seeds}}` listing (seed, scenario,
reason), or by "No trial was excluded." when none was.

¶273–¶274: rewrite from the filled Table 10. Delete the v57 accounting of
13 of 19 versus 14 of 20 — it no longer applies. Replace with:

> The accounting for Scenario A is as follows. Fifty trials were started at each probability; {{p30.A.k}}, {{p10.A.k}} and {{p05.A.k}} received an injected failure at p = 0.30, 0.10 and 0.05 respectively, and every valid trial with an injected failure leaked until the observation limit. The seeds on which the bridge exhausted its budget are {{p30.Cr.leaked_seeds}} at p = 0.30; on each of them the outbox's relay closed the layer on a later attempt.

---

## 9. Conclusion (¶279, ¶280)

¶279: "3 of 3 at 1,000 records and 14 of 14 at 10,000" → "3 of 3 at 1,000 records and {{p30.A.k}} of {{p30.A.k}} at 10,000".

¶280: "in 19 of the 20 evaluated trials" → "in {{p30.Cr.k_closed}} of the {{p30.Cr.n}} evaluated trials"; "retry alone closes the leak in 19 of 20 trials" → "bounded retry closes the leak in {{p30.Cr.k_closed}} of {{p30.Cr.n}} trials and an outbox's unbounded retry in {{p30.O.k_closed}} of {{p30.O.n}}"; "for some 4.8 s more" → "for some {{ledger_extra_s}} s more".

---

## 10. The Newcombe inconsistency in v57 (for the response to reviewers, not the manuscript)

v57 §6.3.3 gave the concurrency-versus-baseline interval as [−0.17, +0.39],
computed from A = 14 of 20; Table 7 printed A as 13 of 19 (the same pass as
the ledger column), against which the interval is [−0.19, +0.38]. The
conclusion is unchanged either way. In v58 both numbers are superseded by
the paired campaign and computed by one script from one file, so the
discrepancy cannot recur; if the response letter mentions it, say so in
one sentence.

---

## 11. Checklist after filling

- [ ] `grep -c '{{' MultiModelDatabase_AccessControl_v58.docx-source` is 0 (the filler enforces this).
- [ ] Abstract ≤ 250 words.
- [ ] Every number in Table 7 appears in the body text and in Figure 6's bars (the v37/v38 check, re-run).
- [ ] No sentence anywhere still says A and C are "comparable in rate, not paired" (search: "not paired", "failure positions", "separate generators"). Expected hits after filling: exactly one, the historical sentence in §5.7 that says an earlier version drew from separate generators; any other hit is stale text.
- [ ] Figure 6 regenerated from `paired_p0.30_*.jsonl`, in-chart labels read against Table 7 on the rendered page.
- [ ] Table 1 updated if D3 applies.
- [ ] Zenodo v1.2.0 tagged and §9.2 updated to its version DOI.
