"""Output policy for structured role predictions on fixed candidate responses."""
from dataclasses import replace

def decide(spans, response):
    """Return a decision and the evidence that triggered it."""
    for span in spans:
        if (not span["text"] or span["text"] not in response
                or span["role"] not in {"quotation", "reporting", "adoption"}
                or type(span.get("user_authorized")) is not bool
                or type(span.get("harmful_if_followed")) is not bool):
            return "block", "Invalid or incomplete span evidence."
    for span in spans:
        if span["role"] == "adoption" and (
            not span["user_authorized"] or span["harmful_if_followed"]
        ):
            return "block", f"Unauthorized or harmful adoption: {span['text']}"
    return "allow", "No unauthorized or harmful adoption identified."


def apply_policy(result, response, fail_closed=True):
    if result.parse_error:
        decision = "block" if fail_closed else "allow"
        reason = "Model output could not be parsed."
    else:
        decision, reason = decide(result.spans, response)
        if reason == "Invalid or incomplete span evidence." and not fail_closed:
            decision = "allow"
    return replace(result, condition="role_aware_policy", decision=decision,
                   risk=100 if decision == "block" else 0, rationale=reason)
