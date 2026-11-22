"""PIGuard baseline: the released classifier from Li et al., "PIGuard: Prompt Injection Guardrail via
Mitigating Overdefense for Free", ACL 2025 (model: huggingface.co/leolee99/PIGuard, MIT license).

PIGuard classifies a piece of TEXT as injection or benign. It never sees the user's request, so we run
it in two positions:
  * piguard          output side: classify the candidate response (a trained text-only guard)
  * piguard_source   input side: classify the external content before the assistant answers; if it
                     flags the source, the response is blocked whatever it says

Install:  pip install -e ".[piguard]"   (transformers + torch; runs on CPU, no API cost)

Long texts: the model is DeBERTa-based with a 512-token window. Truncation would silently drop an
injection at the end of a long email, so we classify overlapping windows and take the MAX injection
probability (our adaptation; state it in the paper). Set `piguard.window_tokens: 0` to use plain
truncation instead.

The label mapping is read from the model config at load time and printed once; check it says which
label means "injection".
"""
from __future__ import annotations

import re
import sys
import threading
import time

_LOCK = threading.Lock()
_INFER_LOCK = threading.Lock()   # one forward pass at a time when called from threads
_STATE: dict = {}
INJECTION_HINTS = ("inject", "malic", "unsafe", "attack", "jailbreak")


def _load(model_id: str):
    with _LOCK:
        if "pipe" in _STATE:
            return _STATE
        try:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
            import torch  # noqa: F401
        except ImportError as e:
            raise SystemExit("PIGuard needs transformers + torch: pip install -e \".[piguard]\"") from e
        tok = AutoTokenizer.from_pretrained(model_id)
        model = AutoModelForSequenceClassification.from_pretrained(model_id, trust_remote_code=True)
        model.eval()
        id2label = {int(k): str(v) for k, v in model.config.id2label.items()}
        inj = [i for i, l in id2label.items() if any(h in l.lower() for h in INJECTION_HINTS)]
        if not inj:  # fall back to the conventional LABEL_1 = positive class
            inj = [1] if 1 in id2label else [max(id2label)]
        print(f"[piguard] {model_id} labels={id2label} -> treating {[id2label[i] for i in inj]} as injection",
              file=sys.stderr)
        _STATE.update(tok=tok, model=model, inj_idx=inj[0], id2label=id2label, pipe=True)
        return _STATE


def _windows(tok, text: str, window: int, stride: int) -> list[str]:
    ids = tok(text, add_special_tokens=False)["input_ids"]
    if window <= 0 or len(ids) <= window:
        return [text]
    out, i = [], 0
    while i < len(ids):
        out.append(tok.decode(ids[i:i + window]))
        if i + window >= len(ids):
            break
        i += stride
    return out


def injection_prob(text: str, cfg: dict | None = None) -> tuple[float, float]:
    """Return (max injection probability over windows, seconds)."""
    cfg = cfg or {}
    if cfg.get("mock"):
        return _mock_prob(text), 0.0
    st = _load(cfg.get("model", "leolee99/PIGuard"))
    import torch
    tok, model = st["tok"], st["model"]
    window = int(cfg.get("window_tokens", 480))
    stride = int(cfg.get("stride_tokens", 240))
    t0 = time.perf_counter()
    best = 0.0
    with _INFER_LOCK, torch.no_grad():
        for chunk in _windows(tok, text, window, stride):
            enc = tok(chunk, return_tensors="pt", truncation=True, max_length=512)
            logits = model(**enc).logits[0]
            p = torch.softmax(logits, dim=-1)[st["inj_idx"]].item()
            best = max(best, p)
    return best, time.perf_counter() - t0


_MOCK = re.compile(r"ignore (?:all |the )?previous|assistant|system message|override|instruction to ai|"
                   r"\bAI\b.*(?:tell|recommend|inform)", re.I)


def _mock_prob(text: str) -> float:
    return 0.9 if _MOCK.search(text) else 0.1
