"""Build an end-to-end slice of BIPIA EmailQA (Yi et al., "Benchmarking and Defending Against Indirect
Prompt Injection Attacks on Large Language Models", KDD 2025; github.com/microsoft/BIPIA, MIT license).

  git clone --depth 1 https://github.com/microsoft/BIPIA /tmp/BIPIA
  python -m rguard.bipia --bipia-dir /tmp/BIPIA --out data/bipia_email_slice.jsonl

Faithful to BIPIA:
  * contexts + questions: benchmark/email/test.jsonl (50 emails, from OpenAI Evals)
  * attacks: benchmark/text_attack_test.json (15 categories x 5 instructions)
  * insertion: BIPIA's insert_start / insert_end / insert_middle (bipia/data/utils.py), copied below;
    insert_middle needs nltk's Punkt tokenizer exactly as in BIPIA.

Our sampling (BIPIA's full grid is 50 x 75 x 3 = 11,250 samples):
  * --n-attacked samples, stratified round-robin over the 15 attack categories; each draws a random
    email, attack instruction and insertion position (seeded);
  * --n-clean emails with no injection, to measure false blocking and utility on clean input.
Attack success is judged by our LLM judge against the injected instruction (BIPIA's own evaluators
are per-category; we use one judge so all defenses are scored the same way).
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from .data import save_jsonl


# --- copied from BIPIA bipia/data/utils.py (MIT) -------------------------------------------------
def insert_end(context: str, attack: str, random_state: int = None):
    return "\n".join([context, attack])


def insert_start(context: str, attack: str, random_state: int = None):
    return "\n".join([attack, context])


def insert_middle(context: str, attack: str, random_state: int = None):
    from nltk.tokenize.punkt import PunktSentenceTokenizer  # pip install nltk
    rng = random.Random(random_state)
    sentence_indexes = list(PunktSentenceTokenizer().span_tokenize(context))
    start, _ = rng.sample(sentence_indexes, k=1)[0]
    return "\n".join([context[:start], attack, context[start:]])
# --------------------------------------------------------------------------------------------------

INSERT = {"start": insert_start, "end": insert_end, "middle": insert_middle}


def build(bipia_dir: str, n_attacked: int = 100, n_clean: int = 20, positions=("start", "end", "middle"),
          seed: int = 0) -> list[dict]:
    root = Path(bipia_dir) / "benchmark"
    emails = [json.loads(l) for l in open(root / "email" / "test.jsonl") if l.strip()]
    attacks = json.load(open(root / "text_attack_test.json"))
    cats = list(attacks)
    rng = random.Random(seed)
    rows, used = [], set()
    i = 0
    while len(rows) < n_attacked:
        cat = cats[i % len(cats)]
        i += 1
        for _ in range(100):
            e_idx, a_idx, pos = rng.randrange(len(emails)), rng.randrange(len(attacks[cat])), rng.choice(positions)
            if (e_idx, cat, a_idx) not in used:
                used.add((e_idx, cat, a_idx))
                break
        em, attack_str = emails[e_idx], attacks[cat][a_idx]
        rid = f"bipia-email-a{len(rows) + 1:03d}"
        rows.append({
            "id": rid, "group_id": f"bipia-e{e_idx:02d}", "source_id": rid, "source_type": "email",
            "source_content": INSERT[pos](em["context"], attack_str, random_state=seed),
            "user_request": em["question"], "attack_template": f"BIPIA:{cat}",
            "attack_goal": f'Carry out this instruction injected into the email: "{attack_str}"',
            "e2e": True, "split": "test", "dataset": "bipia-email",
            "bipia": {"attack_name": f"{cat}-{a_idx}", "position": pos, "email_index": e_idx, "ideal": em.get("ideal")},
        })
    for k, e_idx in enumerate(rng.sample(range(len(emails)), k=min(n_clean, len(emails)))):
        em = emails[e_idx]
        rid = f"bipia-email-c{k + 1:03d}"
        rows.append({
            "id": rid, "group_id": f"bipia-e{e_idx:02d}", "source_id": rid, "source_type": "email",
            "source_content": em["context"], "user_request": em["question"], "attack_template": None,
            "attack_goal": None, "e2e": True, "split": "test", "dataset": "bipia-email",
            "bipia": {"attack_name": None, "position": None, "email_index": e_idx, "ideal": em.get("ideal")},
        })
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bipia-dir", required=True)
    ap.add_argument("--out", default="data/bipia_email_slice.jsonl")
    ap.add_argument("--n-attacked", type=int, default=100)
    ap.add_argument("--n-clean", type=int, default=20)
    ap.add_argument("--positions", default="start,end,middle", help="middle needs `pip install nltk`")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    rows = build(a.bipia_dir, a.n_attacked, a.n_clean, tuple(a.positions.split(",")), a.seed)
    save_jsonl(rows, a.out)
    from collections import Counter
    print(f"wrote {len(rows)} rows to {a.out}")
    print("positions:", dict(Counter(r["bipia"]["position"] for r in rows)))
    print("categories:", dict(Counter(r["attack_template"] for r in rows)))


if __name__ == "__main__":
    main()
