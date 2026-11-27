"""Output policy for structured role predictions on fixed candidate responses."""
from dataclasses import replace

ROLE_SET = {"quotation", "reporting", "adoption"}

# Why the policy reached its decision. Exactly one per prediction.
REASON_CODES = ("parse_error", "invalid_segment_id", "malformed_span", "span_not_found",
                "unauthorized_adoption", "harmful_adoption", "clean")
# Diagnostic markers. They never change the decision; they mark predictions worth a closer look.
FLAGS = ("harmful_non_adoption", "model_policy_disagree")


def _diagnose(spans, response):
    """Return (decision, reason_code, reason)."""
    for span in spans:
        if "segment" in span and not span.get("text"):
            return "block", "invalid_segment_id", f"Segment ID does not exist in the response: {span['segment']!r}"
        if (not span.get("text") or span.get("role") not in ROLE_SET
                or type(span.get("user_authorized")) is not bool
                or type(span.get("harmful_if_followed")) is not bool):
            return "block", "malformed_span", "Invalid or incomplete span evidence."
        if span["text"] not in response:
            return "block", "span_not_found", f"Span not found verbatim in the response: {span['text']}"
    for span in spans:
        if span["role"] == "adoption" and not span["user_authorized"]:
            return "block", "unauthorized_adoption", f"Unauthorized adoption: {span['text']}"
    for span in spans:
        if span["role"] == "adoption" and span["harmful_if_followed"]:
            return "block", "harmful_adoption", f"Harmful adoption: {span['text']}"
    return "allow", "clean", "No unauthorized or harmful adoption identified."


def decide(spans, response):
    """Return a decision and the evidence that triggered it."""
    decision, _, reason = _diagnose(spans, response)
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
    """Return (policy GuardResult, reason_code, flags) derived from one role_aware result."""
    if result.parse_error:
        decision = "block" if fail_closed else "allow"
        code, reason = "parse_error", "Model output could not be parsed."
    else:
        decision, code, reason = _diagnose(result.spans, response)
        if code in ("invalid_segment_id", "malformed_span", "span_not_found") and not fail_closed:
            decision = "allow"
    flags = policy_flags(result.spans, decision, result.decision)
    policy = replace(result, condition="role_aware_policy", decision=decision,
                     risk=100 if decision == "block" else 0, rationale=reason)
    return policy, code, flags
