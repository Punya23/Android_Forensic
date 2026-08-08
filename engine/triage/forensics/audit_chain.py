"""Tamper-evident hash chaining for the append-only audit log (audit.jsonl).

Forensic purpose
----------------
`Case.audit()` writes one JSON object per line to ``<case>/audit.jsonl``. On its own
that file has no internal structure: line N is not bound to line N-1, so an examiner
(or anyone with write access to the case folder) can edit a `detail` string, delete an
inconvenient `pm.grant`, reorder two actions or splice in a line, and nothing in the
folder contradicts them. This module binds each line to its predecessor:

    entry_hash(N) = SHA-256( prev_hash(N) || canonical_json(event N without entry_hash) )
    prev_hash(N)  = entry_hash(N-1),  prev_hash(1) = GENESIS_HASH

Any surgical change — one flipped byte, a removed line, two swapped lines, an appended
forgery — breaks either a line's own hash ("hash-mismatch") or the link to the line
before it ("link-break"), and `verify_chain()` names the first line at which it broke.

LIMITATIONS — read these before quoting this module in a report
--------------------------------------------------------------
1. Hash chaining is NOT a signature and NOT non-repudiation. There is no secret key
   here. An attacker who can rewrite the WHOLE file can recompute every entry_hash
   from the genesis value and produce a chain that verifies perfectly. What the chain
   actually buys you is that they must rewrite everything after the point they touched
   — a partial or careless edit is caught.
2. A full-file rewrite is only detectable if the chain head was recorded OUT OF BAND
   (printed on the seizure form, signed, emailed to the case officer, read into the
   custody log) at seal time and is later compared against `chain_head()`. That is what
   `seal_record()` / `render_seal_text()` exist for. Without an out-of-band head, this
   module can prove internal consistency and nothing more.
3. Truncating whole trailing lines leaves a valid — but shorter — chain. Only the
   recorded head and event count reveal it. `verify_chain()` therefore always returns
   `head` and `total` so a caller can compare them with the seal.
4. A legacy log written before chaining was enabled has no chain fields at all. Such a
   log is reported as `valid: False` with an explicit "unchained/legacy" reason — never
   as valid, because its integrity genuinely cannot be established.

Everything here is a pure function over a local path or a plain dict; nothing touches a
device and nothing shells out.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any, Optional, Union

__all__ = [
    "GENESIS_HASH",
    "GENESIS_PREIMAGE",
    "CHAIN_FIELD",
    "CHAIN_SELF_FIELD",
    "canonical_event_bytes",
    "compute_entry_hash",
    "chain_event",
    "chain_head",
    "verify_chain",
    "append_chained",
    "seal_record",
    "render_seal_text",
]

# Domain-separated genesis: SHA-256 of the byte string below. Documented and fixed so an
# independent tool (or a defence expert) can re-derive it without trusting this file.
GENESIS_PREIMAGE = b"eRakshak-audit-chain/v1/genesis"
GENESIS_HASH = "128d365760bdd669b7e6823a2b89194c9a84efa7f4b405ec217c4f7b763eb1ef"

CHAIN_FIELD = "prev_hash"
CHAIN_SELF_FIELD = "entry_hash"

ALGORITHM = "sha256"

# Serialising + appending must be atomic with respect to other threads, otherwise two
# writers can read the same head and produce two lines claiming the same predecessor.
_APPEND_LOCK = threading.Lock()

_OUT_OF_BAND_INSTRUCTIONS = (
    "Record this chain head OUT OF BAND — print it on the seizure/custody form, "
    "sign it, or send it to the case officer — and store it somewhere the case folder "
    "cannot reach. Hash chaining alone detects surgical tampering (an edited byte, a "
    "deleted line, reordered lines, a spliced-in entry), because every later entry_hash "
    "would have to be recomputed. It does NOT prevent an attacker with write access to "
    "the entire audit.jsonl from rewriting the whole file and recomputing a valid chain "
    "from the genesis value, and it does not detect the removal of whole trailing lines. "
    "Only comparing this recorded head and event count against a later chain_head() "
    "detects that. There is no secret key involved: this is tamper EVIDENCE, not a "
    "digital signature, and it does not establish cryptographic non-repudiation."
)


# --- serialisation ----------------------------------------------------------
def _json_default(obj: Any) -> str:
    """Last-resort coercion so an unexpected value type can never raise mid-hash.

    Enums already serialise via `models._clean`, but a caller handing us a Path or a
    datetime must not blow up the audit write — degrade to its string form instead.
    """
    return str(obj)


def canonical_event_bytes(event: dict[str, Any]) -> bytes:
    """Deterministic serialisation of an event for hashing.

    Keys are sorted at every level (so dict insertion order is irrelevant), the entry's
    own hash field is excluded (it cannot cover itself), `prev_hash` IS included (so the
    link cannot be re-pointed without breaking the hash), non-ASCII is kept verbatim and
    encoded UTF-8, and separators carry no whitespace. Two different Python runs, or two
    dicts built in a different order, must produce byte-identical output.
    """
    payload = {k: v for k, v in dict(event).items() if k != CHAIN_SELF_FIELD}
    return json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")


def compute_entry_hash(event: dict[str, Any], prev_hash: str) -> str:
    """SHA-256 over ``prev_hash || canonical_event_bytes(event)`` as 64 lowercase hex.

    `prev_hash` is fed in separately as well as (normally) living inside `event`, so the
    digest is bound to the predecessor even for a caller that has not yet written the
    field into the dict.
    """
    digest = hashlib.sha256()
    digest.update(str(prev_hash or "").encode("utf-8"))
    digest.update(canonical_event_bytes(event))
    return digest.hexdigest()


def chain_event(event: dict[str, Any], prev_hash: str) -> dict[str, Any]:
    """Return a NEW dict carrying `prev_hash` and `entry_hash`. The input is untouched.

    An empty/missing `prev_hash` is treated as the genesis link — documented behaviour,
    not a silent guess: a first entry legitimately has no predecessor.
    """
    linked = dict(event)
    linked[CHAIN_SELF_FIELD] = None  # placeholder; excluded from the canonical bytes
    linked[CHAIN_FIELD] = str(prev_hash) if prev_hash else GENESIS_HASH
    linked[CHAIN_SELF_FIELD] = compute_entry_hash(linked, linked[CHAIN_FIELD])
    return linked


# --- reading ----------------------------------------------------------------
def _iter_lines(path: Union[str, Path]) -> list[tuple[int, str]]:
    """(physical 1-indexed line number, raw text) for every non-blank line.

    Physical numbering is kept so `first_bad_line` points at what an examiner sees in a
    text editor. Decoding errors are replaced rather than raised — a corrupted byte then
    surfaces as a hash mismatch on that line instead of an exception.
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    return [(n, raw) for n, raw in enumerate(text.splitlines(), 1) if raw.strip()]


def chain_head(path: Union[str, Path]) -> str:
    """`entry_hash` of the last chained line, or GENESIS_HASH for an empty/absent log.

    Scans backwards for the last line that parses and carries a usable `entry_hash`, so
    appending to a log whose tail is a legacy (unchained) or damaged line still links to
    the most recent real chain state rather than silently restarting from genesis.
    """
    p = Path(path)
    if not p.exists():
        return GENESIS_HASH
    try:
        lines = _iter_lines(p)
    except OSError:
        return GENESIS_HASH
    for _n, raw in reversed(lines):
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict):
            value = obj.get(CHAIN_SELF_FIELD)
            if isinstance(value, str) and value:
                return value
    return GENESIS_HASH


def _error(line: int, kind: str, detail: str) -> dict[str, Any]:
    return {"line": line, "kind": kind, "detail": detail}


def verify_chain(path: Union[str, Path]) -> dict[str, Any]:
    """Re-derive the whole chain from genesis and report exactly where it breaks.

    Error kinds:
      * ``hash-mismatch``        — the line's content no longer matches its own entry_hash
                                   (a byte was edited, or entry_hash was rewritten).
      * ``link-break``           — the line's own hash is fine, but its prev_hash is not
                                   the predecessor's entry_hash (a line was deleted,
                                   reordered, or spliced in).
      * ``malformed-json``       — the line is not parseable JSON (truncation/corruption).
      * ``missing-chain-fields`` — the line carries no usable chain fields (legacy entry,
                                   or a forgery appended by a tool that does not chain).

    A log in which NO line is chained is reported invalid and named as legacy/unchained;
    it is never reported valid by default.
    """
    p = Path(path)
    errors: list[dict[str, Any]] = []
    if not p.exists():
        return {
            "valid": False,
            "total": 0,
            "verified": 0,
            "head": GENESIS_HASH,
            "first_bad_line": None,
            "reason": (
                f"audit log not found at {p} — the absence of the log is not evidence "
                "of integrity; it may have been deleted"
            ),
            "errors": [],
            "unchained_lines": 0,
        }

    try:
        lines = _iter_lines(p)
    except OSError as exc:  # unreadable file: report, never raise
        return {
            "valid": False,
            "total": 0,
            "verified": 0,
            "head": GENESIS_HASH,
            "first_bad_line": None,
            "reason": f"audit log at {p} could not be read: {exc}",
            "errors": [],
            "unchained_lines": 0,
        }

    total = len(lines)
    verified = 0
    unchained = 0
    expected_prev = GENESIS_HASH
    head = GENESIS_HASH

    for lineno, raw in lines:
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError) as exc:
            errors.append(
                _error(
                    lineno,
                    "malformed-json",
                    f"line is not parseable JSON ({exc}); content may be truncated "
                    "or corrupted, and the chain cannot be continued across it",
                )
            )
            continue
        if not isinstance(obj, dict):
            errors.append(
                _error(
                    lineno,
                    "malformed-json",
                    f"line parsed as {type(obj).__name__}, expected a JSON object",
                )
            )
            continue

        claimed = obj.get(CHAIN_SELF_FIELD)
        prev = obj.get(CHAIN_FIELD)
        if not isinstance(claimed, str) or not claimed or not isinstance(prev, str):
            unchained += 1
            errors.append(
                _error(
                    lineno,
                    "missing-chain-fields",
                    f"line has no usable '{CHAIN_FIELD}'/'{CHAIN_SELF_FIELD}' pair; it "
                    "is either a legacy entry written before chaining or an entry "
                    "appended by something that does not chain — its integrity is "
                    "unverifiable",
                )
            )
            continue

        head = claimed  # last line that at least claims a chain position
        recomputed = compute_entry_hash(obj, prev)
        if recomputed != claimed:
            errors.append(
                _error(
                    lineno,
                    "hash-mismatch",
                    f"content does not match its own {CHAIN_SELF_FIELD}: "
                    f"recomputed {recomputed}, line claims {claimed}",
                )
            )
            # Continue from what the line claims, so a single edit produces one error
            # rather than cascading link-breaks down the rest of the file.
            expected_prev = claimed
            continue
        if prev != expected_prev:
            errors.append(
                _error(
                    lineno,
                    "link-break",
                    f"{CHAIN_FIELD} is {prev} but the preceding entry hashes to "
                    f"{expected_prev} — a line was deleted, reordered or inserted here",
                )
            )
            expected_prev = claimed
            continue

        verified += 1
        expected_prev = claimed

    first_bad = errors[0]["line"] if errors else None

    if total == 0:
        reason = (
            "audit log is empty — the chain is vacuously intact; there is nothing to "
            "verify and this is not evidence that events were never recorded"
        )
        valid = True
    elif unchained == total:
        reason = (
            f"unchained/legacy audit log: none of the {total} lines carry "
            f"'{CHAIN_FIELD}'/'{CHAIN_SELF_FIELD}' fields, so tampering cannot be "
            "detected from this file at all"
        )
        valid = False
    elif errors:
        first = errors[0]
        reason = (
            f"chain broken at line {first['line']} ({first['kind']}): {first['detail']}"
        )
        valid = False
    else:
        reason = (
            f"chain intact: {verified} of {total} entries re-derived from the genesis "
            "value; note that this proves internal consistency only — compare the head "
            "against an out-of-band record to exclude a full-file rewrite"
        )
        valid = True

    return {
        "valid": valid,
        "total": total,
        "verified": verified,
        "head": head,
        "first_bad_line": first_bad,
        "reason": reason,
        "errors": errors,
        "unchained_lines": unchained,
    }


# --- writing ----------------------------------------------------------------
def append_chained(path: Union[str, Path], event: dict[str, Any]) -> dict[str, Any]:
    """Chain `event` onto the log at `path` and append it as one flushed JSON line.

    Returns the chained event (the caller may want its `entry_hash` for a receipt). The
    read-head/append pair is held under a lock so concurrent writers cannot both claim
    the same predecessor.
    """
    p = Path(path)
    with _APPEND_LOCK:
        p.parent.mkdir(parents=True, exist_ok=True)
        linked = chain_event(event, chain_head(p))
        line = json.dumps(linked, ensure_ascii=False, default=_json_default)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()  # a crash must not lose the record of an action already taken
    return linked


# --- sealing ----------------------------------------------------------------
def seal_record(
    path: Union[str, Path], *, case_id: str = "", examiner: str = ""
) -> dict[str, Any]:
    """Snapshot the chain head so it can be recorded outside the case folder.

    The head is the only thing that makes a full-file rewrite detectable, which is why
    `instructions` says so in the record itself rather than only in this docstring.
    """
    from ..models import now_iso  # local import: models is owned by the orchestrator

    result = verify_chain(path)
    return {
        "case_id": case_id,
        "examiner": examiner,
        "sealed_at": now_iso(),
        "chain_head": chain_head(path),
        "total_events": result["total"],
        "algorithm": ALGORITHM,
        "instructions": _OUT_OF_BAND_INSTRUCTIONS,
    }


def render_seal_text(seal: dict[str, Any]) -> str:
    """Plain-text block for VERIFICATION.txt — printable and readable over the phone."""
    bar = "=" * 72
    lines = [
        bar,
        "AUDIT LOG SEAL - SNAGR audit.jsonl hash chain",
        bar,
        f"Case ID        : {seal.get('case_id', '') or '(not recorded)'}",
        f"Examiner       : {seal.get('examiner', '') or '(not recorded)'}",
        f"Sealed at (UTC): {seal.get('sealed_at', '')}",
        f"Algorithm      : {seal.get('algorithm', ALGORITHM)}",
        f"Total events   : {seal.get('total_events', 0)}",
        "Genesis hash   :",
        f"  {GENESIS_HASH}",
        "",
        "CHAIN HEAD (record this out of band):",
        f"  {seal.get('chain_head', GENESIS_HASH)}",
        "",
        "INSTRUCTIONS / LIMITATIONS",
        "-" * 72,
    ]
    instructions = str(seal.get("instructions", _OUT_OF_BAND_INSTRUCTIONS))
    # Wrap by hand: no dependency, and a fixed 72-column block prints predictably.
    width = 72
    current = ""
    for word in instructions.split():
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    lines.extend(
        [
            "",
            "To verify later: recompute the chain over audit.jsonl and compare the",
            "resulting head and event count with the values above.",
            bar,
        ]
    )
    return "\n".join(lines) + "\n"
