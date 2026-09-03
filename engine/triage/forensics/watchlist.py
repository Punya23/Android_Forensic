"""Examiner-curated watchlist matching: exact-value hits against a maintained list of
persons/identifiers of interest, checked against every case's own contacts/messages/
calls. Unlike a scam or contradiction flag, a watchlist entry is examiner-supplied
ground truth — an exact match on a number the department already knows matters — so a
hit is reported at CRITICAL severity without the hedging a keyword guess needs.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Set
import datetime

logger = logging.getLogger(__name__)


class WatchlistMatcher:
    def __init__(self, watchlist_path: Path):
        self.watchlist_path = Path(watchlist_path)
        self.watchlist: Dict[str, Set[str]] = {
            "phone_numbers": set(),
            "upi_ids": set(),
            "bank_accounts": set(),
            "emails": set(),
            "names": set(),
        }
        #: Set when the watchlist file exists but couldn't be read — an empty
        #: watchlist after a load failure must not look identical to "no watchlist
        #: configured": an examiner who added entries and then sees zero matches
        #: needs to know the file was corrupt, not conclude no match exists.
        self.load_error: str = ""
        self.load_watchlist(self.watchlist_path)

    def load_watchlist(self, watchlist_path: Path) -> Dict[str, List[str]]:
        """Load watchlist from JSON file."""
        self.load_error = ""
        if not watchlist_path.exists():
            return {k: list(v) for k, v in self.watchlist.items()}

        try:
            with open(watchlist_path, "r") as f:
                data = json.load(f)
            for cat in self.watchlist.keys():
                if cat in data:
                    self.watchlist[cat] = set(data[cat])
        except Exception as e:
            self.load_error = f"{watchlist_path}: {e}"
            logger.warning("Watchlist load failed, treating as empty: %s", self.load_error)

        return {k: list(v) for k, v in self.watchlist.items()}

    def _save_watchlist(self):
        """Save the watchlist to disk atomically — a write interrupted mid-flush must
        never leave a truncated, half-written watchlist that then loads as empty."""
        data = {k: list(v) for k, v in self.watchlist.items()}
        self.watchlist_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.watchlist_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self.watchlist_path)

    def add_to_watchlist(self, category: str, value: str) -> None:
        """Add new entry to watchlist and preserve existing entries."""
        if category in self.watchlist:
            self.watchlist[category].add(value)
            self._save_watchlist()

    def remove_from_watchlist(self, category: str, value: str) -> None:
        """Remove entry from watchlist."""
        if category in self.watchlist and value in self.watchlist[category]:
            self.watchlist[category].remove(value)
            self._save_watchlist()

    def match_data(self, data: List[Dict], data_type: str) -> List[Dict]:
        """Match extracted data against watchlist. Return matches with metadata."""
        matches = []
        for item in data:
            if data_type == "contact":
                number = item.get("number", "")
                name = item.get("name", "")
                email = item.get("email", "")
                if number in self.watchlist["phone_numbers"]:
                    matches.append(
                        {"category": "phone_numbers", "value": number, "source": item}
                    )
                if name and name in self.watchlist["names"]:
                    matches.append({"category": "names", "value": name, "source": item})
                if email and email in self.watchlist["emails"]:
                    matches.append(
                        {"category": "emails", "value": email, "source": item}
                    )

            elif data_type == "message":
                # Very basic matching in body for upi/bank, ideally would use regex extraction first
                body = str(item.get("body", ""))
                for upi in self.watchlist["upi_ids"]:
                    if upi in body:
                        matches.append(
                            {"category": "upi_ids", "value": upi, "source": item}
                        )
                for acc in self.watchlist["bank_accounts"]:
                    if acc in body:
                        matches.append(
                            {"category": "bank_accounts", "value": acc, "source": item}
                        )
                # Check sender
                sender = item.get("sender", "")
                if sender in self.watchlist["phone_numbers"]:
                    matches.append(
                        {"category": "phone_numbers", "value": sender, "source": item}
                    )

            elif data_type == "call":
                number = item.get("number", "")
                if number in self.watchlist["phone_numbers"]:
                    matches.append(
                        {"category": "phone_numbers", "value": number, "source": item}
                    )

        return matches

    def get_watchlist_alerts(self, matches: List[Dict]) -> List[Dict]:
        """Generate alerts for matches."""
        alerts = []
        for match in matches:
            alerts.append(
                {
                    "severity": "CRITICAL",
                    "category": match["category"],
                    "value": match["value"],
                    "source_context": str(
                        match["source"].get("source_file", "Unknown")
                    ),
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            )
        return alerts


# Standalone function wrappers as requested by the spec
def load_watchlist(watchlist_path: Path) -> Dict[str, List[str]]:
    matcher = WatchlistMatcher(watchlist_path)
    return matcher.load_watchlist(watchlist_path)


def match_data(data: List[Dict], watchlist: Dict) -> List[Dict]:
    # Need to reconstruct matcher just to use watchlist matching if used procedurally
    matcher = WatchlistMatcher(Path("/dev/null"))  # Dummy path
    for k, v in watchlist.items():
        if k in matcher.watchlist:
            matcher.watchlist[k] = set(v)
    # The requirement didn't specify data type in match_data, so we try to infer it
    all_matches = []
    # Infer type based on keys
    for item in data:
        if "body" in item and "sender" in item:
            all_matches.extend(matcher.match_data([item], "message"))
        elif "call_type" in item:
            all_matches.extend(matcher.match_data([item], "call"))
        elif "name" in item and "number" in item:
            all_matches.extend(matcher.match_data([item], "contact"))
    return all_matches


def add_to_watchlist(watchlist_path: Path, category: str, value: str) -> None:
    matcher = WatchlistMatcher(watchlist_path)
    matcher.add_to_watchlist(category, value)


def remove_from_watchlist(watchlist_path: Path, category: str, value: str) -> None:
    matcher = WatchlistMatcher(watchlist_path)
    matcher.remove_from_watchlist(category, value)


def get_watchlist_alerts(matches: List[Dict]) -> List[Dict]:
    matcher = WatchlistMatcher(Path("/dev/null"))
    return matcher.get_watchlist_alerts(matches)
