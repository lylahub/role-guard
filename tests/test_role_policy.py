import json
from pathlib import Path

import yaml

from rguard import run_guards
from rguard.data import load_jsonl, validate
from rguard.guards import GuardResult
from rguard.role_policy import apply_policy, decide


ROOT = Path(__file__).resolve().parents[1]
CONTRAST = ROOT / "data" / "role_policy_examples.jsonl"
MAIN = ROOT / "data" / "examples.jsonl"


def test_policy_uses_role_and_authorization():
    text = "Close the cabinet."
    span = {"text": text, "role": "adoption", "user_authorized": False,
            "harmful_if_followed": False}
    model = GuardResult("role_aware", "allow", 10, spans=[span])
    assert apply_policy(model, text).decision == "block"
    assert model.decision == "allow"
    assert decide([{**span, "role": "reporting"}], text)[0] == "allow"
    assert decide([{**span, "user_authorized": True}], text)[0] == "allow"
    invalid = GuardResult("role_aware", "allow", 10, spans=[{**span, "user_authorized": "false"}])
    assert apply_policy(invalid, text).decision == "block"
    assert apply_policy(invalid, text, fail_closed=False).decision == "allow"


def test_combined_batch_reuses_model_call(tmp_path, monkeypatch):
    original = run_guards.run_guard
    calls = []

    def counted(llm, condition, ex, response, **kwargs):
        calls.append((condition, ex["id"]))
        return original(llm, condition, ex, response, **kwargs)

    monkeypatch.setattr(run_guards, "run_guard", counted)
    config = yaml.safe_load((ROOT / "configs" / "default.yaml").read_text())
    config.update(results_dir=str(tmp_path), cache_dir=None, n_bootstrap=10)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config))
    rows = load_jsonl(MAIN) + load_jsonl(CONTRAST)
    assert validate(rows) == ([], [])
    run_guards.main(["--config", str(config_path), "--data", str(MAIN), "--data", str(CONTRAST),
                     "--split", "test", "--mock", "--guards", "task_alignment,role_aware",
                     "--compare-role-policy", "--name", "paired"])
    expected = {(guard, r["id"]) for guard in ("task_alignment", "role_aware")
                for r in rows if r["split"] == "test"}
    assert set(calls) == expected and len(calls) == len(expected)
    result = json.loads((tmp_path / "paired" / "results.json").read_text())
    assert set(result["guards"]) == {"task_alignment", "role_aware", "role_aware_policy"}
    assert "task_alignment" in result["comparisons"]
    oracle = result["policy_diagnostics"]["oracle_evidence"]
    assert oracle["oracle_correct"] == oracle["n"] == 9
    assert set(result["policy_slices"]) == {
        "mixed_roles", "authorization_flip", "role_flip", "suggestion_wording",
    }
    variants = result["policy_variants"]
    assert {(m["variant"], m["guard"], m["n"]) for m in variants} == {
        (variant, guard, 1)
        for variant in ("neutral_report", "direct_recommendation", "hedged_recommendation")
        for guard in result["guards"]
    }
    assert "hedged_recommendation" in (tmp_path / "paired" / "report.md").read_text()
