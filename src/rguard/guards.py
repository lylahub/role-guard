"""Guard conditions. Each returns a GuardResult with a decision, a 0-100 risk score and cost info."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from . import ROLES
from .llm import LLM
from .prompts import assistant_prompt, guard_prompt, judge_prompt

GUARD_CONDITIONS = ("text_only", "context", "context_cot", "role_aware")


@dataclass
class GuardResult:
    condition: str
    decision: str              # "allow" | "block"
    risk: int                  # 0-100
    spans: list = field(default_factory=list)   # role_aware only
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


def run_guard(llm: LLM, condition: str, ex: dict, response: str, fail_closed: bool = True) -> GuardResult:
    """Run one guard condition on (example, candidate response).

    If the model output can't be parsed, the guard fails closed (block, risk 100) by default and records
    the error, so parse failures show up in the results instead of silently crashing the run.
    """
    if condition not in GUARD_CONDITIONS:
        raise ValueError(f"Unknown guard {condition!r}; choose from {GUARD_CONDITIONS}")
    system, user = guard_prompt(condition, ex, response)
    try:
        out, resp = llm.complete_json(system, user, task=f"guard:{condition}")
    except Exception as e:  # noqa: BLE001
        d = "block" if fail_closed else "allow"
        return GuardResult(condition, d, 100 if fail_closed else 0, parse_error=str(e)[:300])

    decision = _norm_decision(out.get("decision"))
    spans = []
    if condition == "role_aware":
        for s in out.get("spans") or []:
            role = str(s.get("role", "")).lower().strip()
            if role == "analysis":
                role = "reporting"
            spans.append({
                "text": str(s.get("text", "")),
                "role": role if role in ROLES else "unknown",
                "user_authorized": bool(s.get("user_authorized", False)),
                "harmful_if_followed": bool(s.get("harmful_if_followed", False)),
            })
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
        return {"attack_adopted": bool(out.get("attack_adopted")),
                "task_completed": bool(out.get("task_completed")),
                "rationale": str(out.get("rationale", ""))}
    except Exception as e:  # noqa: BLE001
        return {"attack_adopted": None, "task_completed": None, "rationale": f"judge error: {e}"}
