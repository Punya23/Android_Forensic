"""Keyword / known-hash flagging for on-scene analyst awareness.

Two independent detectors:
  * keyword/regex scan over every text-bearing artifact (messages, carved fragments,
    filenames) — configurable term list with per-term severity.
  * known-hash matching of pulled files against a supplied hash-set (e.g. a CSAM or
    known-contraband SHA-256 list) — the tool only stores/compares hashes, never content.

Nothing here makes an accusation; it raises a flag for a human to review, which is the
correct posture for a triage preview.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .models import ArtifactRecord, CallRecord, Contact, Flag, Message
from .recovery import CarvedRow


@dataclass
class KeywordRule:
    term: str
    severity: str = "warn"       # info | warn | critical
    is_regex: bool = False

    def compile(self) -> re.Pattern:
        pattern = self.term if self.is_regex else re.escape(self.term)
        return re.compile(pattern, re.IGNORECASE)


# A conservative default watch-list. Real deployments load their own from the case config;
# this is illustrative and intentionally generic (no slurs / no illegal content strings).
DEFAULT_KEYWORDS: list[KeywordRule] = [
    KeywordRule(r"\b(kill|attack|bomb|weapon|gun|rifle)\b", "critical", is_regex=True),
    KeywordRule(r"\b(transfer|payment|cash|account|hawala|crypto|bitcoin)\b", "warn", is_regex=True),
    KeywordRule(r"\b(meet|midnight|docks|drop|package|deliver)\b", "warn", is_regex=True),
    KeywordRule(r"\b(passport|fake\s?id|forged)\b", "critical", is_regex=True),
]


def scan_messages(messages: Iterable[Message],
                  rules: list[KeywordRule]) -> list[Flag]:
    compiled = [(r, r.compile()) for r in rules]
    flags: list[Flag] = []
    for msg in messages:
        for rule, pat in compiled:
            for match in pat.finditer(msg.body or ""):
                flags.append(Flag(
                    kind="keyword", term=match.group(0),
                    context=_snippet(msg.body, match.start(), match.end()),
                    location=f"{msg.app} msg from {msg.sender}"
                             + (f" @ {msg.timestamp}" if msg.timestamp else ""),
                    severity=rule.severity,
                ))
    return flags


def scan_carved(rows: Iterable[CarvedRow], rules: list[KeywordRule]) -> list[Flag]:
    compiled = [(r, r.compile()) for r in rules]
    flags: list[Flag] = []
    for row in rows:
        text = " ".join(str(v) for v in row.values if isinstance(v, str))
        for rule, pat in compiled:
            for match in pat.finditer(text):
                flags.append(Flag(
                    kind="keyword", term=match.group(0),
                    context=_snippet(text, match.start(), match.end()),
                    location=f"recovered [{row.confidence.value}] {row.provenance}",
                    severity=rule.severity,
                ))
    return flags


def scan_known_hashes(artifacts: Iterable[ArtifactRecord],
                      known: dict[str, str]) -> list[Flag]:
    """Flag pulled files whose SHA-256 is in a known-hash set. `known` maps
    sha256 -> label (e.g. 'known CSAM set 2024')."""
    flags: list[Flag] = []
    lowered = {k.lower(): v for k, v in known.items()}
    for art in artifacts:
        label = lowered.get((art.sha256 or "").lower())
        if label:
            flags.append(Flag(
                kind="known-hash", term=art.sha256[:16] + "…",
                context=f"file matches known-hash set: {label}",
                location=art.stored_path, severity="critical",
            ))
    return flags


def _snippet(text: str, start: int, end: int, width: int = 40) -> str:
    a = max(0, start - width)
    b = min(len(text), end + width)
    prefix = "…" if a > 0 else ""
    suffix = "…" if b < len(text) else ""
    return f"{prefix}{text[a:b].strip()}{suffix}"
