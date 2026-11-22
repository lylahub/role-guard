# Third-party material

## BIPIA (data/bipia_email_slice.jsonl, src/rguard/bipia.py)
Derived from BIPIA, https://github.com/microsoft/BIPIA — MIT License, Copyright (c) Microsoft Corporation.
Email contexts and questions in BIPIA's EmailQA come from OpenAI Evals (https://github.com/openai/evals, MIT License).
The insertion functions in `src/rguard/bipia.py` are copied from `bipia/data/utils.py`.
Cite: Yi et al., "Benchmarking and Defending Against Indirect Prompt Injection Attacks on Large Language Models" (KDD 2025).

## PIGuard (src/rguard/piguard.py)
Uses the released model `leolee99/PIGuard` (MIT License), downloaded at run time; no weights are included here.
Cite: Li et al., "PIGuard: Prompt Injection Guardrail via Mitigating Overdefense for Free" (ACL 2025).

## PromptArmor (src/rguard/promptarmor.py)
Reimplemented from the paper's text; no code was released. The guardrail prompt is quoted from the paper's Figure 2.
Cite: Shi et al., "PromptArmor: Simple yet Effective Prompt Injection Defenses", arXiv:2507.15219 (2025).
