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

## Baselines and external benchmark (add to Design / Dataset and Evaluation)
Besides our three prompted guards, we compare against two published defenses. **PIGuard** (Li et al.,
ACL 2025) is a trained prompt-injection classifier; it sees only text, so we apply it to the candidate
response and, separately, to the external content. **PromptArmor** (Shi et al., 2025) prompts an
off-the-shelf LLM to detect injected text in the input and removes it by fuzzy matching before the
assistant answers; we use the prompt from the paper's Figure 2 with the same model as our guards. It is
an input-side defense, so we evaluate it end to end only. To check generalization beyond our own data,
we also run the end-to-end evaluation on a 120-example slice of **BIPIA EmailQA** (Yi et al., KDD 2025):
100 attacked samples stratified over its 15 attack categories and 20 clean emails. BIPIA contains no
cases where repeating the injected text is legitimate, so it measures attack blocking and utility on
ordinary tasks; our own dataset remains the test of role distinctions.

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
- **H5 (input-side defenses):** PromptArmor and source-side PIGuard achieve low attack success, but
  fail legitimate quotation/analysis requests about the injected text (PromptArmor removes it; source-side
  PIGuard blocks the whole response), so their useful completion on those cases is lower than role_aware's.
- **H6 (generalization):** On BIPIA, role_aware's attack success is comparable to the other guards' and
  its false-block rate on the 20 clean emails is no higher.
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
Lyla Liu (P1) leads data construction, the annotation guide and agreement analysis. Effie Wu (P2) leads the baseline guards,
the evaluation harness, the end-to-end assistant and security evaluation. Sicheng Mo (P3) leads the role-aware guard,
role-classification analysis and failure analysis. Effie and Sicheng serve as second annotators. All three
contribute to the report. Timeline: week 1 setup and pilot (40 examples), week 2 full dataset and main
runs, week 3 end-to-end evaluation, analysis and writing.

## Prior work
Work completed before the course: None.
At proposal submission, completed work consists of: a literature review (Task Shield, PIGuard,
PromptArmor, Andreas 2022, Clark & Brennan 1991), design discussions that settled the threat model and
the three guard conditions, and the initial project scaffold. *(Edit to match what you had actually
done by the submission date.)*
