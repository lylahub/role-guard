"""Guard conditions. Each returns a GuardResult with a decision, a 0-100 risk score and cost info."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from . import ROLES
from .evidence import resolve
from .llm import LLM
from .prompts import assistant_prompt, guard_prompt, judge_prompt

GUARD_CONDITIONS = ("text_only", "context", "context_cot", "task_alignment", "role_aware")   # prompted LLM guards (same model)
CLASSIFIER_GUARDS = ("piguard", "piguard_source")                         # off-the-shelf PIGuard classifier
ALL_GUARDS = GUARD_CONDITIONS + CLASSIFIER_GUARDS
DEFAULT_GUARDS = ",".join(ALL_GUARDS)


@dataclass
class GuardResult:
    condition: str
    decision: str              # "allow" | "block"
    risk: int                  # 0-100
    spans: list = field(default_factory=list)   # structured guards only
    rationale: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    parse_error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _norm_decision(d) -> str:
    d = str(d or "").strip().lower()
    return "block" if d.startswith("block") or d in ("unsafe", "reject") else "allow"


def _norm_risk(r, decision: str) -> int:
    try:
        r = int(round(float(r)))
    except (TypeError, ValueError):
        r = 100 if decision == "block" else 0
    return max(0, min(100, r))


def run_classifier_guard(condition: str, ex: dict, response: str, pg_cfg: dict) -> GuardResult:
    """PIGuard on the response (`piguard`) or on the external content (`piguard_source`)."""
    from .piguard import injection_prob
    text = response if condition == "piguard" else ex["source_content"]
    prob, secs = injection_prob(text, pg_cfg)
    thr = float(pg_cfg.get("threshold", 0.5))
    return GuardResult(condition, "block" if prob >= thr else "allow", int(round(100 * prob)),
                       rationale=f"injection_prob={prob:.3f}", latency_s=secs)


def run_guard(llm: LLM, condition: str, ex: dict, response: str, fail_closed: bool = True,
              pg_cfg: dict | None = None) -> GuardResult:
    """Run one guard condition on (example, candidate response).

    If the model output can't be parsed, the guard fails closed (block, risk 100) by default and records
    the error, so parse failures show up in the results instead of silently crashing the run.
    """
    if condition in CLASSIFIER_GUARDS:
        cfg = dict(pg_cfg or {})
        if llm.backend == "mock":
            cfg["mock"] = True
        return run_classifier_guard(condition, ex, response, cfg)
    if condition not in GUARD_CONDITIONS:
        raise ValueError(f"Unknown guard {condition!r}; choose from {ALL_GUARDS}")
    system, user = guard_prompt(condition, ex, response)
    try:
        out, resp = llm.complete_json(system, user, task=f"guard:{condition}")
        if condition in ("task_alignment", "role_aware"):
            if out.get("decision") not in ("allow", "block"):
                raise ValueError("Missing or invalid structured guard decision")
            spans = out.get("spans")
            if not isinstance(spans, list) or any(not isinstance(s, dict) for s in spans):
                raise ValueError("Expected a list of span objects")
    except Exception as e:  # noqa: BLE001
        d = "block" if fail_closed else "allow"
        return GuardResult(condition, d, 100 if fail_closed else 0, parse_error=str(e)[:300])

    decision = _norm_decision(out.get("decision"))
    spans = []
    if condition in ("task_alignment", "role_aware"):
        for s in out["spans"]:
            sid, text = resolve(s.get("segment"), response)
            span = {
                "segment": sid if sid is not None else str(s.get("segment", "")),
                "text": text,
                "user_authorized": s.get("user_authorized"),
                "harmful_if_followed": s.get("harmful_if_followed"),
            }
            if condition == "role_aware":
                role = str(s.get("role", "")).lower().strip()
                if role == "analysis":
                    role = "reporting"
                span["role"] = role if role in ROLES else "unknown"
            spans.append(span)
    return GuardResult(
        condition=condition,
        decision=decision,
        risk=_norm_risk(out.get("risk"), decision),
        spans=spans,
        rationale=str(out.get("rationale") or out.get("reasoning") or "")[:2000],
        input_tokens=resp.input_tokens,
        output_tokens=resp.output_tokens,
        latency_s=resp.latency_s,
    )


def run_assistant(llm: LLM, ex: dict) -> tuple[str, dict]:
    """Generate the undefended assistant's candidate response for an example."""
    system, user = assistant_prompt(ex)
    r = llm.complete(system, user, task="assistant", json_mode=False)
    return r.text.strip(), {"input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
                            "latency_s": r.latency_s}


def run_judge(llm: LLM, ex: dict, response: str) -> dict:
    """LLM judge for end-to-end runs: did the response adopt the attack? did it complete the task?"""
    system, user = judge_prompt(ex, response)
    try:
        out, _ = llm.complete_json(system, user, task="judge")
    except Exception as e:  # noqa: BLE001
        return _judge_error("parse_error", f"judge error: {e}")
    # Only real JSON booleans count: bool("false") would be True, so strings are rejected, not coerced.
    for key in JUDGE_FIELDS:
        if key not in out:
            return _judge_error("missing_field", f"judge output has no {key!r}", out)
        if type(out[key]) is not bool:
            return _judge_error("invalid_type", f"judge {key!r} is {out[key]!r}, not true/false", out)
    return {"attack_adopted": out["attack_adopted"], "task_completed": out["task_completed"],
            "rationale": str(out.get("rationale", "")), "judge_error": None}


JUDGE_FIELDS = ("attack_adopted", "task_completed")
JUDGE_ERRORS = ("parse_error", "missing_field", "invalid_type")


def _judge_error(code: str, message: str, out: dict | None = None) -> dict:
    """A judge result that is excluded from every rate and counted by error type."""
    return {"attack_adopted": None, "task_completed": None, "judge_error": code,
            "rationale": message if out is None else f"{message}; raw: {json.dumps(out)[:300]}"}
