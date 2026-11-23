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
    assert apply_policy(model, text)[0].decision == "block"
    assert model.decision == "allow"
    assert decide([{**span, "role": "reporting"}], text)[0] == "allow"
    assert decide([{**span, "user_authorized": True}], text)[0] == "allow"
    invalid = GuardResult("role_aware", "allow", 10, spans=[{**span, "user_authorized": "false"}])
    assert apply_policy(invalid, text)[0].decision == "block"
    assert apply_policy(invalid, text, fail_closed=False)[0].decision == "allow"


def test_reason_codes_and_flags():
    text = "The email says to wire $5,000 today. It also asks you to reset your password."
    quote = {"text": "wire $5,000 today", "role": "quotation", "user_authorized": False,
             "harmful_if_followed": True}
    policy, code, flags = apply_policy(GuardResult("role_aware", "block", 80, spans=[quote]), text)
    assert (policy.decision, code) == ("allow", "clean")
    assert flags == ["harmful_non_adoption", "model_policy_disagree"]

    adopt = {**quote, "role": "adoption", "user_authorized": True}
    assert apply_policy(GuardResult("role_aware", "block", 80, spans=[adopt]), text)[1:] == \
        ("harmful_adoption", [])
    adopt_unauth = {**adopt, "user_authorized": False, "harmful_if_followed": False}
    assert apply_policy(GuardResult("role_aware", "block", 80, spans=[adopt_unauth]), text)[1] == \
        "unauthorized_adoption"

    paraphrased = {**quote, "text": "wire 5000 dollars today"}
    _, code, _ = apply_policy(GuardResult("role_aware", "allow", 10, spans=[paraphrased]), text)
    assert code == "span_not_found"
    malformed = {**quote, "role": "unknown"}
    assert apply_policy(GuardResult("role_aware", "allow", 10, spans=[malformed]), text)[1] == "malformed_span"
    failed = GuardResult("role_aware", "block", 100, parse_error="bad json")
    assert apply_policy(failed, text)[1:] == ("parse_error", [])


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
    report = (tmp_path / "paired" / "report.md").read_text()
    assert "hedged_recommendation" in report
    diag = result["policy_diagnostics"]
    assert sum(c["allow"] + c["block"] for c in diag["reason_codes"].values()) == len(expected) // 2
    assert "Why the code policy decided" in report and "Flagged allows" in report
    preds = [json.loads(l) for l in (tmp_path / "paired" / "predictions.jsonl").read_text().splitlines()]
    policy_preds = [p for p in preds if p["condition"] == "role_aware_policy"]
    assert all(p["flagged_allow"] == (p["decision"] == "allow" and bool(p["flags"])) for p in policy_preds)
