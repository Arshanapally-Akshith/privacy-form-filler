"""Validation tests for the committed Phase 6 evaluation dataset (BUILD.md Phase 6
deliverable, DECISIONS.md V3/V4/V5/V6/V17). These tests load
eval/dataset/phase6_eval_cases.json directly -- the committed artifact -- rather than
calling generate_dataset(); the one exception (test_committed_dataset_matches_the_generator)
exists specifically to catch the committed file drifting from what the generator would
produce today (a hand-edit, or an uncommitted generator change).
"""

import json
from pathlib import Path
from typing import Any

from app.config.form_schema import load_form_schemas
from eval.dataset.generate_phase6_dataset import (
    DATASET_PATH,
    OCR_MANIFEST_RELATIVE_PATH,
    _json_default,
    generate_dataset,
)
from eval.dataset.identity_generator import IDENTITY_GENERATOR_VERSION

_ADVERSARIAL_TYPES = {"conflict", "missing_field", "near_duplicate_name", "unusual_format"}


def _load_committed_dataset() -> dict[str, Any]:
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def _schema_field_names() -> dict[str, set[str]]:
    return {schema.id: {field.name for field in schema.fields} for schema in load_form_schemas()}


def test_case_count_is_within_the_planned_range() -> None:
    dataset = _load_committed_dataset()
    case_count = len(dataset["cases"])

    assert 50 <= case_count <= 60
    assert dataset["metadata"]["case_count"] == case_count


def test_both_form_schemas_are_covered_with_reasonable_balance() -> None:
    dataset = _load_committed_dataset()
    schema_ids = [case["form_schema_id"] for case in dataset["cases"]]

    assert set(schema_ids) == {"kyc_account_opening", "insurance_policy_application"}
    kyc_count = schema_ids.count("kyc_account_opening")
    insurance_count = schema_ids.count("insurance_policy_application")
    assert kyc_count >= 20
    assert insurance_count >= 20


def test_adversarial_coverage_is_roughly_20_percent_and_covers_all_four_types() -> None:
    dataset = _load_committed_dataset()
    cases = dataset["cases"]
    adversarial_cases = [case for case in cases if case["adversarial"] is not None]

    proportion = len(adversarial_cases) / len(cases)
    assert 0.15 <= proportion <= 0.30, f"adversarial proportion {proportion:.2%} is not roughly 20% (DECISIONS.md V4)"

    observed_types = {case["adversarial"]["adversarial_type"] for case in adversarial_cases}
    assert observed_types == _ADVERSARIAL_TYPES


def test_adversarial_types_are_represented_in_both_schemas() -> None:
    dataset = _load_committed_dataset()
    by_schema: dict[str, set[str]] = {}
    for case in dataset["cases"]:
        if case["adversarial"] is None:
            continue
        by_schema.setdefault(case["form_schema_id"], set()).add(case["adversarial"]["adversarial_type"])

    assert by_schema["kyc_account_opening"]
    assert by_schema["insurance_policy_application"]


def test_metadata_is_complete_and_internally_consistent() -> None:
    dataset = _load_committed_dataset()
    metadata = dataset["metadata"]

    for key in (
        "dataset_version",
        "identity_generator_version",
        "generator_seed",
        "generator_commit",
        "generated_at",
        "reference_date",
        "case_count",
        "ocr_manifest_path",
    ):
        assert key in metadata, f"missing metadata key: {key}"
        assert metadata[key] not in (None, ""), f"metadata key {key!r} is empty"

    assert metadata["identity_generator_version"] == IDENTITY_GENERATOR_VERSION
    assert isinstance(metadata["generator_seed"], int)
    assert metadata["ocr_manifest_path"] == OCR_MANIFEST_RELATIVE_PATH


def test_every_case_id_is_unique() -> None:
    dataset = _load_committed_dataset()
    case_ids = [case["case_id"] for case in dataset["cases"]]
    assert len(case_ids) == len(set(case_ids))


def test_every_case_ground_truth_has_exactly_the_fields_its_schema_defines() -> None:
    dataset = _load_committed_dataset()
    field_names_by_schema = _schema_field_names()

    for case in dataset["cases"]:
        expected_fields = field_names_by_schema[case["form_schema_id"]]
        assert set(case["ground_truth"]) == expected_fields, case["case_id"]


def test_missing_field_cases_null_out_at_least_one_required_field() -> None:
    dataset = _load_committed_dataset()
    field_specs_by_schema = {
        schema.id: {field.name: field.required for field in schema.fields} for schema in load_form_schemas()
    }

    missing_field_cases = [
        case for case in dataset["cases"] if case["adversarial"] is not None and case["adversarial"]["adversarial_type"] == "missing_field"
    ]
    assert missing_field_cases

    for case in missing_field_cases:
        required_fields = {name for name, required in field_specs_by_schema[case["form_schema_id"]].items() if required}
        null_required_fields = {name for name in required_fields if case["ground_truth"][name] is None}
        assert null_required_fields, f"{case['case_id']} does not null out any required field"


def test_conflict_cases_never_have_a_null_ground_truth_for_the_conflicted_field() -> None:
    dataset = _load_committed_dataset()
    conflict_cases = [
        case for case in dataset["cases"] if case["adversarial"] is not None and case["adversarial"]["adversarial_type"] == "conflict"
    ]
    assert conflict_cases

    for case in conflict_cases:
        field = case["adversarial"]["detail"]["field"]
        assert case["ground_truth"][field] is not None
        assert case["ground_truth"][field] == case["adversarial"]["detail"]["primary_value"]


def test_ocr_manifest_cases_exist_in_the_main_dataset_with_matching_ground_truth() -> None:
    """The core OCR-coverage requirement: fixtures/phase6_ocr/manifest.json is the sole
    authoritative document->image mapping (never duplicated here), but the cases it points
    at must be real, ground-truth-identical entries in the main dataset."""
    dataset = _load_committed_dataset()
    cases_by_id = {case["case_id"]: case for case in dataset["cases"]}

    manifest_path = Path(dataset["metadata"]["ocr_manifest_path"])
    manifest = json.loads((DATASET_PATH.parent / manifest_path).read_text(encoding="utf-8"))
    assert manifest  # sanity: the loop below would vacuously pass on an empty manifest

    seen_case_ids: set[str] = set()
    for entry in manifest:
        case_id = entry["source_case_id"]
        seen_case_ids.add(case_id)
        assert case_id in cases_by_id, f"OCR manifest references unknown case {case_id!r}"
        assert entry["ground_truth"] == cases_by_id[case_id]["ground_truth"]
        assert entry["form_schema_id"] == cases_by_id[case_id]["form_schema_id"]

    # Both form schemas must have at least one case with OCR coverage (V17).
    schema_ids_with_ocr = {cases_by_id[case_id]["form_schema_id"] for case_id in seen_case_ids}
    assert schema_ids_with_ocr == {"kyc_account_opening", "insurance_policy_application"}


def test_committed_dataset_matches_the_generator() -> None:
    """Guards against the committed file silently drifting from what the generator
    currently produces (a hand-edit, or an uncommitted generator change) -- compares case
    content exactly, ignoring only the two metadata fields that are expected to vary
    between generation runs (generated_at, and generator_commit if HEAD has moved since)."""
    committed = _load_committed_dataset()
    # Round-trip through the same JSON encoding main() uses (json.dumps(..., default=
    # _json_default) then json.loads) before comparing -- generate_dataset() itself returns
    # raw Python objects (e.g. date_of_birth as a real datetime.date), while `committed` was
    # already normalized to JSON-native types when it was written. Comparing the two
    # representations directly would report a spurious diff on every date field.
    fresh = json.loads(json.dumps(generate_dataset(), default=_json_default))

    assert committed["cases"] == fresh["cases"]
    for key in ("dataset_version", "identity_generator_version", "generator_seed", "reference_date", "case_count", "ocr_manifest_path"):
        assert committed["metadata"][key] == fresh["metadata"][key], key
