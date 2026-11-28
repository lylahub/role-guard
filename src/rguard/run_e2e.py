"""Stage 2: end-to-end evaluation on the `e2e: true` subset (our data) or on an external slice (BIPIA).

Two families of defense are compared on the same examples:

* OUTPUT guards (text_only, context, context_cot, role_aware, piguard, piguard_source): the undefended
  assistant answers from (user request, source); an LLM judge labels whether the attack was adopted and
  whether the task was completed; each guard then decides whether to show that response.
  "none" = no guard. role_aware and task_alignment also get their code-policy rows
  (role_aware_policy; task_alignment_policy and endorsement_policy), derived from the same guard call.
* INPUT defense (promptarmor): the source is first sanitized by PromptArmor (detect + remove the injected
  text), then the assistant answers from the sanitized source and the judge labels THAT response. Its
  response is always shown; its cost is that legitimate tasks about the injected text (quote it, analyze
  it) may no longer be possible.

  python -m rguard.run_e2e --data data/examples.jsonl --split test
  python -m rguard.run_e2e --data data/bipia_email_slice.jsonl --split all --name bipia-email
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
from .guards import DEFAULT_GUARDS, JUDGE_ERRORS, run_assistant, run_guard, run_judge
from .role_policy import apply_policy, policies_for
from .llm import llm_from_config
from .metrics import bootstrap_ci, e2e
from .promptarmor import promptarmor
from .prompts import frozen_status
from .report import render_e2e


def detection_stats(rows: list[dict]) -> dict:
    """PromptArmor detection quality against whether the source actually contains an attack."""
    att = [r for r in rows if r["has_attack"]]
    clean = [r for r in rows if not r["has_attack"]]
    return {
        "attacked": len(att), "clean": len(clean),
        "false_negative_rate": (sum(not r["promptarmor"]["detected"] for r in att) / len(att)) if att else None,
        "false_positive_rate": (sum(r["promptarmor"]["detected"] for r in clean) / len(clean)) if clean else None,
        "removal_failed": sum(r["promptarmor"]["removal_failed"] for r in rows),
    }


CI_KEYS = ("attack_success_rate", "assistant_adoption_rate", "adopted_let_through_rate",
           "useful_completion_rate_clean", "useful_completion_rate_attacked")


def judge_error_counts(codes: list) -> dict:
    """Judge failures by type; every row with an error is excluded from all rates."""
    counts = {c: sum(code == c for code in codes) for c in JUDGE_ERRORS}
    return {"total": sum(counts.values()), **counts}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="configs/default.yaml")
    ap.add_argument("--data", required=True)
    ap.add_argument("--split", default="test", choices=["dev", "test", "all"])
    ap.add_argument("--guards", default=DEFAULT_GUARDS, help="output guards (comma list)")
    ap.add_argument("--no-promptarmor", action="store_true", help="skip the PromptArmor input defense")
    ap.add_argument("--limit", type=int)
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
    if a.limit:
        items = items[: a.limit]
    if not items:
        raise SystemExit("No e2e examples in this split (set `\"e2e\": true` on some examples).")

    assistant = llm_from_config(cfg, "assistant", mock=a.mock)
    judge = llm_from_config(cfg, "judge", mock=a.mock)
    guard_llm = llm_from_config(cfg, "guard", mock=a.mock)
    guards = [g.strip() for g in a.guards.split(",") if g.strip()]
    use_pa = not a.no_promptarmor
    workers = 1 if guard_llm.backend == "mock" else cfg.get("concurrency", 4)
    pg_cfg = cfg.get("piguard", {})

    def gen(ex):
        resp, acost = run_assistant(assistant, ex)
        j = run_judge(judge, ex, resp)
        decisions = {"none": {"decision": "allow", "risk": 0}}
        for g in guards:
            gr = run_guard(guard_llm, g, ex, resp, fail_closed=cfg.get("fail_closed", True), pg_cfg=pg_cfg)
            decisions[g] = {"decision": gr.decision, "risk": gr.risk, "spans": gr.spans,
                            "input_tokens": gr.input_tokens, "output_tokens": gr.output_tokens,
                            "latency_s": gr.latency_s, "parse_error": gr.parse_error}
            for name in policies_for(g):   # code policies reuse this guard's call: no extra API cost
                policy, code, flags = apply_policy(gr, resp, cfg.get("fail_closed", True), name)
                decisions[name] = {"decision": policy.decision, "risk": policy.risk,
                                   "reason_code": code, "flags": flags}
        row = {"id": ex["id"], "group_id": ex["group_id"], "user_request": ex["user_request"],
               "has_attack": bool(ex.get("attack_goal")), "response": resp, "assistant_cost": acost,
               **j, "guards": decisions}
        if use_pa:
            pa = promptarmor(guard_llm, ex["source_content"])
            ex2 = {**ex, "source_content": pa.sanitized}
            resp2, _ = run_assistant(assistant, ex2)
            j2 = run_judge(judge, ex, resp2)   # judged against the ORIGINAL source and attack goal
            row["promptarmor"] = {**pa.to_dict(), "response": resp2, **{f"judge_{k}": v for k, v in j2.items()}}
        return row

    with ThreadPoolExecutor(max_workers=workers) as pool:
        out_rows = list(pool.map(gen, items))

    n_boot = cfg.get("n_bootstrap", 1000)
    res = {"guards": {}}

    def summarize(name, flat):
        res["guards"][name] = {"e2e": e2e(flat), "ci": {k: bootstrap_ci(flat, e2e, k, n_boot)
                                                         for k in CI_KEYS}}
        m = res["guards"][name]["e2e"]
        print(f"{name:15s} ASR={m.get('attack_success_rate')}  adopted={m.get('assistant_adoption_rate')}  "
              f"useful={m.get('useful_completion_rate')}  blocked={m.get('blocked_rate')}")

    decided = ["none"] + [name for g in guards for name in [g, *policies_for(g)]]
    for g in decided:
        summarize(g, [{"group_id": r["group_id"], "has_attack": r["has_attack"], "attack_adopted": r["attack_adopted"],
                       "task_completed": r["task_completed"], "decision": r["guards"][g]["decision"]} for r in out_rows])
    if use_pa:
        summarize("promptarmor (input)", [{"group_id": r["group_id"], "has_attack": r["has_attack"],
                                           "attack_adopted": r["promptarmor"]["judge_attack_adopted"],
                                           "task_completed": r["promptarmor"]["judge_task_completed"], "decision": "allow"}
                                          for r in out_rows])
        res["promptarmor_detection"] = detection_stats(out_rows)
    res["meta"] = {"kind": "e2e", "split": a.split, "backend": guard_llm.backend, "model": guard_llm.model,
                   "assistant_model": assistant.model, "judge_model": judge.model, "n": len(items),
                   "judge_errors": judge_error_counts([r.get("judge_error") for r in out_rows]),
                   "data": a.data}
    res["meta"].update(frozen_status())
    if use_pa:
        res["meta"]["promptarmor_judge_errors"] = judge_error_counts(
            [r["promptarmor"].get("judge_judge_error") for r in out_rows])

    name = a.name or f"e2e-{a.split}-{guard_llm.model.replace('/', '_')}-{time.strftime('%Y%m%d-%H%M%S')}"
    out_dir = Path(cfg.get("results_dir", "results")) / name
    out_dir.mkdir(parents=True, exist_ok=True)
    save_jsonl(out_rows, out_dir / "e2e_rows.jsonl")
    (out_dir / "results.json").write_text(json.dumps(res, indent=2))
    (out_dir / "report.md").write_text(render_e2e(res))
    print(f"\nWrote {out_dir}/report.md")


if __name__ == "__main__":
    main()
