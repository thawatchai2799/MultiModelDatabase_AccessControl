# Ablation table

## Injection probability p = 0.05

| configuration | n | leaking without bound | Wilson 95% | drift window, closed trials (median, 95% CI) | revoke latency, caller-observed (median; bridge-reported total) | self-report: false cont. / false non-cont. / attributed | reference − this (Newcombe 95%) |
|---|---|---|---|---|---|---|---|
| A | 50 | 6 of 50 | [0.06, 0.24] | 0.253 s [0.223, 0.282] | 0.258 s | — | (reference) |
| O | 50 | 0 of 50 | [0.00, 0.07] | 0.525 s [0.470, 0.602] | 0.173 s | 0 / 0 / 0 of 0 | +0.12 [+0.02, +0.24] |
| C no retry | 50 | 6 of 50 | [0.06, 0.24] | 0.376 s [0.329, 0.453] | 0.417 s (0.366 s) | — | +0.00 [-0.13, +0.13] |
| C retry | 50 | 0 of 50 | [0.00, 0.07] | 0.293 s [0.271, 0.322] | 0.345 s (0.304 s) | — | +0.12 [+0.02, +0.24] |
| C log | 50 | 0 of 50 | [0.00, 0.07] | 0.318 s [0.299, 0.358] | 0.483 s (0.451 s) | 0 / 0 / 0 of 0 | +0.12 [+0.02, +0.24] |
| C ledger | 50 | 0 of 50 | [0.00, 0.07] | 5.611 s [5.424, 5.749] | 7.951 s (7.912 s) | 0 / 0 / 0 of 0 | +0.12 [+0.02, +0.24] |

Matched pairs (paired fault schedule, 50 shared seeds), each row against the reference 'A': seeds where only the reference leaked / only this configuration leaked, paired difference (Wald 95%), exact McNemar p.

| configuration | ref only | this only | both | neither | paired Δ | Wald 95% | McNemar p |
|---|---|---|---|---|---|---|---|
| O | 6 | 0 | 0 | 44 | +0.12 | [0.03, 0.21] | 0.0312 |
| C no retry | 0 | 0 | 6 | 44 | +0.00 | [0.00, 0.00] | 1.0000 |
| C retry | 6 | 0 | 0 | 44 | +0.12 | [0.03, 0.21] | 0.0312 |
| C log | 6 | 0 | 0 | 44 | +0.12 | [0.03, 0.21] | 0.0312 |
| C ledger | 6 | 0 | 0 | 44 | +0.12 | [0.03, 0.21] | 0.0312 |

Consecutive rungs of the ladder (each against the previous row):

| previous → this | prev only | this only | paired Δ | McNemar p |
|---|---|---|---|---|
| A → O | 6 | 0 | +0.12 | 0.0312 |
| O → C no retry | 0 | 6 | -0.12 | 0.0312 |
| C no retry → C retry | 6 | 0 | +0.12 | 0.0312 |
| C retry → C log | 0 | 0 | +0.00 | 1.0000 |
| C log → C ledger | 0 | 0 | +0.00 | 1.0000 |

Excluded trials (never in a denominator): 
