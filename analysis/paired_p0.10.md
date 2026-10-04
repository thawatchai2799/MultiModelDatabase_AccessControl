# Ablation table

## Injection probability p = 0.1

| configuration | n | leaking without bound | Wilson 95% | drift window, closed trials (median, 95% CI) | revoke latency, caller-observed (median; bridge-reported total) | self-report: false cont. / false non-cont. / attributed | reference − this (Newcombe 95%) |
|---|---|---|---|---|---|---|---|
| A | 50 | 16 of 50 | [0.21, 0.46] | 0.264 s [0.199, 0.332] | 0.217 s | — | (reference) |
| O | 50 | 0 of 50 | [0.00, 0.07] | 0.448 s [0.373, 0.503] | 0.152 s | 0 / 0 / 0 of 0 | +0.32 [+0.19, +0.46] |
| C no retry | 50 | 16 of 50 | [0.21, 0.46] | 0.161 s [0.128, 0.174] | 0.177 s (0.154 s) | — | +0.00 [-0.18, +0.18] |
| C retry | 50 | 0 of 50 | [0.00, 0.07] | 0.119 s [0.105, 0.147] | 0.173 s (0.140 s) | — | +0.32 [+0.19, +0.46] |
| C log | 50 | 0 of 50 | [0.00, 0.07] | 0.146 s [0.126, 0.172] | 0.233 s (0.202 s) | 0 / 0 / 0 of 0 | +0.32 [+0.19, +0.46] |
| C ledger | 50 | 0 of 50 | [0.00, 0.07] | 2.810 s [2.768, 2.864] | 5.354 s (5.341 s) | 0 / 0 / 0 of 0 | +0.32 [+0.19, +0.46] |

Matched pairs (paired fault schedule, 50 shared seeds), each row against the reference 'A': seeds where only the reference leaked / only this configuration leaked, paired difference (Wald 95%), exact McNemar p.

| configuration | ref only | this only | both | neither | paired Δ | Wald 95% | McNemar p |
|---|---|---|---|---|---|---|---|
| O | 16 | 0 | 0 | 34 | +0.32 | [0.19, 0.45] | 0.0000 |
| C no retry | 0 | 0 | 16 | 34 | +0.00 | [0.00, 0.00] | 1.0000 |
| C retry | 16 | 0 | 0 | 34 | +0.32 | [0.19, 0.45] | 0.0000 |
| C log | 16 | 0 | 0 | 34 | +0.32 | [0.19, 0.45] | 0.0000 |
| C ledger | 16 | 0 | 0 | 34 | +0.32 | [0.19, 0.45] | 0.0000 |

Consecutive rungs of the ladder (each against the previous row):

| previous → this | prev only | this only | paired Δ | McNemar p |
|---|---|---|---|---|
| A → O | 16 | 0 | +0.32 | 0.0000 |
| O → C no retry | 0 | 16 | -0.32 | 0.0000 |
| C no retry → C retry | 16 | 0 | +0.32 | 0.0000 |
| C retry → C log | 0 | 0 | +0.00 | 1.0000 |
| C log → C ledger | 0 | 0 | +0.00 | 1.0000 |

Excluded trials (never in a denominator): 
