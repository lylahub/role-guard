"""Code policies that turn a structured guard's evidence into a decision.

Two rules over the same kind of evidence (cited instruction spans with user_authorized and
harmful_if_followed), differing only in whether a communicative role is used:

  role_aware_policy      (from role_aware)      block an ADOPTED instruction that is unauthorized
                                                or harmful; quotation and reporting are allowed.
  task_alignment_policy  (from task_alignment)  no role available, so every cited instruction is
                                                treated as if adopted: block any unauthorized or
                                                harmful one.

Comparing the two isolates what the role label adds when the decision is made by a rule.
"""
from dataclasses import replace

ROLE_SET = {"quotation", "reporting", "adoption"}
# Source guard -> (policy condition name, whether the rule uses roles).
POLICIES = {"role_aware": ("role_aware_policy", True),
            "task_alignment": ("task_alignment_policy", False)}

# Why the policy reached its decision. Exactly one per prediction.
REASON_CODES = ("parse_error", "invalid_segment_id", "malformed_span", "span_not_found",
                "unauthorized_adoption", "harmful_adoption",
                "unauthorized_instruction", "harmful_instruction", "clean")
EVIDENCE_ERRORS = ("invalid_segment_id", "malformed_span", "span_not_found")
# Diagnostic markers. They never change the decision; they mark predictions worth a closer look.
FLAGS = ("harmful_non_adoption", "model_policy_disagree")


def _diagnose(spans, response, use_role=True):
    """Return (decision, reason_code, reason)."""
    for span in spans:
        if "segment" in span and not span.get("text"):
            return "block", "invalid_segment_id", f"Segment ID does not exist in the response: {span['segment']!r}"
        if (not span.get("text") or (use_role and span.get("role") not in ROLE_SET)
                or type(span.get("user_authorized")) is not bool
                or type(span.get("harmful_if_followed")) is not bool):
            return "block", "malformed_span", "Invalid or incomplete span evidence."
        if span["text"] not in response:
            return "block", "span_not_found", f"Span not found verbatim in the response: {span['text']}"
    # Without roles, every cited instruction counts as adopted.
    acted_on = [s for s in spans if not use_role or s["role"] == "adoption"]
    kind = "adoption" if use_role else "instruction"
    for span in acted_on:
        if not span["user_authorized"]:
            return "block", f"unauthorized_{kind}", f"Unauthorized {kind}: {span['text']}"
    for span in acted_on:
        if span["harmful_if_followed"]:
            return "block", f"harmful_{kind}", f"Harmful {kind}: {span['text']}"
    return "allow", "clean", f"No unauthorized or harmful {kind} identified."


def decide(spans, response, use_role=True):
    """Return a decision and the evidence that triggered it."""
    decision, _, reason = _diagnose(spans, response, use_role)
    return decision, reason


def policy_flags(spans, policy_decision, model_decision):
    """Diagnostic markers for one prediction (see FLAGS)."""
    flags = []
    if any(isinstance(s, dict) and s.get("role") in ("quotation", "reporting")
           and s.get("harmful_if_followed") is True for s in spans):
        flags.append("harmful_non_adoption")
    if policy_decision != model_decision:
        flags.append("model_policy_disagree")
    return flags


def apply_policy(result, response, fail_closed=True):
    """Return (policy GuardResult, reason_code, flags) derived from one structured guard result."""
    condition, use_role = POLICIES[result.condition]
    if result.parse_error:
        decision = "block" if fail_closed else "allow"
        code, reason = "parse_error", "Model output could not be parsed."
    else:
        decision, code, reason = _diagnose(result.spans, response, use_role)
        if code in EVIDENCE_ERRORS and not fail_closed:
            decision = "allow"
    flags = policy_flags(result.spans, decision, result.decision)
    policy = replace(result, condition=condition, decision=decision,
                     risk=100 if decision == "block" else 0, rationale=reason)
    return policy, code, flags
