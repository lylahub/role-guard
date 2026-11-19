"""Offline tests (mock backend). Run: pytest -q"""
import json
from pathlib import Path

import pytest
import yaml

from rguard.agreement import cohen_kappa
from rguard.data import assign_splits, components, load_jsonl, validate
from rguard.guards import GUARD_CONDITIONS, run_guard
from rguard.llm import LLM, extract_json
from rguard.metrics import detection, group_consistency, miss_at_fpr, role_metrics
from rguard.prompts import guard_prompt

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "data" / "seed_examples.jsonl"


@pytest.fixture(scope="module")
def rows():
    return load_jsonl(SEED)


def test_seed_is_valid(rows):
    errors, _ = validate(rows)
    assert errors == []


def test_splits_never_share_source_or_template(rows):
    rows = assign_splits([dict(r) for r in rows], 0.3, seed=1)
    for key in ("source_id", "attack_template", "group_id"):
        seen = {}
        for r in rows:
            if r.get(key) is None:
                continue
            assert seen.setdefault(r[key], r["split"]) == r["split"], f"{key} {r[key]} leaks across splits"


def test_text_only_guard_never_sees_source(rows):
    ex = rows[0]
    _, user = guard_prompt("text_only", ex, ex["candidate_response"])
    assert ex["source_content"] not in user and ex["user_request"] not in user


def test_context_and_role_aware_get_identical_inputs(rows):
    ex = rows[0]
    _, u1 = guard_prompt("context", ex, ex["candidate_response"])
    _, u2 = guard_prompt("role_aware", ex, ex["candidate_response"])
    _, u3 = guard_prompt("context_cot", ex, ex["candidate_response"])
    assert u1 == u2 == u3


@pytest.mark.parametrize("cond", GUARD_CONDITIONS)
def test_guards_run_on_mock(rows, cond, tmp_path):
    llm = LLM("mock", "mock", cache_dir=str(tmp_path))
    r = run_guard(llm, cond, rows[0], rows[0]["candidate_response"])
    assert r.decision in ("allow", "block") and 0 <= r.risk <= 100 and not r.parse_error


def test_extract_json_handles_fences_and_prose():
    assert extract_json('Sure!\n```json\n{"a": {"b": "}"}}\n```') == {"a": {"b": "}"}}
    assert extract_json('blah {"decision": "block", "risk": 90} trailing') == {"decision": "block", "risk": 90}


def test_metrics_basic():
    preds = [
        {"group_id": "g", "label": "block", "decision": "block", "risk": 90, "case_type": "attack_adoption"},
        {"group_id": "g", "label": "allow", "decision": "block", "risk": 60, "case_type": "legit_quotation"},
        {"group_id": "h", "label": "allow", "decision": "allow", "risk": 10, "case_type": "legit_analysis"},
        {"group_id": "h", "label": "block", "decision": "allow", "risk": 40, "case_type": "attack_adoption"},
    ]
    d = detection(preds)
    assert d["attack_miss_rate"] == 0.5 and d["false_positive_rate"] == 0.5
    m = miss_at_fpr(preds, 0.0)
    assert m["fpr"] == 0.0 and m["miss_rate"] == 0.5     # threshold 61 blocks only the 90
    assert group_consistency(preds)["contrast_correct"] == 0.0


def test_role_metrics_matching():
    preds = [{"gold_spans": [{"text": "you should re-validate at mail-verify.example now", "role": "adoption"}],
              "spans": [{"text": "you should re-validate at mail-verify.example", "role": "adoption"}]}]
    m = role_metrics(preds)
    assert m["span_recall"] == 1.0 and m["role_accuracy_on_matched"] == 1.0


def test_kappa():
    assert cohen_kappa(["a", "b", "a", "b"], ["a", "b", "a", "b"]) == 1.0
    assert abs(cohen_kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"])) < 1e-9


def test_end_to_end_cli_mock(tmp_path):
    from rguard import run_e2e, run_guards
    cfg = yaml.safe_load(open(ROOT / "configs" / "default.yaml"))
    cfg.update(results_dir=str(tmp_path / "res"), cache_dir=str(tmp_path / "cache"), n_bootstrap=50)
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg))
    run_guards.main(["--config", str(cfg_path), "--data", str(SEED), "--split", "all", "--mock", "--name", "t"])
    res = json.loads((tmp_path / "res" / "t" / "results.json").read_text())
    assert set(res["guards"]) == set(GUARD_CONDITIONS)
    run_e2e.main(["--config", str(cfg_path), "--data", str(SEED), "--split", "all", "--mock", "--name", "e"])
    assert (tmp_path / "res" / "e" / "report.md").exists()
