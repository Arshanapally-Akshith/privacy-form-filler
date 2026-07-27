"""Shared PIN code dataset loader tests (BUILD.md Phase 3, tasks 1 and 4).

Exact counts are asserted deliberately, not just "non-empty" -- this is a pinned, versioned
dataset (DECISIONS.md P6-P9), and BUILD.md's own testing requirement is correctness against
*that* pinned version. A count drifting would mean the committed CSV changed, which should
fail a test, not pass silently.
"""

from app.privacy.pincode_dataset import KNOWN_PINCODES, PINCODE_DISTRICT_STATE_INDEX


def test_known_pincodes_count_matches_the_pinned_dataset_version() -> None:
    assert len(KNOWN_PINCODES) == 19_586


def test_district_state_index_count_after_dropping_na_rows() -> None:
    assert len(PINCODE_DISTRICT_STATE_INDEX) == 19_486


def test_known_pincodes_includes_a_pincode_present_only_via_na_rows() -> None:
    # "121999" exists in the raw government data but only as NA/NA rows -- still a real,
    # allocated PIN code for detection purposes (see pincode_dataset.py's module docstring
    # for why detection and Derive deliberately disagree here).
    assert "121999" in KNOWN_PINCODES
    assert "121999" not in PINCODE_DISTRICT_STATE_INDEX


def test_known_pincodes_excludes_a_genuinely_absent_number() -> None:
    assert "999999" not in KNOWN_PINCODES


def test_district_state_index_resolves_a_clean_unambiguous_pincode() -> None:
    entries = PINCODE_DISTRICT_STATE_INDEX["110001"]
    assert len(entries) == 1
    entry = next(iter(entries))
    assert entry.district == "NEW DELHI"
    assert entry.state == "DELHI"
