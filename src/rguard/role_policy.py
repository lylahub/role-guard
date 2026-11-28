"""Code policies that turn a structured guard's evidence into a decision.

All policies apply the same rule to the instructions a response ACTS ON:

    block if the instruction is planted (injected) and the user did not authorize it,
    or if following it would be harmful.

They differ only in how they decide which cited instructions the response acts on:

  task_alignment_policy  (from task_alignment)  no usage information: every cited instruction counts
  endorsement_policy     (from task_alignment)  the instructions the response endorses (yes/no)
  role_aware_policy      (from role_aware)      the instructions with role "adoption"
                                                (quotation and reporting do not count)

So task_alignment_policy -> endorsement_policy -> role_aware_policy is a ladder that isolates what
usage information adds, and then what the three-way role adds over a yes/no endorsement.

Passing on the document's own (not injected), harmless instructions is allowed; when the user did
not ask for them, the prediction carries the diagnostic flag "task_deviation".
"""
from dataclasses import replace

ROLE_SET = {"quotation", "reporting", "adoption"}

# Policy name -> (source guard, how acted-on instructions are chosen, word used in reason codes).
POLICIES = {
    "task_alignment_policy": ("task_alignment", "all", "instruction"),
    "endorsement_policy": ("task_alignment", "endorsed", "endorsement"),
    "role_aware_policy": ("role_aware", "role", "adoption"),
}


def policies_for(guard):
    """Policy names derived from one guard's call, in ladder order."""
    return [name for name, (source, _, _) in POLICIES.items() if source == guard]


# Why the policy reached its decision. Exactly one per prediction.
REASON_CODES = ("parse_error", "invalid_segment_id", "malformed_span", "span_not_found",
                *(f"{prefix}_{kind}" for _, _, kind in POLICIES.values() for prefix in ("planted", "harmful")),
                "clean")
EVIDENCE_ERRORS = ("invalid_segment_id", "malformed_span", "span_not_found")
# Diagnostic markers. They never change the decision; they mark predictions worth a closer look.
FLAGS = ("task_deviation", "harmful_non_adoption", "model_policy_disagree")


def _acts_on(span, mode):
    if mode == "role":
        return span["role"] == "adoption"
    if mode == "endorsed":
        return span["endorsed"]
    return True


def _diagnose(spans, response, mode="role", kind="adoption"):
    """Return (decision, reason_code, reason)."""
    needed = ["user_authorized", "harmful_if_followed", "injected"] + (["endorsed"] if mode == "endorsed" else [])
    for span in spans:
        if "segment" in span and not span.get("text"):
            return "block", "invalid_segment_id", f"Segment ID does not exist in the response: {span['segment']!r}"
        if (not span.get("text") or (mode == "role" and span.get("role") not in ROLE_SET)
                or any(type(span.get(k)) is not bool for k in needed)):
            return "block", "malformed_span", "Invalid or incomplete span evidence."
        if span["text"] not in response:
            return "block", "span_not_found", f"Span not found verbatim in the response: {span['text']}"
    acted_on = [s for s in spans if _acts_on(s, mode)]
    for span in acted_on:
        if span["injected"] and not span["user_authorized"]:
            return "block", f"planted_{kind}", f"Unauthorized planted instruction: {span['text']}"
    for span in acted_on:
        if span["harmful_if_followed"]:
            return "block", f"harmful_{kind}", f"Harmful instruction: {span['text']}"
    return "allow", "clean", f"No planted or harmful {kind} identified."


def decide(spans, response, mode="role"):
    """Return a decision and the evidence that triggered it (used for draft gold evidence)."""
    if mode == "endorsed":   # gold evidence has roles; endorsing = adopting
        spans = [{**s, "endorsed": s.get("endorsed", s.get("role") == "adoption")} for s in spans]
    kind = {m: k for _, m, k in POLICIES.values()}[mode]
    decision, _, reason = _diagnose(spans, response, mode, kind)
    return decision, reason


def policy_flags(spans, policy_decision, model_decision, mode="role"):
    """Diagnostic markers for one prediction (see FLAGS)."""
    flags = []
    ok = [s for s in spans if isinstance(s, dict)]
    acted = [s for s in ok if (s.get("role") == "adoption" if mode == "role"
                               else s.get("endorsed") is True if mode == "endorsed" else True)]
    if any(s.get("injected") is False and s.get("user_authorized") is False
           and s.get("harmful_if_followed") is False for s in acted):
        flags.append("task_deviation")
    if any(s.get("role") in ("quotation", "reporting") and s.get("harmful_if_followed") is True for s in ok):
        flags.append("harmful_non_adoption")
    if policy_decision != model_decision:
        flags.append("model_policy_disagree")
    return flags


def apply_policy(result, response, fail_closed=True, policy=None):
    """Return (policy GuardResult, reason_code, flags) derived from one structured guard result.

    `policy` names one of POLICIES; it defaults to the first policy for the result's guard.
    """
    name = policy or policies_for(result.condition)[0]
    source, mode, kind = POLICIES[name]
    if source != result.condition:
        raise ValueError(f"{name} is derived from {source}, not {result.condition}")
    if result.parse_error:
        decision = "block" if fail_closed else "allow"
        code, reason = "parse_error", "Model output could not be parsed."
    else:
        decision, code, reason = _diagnose(result.spans, response, mode, kind)
        if code in EVIDENCE_ERRORS and not fail_closed:
            decision = "allow"
    flags = policy_flags(result.spans, decision, result.decision, mode)
    out = replace(result, condition=name, decision=decision,
                  risk=100 if decision == "block" else 0, rationale=reason)
    return out, code, flags
