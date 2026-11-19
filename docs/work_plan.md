# Work plan: Option B, three people, three weeks

**Scope:** about 100 fixed-response examples (about 25 sources × 4 variants), four guard conditions (text_only, context, context_cot ablation, role_aware) on one model, an end-to-end run on about 35 examples, and about 15 role-targeted attacks (`disguised_attack`). If there's time: rerun everything with a second model by changing `configs/`.

Fill in names:

| Role | Person | Owns | Also |
|---|---|---|---|
| P1 Data lead | ______ | annotation guide, sources, paired variants, disguised attacks, agreement stats | failure analysis |
| P2 Baselines & evaluation | ______ | `configs/`, text_only / context / context_cot prompts, `run_guards`, `run_e2e`, judge spot-checks, results tables | second annotator |
| P3 Role-aware guard | ______ | role-aware prompt + rubric, role accuracy, paired-context analysis, failure analysis | second annotator |

## Week 1: setup (checkpoint: all guards run on 40 examples)
- [ ] Everyone: install, run `pytest`, run the mock smoke test (README).
- [ ] P2: choose the model(s), fill in `configs/default.yaml`, run the seed data on the real model.
- [ ] P1: finalize the annotation guide, write 25 source documents and assign attack templates (decide which are dev-only).
- [ ] P1: draft the first 40 examples (`rguard.draft` + review), validate.
- [ ] P3: test the role-aware prompt on seed + the first 40 examples; write down failure patterns.
- [ ] P2/P3: double-annotate the first batch and compute kappa early to find disagreements over the guide.

## Week 2: data and main runs (checkpoint: dataset frozen, main table exists)
- [ ] P1: reach about 100 examples, including about 15 `disguised_attack`; mark about 35 `e2e: true`.
- [ ] P2/P3: finish double annotation of the ~30% subset; adjudicate together.
- [ ] P1: `python -m rguard.data split data/examples.jsonl --dev-frac 0.3`, then **freeze** the dataset (commit it).
- [ ] P3: tune the role-aware prompt on **dev only**. P2 does the same for the baselines. Each prompt gets the same amount of effort.
- [ ] **Freeze all prompts** (git tag `prompts-frozen`), then run `run_guards --split test` once.

## Week 3: end to end, analysis, writing (keep the last two days for writing only)
- [ ] P2: `run_e2e --split test`; hand-check at least 20 judge labels and report judge agreement.
- [ ] P2: optional second-model rerun.
- [ ] P3: role-accuracy and confusion analysis; check whether decisions change correctly within paired groups.
- [ ] P1 + P3: failure analysis. Tag each test error as (a) misread the user's task, (b) wrong role, or (c) right role but wrong decision.
- [ ] Everyone: report and slides.

## Rules that protect validity
1. Never tune prompts on test. If you need to look at test errors, do it after the final run and say so.
2. Every guard gets the same model, temperature and shared policy text (enforced in `prompts.py`).
3. Report CIs. With about 100 examples, differences under about 10 points are usually within noise.
4. Keep all results folders. Results come from the cache, so they can be reproduced.
