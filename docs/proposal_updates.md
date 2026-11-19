# Text to paste into the revised proposal (edit as needed)

## Design and Implementation: add after the three guard methods
All guards use the same underlying model, temperature and security-policy text. The context-aware
and role-aware guards receive identical inputs (user request, source content, candidate response).
They differ only in the procedure the prompt requires. The context-aware guard outputs a decision
directly. The role-aware guard must first (1) extract the instruction spans that come from the
source, (2) label each as quotation, reporting (which includes analysis) or adoption, and (3) decide
using a rubric that combines each span's role with the user's request. Neither guard is a
tool-using agent; both are prompted classifiers with structured (JSON) output. To separate the
effect of role recognition from the effect of extra reasoning, we add a fourth condition: the
context-aware guard with free-form step-by-step reasoning (context-aware + CoT).

## Hypotheses (new subsection)
- **H1 (overdefense):** Compared with the context-aware guard, the role-aware guard has a lower
  false-positive rate on legitimate quotation and analysis, so useful task completion is higher.
- **H2 (security):** Its attack miss rate and end-to-end attack success rate are the same or
  modestly lower. Requiring every source-derived span to be listed should make it harder to miss
  adoptions hidden inside mostly legitimate responses, and attacks that start as a report but end
  in an endorsement.
- **H3 (risk):** On attacks disguised as quotation or reporting, the role-aware guard may be more
  vulnerable if it trusts the role label too much, so we report this subset separately.
- **H4 (cost):** The role-aware guard uses more tokens and has higher latency.
- **Against the text-only guard,** we expect both lower false positives and fewer misses.
- To rule out a guard looking safer just by blocking more, we also compare guards at a matched
  false-positive rate using their 0–100 risk scores.

## Dataset and Evaluation: replace the size and annotation sentences
We will construct about 100 manually reviewed examples in about 25 paired groups (one source
document with 3–5 variants), including about 15 attacks aimed at the role distinctions. Candidate
responses may be drafted with an LLM, but every example is reviewed and labelled by a person.
One member writes the examples; the other two independently label a ~30% subset, and we report
Cohen's kappa and resolve disagreements by majority vote. Development and test splits are separated by
source document and attack template. All guard prompts are frozen before the single test-set run.
End-to-end evaluation uses about 35 examples; an LLM judge labels attack adoption and task
completion, and we hand-check at least 20 judge decisions. All metrics are reported with 95%
bootstrap confidence intervals that resample whole groups.

## Work Plan: replace the division of labor
P1 leads data construction, the annotation guide and agreement analysis. P2 leads the baseline guards,
the evaluation harness, the end-to-end assistant and security evaluation. P3 leads the role-aware guard,
role-classification analysis and failure analysis. P2 and P3 serve as second annotators. All three
contribute to the report. Timeline: week 1 setup and pilot (40 examples), week 2 full dataset and main
runs, week 3 end-to-end evaluation, analysis and writing.

## Prior work: fill in
At proposal submission, completed work consists of: ______.
Work completed before the course: ______ (or "None").
