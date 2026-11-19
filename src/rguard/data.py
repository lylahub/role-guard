"""Dataset loading, validation and leakage-free dev/test splitting.

Run as a CLI:
  python -m rguard.data validate data/examples.jsonl
  python -m rguard.data split data/examples.jsonl --dev-frac 0.3 --seed 0      # writes the `split` field in place
  python -m rguard.data stats data/examples.jsonl
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

from . import CASE_TYPES, LABELS, ROLES

REQUIRED = ("id", "group_id", "source_id", "source_type", "source_content", "user_request",
            "candidate_response", "case_type", "label", "spans")
EXPECTED_LABEL = {
    "legit_quotation": "allow", "legit_analysis": "allow", "benign_adoption": "allow",
    "attack_adoption": "block", "disguised_attack": "block",
}


def load_jsonl(path: str | Path) -> list[dict]:
    rows = []
    with open(path) as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{i}: invalid JSON ({e})") from e
    return rows


def save_jsonl(rows: list[dict], path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def validate(rows: list[dict]) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). Errors should block a run; warnings need a human look."""
    errors, warnings = [], []
    ids = Counter(r.get("id") for r in rows)
    for dup, n in ids.items():
        if n > 1:
            errors.append(f"duplicate id {dup!r} ({n}x)")
    for r in rows:
        rid = r.get("id", "?")
        for k in REQUIRED:
            if k not in r:
                errors.append(f"{rid}: missing field {k!r}")
        if r.get("case_type") not in CASE_TYPES:
            errors.append(f"{rid}: case_type {r.get('case_type')!r} not in {CASE_TYPES}")
        if r.get("label") not in LABELS:
            errors.append(f"{rid}: label {r.get('label')!r} not in {LABELS}")
        exp = EXPECTED_LABEL.get(r.get("case_type"))
        if exp and r.get("label") != exp and not r.get("label_override_reason"):
            warnings.append(f"{rid}: label {r.get('label')} unusual for {r.get('case_type')} "
                            f"(add `label_override_reason` if intended)")
        for s in r.get("spans") or []:
            if s.get("role") not in ROLES:
                errors.append(f"{rid}: span role {s.get('role')!r} not in {ROLES}")
            if s.get("text") and s["text"] not in r.get("candidate_response", ""):
                warnings.append(f"{rid}: span text not found verbatim in candidate_response: {s['text'][:60]!r}")
        if r.get("e2e") and r.get("attack_template") and not r.get("attack_goal"):
            errors.append(f"{rid}: e2e example with an attack needs `attack_goal`")
        if r.get("split") not in (None, "dev", "test"):
            errors.append(f"{rid}: split must be dev/test, got {r.get('split')!r}")
    # A source must have the same content everywhere it is used.
    by_src = defaultdict(set)
    for r in rows:
        by_src[r.get("source_id")].add(r.get("source_content"))
    for sid, contents in by_src.items():
        if len(contents) > 1:
            warnings.append(f"source_id {sid!r} has {len(contents)} different source_content texts")
    return errors, warnings


def components(rows: list[dict]) -> list[list[dict]]:
    """Group examples that must stay in the same split: shared group, source document or attack template."""
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    for r in rows:
        node = f"g:{r['group_id']}"
        union(node, f"s:{r['source_id']}")
        if r.get("attack_template"):
            union(node, f"t:{r['attack_template']}")
    comps = defaultdict(list)
    for r in rows:
        comps[find(f"g:{r['group_id']}")].append(r)
    return list(comps.values())


def assign_splits(rows: list[dict], dev_frac: float = 0.3, seed: int = 0) -> list[dict]:
    comps = components(rows)
    biggest = max(len(c) for c in comps)
    if biggest > 0.5 * len(rows):
        print(f"WARNING: one leakage component holds {biggest}/{len(rows)} examples. Attack templates are "
              f"probably reused across too many sources; give dev and test their own templates.", file=sys.stderr)
    rng = random.Random(seed)
    rng.shuffle(comps)
    n_dev, target = 0, dev_frac * len(rows)
    for c in comps:
        split = "dev" if n_dev < target else "test"
        if split == "dev":
            n_dev += len(c)
        for r in c:
            r["split"] = split
    return rows


def filter_split(rows: list[dict], split: str | None) -> list[dict]:
    if split in (None, "all"):
        return rows
    missing = [r["id"] for r in rows if "split" not in r]
    if missing:
        raise SystemExit(f"{len(missing)} examples have no split; run `python -m rguard.data split ...` first.")
    return [r for r in rows if r["split"] == split]


def stats(rows: list[dict]) -> str:
    out = [f"examples: {len(rows)}   groups: {len({r['group_id'] for r in rows})}   "
           f"sources: {len({r['source_id'] for r in rows})}   e2e: {sum(bool(r.get('e2e')) for r in rows)}"]
    for key in ("split", "case_type", "label", "source_type", "attack_template"):
        c = Counter(str(r.get(key)) for r in rows)
        out.append(f"{key:16s} " + ", ".join(f"{k}={v}" for k, v in sorted(c.items())))
    roles = Counter(s["role"] for r in rows for s in r.get("spans") or [])
    out.append(f"{'span roles':16s} " + ", ".join(f"{k}={v}" for k, v in sorted(roles.items())))
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["validate", "split", "stats"])
    ap.add_argument("path")
    ap.add_argument("--dev-frac", type=float, default=0.3)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    rows = load_jsonl(a.path)
    if a.command == "validate":
        errs, warns = validate(rows)
        for w in warns:
            print("WARN ", w)
        for e in errs:
            print("ERROR", e)
        print(f"{len(rows)} examples, {len(errs)} errors, {len(warns)} warnings")
        sys.exit(1 if errs else 0)
    elif a.command == "split":
        assign_splits(rows, a.dev_frac, a.seed)
        save_jsonl(rows, a.path)
        print(stats(rows))
    else:
        print(stats(rows))


if __name__ == "__main__":
    main()
