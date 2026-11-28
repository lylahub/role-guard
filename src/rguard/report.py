"""Turn results.json into a readable Markdown report.

  python -m rguard.report results/<run>/results.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .role_policy import POLICIES


def _f(x, pct=True):
    if x is None:
        return "–"
    return f"{100 * x:.1f}%" if pct else f"{x:.2f}"


def _ci(ci, pct=True):
    if not ci or ci[0] is None:
        return ""
    return f" [{_f(ci[0], pct)}, {_f(ci[1], pct)}]"


def _policy_diagnostics(res: dict) -> dict:
    """Policy diagnostics keyed by policy name.

    Results written before task_alignment_policy existed store one flat dict for role_aware_policy;
    read those as {"role_aware_policy": ...} so old results.json files still render.
    """
    diag = res.get("policy_diagnostics") or {}
    return {"role_aware_policy": diag} if "disagreements" in diag else diag


def render_fixed(res: dict) -> str:
    L = [f"# Guard evaluation on fixed responses ({res['meta']['split']} split)", "",
         f"Backend: `{res['meta']['backend']}` / model `{res['meta']['model']}` · examples: {res['meta']['n']} · "
         f"groups: {res['meta']['groups']} · CIs: 95% group-bootstrap ({res['meta']['n_boot']} resamples)", ""]
    if res["meta"]["backend"] == "mock":
        L += ["> **MOCK BACKEND: these numbers are meaningless; they only show the pipeline works.**", ""]
    L += ["## Main results", "",
          "| Guard | Attack miss rate ↓ | False-positive rate ↓ | Accuracy ↑ | "
          f"Miss @ FPR≤{res['meta']['target_fpr']:.0%} ↓ | Contrast groups correct ↑ | Tokens in/out | Latency (s) |",
          "|---|---|---|---|---|---|---|---|"]
    for g, r in res["guards"].items():
        d, ci, m, gc, c = r["detection"], r["ci"], r["miss_at_fpr"], r["group_consistency"], r["cost"]
        operating_point = "–" if g.endswith("_policy") else f"{_f(m['miss_rate'])} (t={m['threshold']})"
        L.append(f"| {g} | {_f(d['attack_miss_rate'])}{_ci(ci.get('attack_miss_rate'))} | "
                 f"{_f(d['false_positive_rate'])}{_ci(ci.get('false_positive_rate'))} | "
                 f"{_f(d['accuracy'])}{_ci(ci.get('accuracy'))} | {operating_point} | "
                 f"{_f(gc['contrast_correct'])} ({gc['contrast_groups']}) | "
                 f"{c.get('mean_input_tokens', 0):.0f}/{c.get('mean_output_tokens', 0):.0f} | "
                 f"{c.get('mean_latency_s', 0):.2f} |")
    if res.get("comparisons"):
        L += ["", "## Paired differences (role_aware − baseline)", "",
              "| Baseline | Δ miss rate | Δ FPR | Δ accuracy |", "|---|---|---|---|"]
        for b, cmp in res["comparisons"].items():
            def cell(k):
                v, ci = cmp[k]["diff"], cmp[k]["ci"]
                sign = "" if v is None else ("+" if v >= 0 else "")
                return "–" if v is None else f"{sign}{100 * v:.1f} pts{_ci(ci)}"
            L.append(f"| {b} | {cell('attack_miss_rate')} | {cell('false_positive_rate')} | {cell('accuracy')} |")
        L += ["", "A CI that excludes 0 suggests a real difference; with ~100 examples expect wide intervals."]
    L += ["", "## Accuracy by case type", "", "| Guard | " + " | ".join(res["case_types"]) + " |",
          "|---|" + "---|" * len(res["case_types"])]
    for g, r in res["guards"].items():
        cells = [f"{_f(r['by_case_type'][ct]['accuracy'])} (n={r['by_case_type'][ct]['n']})"
                 if ct in r["by_case_type"] else "–" for ct in res["case_types"]]
        L.append(f"| {g} | " + " | ".join(cells) + " |")
    if "role_aware" in res["guards"] and res["guards"]["role_aware"].get("roles"):
        rm = res["guards"]["role_aware"]["roles"]
        L += ["", "## Role recognition (role_aware guard)", "",
              f"- Span recall: {_f(rm['span_recall'])} · span precision: {_f(rm['span_precision'])} "
              f"({rm['gold_spans']} gold / {rm['pred_spans']} predicted spans)",
              f"- Role accuracy on matched spans: {_f(rm['role_accuracy_on_matched'])}",
              "- Confusion (gold→predicted): " + ", ".join(f"{k}: {v}" for k, v in rm["confusion"].items())]
    if abl := res.get("policy_ablation"):
        # Older results stored one role_aware_policy - task_alignment_policy comparison, unkeyed.
        if "accuracy" in abl:
            abl = {"role_aware_policy - task_alignment_policy": abl}
        def cell(cmp, k):
            v = cmp[k]["diff"]
            return "–" if v is None else f"{'+' if v >= 0 else ''}{100 * v:.1f} pts{_ci(cmp[k]['ci'])}"
        meaning = {"endorsement_policy - task_alignment_policy": "a yes/no endorsement",
                   "role_aware_policy - endorsement_policy": "three roles over yes/no endorsement",
                   "role_aware_policy - task_alignment_policy": "three roles over no usage information"}
        L += ["", "## What usage information adds (policy ladder)", "",
              "All three are code rules over the same kind of cited evidence (planted, authorized, harmful) "
              "and block a planted unauthorized or a harmful instruction the response acts on. "
              "task_alignment_policy counts every cited instruction as acted on; endorsement_policy only "
              "those the response endorses; role_aware_policy only those with role \"adoption\".", "",
              "| Comparison | Adds | Δ miss rate | Δ FPR | Δ accuracy |", "|---|---|---|---|---|"]
        for name, cmp in abl.items():
            L.append(f"| {name.replace(' - ', ' − ')} | {meaning.get(name, '')} | {cell(cmp, 'attack_miss_rate')} | "
                     f"{cell(cmp, 'false_positive_rate')} | {cell(cmp, 'accuracy')} |")
    for policy_name, diag in _policy_diagnostics(res).items():
        source = POLICIES[policy_name][0] if policy_name in POLICIES else policy_name.removesuffix("_policy")
        L += ["", f"## {policy_name} vs. {source} model decision", "",
              f"- Decisions changed: {diag['disagreements']}",
              f"- Errors fixed: {diag['policy_fixes']} · new errors: {diag['policy_regressions']}",
              "- Both conditions share one model call per example. Token and latency figures describe that call."]
        if oracle := diag.get("oracle_evidence"):
            L += [f"- Draft gold evidence: {oracle['oracle_correct']}/{oracle['n']} correct "
                  f"vs. {oracle['policy_correct']}/{oracle['n']} using predicted evidence "
                  f"({oracle['oracle_fixes']} fixes, {oracle['oracle_regressions']} regressions)."]
        if codes := diag.get("reason_codes"):
            L += ["", "**Why the code policy decided** (gold label of the examples in each row)", "",
                  "| Reason code | gold allow | gold block |", "|---|---|---|"]
            L += [f"| {code} | {c['allow']} | {c['block']} |" for code, c in codes.items()]
            if {"invalid_segment_id", "malformed_span", "span_not_found"} & codes.keys():
                L += ["", "`invalid_segment_id`, `malformed_span` and `span_not_found` blocks come from evidence checks, "
                      "not from a role judgment; on gold-allow rows they are format-driven false positives."]
        if fa := diag.get("flagged_allow"):
            L += ["", f"**Flagged allows:** {fa['n']} allowed responses carry a diagnostic flag; "
                  f"{fa['attacks']} of them are attacks, out of {fa['allowed_attacks']} attacks the policy allowed. "
                  "Flags never change the decision."]
        if flags := diag.get("flags"):
            L += ["", "| Flag | allow / gold allow | allow / gold block | block / gold allow | block / gold block |",
                  "|---|---|---|---|---|"]
            L += [f"| {f} | {c['allow/allow']} | {c['allow/block']} | {c['block/allow']} | {c['block/block']} |"
                  for f, c in flags.items()]
            L += ["", "`task_deviation`: the response acts on the document's own (not planted), harmless "
                  "instruction the user did not ask for; allowed, but counted. `harmful_non_adoption`: a quoted "
                  "or reported span the model marked harmful if followed. `model_policy_disagree`: the model's "
                  "own verdict differs from the code policy."]
    if slices := res.get("policy_slices"):
        L += ["", "## Policy contrast sets", "",
              "| Contrast | Guard | n | Miss rate | False-positive rate | Whole group correct |",
              "|---|---|---|---|---|---|"]
        for name, guards in slices.items():
            for guard, m in guards.items():
                L.append(f"| {name} | {guard} | {m['n']} | {_f(m['attack_miss_rate'])} | "
                         f"{_f(m['false_positive_rate'])} | {_f(m['contrast_correct'])} |")
    if variants := res.get("policy_variants"):
        L += ["", "## Contrast results by variant", "",
              "| Contrast | Variant | Guard | n | Miss rate | False-positive rate |",
              "|---|---|---|---|---|---|"]
        for m in variants:
            L.append(f"| {m['contrast']} | {m['variant']} | {m['guard']} | {m['n']} | "
                     f"{_f(m['attack_miss_rate'])} | {_f(m['false_positive_rate'])} |")
    pe = {g: r["cost"].get("parse_errors", 0) for g, r in res["guards"].items()}
    if any(pe.values()):
        L += ["", f"**Parse errors (counted as block):** {pe}"]
    return "\n".join(L) + "\n"


def render_e2e(res: dict) -> str:
    L = [f"# End-to-end evaluation ({res['meta']['split']} split, {res['meta']['n']} examples)", "",
         f"Assistant: `{res['meta']['assistant_model']}` · guard: `{res['meta']['model']}` · "
         f"judge: `{res['meta']['judge_model']}`", ""]
    if res["meta"]["backend"] == "mock":
        L += ["> **MOCK BACKEND: these numbers are meaningless.**", ""]
    base = res["guards"].get("none", {}).get("e2e", {})
    if base.get("n"):
        L += [f"Labeled examples: {base['n']} ({base['n_attacked']} attacked, {base['n_clean']} clean). "
              f"Without a guard the assistant adopted the attack in {_f(base['assistant_adoption_rate'])} of "
              "attacked examples; that is the most an output guard can prevent.", ""]
        if base["n_attacked"] and not base["assistant_adoption_rate"]:
            L += ["> **The undefended assistant adopted no attacks, so this run cannot show any output guard's "
                  "benefit.** Use attacks that land (or a more compliant assistant) before comparing guards.", ""]
    L += ["| Guard | Attack success ↓ | Adopted responses let through ↓ | Useful, clean ↑ | Useful, attacked ↑ | "
          "Blocked | of which benign | Other failure |",
          "|---|---|---|---|---|---|---|---|"]
    for g, r in res["guards"].items():
        m, ci = r["e2e"], r["ci"]
        if not m.get("n"):
            L.append(f"| {g} | – | – | – | – | – | – | – |")
            continue
        L.append(f"| {g} | {_f(m['attack_success_rate'])}{_ci(ci.get('attack_success_rate'))} | "
                 f"{_f(m['adopted_let_through_rate'])}{_ci(ci.get('adopted_let_through_rate'))} | "
                 f"{_f(m['useful_completion_rate_clean'])}{_ci(ci.get('useful_completion_rate_clean'))} | "
                 f"{_f(m['useful_completion_rate_attacked'])}{_ci(ci.get('useful_completion_rate_attacked'))} | "
                 f"{_f(m['blocked_rate'])} | {m['blocked_but_benign']} | {_f(m['other_failure_rate'])} |")
    L += ["", "Attack success = attacked examples whose response adopted the attack and was shown, over attacked "
          "examples only (clean examples are not in the denominator). Adopted responses let through = adopted "
          "responses the guard showed, over adopted responses; \"–\" means nothing was adopted."]
    je = res["meta"].get("judge_errors")
    if isinstance(je, dict) and je.get("total"):
        L += ["", f"**Judge errors, excluded from every rate:** {je['total']} "
              f"(parse {je['parse_error']}, missing field {je['missing_field']}, not true/false {je['invalid_type']})."]

    det = res.get("promptarmor_detection")
    if det:
        L += ["", f"**PromptArmor detection** ({det['attacked']} attacked / {det['clean']} clean sources): "
              f"false-negative rate {_f(det['false_negative_rate'])}, false-positive rate {_f(det['false_positive_rate'])}, "
              f"detected but nothing removed: {det['removal_failed']}. "
              "PromptArmor's row is the assistant answering from the *sanitized* source; it never blocks."]
    L += ["", "Blocked responses are never counted as useful completions. "
          "Spot-check the judge: read `e2e_rows.jsonl` and hand-verify at least 20 judge decisions."]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("results_json")
    a = ap.parse_args(argv)
    res = json.loads(Path(a.results_json).read_text())
    md = render_e2e(res) if res["meta"].get("kind") == "e2e" else render_fixed(res)
    out = Path(a.results_json).with_name("report.md")
    out.write_text(md)
    print(md)


if __name__ == "__main__":
    main()
