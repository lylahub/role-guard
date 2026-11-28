"""Stage 1: evaluate guards on FIXED candidate responses (isolates detection performance).

  python -m rguard.run_guards --data data/examples.jsonl --split dev          # iterate on prompts here
  python -m rguard.run_guards --data data/examples.jsonl --split test         # once, after freezing prompts
  python -m rguard.run_guards --data data/seed_examples.jsonl --split all --mock   # offline smoke test

Writes results/<name>/{predictions.jsonl, results.json, report.md}.
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from . import CASE_TYPES
from .data import filter_split, load_jsonl, save_jsonl, validate
from .guards import CLASSIFIER_GUARDS, DEFAULT_GUARDS, run_guard
from .llm import llm_from_config
from .metrics import (bootstrap_ci, by_case_type, cost, detection, group_consistency, miss_at_fpr,
                      paired_difference_ci, role_metrics)
from .prompts import frozen_status
from .report import render_fixed
from .role_policy import FLAGS, POLICIES, REASON_CODES, apply_policy, decide, policies_for


def evaluate(preds_by_guard: dict[str, list[dict]], n_boot: int, target_fpr: float) -> dict:
    out = {}
    for g, preds in preds_by_guard.items():
        out[g] = {
            "detection": detection(preds),
            "ci": {k: bootstrap_ci(preds, detection, k, n_boot)
                   for k in ("attack_miss_rate", "false_positive_rate", "accuracy")},
            "by_case_type": by_case_type(preds),
            "miss_at_fpr": miss_at_fpr(preds, target_fpr),
            "group_consistency": group_consistency(preds),
            "cost": cost(preds),
        }
        if g == "role_aware":
            out[g]["roles"] = role_metrics(preds)
    comparisons = {}
    if "role_aware" in preds_by_guard:
        for b in preds_by_guard:
            if b == "role_aware":
                continue
            comparisons[b] = {}
            for k in ("attack_miss_rate", "false_positive_rate", "accuracy"):
                da = detection(preds_by_guard["role_aware"])[k]
                db = detection(preds_by_guard[b])[k]
                comparisons[b][k] = {
                    "diff": None if da is None or db is None else da - db,
                    "ci": paired_difference_ci(preds_by_guard["role_aware"], preds_by_guard[b], detection, k, n_boot)}
    result = {"guards": out, "comparisons": comparisons}
    diagnostics = {}
    for name, (source, _, _) in POLICIES.items():
        if name in preds_by_guard:
            diagnostics[name] = _policy_diagnostic(preds_by_guard[source], preds_by_guard[name])
    if diagnostics:
        result["policy_diagnostics"] = diagnostics
    # The ladder: same rule, same kind of evidence; each step adds usage information.
    ladder = [("endorsement_policy", "task_alignment_policy"),   # what a yes/no endorsement adds
              ("role_aware_policy", "endorsement_policy"),       # what three roles add over it
              ("role_aware_policy", "task_alignment_policy")]    # both together
    ablation = {}
    for hi, lo in ladder:
        if {hi, lo} <= preds_by_guard.keys():
            a, b = preds_by_guard[hi], preds_by_guard[lo]
            ablation[f"{hi} - {lo}"] = {
                k: {"diff": None if detection(a)[k] is None or detection(b)[k] is None
                    else detection(a)[k] - detection(b)[k],
                    "ci": paired_difference_ci(a, b, detection, k, n_boot)}
                for k in ("attack_miss_rate", "false_positive_rate", "accuracy")}
    if ablation:
        result["policy_ablation"] = ablation
    return result


def _policy_diagnostic(model: list[dict], policy: list[dict]) -> dict:
    """How a code policy's decisions relate to the model verdict it was derived from."""
    paired = list(zip(model, policy, strict=True))
    diagnostic = {
        "disagreements": sum(a["decision"] != b["decision"] for a, b in paired),
        "policy_fixes": sum(a["decision"] != a["label"] == b["decision"] for a, b in paired),
        "policy_regressions": sum(a["decision"] == a["label"] != b["decision"] for a, b in paired),
    }
    annotated = [b for b in policy if b["gold_policy_decision"] is not None]
    if annotated:
        diagnostic["oracle_evidence"] = {
            "n": len(annotated),
            "policy_correct": sum(b["decision"] == b["label"] for b in annotated),
            "oracle_correct": sum(b["gold_policy_decision"] == b["label"] for b in annotated),
            "oracle_fixes": sum(b["decision"] != b["label"] == b["gold_policy_decision"] for b in annotated),
            "oracle_regressions": sum(b["decision"] == b["label"] != b["gold_policy_decision"] for b in annotated),
        }
    diagnostic["reason_codes"] = {
        code: {label: sum(p["reason_code"] == code and p["label"] == label for p in policy)
               for label in ("allow", "block")}
        for code in REASON_CODES if any(p["reason_code"] == code for p in policy)
    }
    diagnostic["flags"] = {
        flag: {f"{decision}/{label}": sum(flag in p["flags"] and p["decision"] == decision
                                          and p["label"] == label for p in policy)
               for decision in ("allow", "block") for label in ("allow", "block")}
        for flag in FLAGS if any(flag in p["flags"] for p in policy)
    }
    flagged = [p for p in policy if p["flagged_allow"]]
    diagnostic["flagged_allow"] = {
        "n": len(flagged),
        "attacks": sum(p["label"] == "block" for p in flagged),
        "allowed_attacks": sum(p["decision"] == "allow" and p["label"] == "block" for p in policy),
    }
    return diagnostic


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="configs/default.yaml")
    ap.add_argument("--data", required=True, action="append",
                    help="JSONL dataset path, repeat to evaluate several files as one batch")
    ap.add_argument("--compare-role-policy", action="store_true",
                    help="also derive code-policy decisions from the same role_aware / task_alignment "
                         "calls (role_aware_policy, task_alignment_policy)")
    ap.add_argument("--split", default="dev", choices=["dev", "test", "all"])
    ap.add_argument("--guards", default=DEFAULT_GUARDS,
                    help="comma list; PIGuard conditions need `pip install -e .[piguard]`")
    ap.add_argument("--name", help="results subfolder (default: <split>-<model>-<timestamp>)")
    ap.add_argument("--mock", action="store_true", help="offline mock backend (pipeline testing only)")
    ap.add_argument("--limit", type=int, help="only the first N examples (quick checks)")
    a = ap.parse_args(argv)

    cfg = yaml.safe_load(open(a.config))
    rows = [row for path in a.data for row in load_jsonl(path)]
    errs, _ = validate(rows)
    if errs:
        raise SystemExit("Dataset has errors; run `python -m rguard.data validate` first:\n  " + "\n  ".join(errs[:20]))
    rows = filter_split(rows, a.split)
    if a.limit:
        rows = rows[: a.limit]
    if a.split == "test":
        print("NOTE: running on TEST. Prompts should be frozen; don't tune on these results.")

    llm = llm_from_config(cfg, "guard", mock=a.mock)
    guards = [g.strip() for g in a.guards.split(",") if g.strip()]
    if a.compare_role_policy and not any(policies_for(g) for g in guards):
        ap.error("--compare-role-policy requires role_aware and/or task_alignment in --guards")
    workers = 1 if llm.backend == "mock" else cfg.get("concurrency", 4)

    preds_by_guard: dict[str, list[dict]] = {}
    all_preds = []
    for g in guards:
        def one(ex, g=g):
            r = run_guard(llm, g, ex, ex["candidate_response"], fail_closed=cfg.get("fail_closed", True),
                          pg_cfg=cfg.get("piguard", {}))
            common = {"id": ex["id"], "group_id": ex["group_id"], "case_type": ex["case_type"],
                      "label": ex["label"], "gold_spans": ex.get("spans", []),
                      "dataset": ex.get("dataset", "main"), "contrast": ex.get("contrast"),
                      "variant": ex.get("variant")}
            model_pred = {**common, **r.to_dict()}
            if not a.compare_role_policy:
                return model_pred, []
            derived = []
            for name in policies_for(g):   # each reuses this guard's call: no extra API cost
                policy, reason_code, flags = apply_policy(r, ex["candidate_response"],
                                                          cfg.get("fail_closed", True), name)
                oracle = None
                if "policy_gold_spans" in ex:
                    oracle, _ = decide(ex["policy_gold_spans"], ex["candidate_response"], POLICIES[name][1])
                derived.append({**common, **policy.to_dict(), "gold_policy_decision": oracle,
                                "reason_code": reason_code, "flags": flags,
                                "flagged_allow": policy.decision == "allow" and bool(flags)})
            return model_pred, derived
        with ThreadPoolExecutor(max_workers=1 if g in CLASSIFIER_GUARDS else workers) as pool:
            batch = list(pool.map(one, rows))
        preds = [model for model, _ in batch]
        preds_by_guard[g] = preds
        all_preds += preds
        for i, name in enumerate(policies_for(g) if a.compare_role_policy else []):
            policy_preds = [derived[i] for _, derived in batch]
            preds_by_guard[name] = policy_preds
            all_preds += policy_preds
        d = detection(preds)
        print(f"{g:12s} miss={d['attack_miss_rate']}  fpr={d['false_positive_rate']}  acc={d['accuracy']}")

    name = a.name or f"{a.split}-{llm.model.replace('/', '_')}-{time.strftime('%Y%m%d-%H%M%S')}"
    out_dir = Path(cfg.get("results_dir", "results")) / name
    out_dir.mkdir(parents=True, exist_ok=True)
    save_jsonl(all_preds, out_dir / "predictions.jsonl")

    res = evaluate(preds_by_guard, cfg.get("n_bootstrap", 1000), cfg.get("target_fpr", 0.10))
    if a.compare_role_policy:
        res["policy_slices"] = {
            name: {
                guard: {
                    **detection(selected),
                    **group_consistency(selected),
                }
                for guard, preds in preds_by_guard.items()
                if (selected := [p for p in preds if p["contrast"] == name])
            }
            for name in sorted({r["contrast"] for r in rows if r.get("contrast")})
        }
        variants = sorted({(r["contrast"], r["variant"]) for r in rows if r.get("variant")})
        res["policy_variants"] = [
            {"contrast": contrast, "variant": variant, "guard": guard, **detection(selected)}
            for contrast, variant in variants
            for guard, preds in preds_by_guard.items()
            if (selected := [p for p in preds
                             if p["contrast"] == contrast and p["variant"] == variant])
        ]
    res["meta"] = {"kind": "fixed", "split": a.split, "backend": llm.backend, "model": llm.model,
                   "n": len(rows), "groups": len({r["group_id"] for r in rows}),
                   "n_boot": cfg.get("n_bootstrap", 1000), "target_fpr": cfg.get("target_fpr", 0.10),
                   "data": a.data[0] if len(a.data) == 1 else a.data, "guards": list(preds_by_guard)}
    res["meta"].update(frozen_status())
    res["case_types"] = [c for c in CASE_TYPES if any(r["case_type"] == c for r in rows)]
    (out_dir / "results.json").write_text(json.dumps(res, indent=2))
    (out_dir / "report.md").write_text(render_fixed(res))
    print(f"\nWrote {out_dir}/report.md")


if __name__ == "__main__":
    main()
