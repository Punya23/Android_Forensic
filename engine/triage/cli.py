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
            print(
                "ERROR: adb binary not found; use --mock for a hardware-free run.",
                file=sys.stderr,
            )
            return 2
        source = RealDeviceSource(adb)

    known: dict[str, str] = {}
    if args.known_hashes:
        known = json.loads(Path(args.known_hashes).read_text())

    # --overwrite: delete the existing case folder so the run is fresh
    if getattr(args, "overwrite", False):
        import shutil as _shutil

        existing = Path(args.out) / args.case
        if existing.exists():
            _shutil.rmtree(existing)
            print(f"[overwrite] removed existing case: {existing}")

    cfg = PipelineConfig(
        case_id=args.case,
        examiner=args.examiner,
        legal_authority=args.authority,
        scope_note=args.scope,
        cases_root=Path(args.out),
        known_hashes=known,
        tier1_contacts=args.tier1_contacts,
        tier1_calllog=args.tier1_calllog,
        tier1_sms=args.tier1_sms,
        tier1_collect_all=getattr(args, "tier1_collect_all", False),
        tier2_telegram=args.tier2_telegram,
        tier2_instagram=getattr(args, "tier2_instagram", False),
        tier2_snapchat=getattr(args, "tier2_snapchat", False),
        tier2_wifi=getattr(args, "tier2_wifi", False),
        tier2_browser_history=getattr(args, "tier2_browser_history", False),
        tier2_maps_location=getattr(args, "tier2_maps_location", False),
        tier2_whatsapp_backup=getattr(args, "tier2_whatsapp_backup", False),
        # Deep artifact stages (see PipelineConfig for what each reads).
        wifi_live=not getattr(args, "no_wifi_live", False),
        scan_encrypted_apps=not getattr(args, "no_encrypted_app_scan", False),
        run_self_validation=not getattr(args, "no_self_validation", False),
        tier2_bt_config=getattr(args, "tier2_bt_config", False),
        tier2_app_presence=getattr(args, "tier2_app_presence", False),
        tier2_antiforensics=getattr(args, "tier2_antiforensics", False),
        tier2_recent_tasks=getattr(args, "tier2_recent_tasks", False),
        case_description=getattr(args, "case_description", "") or "",
        case_number=getattr(args, "case_number", "") or "",
        llm_provider=getattr(args, "llm", "") or "",
        use_case_bank=not getattr(args, "no_case_bank", False),
        learn_from_case=not getattr(args, "no_learn", False),
    )
    summary = run_acquisition(source, cfg, progress=_progress)
    print(json.dumps(summary["counts"], indent=2))
    print(f"\nCase folder: {summary['case_dir']}")
    print(f"Report:      {summary['report']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="triage", description="SNAGR Android triage engine"
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("devices", help="list connected ADB devices")

    a = sub.add_parser("acquire", help="run a full triage acquisition")
    a.add_argument(
        "--mock", help="path to a mock-device fixtures dir (no phone needed)"
    )
    a.add_argument("--serial", help="ADB serial of a real device")
    a.add_argument("--case", required=True, help="case ID")
    a.add_argument("--examiner", required=True, help="examiner name")
    a.add_argument("--authority", default="", help="legal authority reference")
    a.add_argument("--scope", default="", help="scope / minimisation note")
    a.add_argument("--out", default="cases", help="cases root directory")
    a.add_argument(
        "--overwrite",
        action="store_true",
        help="delete and re-create the case folder if it already exists",
    )
    a.add_argument("--known-hashes", help="JSON file: {sha256: label} known-hash set")
    a.add_argument(
        "--tier1-contacts",
        action="store_true",
        help="Run Tier-1 helper to collect contacts",
    )
    a.add_argument(
        "--tier1-calllog",
        action="store_true",
        help="Run Tier-1 helper to collect call log",
    )
    a.add_argument(
        "--tier1-sms", action="store_true", help="Run Tier-1 helper to collect SMS"
    )
    a.add_argument(
        "--tier2-telegram",
        action="store_true",
        help="Root-required: pull cache4.db and run Telegram deep recovery",
    )
    a.add_argument(
        "--case-description",
        dest="case_description",
        default="",
        help="Plain-language case brief → targeted collection plan + AI leads",
    )
    a.add_argument(
        "--case-number",
        dest="case_number",
        default="",
        help="FIR / crime number, recorded on the case profile and used when "
        "learning from this case's outcome",
    )
    a.add_argument(
        "--llm",
        default="",
        help="LLM provider for the intelligence layer: heuristic|ollama",
    )
    a.add_argument(
        "--no-case-bank",
        dest="no_case_bank",
        action="store_true",
        help="Plan from the expert ontology alone: skip prior-case retrieval and "
        "the learned knowledge graph",
    )
    a.add_argument(
        "--no-learn",
        dest="no_learn",
        action="store_true",
        help="Do not feed this run's outcome back into the knowledge graph",
    )

    # -- Tier-1 / Tier-2 stages that existed but had no CLI switch -------------
    a.add_argument(
        "--tier1-collect-all",
        action="store_true",
        help="Tier 1 (STATE-CHANGING): helper APK dump_all — media inventory, apps, "
        "accounts, calendar, usage. Installs an APK and grants permissions; both are "
        "audited and reversed with verification.",
    )
    a.add_argument(
        "--tier2-instagram", action="store_true", help="Root: pull and recover direct.db"
    )
    a.add_argument(
        "--tier2-snapchat", action="store_true", help="Root: pull and recover arroyo.db"
    )
    a.add_argument(
        "--tier2-wifi",
        action="store_true",
        help="Root: recover saved Wi-Fi credentials from the system config",
    )
    a.add_argument(
        "--tier2-whatsapp-backup",
        action="store_true",
        help="Root: decrypt msgstore.db.crypt* backups",
    )
    a.add_argument(
        "--tier2-browser-history",
        action="store_true",
        help="Root: pull Chrome/Brave/Samsung Internet/Edge History and Firefox "
        "places.sqlite from app-private storage and recover deleted rows",
    )
    a.add_argument(
        "--tier2-maps-location",
        action="store_true",
        help="Root: pull Google Maps navigation history (da_destination_history), saved "
        "places (gmm_myplaces.db), map searches, and the Play-services cell/WiFi "
        "geolocation cache — the last of which holds positions from periods when GPS "
        "was switched off",
    )

    # -- Deep artifact stages --------------------------------------------------
    a.add_argument(
        "--tier2-bt-config",
        action="store_true",
        help="Root: parse /data/misc/bluedroid/bt_config.conf. NOTE the bond "
        "timestamps are pairing-record writes, NOT connection or co-location times.",
    )
    a.add_argument(
        "--tier2-app-presence",
        action="store_true",
        help="Root: packages.xml + usagestats + gass.db — evidence that an app was "
        "present or executed, which survives uninstall",
    )
    a.add_argument(
        "--tier2-antiforensics",
        action="store_true",
        help="Root: enumerate Android users (work profiles / clones / Secure Folder), "
        "privacy-app packages and the factory-reset trace. Observations only — the "
        "output never asserts intent.",
    )
    a.add_argument(
        "--tier2-recent-tasks",
        action="store_true",
        help="Root + AFU: /data/system_ce/0/recent_tasks and task snapshots. Skipped "
        "with a stated reason on a BFU device (credential-encrypted, unreadable).",
    )
    a.add_argument(
        "--no-wifi-live",
        action="store_true",
        help="Skip the non-root live Wi-Fi capture (dumpsys wifi/netstats/connectivity). "
        "That data is volatile and is lost on reboot, so skipping it forfeits it.",
    )
    a.add_argument(
        "--no-encrypted-app-scan",
        action="store_true",
        help="Skip cataloguing SQLCipher app databases. Without it, an encrypted "
        "Signal/Threema database is indistinguishable from the app never being installed.",
    )
    a.add_argument(
        "--no-self-validation",
        action="store_true",
        help="Skip the offline known-answer self-test. The case will then carry no "
        "validation record — which is not a pass.",
    )

    args = p.parse_args(argv)
    if args.cmd == "devices":
        return cmd_devices(args)
    if args.cmd == "acquire":
        return cmd_acquire(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
