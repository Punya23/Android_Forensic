"""Tests for the hotspot client-lease parser (triage.parsers.dhcp_leases)."""

from __future__ import annotations

from pathlib import Path

from triage.parsers.dhcp_leases import (
    CAVEAT_NOT_PERSISTED,
    LEASE_PATHS,
    collect_hotspot_leases,
    parse_dnsmasq_leases,
)


def _write_leases(tmp_path: Path, text: str, name: str = "dnsmasq.leases") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_both_known_paths_are_probed():
    device_paths = [p for p, _ in LEASE_PATHS]
    assert "/data/misc/dhcp/dnsmasq.leases" in device_paths
    assert "/data/misc/wifi/dnsmasq.leases" in device_paths


def test_parses_a_well_formed_lease_line(tmp_path: Path):
    path = _write_leases(
        tmp_path,
        "1735689600 aa:bb:cc:dd:ee:ff 192.168.43.5 android-abcdef 01:aa:bb:cc:dd:ee:ff\n",
    )
    leases = parse_dnsmasq_leases(path)
    assert len(leases) == 1
    lease = leases[0]
    assert lease["mac"] == "aa:bb:cc:dd:ee:ff"
    assert lease["ip"] == "192.168.43.5"
    assert lease["hostname"] == "android-abcdef"
    assert lease["client_id"] == "01:aa:bb:cc:dd:ee:ff"
    assert lease["lease_expiry"] == "2025-01-01T00:00:00Z"
    assert lease["source_file"] == path.name


def test_wildcard_hostname_and_client_id_become_empty_not_literal_star(tmp_path: Path):
    path = _write_leases(
        tmp_path, "1735689600 aa:bb:cc:dd:ee:ff 192.168.43.5 * *\n"
    )
    lease = parse_dnsmasq_leases(path)[0]
    assert lease["hostname"] == ""
    assert lease["client_id"] == ""


def test_zero_expiry_is_a_static_lease_not_a_1970_date(tmp_path: Path):
    path = _write_leases(tmp_path, "0 aa:bb:cc:dd:ee:ff 192.168.43.5 static-host\n")
    lease = parse_dnsmasq_leases(path)[0]
    assert lease["lease_expiry"] == ""


def test_malformed_lines_are_skipped_not_fatal(tmp_path: Path):
    path = _write_leases(
        tmp_path,
        "\n"
        "garbage line with too few fields\n"
        "1735689600 not-a-mac 192.168.43.5 host\n"
        "1735689600 aa:bb:cc:dd:ee:ff 192.168.43.5 host\n",
    )
    leases = parse_dnsmasq_leases(path)
    assert len(leases) == 1
    assert leases[0]["mac"] == "aa:bb:cc:dd:ee:ff"


def test_missing_file_returns_empty_not_error(tmp_path: Path):
    assert parse_dnsmasq_leases(tmp_path / "does-not-exist.leases") == []


def test_collect_dedupes_across_current_and_legacy_path(tmp_path: Path):
    current = _write_leases(
        tmp_path,
        "1735689600 aa:bb:cc:dd:ee:ff 192.168.43.5 phone-a\n"
        "1735693200 11:22:33:44:55:66 192.168.43.6 phone-b\n",
        name="dnsmasq.leases",
    )
    legacy = _write_leases(
        tmp_path,
        # Same lease as `current`, plus a legacy-only entry not seen elsewhere.
        "1735689600 aa:bb:cc:dd:ee:ff 192.168.43.5 phone-a\n"
        "1735696800 77:88:99:aa:bb:cc 192.168.43.7 phone-c\n",
        name="dnsmasq.leases.wifi",
    )
    result = collect_hotspot_leases(
        {
            "/data/misc/dhcp/dnsmasq.leases": current,
            "/data/misc/wifi/dnsmasq.leases": legacy,
        }
    )
    macs = {lease["mac"] for lease in result["leases"]}
    assert macs == {"aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66", "77:88:99:aa:bb:cc"}
    assert len(result["leases"]) == 3


def test_leases_present_carries_client_vs_host_caveat(tmp_path: Path):
    path = _write_leases(
        tmp_path, "1735689600 aa:bb:cc:dd:ee:ff 192.168.43.5 phone-a\n"
    )
    result = collect_hotspot_leases({"/data/misc/dhcp/dnsmasq.leases": path})
    assert result["leases"]
    assert any("CLIENT" in c for c in result["caveats"])


def test_no_leases_found_yields_no_caveat_from_the_collector(tmp_path: Path):
    # The collector only speaks to what it parsed; the "not persisted on this
    # Android version" caveat belongs to the pipeline layer, which knows
    # whether any file was found at all versus found-but-empty.
    result = collect_hotspot_leases({})
    assert result["leases"] == []
    assert result["caveats"] == []


def test_not_persisted_caveat_names_the_mainline_tethering_module():
    assert "mainline" in CAVEAT_NOT_PERSISTED.lower()
    assert "not evidence" in CAVEAT_NOT_PERSISTED.lower()
