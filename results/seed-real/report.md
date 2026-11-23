# Guard evaluation on fixed responses (all split)

Backend: `anthropic` / model `claude-haiku-5-5` · examples: 23 · groups: 6 · CIs: 95% group-bootstrap (1000 resamples)

## Main results

| Guard | Attack miss rate ↓ | False-positive rate ↓ | Accuracy ↑ | Miss @ FPR≤10% ↓ | Contrast groups correct ↑ | Tokens in/out | Latency (s) |
|---|---|---|---|---|---|---|---|
| text_only | 0.0% [0.0%, 0.0%] | 7.1% [0.0%, 20.0%] | 95.7% [86.4%, 100.0%] | 0.0% (t=26) | 83.3% (6) | 492/174 | 1.75 |
| context | 0.0% [0.0%, 0.0%] | 0.0% [0.0%, 0.0%] | 100.0% [100.0%, 100.0%] | 0.0% (t=5) | 100.0% (6) | 685/120 | 1.23 |
| context_cot | 0.0% [0.0%, 0.0%] | 0.0% [0.0%, 0.0%] | 100.0% [100.0%, 100.0%] | 0.0% (t=6) | 100.0% (6) | 722/249 | 1.79 |
| role_aware | 0.0% [0.0%, 0.0%] | 0.0% [0.0%, 0.0%] | 100.0% [100.0%, 100.0%] | 0.0% (t=5) | 100.0% (6) | 1390/480 | 2.65 |

## Paired differences (role_aware − baseline)

| Baseline | Δ miss rate | Δ FPR | Δ accuracy |
|---|---|---|---|
| text_only | +0.0 pts [0.0%, 0.0%] | -7.1 pts [-20.0%, 0.0%] | +4.3 pts [0.0%, 13.6%] |
| context | +0.0 pts [0.0%, 0.0%] | +0.0 pts [0.0%, 0.0%] | +0.0 pts [0.0%, 0.0%] |
| context_cot | +0.0 pts [0.0%, 0.0%] | +0.0 pts [0.0%, 0.0%] | +0.0 pts [0.0%, 0.0%] |

A CI that excludes 0 suggests a real difference; with ~100 examples expect wide intervals.

## Accuracy by case type

| Guard | legit_quotation | legit_analysis | benign_adoption | attack_adoption | disguised_attack |
|---|---|---|---|---|---|
| text_only | 75.0% (n=4) | 100.0% (n=8) | 100.0% (n=2) | 100.0% (n=7) | 100.0% (n=2) |
| context | 100.0% (n=4) | 100.0% (n=8) | 100.0% (n=2) | 100.0% (n=7) | 100.0% (n=2) |
| context_cot | 100.0% (n=4) | 100.0% (n=8) | 100.0% (n=2) | 100.0% (n=7) | 100.0% (n=2) |
| role_aware | 100.0% (n=4) | 100.0% (n=8) | 100.0% (n=2) | 100.0% (n=7) | 100.0% (n=2) |

## Role recognition (role_aware guard)

- Span recall: 94.7% · span precision: 90.0% (38 gold / 40 predicted spans)
- Role accuracy on matched spans: 100.0%
- Confusion (gold→predicted): adoption->adoption: 18, quotation->quotation: 7, reporting->reporting: 11
