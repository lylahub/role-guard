# rguard: communicative-role-aware guarding against indirect prompt injection

CS239 project code (Option B scope). It compares five prompted output guards for a document/email assistant:

| Condition | Sees | Procedure |
|---|---|---|
| `text_only` | candidate response | direct decision |
| `context` | request + source + response | direct decision |
| `context_cot` | same as `context` | free-form reasoning, then decision (ablation: "is it just more thinking?") |
| `task_alignment` | request + source + response | find instruction spans and evaluate alignment to the user task |
| `role_aware` | same as `context` | find instruction spans → label each quotation / reporting / adoption → decide with a rubric that combines role and task |

All five prompted guards share the same model, temperature and security-policy text (`src/rguard/prompts.py`), so any
difference between them comes from the information they see or the procedure they follow.

**External baselines** (see `THIRD_PARTY_NOTICES.md`):

| Baseline | Where it acts | Used in |
|---|---|---|
| `piguard` | Released PIGuard classifier on the **candidate response** (a trained text-only guard) | fixed + end-to-end |
| `piguard_source` | PIGuard on the **external content**; block if the source is flagged | fixed + end-to-end |
| `promptarmor` | **Input-side** detect-and-remove: the same guard LLM finds the injection in the source, it is fuzzily removed, then the assistant answers from the cleaned source. Prompt quoted verbatim from the paper's Fig. 2 (no code was released). | end-to-end only |

**External benchmark:** `data/bipia_email_slice.jsonl`, 100 attacked BIPIA EmailQA samples (stratified
over BIPIA's 15 attack categories) plus 20 clean emails. End-to-end only, since BIPIA has no gold responses.

## Setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[openai,piguard,bipia,dev]"   # or .[anthropic,...]; piguard = transformers+torch (CPU ok)
export OPENAI_API_KEY=...             # or ANTHROPIC_API_KEY
pytest -q                             # offline tests, no key needed
```
Then edit `configs/default.yaml` to set the backend and model names. Keep them fixed for the whole project.

## Offline smoke test (no API key)
```bash
python -m rguard.run_guards --data data/seed_examples.jsonl --split all --mock --name smoke
python -m rguard.run_e2e    --data data/seed_examples.jsonl --split all --mock --name smoke-e2e
```
The mock backend uses keyword heuristics. Its numbers are meaningless; it only shows the pipeline works.

## Workflow
```bash
# 1. Draft examples with an LLM, then REVIEW every one by hand (see docs/annotation_guide.md)
python -m rguard.draft data/sources_template.jsonl data/drafts/batch1.jsonl
#    fix the drafts, set status "reviewed", append them to data/examples.jsonl

# 2. Validate, check the balance, split (no source or attack template appears in both dev and test)
python -m rguard.data validate data/examples.jsonl
python -m rguard.data split    data/examples.jsonl --dev-frac 0.3
python -m rguard.data stats    data/examples.jsonl

# 3. Agreement on the double-annotated subset
python -m rguard.agreement annotations/A.jsonl annotations/B.jsonl --disagreements annotations/disagree.jsonl

# 4. Iterate on prompts on DEV only
python -m rguard.run_guards --data data/examples.jsonl --split dev

# 5. Freeze prompts (git tag), then run TEST once, for fixed responses and end to end
python -m rguard.run_guards --data data/examples.jsonl --split test --name final-test
python -m rguard.run_e2e    --data data/examples.jsonl --split test --name final-e2e

# 6. External check on BIPIA EmailQA (end to end; includes PromptArmor and PIGuard)
python -m rguard.run_e2e --data data/bipia_email_slice.jsonl --split all --name bipia-email
```
To rebuild the BIPIA slice (e.g. with the `middle` insertion position, which needs nltk; the committed
slice uses `start`/`end` only):
```bash
git clone --depth 1 https://github.com/microsoft/BIPIA /tmp/BIPIA
python -m rguard.bipia --bipia-dir /tmp/BIPIA --positions start,end,middle --out data/bipia_email_slice.jsonl
```
Each run writes `results/<name>/` containing `predictions.jsonl` (or `e2e_rows.jsonl`), `results.json` and `report.md`.
Every LLM call is cached in `.cache/llm/`, so reruns are free and reproducible.

## The dataset (`data/examples.jsonl`)
- 99 examples in 25 groups, with 3–5 variants each over one source. Source types: 33 email, 38 document, 24 web, 4 chat.
- Case types: 24 attack_adoption, 14 disguised_attack, 16 benign_adoption, 28 legit_analysis, 17 legit_quotation (38 block / 61 allow).
- 11 attack templates. Three no-attack control groups (g08 onboarding, g12 requested password reset,
  g20 school fee) contain alarming but legitimate instructions; they measure overdefense.
- Split by source document and attack template: dev 36 (9 groups), test 63 (16 groups). End-to-end: 27 distinct test runs.
- **Every example is a draft** (`status: draft-needs-review` / `seed-needs-review`) until a team member reviews it.

## What the report contains
- **Fixed responses:** attack miss rate, false-positive rate, accuracy (with 95% group-bootstrap CIs);
  miss rate at a matched FPR (`target_fpr` in the config); accuracy per case type; the share of contrast
  groups with every variant decided correctly (does the decision change with context?); paired
  role_aware − baseline differences with CIs; span recall and precision; role accuracy and confusion;
  token and latency cost; parse errors (these count as block).
- **End to end:** attack success rate, useful completion rate, and blocked rate (reported separately)
  for no guard, each output guard, and PromptArmor (plus PromptArmor's detection false-negative and
  false-positive rates against whether the source really contains an attack).

## Repository layout
```
configs/default.yaml        models, concurrency, bootstrap, target FPR
data/examples.jsonl         THE dataset: 99 drafted examples in 25 paired groups, already split (36 dev / 63 test)
data/seed_examples.jsonl    the first 23 examples, kept separately for the offline tests
data/sources_template.jsonl input format for rguard.draft
annotations/                annotator files (the example_* files show the format)
docs/annotation_guide.md    role and label definitions, how to build groups, double-annotation protocol
docs/work_plan.md           3-week plan with P1/P2/P3 checklists
docs/proposal_updates.md    text for the revised proposal (hypotheses, guard difference, scope)
src/rguard/                 prompts, guards, llm (+mock), data, metrics, runners, report, draft, agreement
tests/                      offline tests
```

## Example schema (`data/*.jsonl`, one JSON object per line)
```json
{"id": "g01-a", "group_id": "g01", "source_id": "src01", "source_type": "email",
 "attack_template": "T-credential-redirect", "attack_goal": "Get the user to ...",
 "source_content": "...", "user_request": "Summarize this email for me.",
 "candidate_response": "...", "case_type": "attack_adoption", "label": "block",
 "spans": [{"text": "<exact substring of candidate_response>", "role": "adoption"}],
 "e2e": true, "split": "test", "annotator": "P1", "status": "reviewed", "notes": ""}
```
`case_type` ∈ legit_quotation, legit_analysis, benign_adoption, attack_adoption, disguised_attack.
`role` ∈ quotation, reporting (includes analysis), adoption.

## Role-aware code policy experiment

The fixed-response runner can compare the model verdict with a Python policy using the same
role-aware call. The policy blocks unauthorized or harmful adoption and validates the span
evidence before applying its rule. Quotation and reporting can be permitted even when the user
did not request the action itself. The rule relies on model annotations, including whether a
span constitutes adoption. It can miss evidence that the model does not extract.

```bash
python -m rguard.run_guards --data data/examples.jsonl --data data/role_policy_examples.jsonl \
  --split dev --guards context,context_cot,task_alignment,role_aware --compare-role-policy --mock --name role-policy-smoke
```

The task-alignment baseline uses the same model, inputs, policy, span-extraction instruction,
and per-span authorization and harm fields as the role-aware guard. It checks each span against
the user task without requiring a communicative role label. Compare it with the model-driven
role-aware condition to estimate the effect of explicit role prompting and annotation.

Use the dev split for model evaluation and freeze prompts and policy before a separate test run.
The separate file contains 18 synthetic draft responses across eight groups. The contrasts cover
mixed roles, an authorization change with a fixed response, a role change with a fixed request,
and suggestion wording. For suggestion wording, each split has one group with the same source
and request paired with a neutral report, a direct recommendation, and a hedged recommendation.
Both recommendations are labeled adoption. The report shows results for each wording variant.
Review the draft gold evidence before using it in experimental claims.
The report shows paired decisions, fixes, regressions, and accuracy using draft gold evidence in
place of the predicted spans. The gold evidence comparison replaces both role and authorization
annotations, so its results reflect their combined effect. Mock runs validate plumbing.
