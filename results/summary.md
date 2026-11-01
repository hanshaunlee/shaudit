Budget 10 probes per audit. Methods: {"random": {"policy": "random", "visibility": "standard", "budget": 10, "alpha": 1.0, "beta": 0.1, "gamma": 0.5, "n_samples": 16, "contain": true, "tau": 0.05, "other_prior": 0.1, "other_tau": 0.3, "lam": 3.0, "whitebox": false, "seed": 0}, "checklist": {"policy": "checklist", "visibility": "standard", "budget": 10, "alpha": 1.0, "beta": 0.1, "gamma": 0.5, "n_samples": 16, "contain": true, "tau": 0.05, "other_prior": 0.1, "other_tau": 0.3, "lam": 3.0, "whitebox": false, "seed": 0}, "active": {"policy": "active", "visibility": "standard", "budget": 10, "alpha": 2.0, "beta": 0.0, "gamma": 0.5, "n_samples": 16, "contain": true, "tau": 0.05, "other_prior": 0.1, "other_tau": 0.3, "lam": 3.0, "whitebox": false, "seed": 0}, "lookahead": {"policy": "lookahead", "visibility": "standard", "budget": 10, "alpha": 2.0, "beta": 0.0, "gamma": 0.5, "n_samples": 16, "contain": true, "tau": 0.05, "other_prior": 0.1, "other_tau": 0.3, "lam": 3.0, "whitebox": false, "seed": 0}, "oracle(full-obs)": {"policy": "active", "visibility": "full", "budget": 10, "alpha": 2.0, "beta": 0.0, "gamma": 0.5, "n_samples": 16, "contain": true, "tau": 0.05, "other_prior": 0.1, "other_tau": 0.3, "lam": 3.0, "whitebox": false, "seed": 0}}

### dev

| probe policy | n | hit_rate | probes_to_first | coverage | claim_rate | false_claim_rate | insufficient_evidence | closed | legit_completion | cost | replays |
|---|---|---|---|---|---|---|---|---|---|---|---|
| active | 96 | 1.00 | 1.71 | 0.90 | 0.60 | 0.00 | 0.09 | 1.00 | 1.00 | 1.18 | 469.72 |
| checklist | 96 | 0.91 | 4.03 | 0.77 | 0.47 | 0.00 | 0.03 | 0.85 | 0.97 | 1.03 | 414.51 |
| lookahead | 96 | 1.00 | 1.56 | 0.89 | 0.61 | 0.00 | 0.10 | 1.00 | 1.00 | 1.18 | 464.71 |
| oracle(full-obs) | 96 | 0.99 | 1.63 | 0.90 | 0.82 | 0.00 | 0.01 | 0.99 | 1.00 | 1.16 | 463.83 |
| random | 96 | 0.93 | 3 | 0.79 | 0.11 | 0.00 | 0.12 | 0.86 | 0.98 | 1.08 | 428.69 |

| containment | n | closed | violation_rate | legit_completion | cost | latency |
|---|---|---|---|---|---|---|
| CEGC (active) | 96 | 1.00 | 0.00 | 1.00 | 1.18 | 0 |
| blanket | 96 | 1.00 | 0.00 | 0.09 | 11.62 | 0 |
| parent_only | 96 | 0.22 | 0.38 | 0.91 | 0.00 | 7.27 |
| reference_impl | 96 | 1.00 | 0.00 | 1.00 | 0.00 | 0 |
| static_lineage | 96 | 0.28 | 0.17 | 0.91 | 1.07 | 5.94 |

| optimizer (same discovered paths) | n_contained | grouped_cost | grouped_closed | mincut_cost | mincut_blocks_known | mincut_closed | mincut_differs |
|---|---|---|---|---|---|---|---|
| active policy | 75 | 1.51 | 1.00 | 1.62 | 0.99 | 0.84 | 0.36 |

### gen

| probe policy | n | hit_rate | probes_to_first | coverage | claim_rate | false_claim_rate | insufficient_evidence | closed | legit_completion | cost | replays |
|---|---|---|---|---|---|---|---|---|---|---|---|
| active | 32 | 1.00 | 1.28 | 0.78 | 0.50 | 0.00 | 0.19 | 1.00 | 1.00 | 2.09 | 691.72 |
| checklist | 32 | 1.00 | 3.22 | 0.71 | 0.19 | 0.00 | 0.12 | 0.88 | 1.00 | 2.02 | 674.09 |
| lookahead | 32 | 1.00 | 1.22 | 0.81 | 0.41 | 0.00 | 0.19 | 1.00 | 1.00 | 2.09 | 687.62 |
| oracle(full-obs) | 32 | 1.00 | 1.19 | 0.86 | 0.66 | 0.00 | 0.00 | 1.00 | 1.00 | 2.09 | 727.94 |
| random | 32 | 0.91 | 2.66 | 0.54 | 0.03 | 0.00 | 0.34 | 0.59 | 0.91 | 1.72 | 592.03 |

| containment | n | closed | violation_rate | legit_completion | cost | latency |
|---|---|---|---|---|---|---|
| CEGC (active) | 32 | 1.00 | 0.00 | 1.00 | 2.09 | 0 |
| blanket | 32 | 1.00 | 0.00 | 0.11 | 12.00 | 0 |
| parent_only | 32 | 0.00 | 0.61 | 0.76 | 0.00 | 13.16 |
| reference_impl | 32 | 1.00 | 0.00 | 1.00 | 0.00 | 0 |
| static_lineage | 32 | 0.00 | 0.33 | 0.76 | 1.19 | 12.12 |

| optimizer (same discovered paths) | n_contained | grouped_cost | grouped_closed | mincut_cost | mincut_blocks_known | mincut_closed | mincut_differs |
|---|---|---|---|---|---|---|---|
| active policy | 32 | 2.09 | 1.00 | 2.98 | 1.00 | 0.75 | 0.66 |

### hard

| probe policy | n | hit_rate | probes_to_first | coverage | claim_rate | false_claim_rate | insufficient_evidence | closed | legit_completion | cost | replays |
|---|---|---|---|---|---|---|---|---|---|---|---|
| active | 18 | 1.00 | 5.17 | 0.96 | 0.17 | 0.00 | 0.61 | 0.94 | 1.00 | 1.08 | 388.39 |
| checklist | 18 | 0.50 | 7.42 | 0.25 | 0.22 | 0.50 | 0.00 | 0.33 | 0.94 | 1.36 | 195.17 |
| lookahead | 18 | 1.00 | 4.50 | 0.88 | 0.17 | 0.00 | 0.50 | 0.83 | 1.00 | 1.03 | 375.17 |
| oracle(full-obs) | 18 | 1.00 | 4.92 | 0.75 | 0.50 | 0.33 | 0.33 | 0.67 | 1.00 | 0.94 | 343.50 |
| random | 18 | 0.67 | 6.33 | 0.54 | 0.00 | – | 0.33 | 0.72 | 0.96 | 1.33 | 242.83 |

| containment | n | closed | violation_rate | legit_completion | cost | latency |
|---|---|---|---|---|---|---|
| CEGC (active) | 18 | 0.94 | 0.00 | 1.00 | 1.08 | 0.06 |
| blanket | 18 | 1.00 | 0.00 | 0.19 | 9.75 | 0 |
| parent_only | 18 | 0.33 | 0.17 | 0.86 | 1.33 | 4.83 |
| reference_impl | 18 | 1.00 | 0.00 | 1.00 | 0.00 | 0 |
| static_lineage | 18 | 0.33 | 0.07 | 0.97 | 1.12 | 3.44 |

| optimizer (same discovered paths) | n_contained | grouped_cost | grouped_closed | mincut_cost | mincut_blocks_known | mincut_closed | mincut_differs |
|---|---|---|---|---|---|---|---|
| active policy | 12 | 1.62 | 0.92 | 1.77 | 1.00 | 0.92 | 0.08 |
