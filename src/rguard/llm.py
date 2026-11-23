"""LLM backends with on-disk caching and token/latency accounting.

Backends:
  - "mock":      offline, deterministic heuristics. ONLY for testing the pipeline; its numbers mean nothing.
  - "openai":    needs `pip install openai` and OPENAI_API_KEY.
  - "anthropic": needs `pip install anthropic` and ANTHROPIC_API_KEY.

All calls go through `LLM.complete(system, user, task=...)`, which returns an `LLMResponse`.
Responses are cached by (backend, model, temperature, system, user) so reruns are free and reproducible.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class LLMResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    cached: bool = False
    raw: dict = field(default_factory=dict)


def extract_json(text: str) -> dict:
    """Parse the first JSON object in `text` (tolerates code fences and surrounding prose)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    if start < 0:
        raise ValueError(f"No JSON object in model output: {text[:200]!r}")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError(f"Unbalanced JSON in model output: {text[:200]!r}")


class LLM:
    def __init__(self, backend: str = "mock", model: str = "mock", temperature: float = 0.0,
                 max_tokens: int = 1500, cache_dir: str | None = ".cache/llm", max_retries: int = 3):
        self.backend = backend
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.cache_path = None
        self._cache: dict[str, dict] = {}
        if cache_dir:
            Path(cache_dir).mkdir(parents=True, exist_ok=True)
            self.cache_path = Path(cache_dir) / f"{backend}__{re.sub(r'[^A-Za-z0-9._-]', '_', model)}.jsonl"
            if self.cache_path.exists():
                with open(self.cache_path) as f:
                    for line in f:
                        if line.strip():
                            rec = json.loads(line)
                            self._cache[rec["key"]] = rec
        self._client = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ public
    def complete(self, system: str, user: str, task: str = "generic", json_mode: bool = True) -> LLMResponse:
        key = hashlib.sha256(
            json.dumps([self.backend, self.model, self.temperature, system, user]).encode()
        ).hexdigest()
        if key in self._cache:
            r = self._cache[key]
            return LLMResponse(r["text"], r["input_tokens"], r["output_tokens"], r["latency_s"], cached=True)

        t0 = time.perf_counter()
        last_err = None
        for attempt in range(self.max_retries):
            try:
                if self.backend == "mock":
                    from .mock import mock_complete
                    resp = mock_complete(system, user, task)
                elif self.backend == "openai":
                    resp = self._openai(system, user, json_mode)
                elif self.backend == "anthropic":
                    resp = self._anthropic(system, user)
                else:
                    raise ValueError(f"Unknown backend {self.backend!r}")
                break
            except Exception as e:  # network / rate-limit errors
                last_err = e
                if self.backend == "mock":
                    raise
                time.sleep(2 ** attempt * 2)
        else:
            raise RuntimeError(f"LLM call failed after {self.max_retries} attempts: {last_err}")
        resp.latency_s = time.perf_counter() - t0

        if self.cache_path:
            rec = {"key": key, "task": task, "text": resp.text, "input_tokens": resp.input_tokens,
                   "output_tokens": resp.output_tokens, "latency_s": resp.latency_s}
            with self._lock:
                self._cache[key] = rec
                with open(self.cache_path, "a") as f:
                    f.write(json.dumps(rec) + "\n")
        return resp

    def complete_json(self, system: str, user: str, task: str = "generic") -> tuple[dict, LLMResponse]:
        """Call the model and parse JSON; on a parse failure, ask once more with a stricter reminder."""
        resp = self.complete(system, user, task=task)
        try:
            return extract_json(resp.text), resp
        except (ValueError, json.JSONDecodeError):
            retry_user = user + "\n\nReturn ONLY one valid JSON object matching the schema. No other text."
            resp2 = self.complete(system, retry_user, task=task)
            resp2.input_tokens += resp.input_tokens
            resp2.output_tokens += resp.output_tokens
            resp2.latency_s += resp.latency_s
            return extract_json(resp2.text), resp2

    # --------------------------------------------------------------- backends
    def _openai(self, system: str, user: str, json_mode: bool) -> LLMResponse:
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI()
        kwargs = dict(model=self.model, max_completion_tokens=self.max_tokens,
                      messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        if not getattr(self, "_no_temperature", False):
            kwargs["temperature"] = self.temperature
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            r = self._client.chat.completions.create(**kwargs)
        except Exception as e:  # some models only accept their default temperature
            if "temperature" in str(e).lower() and "temperature" in kwargs:
                self._no_temperature = True
                print(f"[llm] {self.model} rejected temperature={self.temperature}; using the model default. "
                      f"Report this in the paper.", file=sys.stderr)
                kwargs.pop("temperature")
                r = self._client.chat.completions.create(**kwargs)
            else:
                raise
        u = r.usage
        return LLMResponse(r.choices[0].message.content or "", u.prompt_tokens if u else 0,
                           u.completion_tokens if u else 0)

    def _anthropic(self, system: str, user: str) -> LLMResponse:
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()
        kwargs = dict(model=self.model, system=system, max_tokens=self.max_tokens,
                      messages=[{"role": "user", "content": user}])
        if not getattr(self, "_no_temperature", False):
            kwargs["temperature"] = self.temperature
        try:
            r = self._client.messages.create(**kwargs)
        except Exception as e:
            if "temperature" in str(e).lower() and "temperature" in kwargs:
                self._no_temperature = True
                print(f"[llm] {self.model} rejected temperature={self.temperature}; using the model default. "
                      f"Report this in the paper.", file=sys.stderr)
                kwargs.pop("temperature")
                r = self._client.messages.create(**kwargs)
            else:
                raise
        text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
        return LLMResponse(text, r.usage.input_tokens, r.usage.output_tokens)


def llm_from_config(cfg: dict, role: str = "guard", mock: bool = False) -> LLM:
    """Build an LLM from the `models.<role>` section of the config (falls back to `models.guard`).

    `mock=True` (the CLIs' --mock flag) forces the offline mock backend for every role.
    """
    m = cfg["models"].get(role) or cfg["models"]["guard"]
    backend, model = m["backend"], m["model"]
    if mock or backend == "mock":
        backend, model = "mock", "mock"
    return LLM(backend=backend, model=model, temperature=m.get("temperature", 0.0),
               max_tokens=m.get("max_tokens", 1500), cache_dir=cfg.get("cache_dir", ".cache/llm"))
