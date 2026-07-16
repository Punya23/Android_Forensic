"""Traffic-light triage risk assessment.

Distils the flags, recovered-data signals, and artifact spread into a single RED / AMBER /
GREEN verdict plus a transparent scorecard — the "seconds-to-results, simple traffic-light
interface" that field-triage tools (Cyacomb) lead with. This is explicitly a *prioritisation
aid for a human*, never an automated determination of guilt; every point in the score cites
the concrete evidence behind it so an officer can see exactly why the light is the colour it
is.
"""
from __future__ import annotations

from typing import Any


def assess_risk(*, flags: list[dict], recovered: list[dict],
                counts: dict[str, int],
                notable_apps: list[dict] | None = None,
                trashed_media: int = 0) -> dict[str, Any]:
    reasons: list[dict[str, Any]] = []
    score = 0

    # Anti-forensic / vault apps present on the device are a strong prioritisation signal.
    notable_apps = notable_apps or []
    anti_forensic = [a for a in notable_apps if a.get("category") == "anti_forensic"]
    if anti_forensic:
        pts = min(25, 12 * len(anti_forensic))
        score += pts
        names = sorted({a.get("friendly_name") or a.get("label") or a.get("package", "")
                        for a in anti_forensic})
        reasons.append({"points": pts, "label": "vault / anti-forensic app installed",
                        "detail": f"{len(anti_forensic)} content-hiding app(s): {', '.join(n for n in names if n)[:120]}",
                        "severity": "warn"})

    # A large amount of recently-trashed media suggests deletion activity worth reviewing.
    if trashed_media >= 3:
        pts = min(15, trashed_media)
        score += pts
        reasons.append({"points": pts, "label": "trashed media present",
                        "detail": f"{trashed_media} item(s) in the MediaStore trash (recently deleted)",
                        "severity": "warn"})

    crit = [f for f in flags if f.get("severity") == "critical"]
    warn = [f for f in flags if f.get("severity") == "warn"]
    known_hash = [f for f in flags if f.get("kind") == "known-hash"]

    if known_hash:
        score += 60
        reasons.append({"points": 60, "label": "known-hash content match",
                        "detail": f"{len(known_hash)} file(s) match a known-illegal-content hash set",
                        "severity": "critical"})
    if crit:
        pts = min(40, 10 * len(crit))
        score += pts
        terms = sorted({f["term"] for f in crit})
        reasons.append({"points": pts, "label": "critical keyword hits",
                        "detail": f"{len(crit)} hit(s) on: {', '.join(terms[:8])}",
                        "severity": "critical"})
    if warn:
        pts = min(20, 3 * len(warn))
        score += pts
        reasons.append({"points": pts, "label": "watch-list keyword hits",
                        "detail": f"{len(warn)} hit(s) across messages & recovered content",
                        "severity": "warn"})

    # Deleted content that was recovered is itself a signal worth surfacing.
    deleted = [r for r in recovered if r.get("confidence") in ("recovered", "carved")]
    if deleted:
        pts = min(15, len(deleted) // 5)
        pts = max(pts, 5) if deleted else 0
        score += pts
        reasons.append({"points": pts, "label": "recovered deleted content",
                        "detail": f"{len(deleted)} deleted/carved row(s) recovered — deletion activity present",
                        "severity": "warn"})

    # Flags found specifically inside recovered/deleted content are extra notable.
    in_recovered = [f for f in flags if "recovered" in (f.get("location") or "").lower()]
    if in_recovered:
        score += 10
        reasons.append({"points": 10, "label": "keywords inside deleted content",
                        "detail": f"{len(in_recovered)} watch-list hit(s) found in recovered/deleted data",
                        "severity": "warn"})

    score = min(score, 100)
    if score >= 60 or known_hash or len(crit) >= 2:
        level = "red"
    elif score >= 20 or crit or deleted or anti_forensic:
        level = "amber"
    else:
        level = "green"

    headline = {
        "red": "High-priority — strong indicators present; recommend full lab examination",
        "amber": "Review recommended — notable indicators; officer discretion",
        "green": "Low indicators surfaced in this triage preview",
    }[level]

    return {
        "level": level,
        "score": score,
        "headline": headline,
        "reasons": sorted(reasons, key=lambda r: -r["points"]),
        "disclaimer": "Prioritisation aid only. A triage light is not a determination of "
                      "guilt and does not replace full forensic examination.",
    }
