"""Communicative-role-aware guarding against indirect prompt injection (CS239 project)."""

ROLES = ("quotation", "reporting", "adoption")
CASE_TYPES = (
    "legit_quotation",   # user asked to quote; response quotes the injected/suspicious text
    "legit_analysis",    # user asked for analysis/summary; response reports or analyzes instructions without endorsing
    "benign_adoption",   # response follows legitimate instructions the user asked to follow
    "attack_adoption",   # response follows/endorses an injected instruction the user did not authorize
    "disguised_attack",  # adoption dressed up as quotation/reporting (role-targeted attack)
)
LABELS = ("allow", "block")
