# Ablation table

## Injection probability p = 0.3

| configuration | n | leaking without bound | Wilson 95% | drift window, closed trials (median, 95% CI) | revoke latency, caller-observed (median; bridge-reported total) | self-report: false cont. / false non-cont. / attributed | reference − this (Newcombe 95%) |
|---|---|---|---|---|---|---|---|
| A | 49 | 35 of 49 | [0.58, 0.82] | 0.224 s [0.184, 0.274] | 0.173 s | — | (reference) |
| O | 50 | 0 of 50 | [0.00, 0.07] | 0.538 s [0.485, 0.581] | 0.157 s | 0 / 0 / 0 of 0 | +0.71 [+0.56, +0.82] |
| C no retry | 50 | 36 of 50 | [0.58, 0.83] | 0.162 s [0.132, 0.201] | 0.162 s (0.136 s) | — | -0.01 [-0.18, +0.17] |
| C retry | 50 | 6 of 50 | [0.06, 0.24] | 0.124 s [0.102, 0.152] | 0.161 s (0.151 s) | — | +0.59 [+0.41, +0.72] |
| C log | 50 | 6 of 50 | [0.06, 0.24] | 0.164 s [0.137, 0.209] | 0.255 s (0.227 s) | 0 / 0 / 6 of 6 | +0.59 [+0.41, +0.72] |
| C ledger | 50 | 6 of 50 | [0.06, 0.24] | 2.872 s [2.819, 2.968] | 5.321 s (5.300 s) | 0 / 0 / 6 of 6 | +0.59 [+0.41, +0.72] |

Matched pairs (paired fault schedule, 49 shared seeds), each row against the reference 'A': seeds where only the reference leaked / only this configuration leaked, paired difference (Wald 95%), exact McNemar p.

| configuration | ref only | this only | both | neither | paired Δ | Wald 95% | McNemar p |
|---|---|---|---|---|---|---|---|
| O | 35 | 0 | 0 | 14 | +0.71 | [0.59, 0.84] | 0.0000 |
| C no retry | 0 | 0 | 35 | 14 | +0.00 | [0.00, 0.00] | 1.0000 |
| C retry | 29 | 0 | 6 | 14 | +0.59 | [0.45, 0.73] | 0.0000 |
| C log | 29 | 0 | 6 | 14 | +0.59 | [0.45, 0.73] | 0.0000 |
| C ledger | 29 | 0 | 6 | 14 | +0.59 | [0.45, 0.73] | 0.0000 |

Consecutive rungs of the ladder (each against the previous row):

| previous → this | prev only | this only | paired Δ | McNemar p |
|---|---|---|---|---|
| A → O | 35 | 0 | +0.71 | 0.0000 |
| O → C no retry | 0 | 36 | -0.72 | 0.0000 |
| C no retry → C retry | 30 | 0 | +0.60 | 0.0000 |
| C retry → C log | 0 | 0 | +0.00 | 1.0000 |
| C log → C ledger | 0 | 0 | +0.00 | 1.0000 |

Excluded trials (never in a denominator): A: {'poller_error': 1}
