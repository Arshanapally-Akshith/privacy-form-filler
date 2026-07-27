"""Shared PIN code dataset loading (BUILD.md Phase 3, tasks 1 and 4).

Loads app/config/data/pincode_district_state.csv (DECISIONS.md P6-P9) exactly once, at
import time, and exposes it in the two projections its two real consumers need: a bare
membership set (app.privacy.detection, PIN-code detection by dataset membership) and a
richer pincode -> {(district, state)} mapping (app.privacy.derive). A single shared loader,
not two duplicated ones -- Commit 1 deliberately kept detection's own loader minimal and
un-extracted ("a single current consumer doesn't justify a shared module"); now that Derive
is a second real consumer of the same file, CLAUDE.md §9's own bar ("two concrete
implementations justify an interface") is met.

Rows where district or state is the literal string "NA" (a raw-data placeholder for
missing/unresolved values in the source government dataset) are dropped from the
district/state mapping -- but NOT from the membership set. A PIN code present in the raw
government data only via NA-placeholder rows (e.g. "121999") is still a real, allocated PIN
code for detection purposes (a 6-digit number that plausibly is a PIN code); it is simply
one Derive cannot confidently resolve a district/state for. Detection and Derive therefore
deliberately disagree here, for a documented reason -- not an inconsistency to paper over.
"""

import csv
from dataclasses import dataclass

from app.privacy.constants import PINCODE_DATASET_PATH


@dataclass(frozen=True)
class PincodeEntry:
    district: str
    state: str


def _load_pincode_data() -> tuple[frozenset[str], dict[str, frozenset[PincodeEntry]]]:
    known_pincodes: set[str] = set()
    index: dict[str, set[PincodeEntry]] = {}
    with PINCODE_DATASET_PATH.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            pincode = row["pincode"]
            known_pincodes.add(pincode)

            district, state = row["district"], row["statename"]
            if district == "NA" or state == "NA":
                continue
            index.setdefault(pincode, set()).add(PincodeEntry(district=district, state=state))

    return frozenset(known_pincodes), {pincode: frozenset(entries) for pincode, entries in index.items()}


KNOWN_PINCODES, PINCODE_DISTRICT_STATE_INDEX = _load_pincode_data()
