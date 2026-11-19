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
from .guards import GUARD_CONDITIONS, run_guard
from .llm import llm_from_config
from .metrics import (bootstrap_ci, by_case_type, cost, detection, group_consistency, miss_at_fpr,
                      paired_difference_ci, role_metrics)
from .report import render_fixed


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
    return {"guards": out, "comparisons": comparisons}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="configs/default.yaml")
    ap.add_argument("--data", required=True)
    ap.add_argument("--split", default="dev", choices=["dev", "test", "all"])
    ap.add_argument("--guards", default=",".join(GUARD_CONDITIONS))
    ap.add_argument("--name", help="results subfolder (default: <split>-<model>-<timestamp>)")
    ap.add_argument("--mock", action="store_true", help="offline mock backend (pipeline testing only)")
    ap.add_argument("--limit", type=int, help="only the first N examples (quick checks)")
    a = ap.parse_args(argv)

    cfg = yaml.safe_load(open(a.config))
    rows = load_jsonl(a.data)
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
    workers = 1 if llm.backend == "mock" else cfg.get("concurrency", 4)

    preds_by_guard: dict[str, list[dict]] = {}
    all_preds = []
    for g in guards:
        def one(ex, g=g):
            r = run_guard(llm, g, ex, ex["candidate_response"], fail_closed=cfg.get("fail_closed", True))
            return {"id": ex["id"], "group_id": ex["group_id"], "case_type": ex["case_type"],
                    "label": ex["label"], "gold_spans": ex.get("spans", []), **r.to_dict()}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            preds = list(pool.map(one, rows))
        preds_by_guard[g] = preds
        all_preds += preds
        d = detection(preds)
        print(f"{g:12s} miss={d['attack_miss_rate']}  fpr={d['false_positive_rate']}  acc={d['accuracy']}")

    name = a.name or f"{a.split}-{llm.model.replace('/', '_')}-{time.strftime('%Y%m%d-%H%M%S')}"
    out_dir = Path(cfg.get("results_dir", "results")) / name
    out_dir.mkdir(parents=True, exist_ok=True)
    save_jsonl(all_preds, out_dir / "predictions.jsonl")

    res = evaluate(preds_by_guard, cfg.get("n_bootstrap", 1000), cfg.get("target_fpr", 0.10))
    res["meta"] = {"kind": "fixed", "split": a.split, "backend": llm.backend, "model": llm.model,
                   "n": len(rows), "groups": len({r["group_id"] for r in rows}),
                   "n_boot": cfg.get("n_bootstrap", 1000), "target_fpr": cfg.get("target_fpr", 0.10),
                   "data": a.data, "guards": guards}
    res["case_types"] = [c for c in CASE_TYPES if any(r["case_type"] == c for r in rows)]
    (out_dir / "results.json").write_text(json.dumps(res, indent=2))
    (out_dir / "report.md").write_text(render_fixed(res))
    print(f"\nWrote {out_dir}/report.md")


if __name__ == "__main__":
    main()
