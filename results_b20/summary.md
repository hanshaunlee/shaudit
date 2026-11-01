Budget 20 probes per audit. Methods: {"random": {"policy": "random", "visibility": "standard", "budget": 10, "alpha": 1.0, "beta": 0.1, "gamma": 0.5, "n_samples": 16, "contain": true, "tau": 0.05, "other_prior": 0.1, "other_tau": 0.3, "lam": 3.0, "whitebox": false, "seed": 0}, "checklist": {"policy": "checklist", "visibility": "standard", "budget": 10, "alpha": 1.0, "beta": 0.1, "gamma": 0.5, "n_samples": 16, "contain": true, "tau": 0.05, "other_prior": 0.1, "other_tau": 0.3, "lam": 3.0, "whitebox": false, "seed": 0}, "active": {"policy": "active", "visibility": "standard", "budget": 10, "alpha": 2.0, "beta": 0.0, "gamma": 0.5, "n_samples": 16, "contain": true, "tau": 0.05, "other_prior": 0.1, "other_tau": 0.3, "lam": 3.0, "whitebox": false, "seed": 0}, "lookahead": {"policy": "lookahead", "visibility": "standard", "budget": 10, "alpha": 2.0, "beta": 0.0, "gamma": 0.5, "n_samples": 16, "contain": true, "tau": 0.05, "other_prior": 0.1, "other_tau": 0.3, "lam": 3.0, "whitebox": false, "seed": 0}, "oracle(full-obs)": {"policy": "active", "visibility": "full", "budget": 10, "alpha": 2.0, "beta": 0.0, "gamma": 0.5, "n_samples": 16, "contain": true, "tau": 0.05, "other_prior": 0.1, "other_tau": 0.3, "lam": 3.0, "whitebox": false, "seed": 0}}

### dev

| probe policy | n | hit_rate | probes_to_first | coverage | claim_rate | false_claim_rate | insufficient_evidence | closed | legit_completion | cost | replays |
|---|---|---|---|---|---|---|---|---|---|---|---|
| active | 96 | 1.00 | 1.71 | 0.90 | 0.90 | 0.00 | 0.10 | 1.00 | 1.00 | 1.18 | 469.74 |
| checklist | 96 | 1.00 | 4.23 | 0.88 | 0.68 | 0.00 | 0.06 | 0.97 | 1.00 | 1.15 | 459.68 |
| lookahead | 96 | 1.00 | 1.56 | 0.89 | 0.88 | 0.00 | 0.11 | 1.00 | 1.00 | 1.18 | 465 |
| oracle(full-obs) | 96 | 1.00 | 1.65 | 0.91 | 0.97 | 0.00 | 0.01 | 1.00 | 1.00 | 1.18 | 470.17 |
| random | 96 | 0.96 | 3.48 | 0.83 | 0.44 | 0.00 | 0.12 | 0.93 | 1.00 | 1.12 | 444.23 |

| containment | n | closed | violation_rate | legit_completion | cost | latency |
|---|---|---|---|---|---|---|
| CEGC (active) | 96 | 1.00 | 0.00 | 1.00 | 1.18 | 0 |
| blanket | 96 | 1.00 | 0.00 | 0.09 | 11.62 | 0 |
| parent_only | 96 | 0.22 | 0.38 | 0.91 | 0.00 | 7.27 |
| reference_impl | 96 | 1.00 | 0.00 | 1.00 | 0.00 | 0 |
| static_lineage | 96 | 0.28 | 0.17 | 0.91 | 1.07 | 5.94 |

| optimizer (same discovered paths) | n_contained | grouped_cost | grouped_closed | mincut_cost | mincut_blocks_known | mincut_closed | mincut_differs |
|---|---|---|---|---|---|---|---|
| active policy | 75 | 1.51 | 1.00 | 1.63 | 0.99 | 0.84 | 0.37 |

### gen

| probe policy | n | hit_rate | probes_to_first | coverage | claim_rate | false_claim_rate | insufficient_evidence | closed | legit_completion | cost | replays |
|---|---|---|---|---|---|---|---|---|---|---|---|
| active | 32 | 1.00 | 1.28 | 0.78 | 0.81 | 0.00 | 0.19 | 1.00 | 1.00 | 2.09 | 687.53 |
| checklist | 32 | 1.00 | 3.22 | 0.72 | 0.56 | 0.00 | 0.12 | 0.91 | 1.00 | 2.03 | 678.28 |
| lookahead | 32 | 1.00 | 1.22 | 0.81 | 0.81 | 0.00 | 0.19 | 1.00 | 1.00 | 2.09 | 687.28 |
| oracle(full-obs) | 32 | 1.00 | 1.19 | 0.86 | 1.00 | 0.00 | 0.00 | 1.00 | 1.00 | 2.09 | 731.81 |
| random | 32 | 0.97 | 3.19 | 0.62 | 0.31 | 0.00 | 0.34 | 0.72 | 0.98 | 1.94 | 646.69 |

| containment | n | closed | violation_rate | legit_completion | cost | latency |
|---|---|---|---|---|---|---|
| CEGC (active) | 32 | 1.00 | 0.00 | 1.00 | 2.09 | 0 |
| blanket | 32 | 1.00 | 0.00 | 0.11 | 12.00 | 0 |
| parent_only | 32 | 0.00 | 0.61 | 0.76 | 0.00 | 13.16 |
| reference_impl | 32 | 1.00 | 0.00 | 1.00 | 0.00 | 0 |
| static_lineage | 32 | 0.00 | 0.33 | 0.76 | 1.19 | 12.12 |

| optimizer (same discovered paths) | n_contained | grouped_cost | grouped_closed | mincut_cost | mincut_blocks_known | mincut_closed | mincut_differs |
|---|---|---|---|---|---|---|---|
| active policy | 32 | 2.09 | 1.00 | 3.00 | 1.00 | 0.81 | 0.66 |

### hard

| probe policy | n | hit_rate | probes_to_first | coverage | claim_rate | false_claim_rate | insufficient_evidence | closed | legit_completion | cost | replays |
|---|---|---|---|---|---|---|---|---|---|---|---|
| active | 18 | 1.00 | 5.17 | 1.00 | 0.33 | 0.00 | 0.67 | 1.00 | 1.00 | 1.11 | 394.39 |
| checklist | 18 | 0.92 | 8.25 | 0.75 | 0.28 | 0.40 | 0.39 | 0.72 | 0.99 | 1.07 | 332.61 |
| lookahead | 18 | 1.00 | 4.50 | 0.92 | 0.44 | 0.25 | 0.56 | 0.89 | 1.00 | 1.06 | 380.11 |
| oracle(full-obs) | 18 | 1.00 | 4.92 | 0.96 | 0.39 | 0.14 | 0.61 | 0.94 | 1.00 | 1.08 | 375.61 |
| random | 18 | 0.83 | 8.58 | 0.75 | 0.22 | 0.00 | 0.50 | 0.89 | 0.98 | 1.25 | 298.39 |

| containment | n | closed | violation_rate | legit_completion | cost | latency |
|---|---|---|---|---|---|---|
| CEGC (active) | 18 | 1.00 | 0.00 | 1.00 | 1.11 | 0 |
| blanket | 18 | 1.00 | 0.00 | 0.19 | 9.75 | 0 |
| parent_only | 18 | 0.33 | 0.17 | 0.86 | 1.33 | 4.83 |
| reference_impl | 18 | 1.00 | 0.00 | 1.00 | 0.00 | 0 |
| static_lineage | 18 | 0.33 | 0.07 | 0.97 | 1.12 | 3.44 |

| optimizer (same discovered paths) | n_contained | grouped_cost | grouped_closed | mincut_cost | mincut_blocks_known | mincut_closed | mincut_differs |
|---|---|---|---|---|---|---|---|
| active policy | 12 | 1.67 | 1.00 | 1.67 | 1.00 | 1.00 | 0.00 |
