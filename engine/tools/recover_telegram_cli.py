#!/usr/bin/env python3
"""recover_telegram_cli.py — standalone Telegram cache4.db forensic recovery CLI.

Usage (from the engine/ directory):
    python tools/recover_telegram_cli.py /path/to/cache4.db
    python tools/recover_telegram_cli.py /path/to/cache4.db --output results.json
    python tools/recover_telegram_cli.py /path/to/cache4.db --verbose

Typical field workflow (rooted device):
    # 1. Copy the DB out via root.
    adb shell su -c "cp /data/data/org.telegram.messenger/files/cache4.db /sdcard/Download/tg_cache4.db"
    adb pull /sdcard/Download/tg_cache4.db ./tg_cache4.db

    # 2. Run this tool.
    python tools/recover_telegram_cli.py ./tg_cache4.db --output ./tg_recovery.json

No-root note
------------
If ``cache4.db`` was not pulled (device is not rooted), the tool prints the
standard fallback message and exits cleanly:

    "Telegram full chat history requires root. Only media from gallery is available."

Exit codes:
    0 — success (even if zero messages were found)
    1 — file not found / not available (no root)
    2 — unexpected internal error
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure the engine package is importable when run from engine/tools/.
_ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))

from triage.parsers.telegram import (  # noqa: E402
    recover_telegram_messages,
    export_recovered_messages_json,
    detect_telegram_schema,
)
from triage.config import Confidence  # noqa: E402

# ANSI colour codes (disabled on non-TTY)
_IS_TTY = sys.stdout.isatty()
_RESET = "\033[0m" if _IS_TTY else ""
_BOLD = "\033[1m" if _IS_TTY else ""
_GREEN = "\033[92m" if _IS_TTY else ""
_YELLOW = "\033[93m" if _IS_TTY else ""
_CYAN = "\033[96m" if _IS_TTY else ""
_RED = "\033[91m" if _IS_TTY else ""
_DIM = "\033[2m" if _IS_TTY else ""


_CONFIDENCE_STYLE = {
    Confidence.LIVE.value: _GREEN,
    Confidence.RECOVERED_VERIFIED.value: _CYAN,
    Confidence.CARVED_PARTIAL.value: _YELLOW,
    Confidence.DELETION_DETECTED.value: _RED,
}

_CONFIDENCE_LABEL = {
    Confidence.LIVE.value: "Live",
    Confidence.RECOVERED_VERIFIED.value: "Recovered — Verified",
    Confidence.CARVED_PARTIAL.value: "Carved — Partial",
    Confidence.DELETION_DETECTED.value: "Deletion Detected — No Content",
}


def _badge(conf: str) -> str:
    colour = _CONFIDENCE_STYLE.get(conf, "")
    label = _CONFIDENCE_LABEL.get(conf, conf)
    return f"{colour}[{label}]{_RESET}"


def _print_schema(schema: dict) -> None:
    print(f"\n{_BOLD}Schema detected:{_RESET} {schema.get('version_label', 'unknown')}")
    print(
        f"  Columns ({schema.get('col_count', 0)}): "
        f"{', '.join(schema.get('raw_columns', []))}"
    )
    print(f"  Canonical mapping:")
    for k, v in schema.get("mapping", {}).items():
        print(f"    {k:15s} → {v or '(not found)'}")


def _print_summary(counts: dict) -> None:
    print(f"\n{_BOLD}Recovery summary:{_RESET}")
    print(f"  {_GREEN}Live rows               {_RESET}: {counts.get('live', 0)}")
    print(
        f"  {_CYAN}Recovered — Verified    {_RESET}: {counts.get('recovered_verified', 0)}"
    )
    print(
        f"  {_YELLOW}Carved — Partial        {_RESET}: {counts.get('carved_partial', 0)}"
    )
    print(
        f"  {_RED}Deletion Detected       {_RESET}: {counts.get('deletion_detected', 0)}"
    )
    print(f"  {_BOLD}Total                   {_RESET}: {counts.get('total', 0)}")


def _print_messages(messages: list[dict], verbose: bool, limit: int = 50) -> None:
    shown = messages[:limit]
    print(f"\n{_BOLD}Messages ({len(shown)} of {len(messages)} shown):{_RESET}")
    print("-" * 72)
    for i, msg in enumerate(shown, 1):
        badge = _badge(msg.get("confidence", ""))
        ts = msg.get("timestamp") or "unknown time"
        body = msg.get("body", "").replace("\n", " ").strip()
        sender = msg.get("sender", "<unknown>")
        print(f"{i:>4}. {badge}  [{ts}]  {_BOLD}{sender}{_RESET}")
        print(f"      {body[:120]}{'…' if len(body) > 120 else ''}")
        if verbose:
            print(f"      {_DIM}provenance: {msg.get('provenance', '')}{_RESET}")
            print(
                f"      {_DIM}carve_method: {msg.get('carve_method', '')}"
                f"  page: {msg.get('page')}  offset: {msg.get('offset')}{_RESET}"
            )
            if msg.get("warnings"):
                for w in msg["warnings"]:
                    print(f"      {_YELLOW}⚠  {w}{_RESET}")
        print()

    if len(messages) > limit:
        print(f"  … {len(messages) - limit} more rows (use --output to see all)")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Telegram cache4.db forensic recovery — eRakshak Android Triage",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "db_path",
        metavar="CACHE4_DB",
        help="Path to the locally-obtained cache4.db file",
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="OUTPUT_JSON",
        help="Write full recovery results to this JSON file",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show provenance detail for each message",
    )
    parser.add_argument(
        "--schema-only",
        action="store_true",
        help="Print schema info and exit without running recovery",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Max messages to display in the terminal (default: 50)",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path)

    print(f"\n{_BOLD}eRakshak Telegram Recovery CLI{_RESET}")
    print(f"Target : {db_path}")

    if not db_path.exists():
        print(f"\n{_RED}File not found: {db_path}{_RESET}")
        print(
            "\nTelegram full chat history requires root. "
            "Only media from gallery is available."
        )
        return 1

    # --schema-only mode.
    if args.schema_only:
        schema = detect_telegram_schema(db_path)
        _print_schema(schema.__dict__ if hasattr(schema, "__dict__") else {})
        return 0

    print("Running recovery …")

    try:
        result = recover_telegram_messages(db_path)
    except Exception as exc:
        print(f"\n{_RED}Unexpected error: {exc}{_RESET}", file=sys.stderr)
        return 2

    if not result.get("available"):
        print(f"\n{_RED}{result.get('error', 'Recovery unavailable')}{_RESET}")
        return 1

    # Print schema info.
    if result.get("schema"):
        _print_schema(result["schema"])

    # Print summary.
    _print_summary(result.get("counts", {}))

    # Print messages.
    messages = result.get("messages", [])
    if messages:
        _print_messages(messages, verbose=args.verbose, limit=args.limit)
    else:
        print(f"\n{_YELLOW}No messages found.{_RESET}")

    # JSON export.
    if args.output:
        out_path = Path(args.output)
        try:
            export_recovered_messages_json(result, out_path)
            print(f"\n{_GREEN}Full results written to: {out_path}{_RESET}")
        except Exception as exc:
            print(f"\n{_RED}Failed to write JSON: {exc}{_RESET}", file=sys.stderr)
            return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
