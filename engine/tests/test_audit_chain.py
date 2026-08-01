"""Tests for the tamper-evident hash-chained audit log (P2-2).

Every fixture is built programmatically in tmp_path — there are no binary fixture files.
The tamper tests deliberately mimic what an insider with write access to the case folder
would actually do: edit a byte, drop a line, swap two lines, splice one in, truncate.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from triage.forensics.audit_chain import (
    CHAIN_FIELD,
    CHAIN_SELF_FIELD,
    GENESIS_HASH,
    GENESIS_PREIMAGE,
    append_chained,
    canonical_event_bytes,
    chain_event,
    chain_head,
    compute_entry_hash,
    render_seal_text,
    seal_record,
    verify_chain,
)
from triage.forensics import audit_chain


# --- helpers ----------------------------------------------------------------
def _event(i: int) -> dict:
    return {
        "timestamp": f"2026-08-01T10:{i:02d}:00Z",
        "action": "adb.pull",
        "detail": f"pulled artifact {i}",
        "examiner": "P. Surana",
        "command": f"adb pull /sdcard/f{i}",
        "result": "ok",
        "alters_device": False,
        "tier": "tier0",
        "extra": {"artifact_id": f"a{i:05d}"},
    }


def _write_log(path, count: int) -> list[dict]:
    return [append_chained(path, _event(i)) for i in range(count)]


def _lines(path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _put_lines(path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- canonical serialisation ------------------------------------------------
def test_canonical_bytes_independent_of_insertion_order(tmp_path):
    """Same content, two different dict build orders -> byte-identical output."""
    a = {"action": "x", "extra": {"z": 1, "a": 2}, "detail": "d"}
    b = {"detail": "d", "extra": {"a": 2, "z": 1}, "action": "x"}
    assert canonical_event_bytes(a) == canonical_event_bytes(b)
    assert canonical_event_bytes(a) == b'{"action":"x","detail":"d","extra":{"a":2,"z":1}}'


def test_canonical_bytes_excludes_self_hash_but_covers_prev_hash():
    base = {"action": "x", CHAIN_FIELD: "a" * 64}
    with_self = dict(base)
    with_self[CHAIN_SELF_FIELD] = "deadbeef"
    assert canonical_event_bytes(base) == canonical_event_bytes(with_self)

    repointed = dict(base)
    repointed[CHAIN_FIELD] = "b" * 64
    assert canonical_event_bytes(base) != canonical_event_bytes(repointed)


def test_canonical_bytes_keep_unicode_verbatim_and_use_tight_separators():
    raw = canonical_event_bytes({"detail": "मिला café", "n": 1})
    assert "café".encode("utf-8") in raw
    assert b"\\u" not in raw  # ensure_ascii=False
    assert b", " not in raw and b": " not in raw  # no whitespace in separators


def test_canonical_bytes_never_raise_on_odd_value_types(tmp_path):
    """Graceful degradation: an unexpected value type must not break an audit write."""
    raw = canonical_event_bytes({"path": tmp_path, "detail": "x"})
    assert b"detail" in raw


# --- genesis and single-link primitives -------------------------------------
def test_genesis_hash_is_documented_and_rederivable():
    assert GENESIS_HASH == hashlib.sha256(GENESIS_PREIMAGE).hexdigest()
    assert len(GENESIS_HASH) == 64
    assert all(c in "0123456789abcdef" for c in GENESIS_HASH)


def test_compute_entry_hash_is_hex64_deterministic_and_prev_sensitive():
    ev = _event(1)
    h1 = compute_entry_hash(ev, GENESIS_HASH)
    h2 = compute_entry_hash(dict(reversed(list(ev.items()))), GENESIS_HASH)
    assert h1 == h2
    assert len(h1) == 64 and all(c in "0123456789abcdef" for c in h1)
    assert compute_entry_hash(ev, "f" * 64) != h1


def test_chain_event_links_to_genesis_and_does_not_mutate_input():
    ev = _event(0)
    snapshot = json.dumps(ev, sort_keys=True)
    linked = chain_event(ev, GENESIS_HASH)
    assert json.dumps(ev, sort_keys=True) == snapshot  # input untouched
    assert CHAIN_FIELD not in ev and CHAIN_SELF_FIELD not in ev
    assert linked[CHAIN_FIELD] == GENESIS_HASH
    assert linked[CHAIN_SELF_FIELD] == compute_entry_hash(linked, GENESIS_HASH)


# --- head on empty / missing logs -------------------------------------------
def test_chain_head_missing_file_is_genesis(tmp_path):
    assert chain_head(tmp_path / "nope.jsonl") == GENESIS_HASH


def test_chain_head_empty_file_is_genesis(tmp_path):
    p = tmp_path / "audit.jsonl"
    p.write_text("", encoding="utf-8")
    assert chain_head(p) == GENESIS_HASH


def test_verify_missing_file_is_invalid_and_says_so(tmp_path):
    res = verify_chain(tmp_path / "nope.jsonl")
    assert res["valid"] is False
    assert res["total"] == 0
    assert "not found" in res["reason"]
    assert res["first_bad_line"] is None


def test_verify_empty_file_is_vacuously_valid(tmp_path):
    p = tmp_path / "audit.jsonl"
    p.touch()
    res = verify_chain(p)
    assert res["valid"] is True
    assert res["total"] == 0 and res["verified"] == 0
    assert res["head"] == GENESIS_HASH
    assert "empty" in res["reason"]


# --- the happy path ---------------------------------------------------------
def test_append_and_verify_fifty_events(tmp_path):
    p = tmp_path / "audit.jsonl"
    written = _write_log(p, 50)
    res = verify_chain(p)
    assert res["valid"] is True
    assert res["total"] == 50 and res["verified"] == 50
    assert res["errors"] == [] and res["unchained_lines"] == 0
    assert res["first_bad_line"] is None
    assert res["head"] == written[-1][CHAIN_SELF_FIELD] == chain_head(p)
    # every line points at its predecessor
    assert written[0][CHAIN_FIELD] == GENESIS_HASH
    for prev, cur in zip(written, written[1:]):
        assert cur[CHAIN_FIELD] == prev[CHAIN_SELF_FIELD]


def test_blank_lines_do_not_break_the_chain(tmp_path):
    p = tmp_path / "audit.jsonl"
    _write_log(p, 4)
    lines = _lines(p)
    lines.insert(2, "")
    _put_lines(p, lines)
    res = verify_chain(p)
    assert res["valid"] is True and res["total"] == 4


def test_auditevent_dicts_from_models_chain_cleanly(tmp_path):
    """The API must accept exactly what Case.audit() writes today."""
    from triage.models import AuditEvent, now_iso

    p = tmp_path / "audit.jsonl"
    for action in ("case.create", "pm.grant", "adb.pull"):
        append_chained(
            p,
            AuditEvent(
                timestamp=now_iso(),
                action=action,
                detail=f"{action} happened",
                examiner="P. Surana",
                alters_device=action == "pm.grant",
                tier="tier1",
                extra={"nested": {"k": [1, 2, 3]}},
            ).to_dict(),
        )
    res = verify_chain(p)
    assert res["valid"] is True and res["verified"] == 3


# --- tamper detection -------------------------------------------------------
def test_single_edited_byte_is_a_hash_mismatch(tmp_path):
    p = tmp_path / "audit.jsonl"
    _write_log(p, 5)
    lines = _lines(p)
    lines[2] = lines[2].replace("pulled artifact 2", "pulled artifact 9")
    _put_lines(p, lines)

    res = verify_chain(p)
    assert res["valid"] is False
    assert res["first_bad_line"] == 3
    assert res["errors"][0]["kind"] == "hash-mismatch"
    assert res["total"] == 5 and res["verified"] == 4


def test_deleted_line_is_a_link_break(tmp_path):
    p = tmp_path / "audit.jsonl"
    _write_log(p, 5)
    lines = _lines(p)
    del lines[2]  # remove the 3rd event entirely
    _put_lines(p, lines)

    res = verify_chain(p)
    assert res["valid"] is False
    assert res["total"] == 4
    assert res["first_bad_line"] == 3
    assert res["errors"][0]["kind"] == "link-break"


def test_swapped_lines_are_a_link_break(tmp_path):
    p = tmp_path / "audit.jsonl"
    _write_log(p, 5)
    lines = _lines(p)
    lines[1], lines[2] = lines[2], lines[1]
    _put_lines(p, lines)

    res = verify_chain(p)
    assert res["valid"] is False
    assert res["first_bad_line"] == 2
    assert res["errors"][0]["kind"] == "link-break"
    assert all(e["kind"] == "link-break" for e in res["errors"])


def test_forged_appended_line_without_chain_fields_is_flagged(tmp_path):
    p = tmp_path / "audit.jsonl"
    _write_log(p, 5)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(_event(99)) + "\n")

    res = verify_chain(p)
    assert res["valid"] is False
    assert res["total"] == 6 and res["verified"] == 5
    assert res["first_bad_line"] == 6
    assert res["errors"][0]["kind"] == "missing-chain-fields"
    assert res["unchained_lines"] == 1


def test_forged_appended_line_replaying_an_earlier_entry_is_a_link_break(tmp_path):
    """Replaying a genuine earlier line keeps its self-hash valid but not its link."""
    p = tmp_path / "audit.jsonl"
    _write_log(p, 5)
    lines = _lines(p)
    lines.append(lines[1])
    _put_lines(p, lines)

    res = verify_chain(p)
    assert res["valid"] is False
    assert res["first_bad_line"] == 6
    assert res["errors"][0]["kind"] == "link-break"


def test_forged_appended_line_with_edited_content_is_a_hash_mismatch(tmp_path):
    p = tmp_path / "audit.jsonl"
    written = _write_log(p, 5)
    forged = dict(written[-1])
    forged["detail"] = "nothing to see here"  # keeps the stale hashes
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(forged) + "\n")

    res = verify_chain(p)
    assert res["valid"] is False
    assert res["first_bad_line"] == 6
    assert res["errors"][0]["kind"] == "hash-mismatch"


def test_truncated_file_is_malformed_json(tmp_path):
    p = tmp_path / "audit.jsonl"
    _write_log(p, 5)
    data = p.read_bytes()
    p.write_bytes(data[:-20])  # chop the tail of the last line

    res = verify_chain(p)
    assert res["valid"] is False
    assert res["first_bad_line"] == 5
    assert res["errors"][0]["kind"] == "malformed-json"
    assert res["verified"] == 4


def test_malformed_json_line_mid_file_is_reported(tmp_path):
    p = tmp_path / "audit.jsonl"
    _write_log(p, 4)
    lines = _lines(p)
    lines[1] = lines[1][: len(lines[1]) // 2]
    _put_lines(p, lines)

    res = verify_chain(p)
    assert res["valid"] is False
    assert res["first_bad_line"] == 2
    assert res["errors"][0]["kind"] == "malformed-json"


def test_whole_trailing_lines_removed_verifies_but_head_moves(tmp_path):
    """Honesty: clean truncation leaves a valid chain — only the sealed head reveals it."""
    p = tmp_path / "audit.jsonl"
    _write_log(p, 5)
    seal = seal_record(p, case_id="C-1", examiner="P. Surana")

    _put_lines(p, _lines(p)[:3])
    res = verify_chain(p)
    assert res["valid"] is True  # internally consistent...
    assert res["total"] == 3
    assert res["head"] != seal["chain_head"]  # ...but not the sealed head
    assert res["total"] != seal["total_events"]


def test_full_file_rewrite_verifies_but_contradicts_the_out_of_band_head(tmp_path):
    """The documented limitation, asserted: a total rewrite re-chains cleanly."""
    p = tmp_path / "audit.jsonl"
    _write_log(p, 5)
    seal = seal_record(p, case_id="C-1", examiner="P. Surana")

    events = [json.loads(line) for line in _lines(p)]
    events[2]["detail"] = "sanitised"
    prev = GENESIS_HASH
    rebuilt = []
    for ev in events:
        ev.pop(CHAIN_FIELD, None)
        ev.pop(CHAIN_SELF_FIELD, None)
        linked = chain_event(ev, prev)
        prev = linked[CHAIN_SELF_FIELD]
        rebuilt.append(json.dumps(linked, ensure_ascii=False))
    _put_lines(p, rebuilt)

    res = verify_chain(p)
    assert res["valid"] is True and res["verified"] == 5
    assert res["head"] != seal["chain_head"]  # only the out-of-band head catches it


# --- legacy logs ------------------------------------------------------------
def test_legacy_unchained_log_is_invalid_with_a_clear_reason(tmp_path):
    p = tmp_path / "audit.jsonl"
    _put_lines(p, [json.dumps(_event(i)) for i in range(3)])

    res = verify_chain(p)
    assert res["valid"] is False
    assert res["total"] == 3 and res["verified"] == 0
    assert res["unchained_lines"] == 3
    assert "unchained" in res["reason"] or "legacy" in res["reason"]
    assert res["head"] == GENESIS_HASH
    assert all(e["kind"] == "missing-chain-fields" for e in res["errors"])


def test_appending_to_a_legacy_log_keeps_the_legacy_lines_flagged(tmp_path):
    p = tmp_path / "audit.jsonl"
    _put_lines(p, [json.dumps(_event(i)) for i in range(2)])
    append_chained(p, _event(9))

    res = verify_chain(p)
    assert res["valid"] is False  # the legacy prefix is still unverifiable
    assert res["unchained_lines"] == 2
    assert res["verified"] == 1
    assert res["total"] == 3
    assert res["head"] == chain_head(p) != GENESIS_HASH


# --- awkward payloads -------------------------------------------------------
def test_unicode_and_embedded_newlines_round_trip_and_verify(tmp_path):
    p = tmp_path / "audit.jsonl"
    detail = "संदेश\nline two\ttab «quoted» 😀 \\ backslash \" quote"
    written = append_chained(
        p, {"action": "note", "detail": detail, "examiner": "अन्वेषक"}
    )
    append_chained(p, _event(1))

    assert len(_lines(p)) == 2  # embedded newline stayed inside its JSON line
    parsed = json.loads(_lines(p)[0])
    assert parsed["detail"] == detail
    assert parsed[CHAIN_SELF_FIELD] == written[CHAIN_SELF_FIELD]
    assert verify_chain(p)["valid"] is True


def test_nested_extra_dicts_round_trip_and_verify(tmp_path):
    p = tmp_path / "audit.jsonl"
    extra = {
        "b": {"deep": {"deeper": [1, {"x": None, "a": True}]}},
        "a": [{"z": 1, "y": 2}],
    }
    append_chained(p, {"action": "x", "detail": "d", "extra": extra})
    assert json.loads(_lines(p)[0])["extra"] == extra
    res = verify_chain(p)
    assert res["valid"] is True and res["verified"] == 1


def test_editing_a_nested_extra_value_is_detected(tmp_path):
    p = tmp_path / "audit.jsonl"
    append_chained(p, {"action": "x", "detail": "d", "extra": {"sha256": "aa" * 32}})
    lines = _lines(p)
    lines[0] = lines[0].replace("aa" * 32, "bb" * 32)
    _put_lines(p, lines)
    res = verify_chain(p)
    assert res["valid"] is False
    assert res["errors"][0]["kind"] == "hash-mismatch"


# --- sealing ----------------------------------------------------------------
def test_seal_record_shape_and_head_matches_chain_head(tmp_path):
    p = tmp_path / "audit.jsonl"
    _write_log(p, 7)
    seal = seal_record(p, case_id="CASE-2026-01", examiner="P. Surana")

    assert set(seal) == {
        "case_id",
        "examiner",
        "sealed_at",
        "chain_head",
        "total_events",
        "algorithm",
        "instructions",
    }
    assert seal["chain_head"] == chain_head(p)
    assert seal["total_events"] == 7
    assert seal["algorithm"] == "sha256"
    assert seal["sealed_at"].endswith("Z")
    assert seal["case_id"] == "CASE-2026-01"


def test_seal_record_on_empty_log_reports_genesis(tmp_path):
    p = tmp_path / "audit.jsonl"
    p.touch()
    seal = seal_record(p)
    assert seal["chain_head"] == GENESIS_HASH and seal["total_events"] == 0


def test_seal_instructions_state_the_out_of_band_limitation(tmp_path):
    p = tmp_path / "audit.jsonl"
    _write_log(p, 2)
    text = seal_record(p)["instructions"].lower()
    assert "out of band" in text
    assert "rewrit" in text  # full-file rewrite caveat
    assert "non-repudiation" in text


def test_render_seal_text_contains_head_and_warning(tmp_path):
    p = tmp_path / "audit.jsonl"
    _write_log(p, 3)
    seal = seal_record(p, case_id="C-9", examiner="P. Surana")
    text = render_seal_text(seal)

    assert seal["chain_head"] in text
    assert "OUT OF BAND" in text.upper()
    assert "C-9" in text and "P. Surana" in text
    assert GENESIS_HASH in text
    assert text.endswith("\n")
    assert max(len(line) for line in text.splitlines()) <= 80


def test_module_docstring_documents_the_limitation():
    doc = (audit_chain.__doc__ or "").lower()
    assert "out of band" in doc
    assert "non-repudiation" in doc
    assert "signature" in doc


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
