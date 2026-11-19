"""Draft candidate responses with an LLM so humans only need to REVIEW, not write from scratch.

Input: a JSONL of sources, one per line:
  {"source_id": "src07", "group_id": "g07", "source_type": "email", "source_content": "...",
   "attack_template": "T-hidden-note", "attack_goal": "...",
   "variants": [{"suffix": "a", "user_request": "Summarize this email.", "case_type": "attack_adoption"},
                {"suffix": "b", "user_request": "Is this phishing?", "case_type": "legit_analysis"}]}

Output: examples in the dataset schema with status "draft-needs-review". A person must check every
draft (is the response realistic? is the label right? are spans exact?) and set status to "reviewed"
before it goes into data/examples.jsonl.

  python -m rguard.draft data/sources_todo.jsonl data/drafts/batch1.jsonl --config configs/default.yaml
"""
from __future__ import annotations

import argparse

import yaml

from . import CASE_TYPES, ROLES
from .data import EXPECTED_LABEL, load_jsonl, save_jsonl
from .llm import llm_from_config
from .prompts import draft_prompt


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sources")
    ap.add_argument("out")
    ap.add_argument("--config", default="configs/default.yaml")
    ap.add_argument("--mock", action="store_true", help="use the offline mock backend")
    a = ap.parse_args(argv)
    cfg = yaml.safe_load(open(a.config))
    llm = llm_from_config(cfg, "drafter", mock=a.mock)

    out = []
    for src in load_jsonl(a.sources):
        for v in src["variants"]:
            if v["case_type"] not in CASE_TYPES:
                raise SystemExit(f"{src['source_id']}: bad case_type {v['case_type']!r}")
            system, user = draft_prompt(src, v["user_request"], v["case_type"])
            res, _ = llm.complete_json(system, user, task="draft")
            resp = str(res.get("candidate_response", "")).strip()
            spans = [{"text": s["text"], "role": s["role"]} for s in res.get("instruction_spans") or []
                     if s.get("role") in ROLES and s.get("text") and s["text"] in resp]
            out.append({
                "id": f"{src['group_id']}-{v['suffix']}", "group_id": src["group_id"],
                "source_id": src["source_id"], "source_type": src["source_type"],
                "attack_template": src.get("attack_template"), "attack_goal": src.get("attack_goal"),
                "source_content": src["source_content"], "user_request": v["user_request"],
                "candidate_response": resp, "case_type": v["case_type"],
                "label": EXPECTED_LABEL[v["case_type"]], "spans": spans,
                "e2e": bool(v.get("e2e", False)), "annotator": "", "status": "draft-needs-review", "notes": "",
            })
    save_jsonl(out, a.out)
    print(f"wrote {len(out)} drafts to {a.out}; review every one before merging")


if __name__ == "__main__":
    main()
