"""Entity cross-links: for every named person/number in the case brief, every place
in this case's own collected data where that name or number appears — a same-case,
in-memory answer to "wherever this name is found, what else is it linked to", built
from the same passage-flattening this case's Q&A already uses (:mod:`.case_qa`), not
a new extraction path.

**What this deliberately is not.** Not identity resolution and not a graph database:
two different people who happen to share a name are not distinguished; a name absent
here may still be present under a nickname, alias, or misspelling the case brief did
not list; a phone number formatted differently in different apps is only linked if
:meth:`~.planner.CaseProfile.entities` already normalised it. This is the same
substring-match contract :mod:`.case_qa` uses for its own retrieval, made explicit and
disclosed rather than sold as more than it is — see the eRakshak honesty invariant on
identity merging being a claim that must be disclosed (``analysis/graph.py``).

Postgres/a real graph store is a deliberately deferred next step, per the officer's own
brief: this module produces the same ``{entity -> [occurrence]}`` shape today as plain
JSON (one more ``case.write_derived`` dataset, exactly like every other one in this
package), so a later migration only has to add a storage/query layer on top of an
already-correct linkage, not redesign it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .case_qa import build_passages
from .planner import CaseProfile

#: Occurrences listed per entity before the rest is only counted, not enumerated —
#: the same "cap the display, never the count" shape as ai_findings' own truncation,
#: so a case with hundreds of hits on one name doesn't blow up the report/dashboard.
_MAX_OCCURRENCES_PER_ENTITY = 50

_DISCLAIMER = (
    "Substring match of each case-brief name/number against this case's own collected "
    "text — not identity resolution. Two different people sharing a name are not "
    "distinguished; a name present here only under a nickname, alias, or misspelling "
    "the case brief did not list will not be found. Zero occurrences means the exact "
    "text was not found in what was collected, not that the person had no contact — "
    "see Encryption posture and Deletion evidence for why a real communication can "
    "still be unrecoverable."
)


@dataclass
class EntityLink:
    entity: str
    occurrence_count: int
    datasets: list[str] = field(default_factory=list)
    occurrences: list[dict] = field(default_factory=list)
    truncated: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def build_entity_links(derived: dict[str, Any], profile: CaseProfile) -> dict:
    """Pure function over plain dicts — same contract as ``investigate()`` /
    ``analyze_derived`` — so it is unit-testable with no live case on disk.

    *derived* is ``{dataset_name: rows}`` for the same six sources
    :func:`~.case_qa.build_passages` flattens (messages/recovered/calls/browser/
    locations/contacts).
    """
    entities = [e for e in profile.entities() if e and e.strip()]
    passages = build_passages(derived)

    links: list[EntityLink] = []
    for entity in entities:
        needle = entity.strip().lower()
        hits = [p for p in passages if needle in p.text.lower()]
        hits.sort(key=lambda p: p.timestamp or "")
        links.append(
            EntityLink(
                entity=entity,
                occurrence_count=len(hits),
                datasets=sorted({p.source_type for p in hits}),
                occurrences=[p.to_dict() for p in hits[:_MAX_OCCURRENCES_PER_ENTITY]],
                truncated=max(0, len(hits) - _MAX_OCCURRENCES_PER_ENTITY),
            )
        )
    # Richest-linked entity first — what an examiner opening this dataset wants on top.
    links.sort(key=lambda link: -link.occurrence_count)

    reason = ""
    if not entities:
        reason = "case brief named no people or numbers to cross-link"
    elif not passages:
        reason = (
            "no messages/calls/browser/location/contact rows were collected to "
            "search — this is not a finding that the named entities have no "
            "connection to the device"
        )

    return {
        "entities": [link.to_dict() for link in links],
        "entity_count": len(links),
        "passages_scanned": len(passages),
        "reason": reason,
        "disclaimer": _DISCLAIMER,
    }


def build_entity_links_for_case(case: Any, profile: CaseProfile) -> dict:
    """Read a live case's own derived datasets, cross-link, and persist the result as
    the ``entity_links`` derived dataset. Mirrors ``investigate_case``'s read/compute/
    write shape (:mod:`.investigator`) — same six sources :func:`.case_qa.build_passages`
    flattens, read fresh here rather than threading a shared ``derived`` dict through
    the pipeline, so this stays callable standalone (API re-run) as well as inline
    during acquisition.
    """
    derived = {
        name: case.read_derived(name) or []
        for name in (
            "messages",
            "recovered",
            "calls",
            "browser",
            "locations",
            "contacts",
        )
    }
    bundle = build_entity_links(derived, profile)
    case.write_derived("entity_links", bundle)
    return bundle
