"""Detection recall measurement (BUILD.md Phase 3, task 1, "written alongside": detection
recall on a labeled fixture set, per entity type).

Recall is measured, not assumed -- per BUILD.md's own risk table ("detection recall is
measured, not assumed"). The fixture set deliberately includes cases the heuristic name/
date/address detectors are expected to miss (an all-caps name, an abbreviated-initial name,
an abbreviated month, a building-name address with no recognized street-suffix keyword), so
the measured numbers below are honest, not inflated by only testing easy cases.

Per CLAUDE.md §4, these thresholds are pinned to the values actually measured against this
fixture set and must not be lowered to make CI pass -- a regression here means the code (or
the fixture set) changed in a way that needs investigation, not a threshold adjustment.

DECISIONS.md is deliberately NOT updated by this commit -- recorded there in a dedicated
documentation/baseline commit later, per the approved Phase 3 plan.
"""

import json
from collections import defaultdict
from pathlib import Path

from app.privacy.detection import EntityType, detect_entities

FIXTURES_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "pii_detection" / "labeled_samples.json"

# Measured against tests/fixtures/pii_detection/labeled_samples.json. Structured types
# (regex/checksum/dataset-validated) measure 1.0; the heuristic name/date/address
# detectors measure less than 1.0 by design -- see the fixture file's "known_miss" cases.
_MINIMUM_RECALL_BY_TYPE = {
    EntityType.AADHAAR: 1.0,
    EntityType.PAN: 1.0,
    EntityType.PASSPORT: 1.0,
    EntityType.ACCOUNT_NUMBER: 1.0,
    EntityType.PHONE: 1.0,
    EntityType.EMAIL: 1.0,
    EntityType.PIN_CODE: 1.0,
    EntityType.DATE: 0.6,
    EntityType.ADDRESS: 0.6,
    EntityType.NAME: 0.4,
}


def _load_labeled_samples() -> list[dict]:
    return json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))


def _measure_recall_by_type() -> dict[EntityType, float]:
    found_counts: dict[EntityType, int] = defaultdict(int)
    total_counts: dict[EntityType, int] = defaultdict(int)

    for sample in _load_labeled_samples():
        detected = detect_entities(sample["text"])
        for expected in sample["expected_entities"]:
            entity_type = EntityType(expected["entity_type"])
            total_counts[entity_type] += 1
            if any(
                expected["text"] in d.text
                for d in detected
                if d.entity_type == entity_type
            ):
                found_counts[entity_type] += 1

    return {
        entity_type: found_counts[entity_type] / total_counts[entity_type]
        for entity_type in total_counts
    }


def test_fixture_set_covers_every_entity_type() -> None:
    """A gap here means the recall measurement below is silently incomplete for a type --
    this must fail loudly rather than a type quietly reporting 0/0."""
    measured = _measure_recall_by_type()
    assert set(measured) == set(EntityType)


def test_measured_recall_meets_pinned_floor_per_type() -> None:
    measured = _measure_recall_by_type()
    for entity_type, minimum in _MINIMUM_RECALL_BY_TYPE.items():
        assert measured[entity_type] >= minimum, (
            f"{entity_type.value} recall regressed: {measured[entity_type]:.2f} < pinned floor {minimum:.2f}"
        )
