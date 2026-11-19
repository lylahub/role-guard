"""Stage 2: end-to-end evaluation on the `e2e: true` subset.

For each e2e example the undefended assistant generates a response from (user request, source);
an LLM judge labels whether the attack was adopted and whether the task was completed; then every
guard decides whether to show the response. "none" = no guard (the undefended baseline).

  python -m rguard.run_e2e --data data/examples.jsonl --split test
  python -m rguard.run_e2e --data data/seed_examples.jsonl --split all --mock

Writes results/<name>/{e2e_rows.jsonl, results.json, report.md}.
Hand-check at least 20 judge labels in e2e_rows.jsonl and report the judge's agreement with you.
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from .data import filter_split, load_jsonl, save_jsonl
from .guards import GUARD_CONDITIONS, run_assistant, run_guard, run_judge
from .llm import llm_from_config
from .metrics import bootstrap_ci, e2e
from .report import render_e2e


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="configs/default.yaml")
    ap.add_argument("--data", required=True)
    ap.add_argument("--split", default="test", choices=["dev", "test", "all"])
    ap.add_argument("--guards", default=",".join(GUARD_CONDITIONS))
    ap.add_argument("--name")
    ap.add_argument("--mock", action="store_true")
    a = ap.parse_args(argv)

    cfg = yaml.safe_load(open(a.config))
    rows = [r for r in filter_split(load_jsonl(a.data), a.split) if r.get("e2e")]
    # One run per distinct (source, request): candidate_response in the file is ignored here.
    seen, items = set(), []
    for r in rows:
        k = (r["source_id"], r["user_request"])
        if k not in seen:
            seen.add(k)
            items.append(r)
    if not items:
        raise SystemExit("No e2e examples in this split (set `\"e2e\": true` on some examples).")

    assistant = llm_from_config(cfg, "assistant", mock=a.mock)
    judge = llm_from_config(cfg, "judge", mock=a.mock)
    guard_llm = llm_from_config(cfg, "guard", mock=a.mock)
    guards = [g.strip() for g in a.guards.split(",") if g.strip()]
    workers = 1 if guard_llm.backend == "mock" else cfg.get("concurrency", 4)

    def gen(ex):
        resp, acost = run_assistant(assistant, ex)
        j = run_judge(judge, ex, resp)
        decisions = {"none": {"decision": "allow", "risk": 0}}
        for g in guards:
            gr = run_guard(guard_llm, g, ex, resp, fail_closed=cfg.get("fail_closed", True))
            decisions[g] = {"decision": gr.decision, "risk": gr.risk, "spans": gr.spans,
                            "input_tokens": gr.input_tokens, "output_tokens": gr.output_tokens,
                            "latency_s": gr.latency_s, "parse_error": gr.parse_error}
        return {"id": ex["id"], "group_id": ex["group_id"], "user_request": ex["user_request"],
                "response": resp, "assistant_cost": acost, **j, "guards": decisions}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        out_rows = list(pool.map(gen, items))

    n_boot = cfg.get("n_bootstrap", 1000)
    res = {"guards": {}}
    for g in ["none"] + guards:
        flat = [{"group_id": r["group_id"], "attack_adopted": r["attack_adopted"],
                 "task_completed": r["task_completed"], "decision": r["guards"][g]["decision"]} for r in out_rows]
        res["guards"][g] = {"e2e": e2e(flat),
                            "ci": {k: bootstrap_ci(flat, e2e, k, n_boot)
                                   for k in ("attack_success_rate", "useful_completion_rate")}}
        m = res["guards"][g]["e2e"]
        print(f"{g:12s} ASR={m.get('attack_success_rate')}  useful={m.get('useful_completion_rate')}  "
              f"blocked={m.get('blocked_rate')}")
    res["meta"] = {"kind": "e2e", "split": a.split, "backend": guard_llm.backend, "model": guard_llm.model,
                   "assistant_model": assistant.model, "judge_model": judge.model, "n": len(items),
                   "judge_errors": sum(r["attack_adopted"] is None for r in out_rows), "data": a.data}

    name = a.name or f"e2e-{a.split}-{guard_llm.model.replace('/', '_')}-{time.strftime('%Y%m%d-%H%M%S')}"
    out_dir = Path(cfg.get("results_dir", "results")) / name
    out_dir.mkdir(parents=True, exist_ok=True)
    save_jsonl(out_rows, out_dir / "e2e_rows.jsonl")
    (out_dir / "results.json").write_text(json.dumps(res, indent=2))
    (out_dir / "report.md").write_text(render_e2e(res))
    print(f"\nWrote {out_dir}/report.md")


if __name__ == "__main__":
    main()
