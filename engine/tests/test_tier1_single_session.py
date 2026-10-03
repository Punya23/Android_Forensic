"""One helper session, not four.

The full-collection run already asks the phone for contacts, call log and SMS. The engine used to
ignore the call log and SMS files from that pass and then reinstall the helper once each for
contacts, call log and SMS, which wiped the app's permissions every time (the phone asked again for
everything) and collected contacts twice. Now the single pass ingests all three, and a separate
session runs only for a dataset the single pass did not deliver.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from triage.pipeline import _helper_flows_still_needed  # noqa: E402

ALL_ON = SimpleNamespace(tier1_contacts=True, tier1_calllog=True, tier1_sms=True)


def test_nothing_runs_again_when_the_single_pass_delivered_everything():
    assert _helper_flows_still_needed(ALL_ON, {"contacts", "calllog", "sms"}) == {
        "contacts": False, "calllog": False, "sms": False,
    }


def test_only_the_missing_dataset_gets_a_second_session():
    assert _helper_flows_still_needed(ALL_ON, {"contacts", "calllog"}) == {
        "contacts": False, "calllog": False, "sms": True,
    }


def test_without_a_full_pass_every_requested_flow_still_runs():
    assert _helper_flows_still_needed(ALL_ON, set()) == {"contacts": True, "calllog": True, "sms": True}
    off = SimpleNamespace(tier1_contacts=False, tier1_calllog=True, tier1_sms=False)
    assert _helper_flows_still_needed(off, set()) == {"contacts": False, "calllog": True, "sms": False}
