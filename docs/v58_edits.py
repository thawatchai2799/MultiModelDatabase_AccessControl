"""Every text change from v57 to v58, as exact-match replacements against the
IEEE v57 manuscript (MultiModelDatabase_AccessControl_IEEE_v57.docx).

Each entry names the paragraph by its full v57 text ("old") -- or, for the
bullet paragraphs the IEEE build sets as three runs ("• ", bold lead-in,
body), by the lead-in and the body separately -- and gives the new text with
{{placeholders}} that docs/build_v58.py resolves from the ablation_table.py
reports. A replacement whose "old" does not match exactly one paragraph
aborts the build: this is the same discipline as v57's round 1, where a
wrong match count stops the run rather than editing the wrong paragraph.

Section labels are the IEEE form (Section VI-C3, Table VII), not the
single-column form the revision template was drafted in.

Kinds:
  "para"    -- whole single-run paragraph: old == paragraph text
  "bullet"  -- 3-run bullet: old_lead == runs[1].text, old == runs[2].text;
               new_lead (optional) and new replace them
  "delete"  -- remove the paragraph whose text == old
  "caption" -- Fig Caption paragraph (single run): old == text
"""

EDITS = [
    # ------------------------------------------------------------------ Abstract
    # Tightened to stay under the 250-word limit: v57 was 250 words and the
    # outbox and the paired schedule need ~14 words; the cuts are listed in
    # CHANGELOG (v58).
    {"kind": "abstract", "find": "When one record lives in a relational store, a document store and a vector store at once, revoking a principal is only as strong as the slowest of the three.",
     "new": "When one record lives in relational, document and vector stores at once, revoking a principal is only as strong as the slowest."},
    {"kind": "abstract", "find": "We define the Leak Window — from a revoke being issued to the last instant",
     "new": "We define the Leak Window — from the revoke to the last instant"},
    {"kind": "abstract", "find": "Three architectures are compared: an uncoordinated polyglot stack, a converged PostgreSQL instance",
     "new": "Four architectures are compared: an uncoordinated polyglot stack, a transactional outbox, a converged PostgreSQL instance"},
    {"kind": "abstract", "find": "Consolidation does not remove the problem: six read paths",
     "new": "Consolidation does not help: six read paths"},
    {"kind": "abstract", "find": "and a transaction-pooled connection inheriting a stale session variable leaked on 150 of 150 calls at each scale.",
     "new": "and a pooled connection inheriting a stale session variable leaked on 150 of 150 calls."},
    {"kind": "abstract", "find": "in 14 of 20 trials at 10,000 records.",
     "new": "in {{p30.A.k}} of {{p30.A.n}} trials at 10,000 records, p = 0.30."},
    {"kind": "abstract", "find": "An ablation separates what each mechanism buys: concurrency shortens the window but not the loss rate, bounded retry recovers 19 of 20 trials and the poller confirms containment in the same 19, an append-only table makes the outcome answerable, and the ledger adds only an audit record resisting alteration by the tested administrator, for 4.8 s more.",
     "new": "An ablation with every configuration facing the same injected failures separates what each mechanism buys: concurrency shortens the window but not the loss rate, bounded retry recovers {{p30.Cr.k_closed}} of {{p30.Cr.n}} trials and the outbox’s unbounded retry {{p30.O.k_closed}} of {{p30.O.n}}, an append-only table makes the outcome answerable, and the ledger adds only an audit record the tested administrator could not alter, for {{ledger_extra_s}} s more."},
    {"kind": "abstract", "find": "when nothing fails, but when store writes are dropped it leaks",
     "new": "when nothing fails; when store writes are dropped it leaks"},

    # ------------------------------------------------- VI-B / VI-C: one campaign
    # Round 4 of the v58 review: Sections VI-B and the opening of VI-C (Tables
    # V and VI, four paragraphs) still reported the first submission's 20-seed
    # campaign while VI-C3 reported the paired 50-seed one -- two values for
    # the same cell in one paper. Every 10,000-record faulty figure now comes
    # from the paired campaign; the 1,000-record faulty cells keep the original
    # five-seed run, which was not re-measured.
    {"kind": "para_sub",
     "old_prefix": "• An ablation that separates what each mechanism",
     "find": "We report that most deployments should therefore stop at the table.",
     "new": ("{{IF p30.O.k == 0}}We report that most deployments should therefore build the outbox and keep its "
             "table, and reserve the ledger for settings in which the database’s own administrators are inside the "
             "threat model.{{ELSE}}We report that most deployments should therefore stop at the table.{{ENDIF}}")},
    {"kind": "caption",
     "old_prefix": "Measurement coverage. Five seeds per cell for Scenarios A and C, except the faulty regime at 10,000 records, which uses twenty;",
     "new": ("Measurement coverage. Five seeds per cell for Scenarios A and C, except the faulty regime at 10,000 "
             "records, which uses fifty seeds at each of three injection probabilities (Section V-C), with Scenario "
             "O measured in the same cells; three seeds for Scenario B; the B6 occurrence rate is measured over 50 "
             "repetitions per seed.")},
    {"kind": "cell", "table_header0": "Scenario", "row": "A / C", "old": "faulty (p = 0.30)",
     "new": "faulty (p = 0.30; at 10K also 0.05 and 0.10)"},
    {"kind": "para_sub",
     "old_prefix": "Unless stated otherwise, each cell reports the median over five seeds",
     "find": "over twenty seeds for the faulty regime at 10,000 records,",
     "new": "over fifty seeds for the faulty regime at 10,000 records,"},
    {"kind": "para_sub",
     "old_prefix": "In the faulty regime, each store-write attempt fails independently with probability p = 0.30",
     "find": "Table V reports twenty seeds at 10,000 records, alongside the original five-seed run at 1,000.",
     "new": ("Table V reports the fifty-seed paired campaign of Section VI-C3 at 10,000 records, alongside the "
             "original five-seed run at 1,000.")},
    {"kind": "caption",
     "old_prefix": "Scenario A under injected store-write failure (p = 0.30). The 10,000-record column uses twenty seeds;",
     "new": ("Scenario A under injected store-write failure (p = 0.30). The 10,000-record column is the fifty-seed "
             "paired campaign of Section VI-C3; the 1,000-record column is the original five-seed run and is "
             "reported as an observed count without an interval. § One injected trial at 10,000 records (seed 3) "
             "was excluded by the clock-anomaly guard of Section V-D5, so the denominator is {{p30.A.n}}; its Scenario O "
             "and C outcomes are retained (Table XI). ‖ Over the trials in which no failure was injected: two of "
             "the five at 1,000 records and {{p30.A.n_closed}} of the {{p30.A.n}} at 10,000. Scenario A’s window "
             "reflects the conditions under which it was measured as much as the stack itself, which is the point "
             "Section VI-B makes about the observer.")},
    {"kind": "cell", "table_header0": "", "table_header1": "1K (5 seeds)", "row": "", "old": "10K (20 seeds)", "new": "10K (50 seeds)"},
    {"kind": "cell", "table_header1": "1K (5 seeds)", "row": "Trials in which at least one layer was hit", "old": "14 of 20",
     "new": "{{p30.injected_n}} of 50 ({{p30.A.k}} of {{p30.A.n}} valid) §"},
    {"kind": "cell", "table_header1": "1K (5 seeds)", "row": "Trials leaking past the 30 s limit",
     "old": "14 of 20 = 0.70, Wilson 95% CI [0.48, 0.85] §",
     "new": "{{p30.A.k}} of {{p30.A.n}} = {{p30.A.rate}}, Wilson 95% CI [{{p30.A.lo}}, {{p30.A.hi}}]"},
    {"kind": "cell", "table_header1": "1K (5 seeds)", "row": "Of the trials that were hit, how many leaked without bound",
     "old": "14 of 14", "new": "{{p30.A.k}} of {{p30.A.k}}"},
    {"kind": "cell", "table_header1": "1K (5 seeds)", "row": "Median window of the trials that closed ‖",
     "old": "261 ms", "new": "{{p30.A.win_ms}} ms"},
    {"kind": "para_sub",
     "old_prefix": "Every trial in which a layer was hit leaked until the polling limit",
     "find": ("14 of 14 at 10,000 records, and 3 of 3 at 1,000. Over all twenty evaluated trials at 10,000 records "
              "the observed proportion leaking without bound is 0.70, with a Wilson 95% interval of [0.48, 0.85]."),
     "new": ("{{p30.A.k}} of {{p30.A.k}} at 10,000 records, and 3 of 3 at 1,000. Over all {{p30.A.n}} evaluated "
             "trials at 10,000 records the observed proportion leaking without bound is {{p30.A.rate}}, with a "
             "Wilson 95% interval of [{{p30.A.lo}}, {{p30.A.hi}}].")},
    {"kind": "para_sub",
     "old_prefix": "Scenarios A and C, healthy and faulty regimes. Two quantities are reported for Scenario A",
     "find": "§ The healthy columns are the five-seed runs; the faulty columns at 10,000 records use twenty seeds, with Wilson 95% intervals.",
     "new": ("§ The healthy columns are the five-seed runs; the faulty columns at 10,000 records are from the "
             "fifty-seed paired campaign of Section VI-C3 (Scenario C with the ledger), with Wilson 95% intervals, "
             "and at 1,000 records from the original five-seed run.")},
    {"kind": "cell", "table_header1": "A healthy", "row": "Leak Window, system bound (10K) †", "col": "C faulty",
     "old": "2.62 s", "new": "{{p30.Cled.win_2}} s"},
    {"kind": "cell", "table_header1": "A healthy", "row": "Drift window observed at 10 ms polling ‡", "col": "A faulty",
     # v64: the row pairs 1K / 10K in every other cell; A faulty's 1K value is Table V's 79 ms
     "old": "0.261 s", "new": "0.079 s / {{p30.A.win}} s"},
    {"kind": "cell", "table_header1": "A healthy", "row": "Drift window observed at 10 ms polling ‡", "col": "C faulty",
     "old": "2.62 s", "new": "2.62 s / {{p30.Cled.win_2}} s"},
    {"kind": "cell", "table_header1": "A healthy", "row": "Unbounded leaks at 10K", "col": "A faulty",
     "old": "14 of 20 [0.48, 0.85]", "new": "{{p30.A.k}} of {{p30.A.n}} [{{p30.A.lo}}, {{p30.A.hi}}]"},
    {"kind": "cell", "table_header1": "A healthy", "row": "Unbounded leaks at 10K", "col": "C faulty",
     "old": "1 of 20 [0.01, 0.24]", "new": "{{p30.Cled.k}} of {{p30.Cled.n}} [{{p30.Cled.lo}}, {{p30.Cled.hi}}]"},
    {"kind": "cell", "table_header1": "A healthy", "row": "False containment", "col": "C faulty",
     "old": "0 of 20", "new": "{{p30.Cled.sr_fc}} of {{p30.Cled.n}}"},
    {"kind": "para_sub",
     "old_prefix": "Three Scenario C windows in the region of 2.6–2.8 s appear in this paper",
     "find": "Three Scenario C windows in the region of 2.6–2.8 s appear in this paper and should not be read as one number: the 2.67 s value is the healthy regime at 1,000 records, reported in Table VI; 2.62 s is the faulty regime at 10,000 records, reported in Table VII;",
     "new": ("Three Scenario C windows in the region of 2.6–2.9 s appear in this paper and should not be read as one "
             "number: the 2.67 s value is the healthy regime at 1,000 records, reported in Table VI; "
             "{{p30.Cled.win_2}} s is the faulty regime at 10,000 records, reported in Table VII;")},
    {"kind": "para",
     "old_prefix": "Under the same injected failure rate, and using the definition of recovery in Table II",
     "new": ("Under the same injected failure rate, and using the definition of recovery in Table II — all three "
             "store writes succeeding within the three-attempt budget — Scenario C recovered in {{p30.Cled.k_closed}} "
             "of the {{p30.Cled.n}} evaluated trials at 10,000 records, an observed proportion of "
             "{{p30.Cled.closed_rate}} with a Wilson 95% interval of [{{p30.Cled.closed_lo}}, {{p30.Cled.closed_hi}}], "
             "and in four of five at 1,000. In the {{p30.Cled.k}} trials where it did not, all three retries for "
             "one layer were consumed by injected failures. Under the injection model, and assuming per-attempt "
             "independence, a given layer exhausts its budget with probability p³ = 0.027, and at least one of the "
             "three layers does so with probability 1 − (1 − p³)³ = 0.079 at p = 0.30. That is a property of the "
             "model, not an estimate from the trials; the observed proportion, {{p30.Cled.k}} of {{p30.Cled.n}} or "
             "{{p30.Cled.rate}} with a Wilson 95% interval of [{{p30.Cled.lo}}, {{p30.Cled.hi}}], is consistent with "
             "it. In each of those trials the bridge responded to the caller with HTTP 207 rather than 200, while "
             "the ledger’s containment query returned not contained and named the layer at fault. The leaks were "
             "as real as Scenario A’s, and they lasted as long; the difference is that they were reported.")},
    {"kind": "para_sub",
     "old_prefix": "In those terms, this is what the ledger’s extra",
     "find": "the ledger’s extra 4.8 s was observed to buy",
     "new": "the ledger’s extra {{ledger_extra_s}} s was observed to buy"},

    # ------------------------------------------------------------ Introduction
    {"kind": "para_sub",
     "old_prefix": "• A ledger-anchored bridge, built on the Fabric topology",
     "find": "recovered in 19 of 20 trials and, in the twentieth, reported the failure and named the layer.",
     "new": ("recovered in {{p30.Cr.k_closed}} of {{p30.Cr.n}} trials and, in the remaining {{p30.Cr.k}}, reported "
             "the failure and named the layer.")},
    {"kind": "para_sub",
     "old_prefix": "• An ablation that separates what each mechanism",
     "find": ("Running the bridge with no record at all, with an append-only database table in place of the ledger, "
              "and with the ledger, under one injected-failure schedule, shows that the bounded retry does nearly all "
              "of the containment work, that a plain table makes the outcome as answerable as the ledger does at a "
              "fraction of the cost, and that what the ledger adds is an audit record which survived the deletion "
              "attempt we made against both backends as the system’s own administrator."),
     "new": ("Running the uncoordinated stack, a transactional outbox, and the bridge with no record at all, with an "
             "append-only database table in place of the ledger, and with the ledger — all under one injected-failure "
             "schedule in which the same attempt fails in every configuration or in none, so that the configurations "
             "differ in revoke discipline alone — shows that retry does nearly all of the containment work whether "
             "bounded or not, that a plain table makes the outcome as answerable as the ledger does at a fraction of "
             "the cost, and that what the ledger adds is an audit record which survived the deletion attempt we made "
             "against both backends as the system’s own administrator.")},

    # ------------------------------------------------------- II-B tree-rule (D6a)
    {"kind": "para",
     "old": ("Access control at the network perimeter has met a version of the same tension one layer down. The "
             "structure in which a policy is expressed determines both whether its decisions can be reasoned about and "
             "how fast they can be evaluated, and a hybrid tree-rule organisation was proposed precisely to keep an "
             "anomaly-free, inspectable rule structure while still sustaining high-speed transmission [26]. That trade — "
             "paying something in the data path to keep a policy’s behaviour accountable — recurs in this work in a "
             "different form: the design in Section IV accepts additional revocation latency in exchange for a record "
             "of each decision that can be queried afterwards. The question here is not which packets are admitted, "
             "but whether a withdrawn authorisation has actually taken effect in every store that holds the record."),
     "new": ("Access control at the network perimeter has met a version of the same tension one layer down. A "
             "listed-rule firewall can carry rules that are shadowed by or redundant with earlier ones, so that the "
             "policy as written and the policy as enforced quietly diverge without anything in the system reporting "
             "it; the tree-rule firewall was designed to remove those anomalies by construction, so that a policy’s "
             "effective behaviour can be read off its structure rather than assumed [26]. That trade — paying "
             "something in how a policy is expressed to keep its behaviour accountable — recurs in this work in a "
             "different form: the design in Section IV accepts additional revocation latency in exchange for a record "
             "of each decision that can be queried afterwards. The question here is not which packets are admitted, "
             "but whether a withdrawn authorisation has actually taken effect in every store that holds the record.")},

    # ------------------------------------------------------ II-D remove [45] (D6c)
    {"kind": "para",
     "old": ("It is worth separating the two halves of the problem. Establishing that a principal is who they claim to "
             "be is an authentication question, and strengthening it — for instance by making the credential itself "
             "change between sessions, as in dynamic password schemes for web applications [45] — does nothing about "
             "a principal who was legitimately authenticated and whose authorisation has since been withdrawn. This "
             "paper assumes authentication is solved and measures what happens afterwards."),
     "new": ("It is worth separating the two halves of the problem. Establishing that a principal is who they claim to "
             "be is an authentication question, and strengthening it — for instance by making the credential itself "
             "change between sessions — does nothing about a principal who was legitimately authenticated and whose "
             "authorisation has since been withdrawn. This paper assumes authentication is solved and measures what "
             "happens afterwards.")},

    # ------------------------------------------------------- II-E lottery (D6b)
    {"kind": "para_sub",
     "old_prefix": "Using a permissioned ledger to make security-relevant events",
     "find": ("The pattern recurs wherever several parties must agree on the history of an asset without a single "
              "trusted intermediary: a survey of NFT and blockchain techniques for agricultural product trading "
              "examines exactly that use of a ledger — as a checkable record of provenance rather than as an "
              "enforcement mechanism — and locates its value in the same place [50]."),
     "new": ("The pattern recurs wherever several parties must agree on the history of an asset without a single "
             "trusted intermediary — agricultural product trading, where a survey of NFT and blockchain techniques "
             "locates the ledger’s value in a checkable record of provenance rather than in enforcement [50], or a "
             "public lottery whose draw records must be verifiable by the public rather than trusted to the operator "
             "[LOTTERY] — and the property those settings pay for is the one Section VI-C4 measures here: a record "
             "that survives the people who run the system.")},

    # ---------------------------------------------------------------- V-C
    {"kind": "para",
     "old": ("Three scenarios are compared. Scenario A is an uncoordinated polyglot stack: the revoke is issued to the "
             "three stores directly, in sequence, with no retry and no shared record of the outcome. Scenario B is a "
             "converged single engine, PostgreSQL with pgvector, JSONB and row-level security, read through six "
             "different paths (B1–B6) that a real application would plausibly use. Scenario C routes the revoke "
             "through the proposed bridge."),
     "new": ("Four scenarios are compared. Scenario A is an uncoordinated polyglot stack: the revoke is issued to the "
             "three stores directly, in sequence, with no retry and no shared record of the outcome. Scenario B is a "
             "converged single engine, PostgreSQL with pgvector, JSONB and row-level security, read through six "
             "different paths (B1–B6) that a real application would plausibly use. Scenario C routes the revoke "
             "through the proposed bridge. Scenario O is a transactional outbox, the durable design a practitioner "
             "without a bridge would build: the relational write and one outbox row per remote layer are committed in "
             "a single PostgreSQL transaction, and a relay drains the outbox to the document and vector stores with "
             "unbounded retry — 50 ms doubling backoff, capped at 1 s, no attempt limit — polling every 20 ms. Its "
             "stores, schema, store operations and ground-truth poller are those of Scenarios A and C. Its own view of "
             "a revoke’s state is the outbox itself: a revoke is reported contained when no row for it is pending. "
             "That report is read from the same database that holds the data, by the same role that can edit both, "
             "which is the property it does not share with the ledger and the reason it is in the comparison.")},
    {"kind": "bullet",
     "old_lead": "Faulty. ",
     "old": ("Each individual store-write attempt fails with probability p, drawn from a seeded generator so that a "
             "run is reproducible. Scenario A makes one attempt per store and does not retry, as an application "
             "without retry logic behaves; Scenario C retries up to three times with exponential backoff under the "
             "identical injection probability. The environment is thus the same for both and only the revoke "
             "discipline differs. Unless stated otherwise p = 0.30."),
     "new": ("Each individual store-write attempt fails with probability p. Whether the k-th attempt on a given layer "
             "fails is a pure function of the seed, the layer and k, computed identically in the Python harness and "
             "in the bridge, so that the same attempt fails in every scenario or in none. Scenario A makes one "
             "attempt per store and does not retry, as an application without retry logic behaves; that attempt is "
             "attempt 0. Scenario O’s relay and Scenario C’s bridge make the same attempt 0 and, if it fails, go on "
             "to attempts 1, 2, … — the bridge stopping after three, the relay not stopping. A seed in which "
             "Scenario A loses its write to one store is therefore a seed in which every other configuration’s first "
             "write to that store also fails, and only its retry discipline decides what happens next. The failure "
             "schedule is thus paired across configurations trial by trial, not merely matched in rate, and "
             "Section VI-C3 analyses the per-seed outcomes as matched pairs. The campaign is run at p = 0.05, 0.10 "
             "and 0.30; the ladder of Section VI-C3 is reported at 0.30 and the proportions at all three.")},

    # ---------------------------------------------------------------- V-G
    {"kind": "para_sub",
     "old_prefix": "The number of repetitions differs by question, deliberately.",
     "find": "Twenty seeds are used for the faulty regime at 10,000 records,",
     "new": "Fifty seeds are used for the faulty regime at 10,000 records, at each of three injection probabilities,"},
    {"kind": "para_sub",
     "old_prefix": "Two things were not randomised, and both are stated rather than defended.",
     "find": ("And the fault draws for Scenario A and for the bridge come from separate generators seeded from the "
              "same (scale, seed) pair, so the two scenarios face the same failure probability but not the same "
              "failure positions — they are comparable in rate, not paired trial by trial."),
     "new": ("The fault draws are paired across scenarios by construction (Section V-C): attempt k on a layer fails "
             "for a given seed in every configuration or in none. This replaces the design of an earlier version of "
             "this study, in which Scenario A and the bridge drew from separate generators at the same probability "
             "and were comparable in rate but not trial by trial; the agreement between the two sides’ draws is "
             "verified by a test that runs the bridge’s generator under Node and compares every value against the "
             "harness’s.")},

    # --------------------------------------------------------------- VI-C3
    {"kind": "para",
     "old": ("Scenario C bundles three mechanisms — bounded retry, concurrent propagation and a record of each "
             "decision — and the results above do not by themselves say which of them produces the improvement, nor "
             "whether the record needs to be a ledger at all. The bridge was therefore run four more times under an "
             "identical injected-failure schedule reused across the configurations (same seeds, same stores): once "
             "with concurrent propagation but the retry disabled, once with the retry restored and no record of any "
             "kind, once recording to an append-only PostgreSQL table in place of the ledger, and once with the "
             "ledger. Each run adds exactly one mechanism to the one before it, so the effect of each can be read off "
             "separately. Figure 6 and Table VII give the five-way comparison."),
     "new": ("Scenario C bundles three mechanisms — bounded retry, concurrent propagation and a record of each "
             "decision — and the results above do not by themselves say which of them produces the improvement, nor "
             "whether the record needs to be a ledger at all, nor how the bridge compares with the durable design a "
             "practitioner would otherwise build. Six configurations were therefore run under one injected-failure "
             "schedule, paired trial by trial across all six (Section V-C), at 10,000 records and fifty seeds: the "
             "uncoordinated stack; the transactional outbox; the bridge with concurrent propagation but the retry "
             "disabled; with the retry restored and no record of any kind; recording to an append-only PostgreSQL "
             "table; and with the ledger. From the third column on, each configuration adds exactly one mechanism: "
             "concurrency to the uncoordinated stack, then the retry, the table and the ledger, each to the "
             "configuration before it. Figure 6 and Table VII give the comparison at p = 0.30; Table VIII gives the "
             "unbounded-leak proportion at all three p.")},
    {"kind": "caption",
     "old_prefix": "Ablation at 10,000 records under injected failure (p = 0.30, twenty seeds per column).",
     "new": ("Ablation at 10,000 records under injected failure (p = 0.30, fifty seeds per column, paired failure "
             "schedule). From the third column on, each column adds one mechanism — the third to the uncoordinated stack "
             "of the first column, the rest to the column on its left. Intervals "
             "are Wilson 95%; differences between columns are given in the text as Newcombe 95% intervals and as "
             "matched-pair counts. Because the schedule is paired, the trials with an injected failure are the same "
             "seeds in every column — {{p30.injected_n}} seeds, with {{p30.injected_layers}} — verified seed by seed. Revoke "
             "latency is caller-observed for every column. † Scenario A’s window is over the {{p30.A.n_closed}} "
             "trials in which no attempt failed; the other {{p30.A.k}} did not close. Table XI lists every trial.")},
    {"kind": "caption",
     "old_prefix": "FIGURE 6. Ablation.",
     "new": ("FIGURE 6. Ablation at p = 0.30. The outbox and the bridge face the same injected failures as the "
             "uncoordinated stack, seed for seed. Concurrent propagation shortens the window and leaves containment "
             "where it found it; bounded retry closes the leak in {{p30.Cr.k_closed}} of {{p30.Cr.n}} trials and the "
             "outbox’s unbounded retry in {{p30.O.k_closed}} of {{p30.O.n}}; a record of any kind makes the outcome "
             "answerable; and the ledger alone makes that record resistant to the people who operate the database.")},
    {"kind": "bullet",
     "old_lead": "Concurrency shortens the window and does nothing for containment. ",
     "old_prefix": "Writing the three stores in parallel rather than in sequence",
     "new": ("Writing the three stores in parallel rather than in sequence, with the retry still disabled, cut the "
             "median window of the trials that closed from {{p30.A.win}} s to {{p30.Cnr.win}} s and the revoke "
             "latency from {{p30.A.lat}} s to {{p30.Cnr.lat}} s. Containment did not follow: {{p30.Cnr.k}} of "
             "{{p30.Cnr.n}} trials still leaked without bound, {{p30.Cnr.rate}} [{{p30.Cnr.lo}}, {{p30.Cnr.hi}}] "
             "against the baseline’s {{p30.A.rate}} [{{p30.A.lo}}, {{p30.A.hi}}]; the Newcombe 95% interval for the "
             "difference is {{p30.diff.Cnr}}. Because the two face the same failures, the comparison can be made "
             "seed by seed: {{IF p30.pair.Cnr.discordant == 0}}the two configurations agreed on every one of the "
             "{{p30.pair.Cnr.n_pairs}} shared seeds — the same {{p30.pair.Cnr.both}} leaked and the same "
             "{{p30.pair.Cnr.neither}} closed — so there is no discordant pair for a test to act on.{{ELSE}}the two "
             "configurations disagreed on {{p30.pair.Cnr.discordant}} seeds — {{p30.pair.Cnr.refonly}} on which only "
             "A leaked and {{p30.pair.Cnr.cmponly}} on which only the concurrent bridge did — an exact McNemar "
             "p {{p30.pair.Cnr.p}}.{{ENDIF}} Concurrency changes how long a revoke takes to land, not whether it "
             "lands.{{IF p30.pair.Cnr.cmponly > 0}} The "
             "{{p30.pair.Cnr.cmponly}} seeds on which the concurrent bridge leaked and the sequential stack did not "
             "are seeds in which the same attempt failed in both; they differ in which layer the poller saw open "
             "last, and are listed in Table XI.{{ENDIF}}")},
    {"kind": "bullet",
     "old_lead": "Retry does all of the containment work, and nothing else is needed for it. ",
     "new_lead": "Retry does the containment work, and the bound on it is what the outbox removes. ",
     "old_prefix": "Adding the three-attempt budget to the same concurrent propagation",
     "new": ("Adding the three-attempt budget to the same concurrent propagation, still with no record of any kind, "
             "took the unbounded-leak proportion from {{p30.Cnr.rate}} [{{p30.Cnr.lo}}, {{p30.Cnr.hi}}] to "
             "{{p30.Cr.rate}} [{{p30.Cr.lo}}, {{p30.Cr.hi}}] — a difference of {{p30.rung.Cnr-Cr.pdiff}} with a "
             "Newcombe 95% interval of {{p30.diff.rung.Cnr-Cr}}, which does not contain zero, and "
             "{{p30.rung.Cnr-Cr.refonly}} seeds recovered against {{p30.rung.Cnr-Cr.cmponly}} lost (McNemar p "
             "{{p30.rung.Cnr-Cr.p}}). The bridge recovered in {{p30.Cr.k_closed}} of {{p30.Cr.n}} trials, every "
             "store write succeeding within the budget, and the independent poller confirmed containment in the same "
             "{{p30.Cr.k_closed}}. The window is unchanged at {{p30.Cr.win}} s and the latency rises only to "
             "{{p30.Cr.lat}} s.{{IF p30.O.k == 0}} The transactional outbox, whose relay retries without limit, "
             "contained every one of its {{p30.O.n}} trials, including the {{p30.Cr.k}} that exhausted the bridge’s "
             "three attempts: on those seeds the relay’s fourth or later attempt landed, and the layer closed "
             "{{p30.O.win_leaked_seeds}} s after the revoke. Its median window over all trials is {{p30.O.win}} s, "
             "governed by the relay cadence and the backoff its dropped attempts cost, and its latency, "
             "{{p30.O.lat}} s, is the cost of one local transaction. A deployment whose only requirement is that "
             "revokes must not be lost should build the outbox; it is cheaper than the bridge and its retry has no "
             "residue.{{ELSE}} The transactional outbox, whose relay retries without limit, leaked without bound in "
             "{{p30.O.k}} of {{p30.O.n}} trials, {{p30.O.rate}} [{{p30.O.lo}}, {{p30.O.hi}}]. [[INSPECT THESE SEEDS "
             "BEFORE ACCEPTING THIS SENTENCE: see revision_v58_template.md §5]]{{ENDIF}}")},
    {"kind": "bullet",
     "old_lead": "An append-only table makes the outcome answerable, for 0.09 s. ",
     "new_lead": "An append-only table makes the outcome answerable, for {{log_extra_s}} s. ",
     "old_prefix": "Recording the same events to a PostgreSQL table with INSERT-only privileges",
     "new": ("Recording the same events to a PostgreSQL table with INSERT-only privileges gives the same containment "
             "verdict as the ledger in all {{p30.Clog.n}} trials — {{p30.Clog.k_closed}} confirmed contained, and the "
             "remaining {{p30.Clog.k}} correctly reported as not contained by both backends, naming the layer in "
             "{{p30.Clog.sr_att}} of {{p30.Clog.sr_attable}} — and raises latency from {{p30.Cr.lat}} s to "
             "{{p30.Clog.lat}} s. The outbox answers the same question from its own table, and in the trials "
             "evaluated its answer was also correct ({{p30.O.sr_fc}} false containments); what it cannot do is answer "
             "it from outside the database that holds the data.")},
    {"kind": "bullet_sub",
     "old_lead": "The ledger buys one property the table cannot provide, for a further 4.8 s of caller-observed revoke latency — and Section VI-C4 measures it. ",
     "new_lead": "The ledger buys one property the table cannot provide, for a further {{ledger_extra_s}} s of caller-observed revoke latency — and Section VI-C4 measures it. ",
     "find": "raising the latency from 0.212 s to 4.987 s pays for.",
     "new": "raising the latency from {{p30.Clog.lat}} s to {{p30.Cled.lat}} s pays for."},
    {"kind": "para_sub",
     "old_prefix": "This is a more honest account of the design",
     "find": "This is a more honest account of the design than the one the healthy and faulty results alone would support.",
     "new": ("This is a more honest account of the design than the one the healthy and faulty results alone would "
             "support, and the outbox sharpens it.")},
    {"kind": "para",
     "old": ("One detail confirms that the runs are comparable rather than merely similar. The fault seed is derived "
             "from the scale and the seed, so Scenario A received identical injected failures in all three runs and "
             "behaved identically in each (the same fourteen seeds were hit, and the same layers in each), and the "
             "bridge received identical per-attempt injections in all three of its columns. Scenario A and Scenario C "
             "draw their injections from separate generators at the same p, so A and C are not hit on the same "
             "trials; what the columns share is the failure rate, not the failure positions. Within the three "
             "Scenario C columns the only thing that differs is the record."),
     "new": ("One check confirms that the six columns are comparable rather than merely similar. Under the paired "
             "schedule the trials with an injected first attempt are the same {{p30.injected_n}} seeds in every column, and "
             "the analysis script verifies, before producing the table, that every seed shows the same injected "
             "attempts in every file it reads and refuses if it does not. Within the six columns the only thing that "
             "differs is the revoke discipline.")},

    # ---------------------------------------------------------------- VII-A
    {"kind": "para_sub",
     "old_prefix": "Section VI-C3 turns that into a ladder with a price on each rung",
     "find": "Concurrent propagation shortens the window — to 0.101 s from the sequential baseline’s 0.261 s —",
     "new": "Concurrent propagation shortens the window — to {{p30.Cnr.win}} s from the sequential baseline’s {{p30.A.win}} s —"},
    {"kind": "para_sub",
     "old_prefix": "Section VI-C3 turns that into a ladder with a price on each rung",
     "find": "A bounded retry then stops revokes being lost in 19 of 20 evaluated trials,",
     "new": "A bounded retry then stops revokes being lost in {{p30.Cr.k_closed}} of {{p30.Cr.n}} evaluated trials,"},
    {"kind": "para_sub",
     "old_prefix": "Section VI-C3 turns that into a ladder with a price on each rung",
     "find": "for a further 4.8 s of revoke latency.",
     "new": "for a further {{ledger_extra_s}} s of revoke latency."},
    {"kind": "para_sub",
     "old_prefix": "Section VI-C3 turns that into a ladder with a price on each rung",
     "find": "Most deployments should stop at the table: it closes the leak, it reports the residue, and it costs almost nothing.",
     "new": ("{{IF p30.O.k == 0}}Most deployments should build the outbox and keep its table: it closes the leak "
             "without residue, it reports every outcome, and it costs one local transaction.{{ELSE}}Most deployments "
             "should stop at the table: it closes the leak, it reports the residue, and it costs almost nothing.{{ENDIF}}")},
    {"kind": "delete",
     "old_prefix": "One limit on how the ablation should be read follows from Section V-G."},
    {"kind": "para",
     "old_prefix": "Two designs this paper does not compare against should be named",
     "new": ("Two durable designs a practitioner will think of are the transactional outbox and a change-data-capture "
             "pipeline. The outbox is measured here, as Scenario O, and the comparison is in Table VII: it gives what "
             "a bounded in-request retry does not — a revoke that survives a process restart and a retry with no "
             "residue — at a cost below the bridge’s, and its own table answers the same question the ledger does. "
             "What it does not give is an answer that survives the people who operate the database, and that is the "
             "only property for which the ledger’s price is paid. A change-data-capture pipeline, which derives the "
             "propagation from the store’s replication stream, was not measured; it would need the same Leak Window "
             "measurement applied to it before it could be placed on the ladder, and its latency would be that of the "
             "capture tool as much as of the pattern.")},

    # ---------------------------------------------------------------- VIII-C / VIII-D
    {"kind": "bullet",
     "old_lead": "The host stalled twice during the campaign, for about 78 and 127 seconds. ",
     "new_lead": "{{host_stall_lead}}",
     "old_prefix": "Virtualised hosts do this.",
     "new": ("Virtualised hosts do this. {{host_stall_each}} reported a window far longer than its own polling limit, "
             "which is physically impossible for the poller and is therefore flagged automatically as a clock anomaly "
             "and excluded rather than reported as a very long leak. {{host_stall_tail}} The guard remains in place "
             "for any future run.")},
    {"kind": "bullet",
     "old_lead": "Three retries do not eliminate unbounded leakage. ",
     "old_prefix": "Under the injection model used here, all three attempts for a layer fail",
     "new": ("Under the injection model used here, all three attempts for a layer fail with probability p³ per layer — "
             "about 8% per revoke at p = 0.30, 0.3% at 0.10 — and this occurred in {{p30.Cr.k}} of {{p30.Cr.n}} "
             "trials at 0.30 and {{p10.Cr.k}} of {{p10.Cr.n}} at 0.10. The outbox’s unbounded retry removed it in "
             "the trials evaluated; a deployment that requires that guarantee should take the outbox’s retry, with "
             "or without the bridge’s record.")},
    {"kind": "para_sub",
     "old_prefix": "The design deliberately uses different numbers of repetitions",
     "find": ("uses twenty seeds per configuration, enough for the observed proportions to be stated as rates: 0.70 "
              "[0.48, 0.85] of Scenario A trials leaked without bound against 0.05 [0.01, 0.24] for every Scenario C "
              "configuration. The two Wilson intervals do not overlap, which we report descriptively; no formal "
              "hypothesis test was performed, and nothing here should be read as a significance claim or generalised "
              "beyond the tested configuration."),
     "new": ("uses fifty seeds per configuration at each of three injection probabilities, enough for the observed "
             "proportions to be stated as rates: {{p30.A.rate}} [{{p30.A.lo}}, {{p30.A.hi}}] of Scenario A trials "
             "leaked without bound at p = 0.30 against {{p30.Cr.rate}} [{{p30.Cr.lo}}, {{p30.Cr.hi}}] for the bridge "
             "with retry. Because the failure schedule is paired, the comparison between configurations is also "
             "reported as matched pairs with an exact McNemar test on the discordant seeds (Table VIII). The test is "
             "on the tested configurations at the tested probabilities; nothing here should be generalised beyond them.")},

    # ---------------------------------------------------------------- IX-A
    {"kind": "para",
     "old": "A. Per-seed results for the twenty-seed grid",
     "new": "A. Per-seed results for the fifty-seed grid"},
    {"kind": "para",
     "old_prefix": "Because the central comparison rests on proportions from twenty trials",
     "new": ("Because the central comparison rests on proportions from fifty trials per configuration at each "
             "probability, Table XI gives the outcome of every one of them at p = 0.30 rather than only the summary. "
             "It also shows what the summary cannot: that the seeds the bridge failed to contain are the same in each "
             "of its three columns with the retry, that the outbox closed them, and that Scenario A leaked without bound in exactly "
             "the trials where a failure was injected.")},
    {"kind": "caption",
     "old_prefix": "Every trial of the twenty-seed faulty grid at 10,000 records (p = 0.30).",
     "new": ("Every trial of the fifty-seed faulty grid at 10,000 records (p = 0.30, paired schedule). Values are the "
             "drift window in seconds; “unbounded” means at least one layer was still readable at the 30 s limit. "
             "{{excluded_seeds_caption}}")},
    {"kind": "para",
     "old_prefix": "The accounting for Scenario A is therefore as follows",
     "new": ("The accounting for Scenario A is as follows. Fifty trials were started at each probability; "
             "{{p30.injected_n}}, {{p10.injected_n}} and {{p05.injected_n}} received an injected failure at p = 0.30, 0.10 "
             "and 0.05 respectively, and every valid trial with an injected failure leaked until the observation "
             "limit ({{p30.A.k}} of {{p30.A.k}} at p = 0.30, with one injected trial excluded by the guard of "
             "Section V-D5). The "
             "seeds on which the bridge exhausted its budget at p = 0.30 are {{p30.Cr.leaked_seeds}}; on each of "
             "them the outbox’s relay closed the layer on a later attempt.")},
    {"kind": "delete",
     "old_prefix": "Seed 10 is also the trial in which all three retries for one layer were consumed"},

    # ---------------------------------------------------------------- Conclusion
    {"kind": "para_sub",
     "old_prefix": "Consolidating the stores does not dispose of the problem",
     "find": "3 of 3 at 1,000 records and 14 of 14 at 10,000",
     "new": "3 of 3 at 1,000 records and {{p30.A.k}} of {{p30.A.k}} at 10,000"},
    {"kind": "para_sub",
     "old_prefix": "The proposed bridge is about 25 times slower on the healthy path.",
     "find": "in 19 of the 20 evaluated trials and reported the twentieth, naming the layer at fault;",
     "new": "in {{p30.Cr.k_closed}} of the {{p30.Cr.n}} evaluated trials and reported the remaining {{p30.Cr.k}}, naming the layer at fault;"},
    {"kind": "para_sub",
     "old_prefix": "The proposed bridge is about 25 times slower on the healthy path.",
     "find": "retry alone closes the leak in 19 of 20 trials, an append-only table makes the residue answerable for a tenth of a second more, and the principal additional property the ledger contributed, for some 4.8 s more,",
     "new": ("bounded retry closes the leak in {{p30.Cr.k_closed}} of {{p30.Cr.n}} trials and an outbox’s unbounded "
             "retry in {{p30.O.k_closed}} of {{p30.O.n}}, an append-only table makes the residue answerable for a "
             "tenth of a second more, and the principal additional property the ledger contributed, for some "
             "{{ledger_extra_s}} s more,")},
    # ------------------------------------------------- Round 7 (reviewer read of v58)
    # F1: v57 cited the deleted reference [45] (Pansa & Chomsiri) in the
    # out-of-scope paragraph; renumbering alone would have pointed that
    # citation at the next entry. The citation goes with the reference.
    {"kind": "para_sub",
     "old_prefix": "Out of scope: authentication itself",
     "find": "whatever mechanism establishes that [45] —",
     "new": "whatever mechanism establishes that —"},
    # F2: the IEEE v57 lost the five guard bullets of Section V-D5 (the
    # paragraph ends in a colon and the list is missing; the non-IEEE v57
    # has it). Restored from the authors' own text, with the stall sentence
    # generalised because a second campaign has stalled since.
    {"kind": "insert_bullets_after",
     "after_prefix": "A measurement harness fails in ways that produce plausible numbers rather than errors",
     "items": [
         "A record that was not retrievable before the revoke marks the trial invalid, rather than contributing a "
         "zero-length window.",
         "A check that raises is counted as an error, never as an answer. Treating a failed query as “not "
         "retrievable” would manufacture containment; treating it as “retrievable” would manufacture a leak.",
         "A window longer than the polling limit is flagged as a clock anomaly. The virtual machine has stalled "
         "during the campaigns (Section VIII-C); each affected trial was detected and excluded rather than "
         "reported as a very long leak.",
         "Every store client carries an explicit timeout, so a hung query surfaces as a recorded error instead of "
         "an unbounded wait.",
         "The harness refuses to append to an existing results file, because results from two different builds "
         "merged into one file produce medians belonging to neither.",
     ]},
    # F3: scenario counts written when there were three scenarios
    {"kind": "para_sub",
     "old_prefix": "This section describes the testbed, the workload, the three scenarios",
     "find": "the three scenarios and their operating regimes",
     "new": "the four scenarios and their operating regimes"},
    {"kind": "para_sub",
     "old_prefix": "Two of the three scenarios are additionally exercised under injected failure",
     "find": "Two of the three scenarios are additionally exercised",
     "new": "Three of the four scenarios — A, O and C — are additionally exercised"},
    # F5: "one trial" detected-but-not-contained was the 20-seed count
    {"kind": "para_sub",
     "old_prefix": "Two consequences of these definitions are worth stating",
     "find": "Section VI-C reports one trial in which the bridge correctly detected and reported a failure that it did not contain.",
     "new": ("Section VI-C reports {{IF p30.Cled.k == 1}}one trial{{ELSE}}{{p30.Cled.k_word}} trials{{ENDIF}} at 10,000 "
             "records in which the bridge correctly detected and reported a failure that it did not contain.")},
    # F6: threats-to-validity bullet contradicted the rate claims of V-G and VIII-D
    {"kind": "bullet",
     "old_lead": "The fault-injection parameters are chosen, not derived. ",
     "old_prefix": "A per-attempt failure probability of p = 0.30 and a batch interval of T = 2 s are plausible",
     "new": ("Per-attempt failure probabilities of p = 0.05, 0.10 and 0.30 and a batch interval of T = 2 s are "
             "plausible but not calibrated against measured failure rates in production systems. This is why the "
             "healthy regime is reported alongside the injected one throughout: a reader can see what the same "
             "systems do when nothing goes wrong, so the choice of p cannot flatter the bridge. The proportions in "
             "Tables VII and VIII are rates at the tested p, with intervals; what they are not is an estimate of how "
             "often a production stack would drop a revoke, which depends on a failure rate this study did not "
             "measure.")},
    # F11: Table III has no Scenario O row; say where O was measured
    {"kind": "para_sub",
     "old_prefix": "Measurement coverage. Five seeds per cell for Scenarios A and C",
     "find": "with Scenario O measured in the same cells;",
     "new": "with Scenario O measured in that cell only;"},
    # F12: Table II defined recovery by the bridge's three-attempt budget; the
    # outbox retries without bound and the abstract uses the term for both
    {"kind": "cell", "table_header0": "Outcome", "row": "Recovery",
     "old": ("Every store write succeeded within the three-attempt retry budget, so the revoke reached all three "
             "layers without intervention. A trial can therefore be contained without being recovered only if the "
             "poller observed closure by some other route, which did not occur in this study."),
     "new": ("Every store write succeeded within the configuration’s retry budget — three attempts for the bridge, "
             "unbounded for the outbox — so the revoke reached all three layers without intervention. A trial can "
             "therefore be contained without being recovered only if the poller observed closure by some other "
             "route, which did not occur in this study.")},
    # F13: Figure 4 caption carried a dangling fragment
    {"kind": "caption",
     "old_prefix": "FIGURE 4. Leak Window against the orderer’s BatchTimeout.",
     "new": ("FIGURE 4. Leak Window against the orderer’s BatchTimeout. Points are medians over three seeds and "
             "error bars are bootstrap 95% confidence intervals of the median; the sweep uses three seeds because "
             "it is a parameter-sensitivity experiment rather than part of the main scale grid. The fit is "
             "0.96×T + 0.81 s.")},
    # ------------------------------------------------- v59: say what the contribution is
    # The editor-style reading of v58 expected reviewers to ask whether the
    # paper claims a new mechanism. It does not, and now says so in one
    # sentence at each end.
    {"kind": "para",
     "old": "This paper makes five contributions.",
     "new": ("This paper makes five contributions. Taken together they are a measurement method and the evidence "
             "it produces, not a new coordination algorithm: the bridge is an existing pattern instrumented so "
             "that what each of its parts buys can be measured, and the comparison with a transactional outbox "
             "is reported so that the reader can see which of those parts are worth their cost.")},
    {"kind": "para_sub",
     "old_prefix": "We also measured an asynchronous variant that halves the latency, and rejected it:",
     "find": "can be made on measurements rather than on intuition.",
     "new": ("can be made on measurements rather than on intuition. What the paper adds is therefore the "
             "measurement and the evidence, not a new coordination mechanism: the outbox closed more leaks than "
             "the bridge, and the case for the ledger rests on the one property the tested administrator could "
             "not alter.")},
    # ------------------------------------------------- v60: the Zenodo version DOI of v1.2.0
    # Release v1.2.0 (commit 4f0f69f) archived by Zenodo on 4 October 2026 as
    # record 23139029; the concept DOI 22066253 is unchanged.
    {"kind": "para_sub",
     "old_prefix": "The artefact — source code, container definitions, chaincode,",
     "find": "the release corresponding to this paper is tagged v1.1.0 and has its own identifier, https://doi.org/10.5281/zenodo.22097730.",
     "new": "the release corresponding to this paper is tagged v1.2.0 and has its own identifier, https://doi.org/10.5281/zenodo.23139029."},
    # ------------------------------------------------- v62: the excluded trial, stated where the number is used
    # An editor-style reading of v61 asked what "35 of 49" is of, and over
    # which seeds the pairs are counted. One word in the abstract; the pair
    # denominators go into the Table VIII caption (TABLE_VIII above).
    {"kind": "abstract",
     "find": "trials at 10,000 records, p = 0.30.",
     "new": "valid trials at 10,000 records, p = 0.30."},
    # ------------------------------------------------- v63: "25 times slower" names its quantity; both authors
    # The factor of 25 is the healthy-path drift window (2.67 s against
    # 104 ms, Section VI-C); caller-observed latency is about 35x (Table X).
    # "Slower" alone let a reader take it for the latency.
    {"kind": "abstract",
     "find": "The bridge is roughly 25 times slower on the healthy path.",
     "new": "The bridge’s healthy-path window is roughly 25 times longer."},
    {"kind": "para_sub",
     "old_prefix": "• A ledger-anchored bridge, built on the Fabric topology",
     "find": "We report that the bridge is about 25 times slower than the uncoordinated baseline when nothing fails,",
     "new": "We report that the bridge’s window is about 25 times longer than the uncoordinated baseline’s when nothing fails,"},
    {"kind": "para_sub",
     "old_prefix": "The proposed bridge is about 25 times slower on the healthy path.",
     "find": "The proposed bridge is about 25 times slower on the healthy path.",
     "new": "The proposed bridge’s window is about 25 times longer on the healthy path."},
    # The declaration is made for both authors, who both reviewed the manuscript.
    {"kind": "para",
     "old": ("The author used a large language model (Anthropic Claude) to assist with language editing, the "
             "derivation and rendering of figures, and the drafting of code. All technical content, mathematical "
             "derivations, and experimental design are the author’s own, and the author reviewed and verified all "
             "text in the final manuscript."),
     "new": ("The authors used a large language model (Anthropic Claude) to assist with language editing, the "
             "derivation and rendering of figures, and the drafting of code. All technical content, mathematical "
             "derivations, and experimental design are the authors’ own, and the authors reviewed and verified all "
             "text in the final manuscript.")},
    # ------------------------------------------------- v64: the factor of 25 is a ratio of measured windows
    # Scenario A's 104 ms is the window under 10 ms polling; its system bound
    # is <= 30 ms (Table VI), so the ratio of measured windows flatters the
    # bridge. The three sentences now say "as measured". Table VI's row of
    # 1K / 10K pairs says so in its label. And the Faulty bullet says what
    # "independently with probability p" means for a schedule that is a
    # pure function of (seed, layer, attempt), with the observed count of
    # injected seeds against the model's expectation.
    {"kind": "abstract",
     "find": "The bridge’s healthy-path window is roughly 25 times longer.",
     "new": "The bridge’s healthy-path window is roughly 25 times the stack’s as measured."},
    {"kind": "para_sub",
     "old_prefix": "• A ledger-anchored bridge, built on the Fabric topology",
     "find": "We report that the bridge’s window is about 25 times longer than the uncoordinated baseline’s when nothing fails,",
     "new": ("We report that the bridge’s window is about 25 times longer than the uncoordinated baseline’s as both "
             "are measured when nothing fails, and more against the baseline’s system bound,")},
    {"kind": "para_sub",
     "old_prefix": "The proposed bridge’s window is about 25 times longer on the healthy path.",
     "find": "The proposed bridge’s window is about 25 times longer on the healthy path.",
     "new": ("The proposed bridge’s window is about 25 times longer than the stack’s as measured on the healthy path, "
             "and more against the stack’s system bound of about 30 ms.")},
    {"kind": "cell", "table_header1": "A healthy", "row": "Drift window observed at 10 ms polling ‡",
     "old": "Drift window observed at 10 ms polling ‡", "new": "Drift window observed at 10 ms polling (1K / 10K) ‡"},
    {"kind": "bullet_sub",
     "old_lead": "Faulty. ",
     "find": ("Whether the k-th attempt on a given layer fails is a pure function of the seed, the layer and k, "
              "computed identically in the Python harness and in the bridge, so that the same attempt fails in "
              "every scenario or in none."),
     "new": ("Whether the k-th attempt on a given layer fails is a pure function of the seed, the layer and k: a "
             "uniform value drawn from a generator seeded by that triple, compared against p, and computed "
             "identically in the Python harness and in the bridge, so that the same attempt fails in every "
             "scenario or in none. Attempts and layers are therefore independent draws at probability p, as the "
             "model of Section VI-C assumes; they are fixed per seed rather than redrawn at run time. Across the "
             "fifty seeds at p = 0.30, {{p30.injected_n}} had at least one first-attempt failure, against "
             "{{p30.injected_expected}} expected.")},
    # ------------------------------------------------- v65: the factor of 25 stays only where it is explained
    # The ratio of 2.67 s to 104 ms compares a window the poller inflates
    # (Scenario A) with one it barely touches (the bridge). The abstract,
    # Section I and Section X now give the two measured quantities instead;
    # Section VI-C keeps the factor with its conditions stated.
    {"kind": "abstract",
     "find": "The bridge’s healthy-path window is roughly 25 times the stack’s as measured.",
     "new": "The bridge’s healthy-path window is 2.67 s."},
    {"kind": "para_sub",
     "old_prefix": "• A ledger-anchored bridge, built on the Fabric topology",
     "find": ("We report that the bridge’s window is about 25 times longer than the uncoordinated baseline’s as both "
              "are measured when nothing fails, and more against the baseline’s system bound,"),
     "new": "We report that the bridge’s healthy-path window is 2.67 s against a stack that closes within about 30 ms,"},
    {"kind": "para_sub",
     "old_prefix": "On the healthy path the bridge is slower, and by a wide margin:",
     "find": "a drift window of 2.67 s against Scenario A’s 104 ms at the same scale, a factor of roughly 25.",
     "new": ("a drift window of 2.67 s against Scenario A’s 104 ms as both are measured under 10 ms polling — a factor "
             "of roughly 25 on the measured values, and more against the stack’s system bound of about 30 ms "
             "(Section VI-B), since the poller slows Scenario A and leaves the bridge almost untouched.")},
    {"kind": "para_sub",
     "old_prefix": "The proposed bridge’s window is about 25 times longer than the stack’s as measured",
     "find": ("The proposed bridge’s window is about 25 times longer than the stack’s as measured on the healthy path, "
              "and more against the stack’s system bound of about 30 ms."),
     "new": "The proposed bridge’s healthy-path window is 2.67 s, against a stack whose window is bounded at about 30 ms."},
    # ------------------------------------------------- v66: one count for Scenario B, everywhere
    # Table IV: B1, B4 and B5 leak by a window well above the 60-160 ms
    # resolution; B6 leaks on every call; B3 strict reached 17 ms at 10K,
    # within the resolution the paper states (Section V-D3); B2 and B3
    # relaxed stayed within one polling interval. So four of six read paths
    # serve revoked data measurably -- the count the contribution already
    # gave, now used by the abstract, Section I, Section VI-A and Section X
    # as well (they said "six"), with the garbled B6 sentence rewritten.
    {"kind": "abstract",
     "find": "Consolidation does not help: six read paths through one database still serve revoked data,",
     "new": "Consolidation does not help: four of six read paths through one database still serve revoked data,"},
    {"kind": "para_sub",
     "old_prefix": "One might expect that consolidating the stores would dispose of the problem,",
     "find": "Six ordinary read paths through a single such instance still serve revoked data after the revoke has committed, and one of them does so on every attempt.",
     "new": "Of six ordinary read paths through a single such instance, four still serve revoked data after the revoke has committed, and one of them does so on every attempt."},
    {"kind": "para_sub",
     "old_prefix": "• An empirical study of six leak mechanisms inside a converged engine",
     "find": "Four of the six serve revoked data measurably. The sixth arises from connection pooling rather than from the database itself,",
     "new": ("Four of the six serve revoked data measurably: three by a timed window and one on every call. That one "
             "arises from connection pooling rather than from the database itself,")},
    {"kind": "para_sub",
     "old_prefix": "Scenario B places every record in a single PostgreSQL 18 instance",
     "find": ("Four of those five exhibited windows above the method’s resolution in at least one tested scale — B1, "
              "B3 under strict ordering, B4 and B5 — and the sixth read path, B6, is not among them. B6 is not among "
              "them because it is an occurrence mechanism rather than a timed window, and is reported separately "
              "after the revoke has committed."),
     "new": ("Three of those five exhibited windows well above the method’s resolution — B1, B4 and B5; B3 under "
             "strict ordering reached 17 ms at 10,000 records, at the edge of what the method resolves (Section "
             "V-D3), and B2 and B3 under relaxed ordering stayed within one polling interval. The sixth read path, "
             "B6, has no timed window: it is an occurrence mechanism, measured as the fraction of calls that "
             "returned revoked data after the revoke had committed, and is reported separately. Four of the six "
             "read paths therefore serve revoked data measurably.")},
    {"kind": "para_sub",
     "old_prefix": "Consolidating the stores does not dispose of the problem:",
     "find": "six ordinary read paths through a single PostgreSQL instance still served revoked data,",
     "new": "four of six ordinary read paths through a single PostgreSQL instance still served revoked data,"},
    # the AI-use declaration: "derivation ... of figures" sat beside "mathematical derivations are the authors' own"
    {"kind": "para_sub",
     "old_prefix": "The authors used a large language model (Anthropic Claude)",
     "find": "the derivation and rendering of figures,",
     "new": "the scripts that render the figures,"},
    # one spelling of the paper's own term: the title has "Authorization"
    {"kind": "replace_body", "find": "authorisation", "new": "authorization", "expect": 8},
    # ------------------------------------------------- v67: the two -ise forms v66 left behind
    {"kind": "replace_body", "find": "unauthorised", "new": "unauthorized", "expect": 1},
    {"kind": "replace_body", "find": "authorised", "new": "authorized", "expect": 1},
]

# Reference list changes (D6). Keys are the v57 numbers.
REF_REPLACE = {
    # v61: DOI from the Springer chapter page (978-3-642-34883-9_22); the
    # ordinal "5th" dropped because that page does not state it.
    26: ("[26]  T. Chomsiri, X. He, and P. Nanda, \"Limitation of listed-rule firewall and the design of tree-rule "
         "firewall,\" in Proc. Int. Conf. Internet and Distributed Computing Systems (IDCS 2012), LNCS vol. 7646, "
         "Berlin, Germany: Springer, 2012, pp. 275–287, doi: 10.1007/978-3-642-34883-9_22."),
    # v61: volume, issue, article number and page count from the ACM PDF
    # (Proc. ACM Manag. Data 4(1), Article 11, 26 pages, Feb. 2026).
    21: ("[21]  H. Zhong, M. Lentz, N. Narodytska, A. Szekeres, and K. Rong, \"Honeybee: efficient role-based "
         "access control for vector databases via dynamic partitioning,\" Proc. ACM on Management of Data, vol. 4, "
         "no. 1 (SIGMOD), Art. no. 11, pp. 1–26, Feb. 2026, doi: 10.1145/3786625."),
}
REF_DELETE = [45]
# Inserted after v57's [50]; cited in the text as [LOTTERY] until numbering is resolved.
REF_INSERT_AFTER = {
    50: ("LOTTERY",
         # v61: venue as IEEE Xplore names it and the DOI (document 8692241)
         "P. Saichua, S. Khunthi, and T. Chomsiri, \"Design of blockchain lottery for Thai government,\" in Proc. "
         "2019 Joint Int. Conf. Digital Arts, Media and Technology with ECTI Northern Section Conf. Electrical, "
         "Electronics, Computer and Telecommunications Engineering (ECTI DAMT-NCON), Nan, Thailand, 2019, pp. 9–12, "
         "doi: 10.1109/ECTI-NCON.2019.8692241."),
}

# Table VII: the new O column goes after A. Row order is v57's. Values per row
# for the new column, and replacements for the existing columns.
TABLE_VII = {
    "header": ["", "A", "O", "+ concurrency", "+ retry", "+ log table", "+ ledger"],
    "rows": {
        "Mechanisms": ["none", "outbox, unbounded retry", "concurrency", "concurrency, retry",
                       "concurrency, retry, append-only table", "concurrency, retry, Fabric ledger"],
        "Trials leaking without bound": [
            "{{p30.A.k}} of {{p30.A.n}} [{{p30.A.lo}}, {{p30.A.hi}}]",
            "{{p30.O.k}} of {{p30.O.n}} [{{p30.O.lo}}, {{p30.O.hi}}]",
            "{{p30.Cnr.k}} of {{p30.Cnr.n}} [{{p30.Cnr.lo}}, {{p30.Cnr.hi}}]",
            "{{p30.Cr.k}} of {{p30.Cr.n}} [{{p30.Cr.lo}}, {{p30.Cr.hi}}]",
            "{{p30.Clog.k}} of {{p30.Clog.n}} [{{p30.Clog.lo}}, {{p30.Clog.hi}}]",
            "{{p30.Cled.k}} of {{p30.Cled.n}} [{{p30.Cled.lo}}, {{p30.Cled.hi}}]"],
        "Drift window (median of the trials that closed)": [
            "{{p30.A.win}} s †", "{{p30.O.win}} s", "{{p30.Cnr.win}} s", "{{p30.Cr.win}} s",
            "{{p30.Clog.win}} s", "{{p30.Cled.win}} s"],
        "Revoke latency (median)": [
            "{{p30.A.lat}} s", "{{p30.O.lat}} s", "{{p30.Cnr.lat}} s", "{{p30.Cr.lat}} s",
            "{{p30.Clog.lat}} s", "{{p30.Cled.lat}} s"],
        "Trials the record could confirm containment": [
            "n/a", "{{p30.O.k_closed}} of {{p30.O.n}}", "0 of {{p30.Cnr.n}}", "0 of {{p30.Cr.n}}",
            "{{p30.Clog.k_closed}} of {{p30.Clog.n}}", "{{p30.Cled.k_closed}} of {{p30.Cled.n}}"],
        "Leak detected and attributed": [
            "never", "{{IF p30.O.k == 0}}no leak to attribute{{ELSE}}{{p30.O.sr_att}} of {{p30.O.sr_attable}}{{ENDIF}}", "never", "never",
            "{{p30.Clog.sr_att}} of {{p30.Clog.sr_attable}}", "{{p30.Cled.sr_att}} of {{p30.Cled.sr_attable}}"],
        "Record survives its own administrator": [
            "n/a", "no (same role, same database)", "n/a", "n/a", "no (measured)", "yes (measured)"],
        "Writer identity attested": ["n/a", "no", "n/a", "n/a", "no", "by design; not measured"],
    },
}

# New Table VIII (the p sweep), placed after Table VII in the same section.
TABLE_VIII = {
    "label": "TABLE VIII",
    "caption": ("Trials leaking without bound at three injection probabilities (10,000 records, fifty seeds per "
                "cell, paired schedule). Each cell is k of n with its Wilson 95% interval; “pairs a/b” gives the seeds "
                "on which only Scenario A leaked (a) and on which only that configuration leaked (b), with the exact "
                "McNemar p-value on the discordant pairs; pairs are counted over the seeds valid in both columns "
                "({{p30.pair.O.n_pairs}} for every comparison with Scenario A at p = 0.30, where seed 3 is excluded "
                "from A, and {{p10.pair.O.n_pairs}} at p = 0.10 and {{p05.pair.O.n_pairs}} at p = 0.05). Only "
                "proportions are reported from the sweep: for every "
                "seed the p = 0.05 cell was the first revoke after a bridge restart, so its timings include "
                "connection warm-up and are not comparable with Table VII’s."),
    "header": ["p", "A", "O", "+ concurrency", "+ retry", "+ log table", "+ ledger"],
    "rows": [[tag_p, f"{{{{{t}.A.k}}}} of {{{{{t}.A.n}}}} [{{{{{t}.A.lo}}}}, {{{{{t}.A.hi}}}}]"]
             + [f"{{{{{t}.{c}.k}}}} of {{{{{t}.{c}.n}}}} [{{{{{t}.{c}.lo}}}}, {{{{{t}.{c}.hi}}}}]; pairs "
                f"{{{{{t}.pair.{c}.refonly}}}}/{{{{{t}.pair.{c}.cmponly}}}}, p {{{{{t}.pair.{c}.p}}}}"
                for c in ("O", "Cnr", "Cr", "Clog", "Cled")]
             for t, tag_p in (("p05", "0.05"), ("p10", "0.10"), ("p30", "0.30"))],
}
