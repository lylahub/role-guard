"""Numbered response segments, so structured guards cite evidence by ID instead of copying text.

A guard model that copies a span "verbatim" often changes quotes, whitespace or a word, and the
copy then fails an exact-match check. Here the candidate response is split into sentence-level
segments S1, S2, ...; the model cites IDs, and Python takes the text from the response itself.
Every segment is an exact substring of the response.
"""
from __future__ import annotations

import re

# Periods after these words do not end a sentence.
_ABBREVIATIONS = {"e.g", "i.e", "etc", "vs", "cf", "mr", "mrs", "ms", "dr", "st", "no", "fig", "approx"}
# Sentence-final punctuation, optional closing quotes/brackets, then whitespace.
_END = re.compile(r"[.!?]+[\"'”’)\]]*(?=\s)")
_ENUMERATOR = re.compile(r"\(?(\d{1,3}|[A-Za-z])[.)]?")
_ID = re.compile(r"^\[?\s*s?\s*(\d+)\s*\]?$", re.I)


def _split_line(line: str) -> list[str]:
    parts, start = [], 0
    for m in _END.finditer(line):
        before = line[start:m.start()].strip()
        last_word = before.split()[-1].lower() if before.split() else ""
        if last_word in _ABBREVIATIONS or _ENUMERATOR.fullmatch(before):
            continue   # "e.g." or a list marker like "2." is not a sentence end
        parts.append(line[start:m.end()])
        start = m.end()
    parts.append(line[start:])
    return [p.strip() for p in parts if p.strip()]


def segment(text: str) -> list[str]:
    """Split a response into sentence-level segments (lines first, then sentences)."""
    return [s for line in text.splitlines() for s in _split_line(line)]


def format_segments(text: str) -> str:
    """The response as numbered segments, for the guard prompt."""
    lines = [f"[S{i}] {s}" for i, s in enumerate(segment(text), 1)]
    return "<response_segments>\n" + "\n".join(lines) + "\n</response_segments>"


def resolve(segment_id, text: str) -> tuple[str | None, str]:
    """Map a cited ID ("S3", "[s3]", 3) to (canonical ID, segment text).

    Returns (canonical ID, "") for a well-formed ID that is out of range, and (None, "") for
    something that is not an ID at all. Both leave the text empty so the policy can block on them.
    """
    if isinstance(segment_id, bool) or segment_id is None:
        return None, ""
    m = _ID.match(str(segment_id).strip())
    if not m:
        return None, ""
    n = int(m.group(1))
    segs = segment(text)
    return f"S{n}", (segs[n - 1] if 1 <= n <= len(segs) else "")
