"""Command-line entry point for the triage engine.

Examples::

    # Run against a mock device (no phone needed) built by tools/make_corpus.py
    python -m triage.cli acquire --mock _corpus/device_A --case CASE-001 --examiner "Insp. R. Sharma"

    # Run against a real connected device (Tier 0)
    python -m triage.cli acquire --serial <adb-serial> --case CASE-001 --examiner "..."

    # List connected devices
    python -m triage.cli devices
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .acquire import MockDeviceSource, RealDeviceSource
from .adb import Adb
from .pipeline import PipelineConfig, run_acquisition


def _progress(stage: str, pct: float, detail: str) -> None:
    bar = "█" * int(pct * 30) + "·" * (30 - int(pct * 30))
    sys.stderr.write(f"\r[{bar}] {pct*100:5.1f}%  {stage:<10} {detail[:44]:<44}")
    sys.stderr.flush()
    if stage == "done":
        sys.stderr.write("\n")


def cmd_devices(_args) -> int:
    devs = Adb.list_devices()
    if not devs:
        print("No devices detected (adb not found or nothing connected).")
        return 0
    for d in devs:
        print(f"  {d['serial']:<24} {d['state']}")
    return 0


def cmd_acquire(args) -> int:
    if args.mock:
        source = MockDeviceSource(Path(args.mock))
    else:
        adb = Adb(serial=args.serial)
        if not adb.available:
            print("ERROR: adb binary not found; use --mock for a hardware-free run.",
                  file=sys.stderr)
            return 2
        source = RealDeviceSource(adb)

    known: dict[str, str] = {}
    if args.known_hashes:
        known = json.loads(Path(args.known_hashes).read_text())

    cfg = PipelineConfig(
        case_id=args.case, examiner=args.examiner,
        legal_authority=args.authority, scope_note=args.scope,
        cases_root=Path(args.out), known_hashes=known,
    )
    summary = run_acquisition(source, cfg, progress=_progress)
    print(json.dumps(summary["counts"], indent=2))
    print(f"\nCase folder: {summary['case_dir']}")
    print(f"Report:      {summary['report']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="triage", description="eRakshak Android triage engine")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("devices", help="list connected ADB devices")

    a = sub.add_parser("acquire", help="run a full triage acquisition")
    a.add_argument("--mock", help="path to a mock-device fixtures dir (no phone needed)")
    a.add_argument("--serial", help="ADB serial of a real device")
    a.add_argument("--case", required=True, help="case ID")
    a.add_argument("--examiner", required=True, help="examiner name")
    a.add_argument("--authority", default="", help="legal authority reference")
    a.add_argument("--scope", default="", help="scope / minimisation note")
    a.add_argument("--out", default="cases", help="cases root directory")
    a.add_argument("--known-hashes", help="JSON file: {sha256: label} known-hash set")

    args = p.parse_args(argv)
    if args.cmd == "devices":
        return cmd_devices(args)
    if args.cmd == "acquire":
        return cmd_acquire(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
