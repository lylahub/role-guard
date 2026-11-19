"""Inter-annotator agreement (Cohen's kappa) for security labels and span roles.

Each annotator file is JSONL with one line per example they double-annotated:
  {"id": "g01-a", "label": "block", "roles": ["adoption"]}
`roles` lists one role per span IN THE ORDER of the `spans` field of the gold file (the data lead
fixes span boundaries; annotators only assign roles and labels).

  python -m rguard.agreement annotations/p2.jsonl annotations/p3.jsonl [--disagreements out.jsonl]
"""
from __future__ import annotations

import argparse
from collections import Counter

from .data import load_jsonl, save_jsonl


def cohen_kappa(a: list, b: list) -> float:
    assert len(a) == len(b) and a, "need two equal-length, non-empty label lists"
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ann_a")
    ap.add_argument("ann_b")
    ap.add_argument("--disagreements", help="write disagreeing items here for the adjudication meeting")
    a = ap.parse_args(argv)
    A = {r["id"]: r for r in load_jsonl(a.ann_a)}
    B = {r["id"]: r for r in load_jsonl(a.ann_b)}
    common = sorted(set(A) & set(B))
    if not common:
        raise SystemExit("no overlapping ids")

    la = [A[i]["label"] for i in common]
    lb = [B[i]["label"] for i in common]
    ra, rb, dis = [], [], []
    for i in common:
        x, y = A[i].get("roles", []), B[i].get("roles", [])
        if len(x) != len(y):
            print(f"WARN {i}: different number of span roles ({len(x)} vs {len(y)}); skipped for role kappa")
        else:
            ra += x
            rb += y
        if A[i]["label"] != B[i]["label"] or x != y:
            dis.append({"id": i, "a": A[i], "b": B[i]})

    print(f"overlap: {len(common)} examples")
    print(f"label agreement: {sum(p == q for p, q in zip(la, lb)) / len(la):.3f}   kappa: {cohen_kappa(la, lb):.3f}")
    if ra:
        print(f"role agreement:  {sum(p == q for p, q in zip(ra, rb)) / len(ra):.3f}   kappa: {cohen_kappa(ra, rb):.3f} "
              f"({len(ra)} spans)")
    print(f"items to adjudicate: {len(dis)}")
    if a.disagreements:
        save_jsonl(dis, a.disagreements)


if __name__ == "__main__":
    main()
