"""Unit tests for eval.harness.k_anonymity (Phase 7 Commit 1).

Two groups, per this commit's own testing requirement:
  1. A small, hand-constructed synthetic dataset with a known-by-construction k for every
     equivalence class -- proves the grouping/summary math itself is correct.
  2. A consistency check against the real committed evaluation dataset
     (eval/dataset/phase6_eval_cases.json) -- proves the module runs end to end against real
     data and produces internally consistent, deterministic numbers. It does not hand-verify
     the real dataset's exact k values (that would just be re-deriving the fixture test by
     hand against 56 real cases); it checks the invariants any correct result must satisfy.

Real PIN codes are used throughout (600001 -> Tamil Nadu/Chennai, 110001 -> Delhi/New Delhi,
999999 -> absent from the committed dataset) so this exercises the real
app.privacy.derive lookup, per this commit's "reuse the real privacy transformation
functions" requirement -- nothing here reimplements state/district resolution.
"""

from datetime import date

from app.privacy.dispatch import DeriveAttribute, PolicyAction
from app.privacy.policy_config import PolicyConfig
from eval.harness.k_anonymity import (
    MIN_K_THRESHOLD,
    NAMED_CONFIGS,
    compute_case_exposure,
    compute_k_anonymity,
    compute_k_anonymity_for_named_configs,
    group_into_equivalence_classes,
    load_dataset_cases,
    load_named_configs,
    result_to_dict,
)

_REFERENCE_DATE = date(2025, 1, 1)

_TAMIL_NADU_PIN = "600001"  # real, unambiguous: Tamil Nadu / Chennai
_DELHI_PIN = "110001"  # real, unambiguous: Delhi / New Delhi
_UNKNOWN_PIN = "999999"  # absent from the committed pincode dataset


def _case(case_id: str, dob: str, pin_code: str | None) -> dict[str, object]:
    return {
        "case_id": case_id,
        "reference_date": _REFERENCE_DATE.isoformat(),
        "ground_truth": {"date_of_birth": dob, "pin_code": pin_code},
    }


def _age_state_config() -> PolicyConfig:
    return PolicyConfig(
        name="test_age_state",
        dataset_version="test",
        field_actions={
            "date_of_birth": PolicyAction.GENERALIZE,
            "pin_code": PolicyAction.DERIVE,
        },
        permitted_cooccurrence_sets=[["age_band", "state"]],
    )


def _strict_config() -> PolicyConfig:
    return PolicyConfig(
        name="test_strict",
        dataset_version="test",
        field_actions={
            "date_of_birth": PolicyAction.TOKENIZE,
            "pin_code": PolicyAction.TOKENIZE,
        },
        permitted_cooccurrence_sets=[],
    )


# ---------------------------------------------------------------------------
# compute_case_exposure
# ---------------------------------------------------------------------------


def test_exposure_computes_real_age_band_and_state() -> None:
    # 24/10/2000 vs 2025-01-01: birthday (2025-10-24) hasn't happened yet -> age 24.
    exposure = compute_case_exposure(
        case_id="c1",
        ground_truth={"date_of_birth": "24/10/2000", "pin_code": _TAMIL_NADU_PIN},
        reference_date=_REFERENCE_DATE,
        config=_age_state_config(),
        derive_attribute=DeriveAttribute.STATE,
    )
    assert exposure.exposed_attributes == (("age_band", "20-29"), ("state", "TAMIL NADU"))


def test_exposure_uses_district_when_config_derives_district() -> None:
    exposure = compute_case_exposure(
        case_id="c1",
        ground_truth={"date_of_birth": "24/10/2000", "pin_code": _TAMIL_NADU_PIN},
        reference_date=_REFERENCE_DATE,
        config=_age_state_config(),
        derive_attribute=DeriveAttribute.DISTRICT,
    )
    assert exposure.exposed_attributes == (("age_band", "20-29"), ("district", "CHENNAI"))


def test_exposure_exposes_nothing_under_strict_config() -> None:
    exposure = compute_case_exposure(
        case_id="c1",
        ground_truth={"date_of_birth": "24/10/2000", "pin_code": _TAMIL_NADU_PIN},
        reference_date=_REFERENCE_DATE,
        config=_strict_config(),
        derive_attribute=None,
    )
    assert exposure.exposed_attributes == ()


def test_exposure_falls_back_to_unexposed_on_unknown_pin_like_real_dispatch() -> None:
    """Mirrors app.privacy.dispatch.apply_action's own fail-closed-to-Tokenize behavior for
    an unresolvable PIN: nothing is exposed for that attribute, and this must not raise."""
    exposure = compute_case_exposure(
        case_id="c1",
        ground_truth={"date_of_birth": "24/10/2000", "pin_code": _UNKNOWN_PIN},
        reference_date=_REFERENCE_DATE,
        config=_age_state_config(),
        derive_attribute=DeriveAttribute.STATE,
    )
    assert exposure.exposed_attributes == (("age_band", "20-29"),)


def test_exposure_handles_missing_ground_truth_value() -> None:
    exposure = compute_case_exposure(
        case_id="c1",
        ground_truth={"date_of_birth": "24/10/2000", "pin_code": None},
        reference_date=_REFERENCE_DATE,
        config=_age_state_config(),
        derive_attribute=DeriveAttribute.STATE,
    )
    assert exposure.exposed_attributes == (("age_band", "20-29"),)


# ---------------------------------------------------------------------------
# hand-constructed dataset with known k values
# ---------------------------------------------------------------------------
#
# 6 cases, all under the age_state-shaped config (age_band + state exposed):
#   3 cases: age band 20-29, Tamil Nadu       -> equivalence class of k=3
#   2 cases: age band 30-39, Tamil Nadu       -> equivalence class of k=2
#   1 case:  age band 40-49, Delhi            -> singleton, k=1
#
# Ages computed by hand against _REFERENCE_DATE (2025-01-01):
#   "01/01/2000" -> birthday_this_year == reference_date -> not yet past -> age 24 (20-29)
#   "15/06/2003" -> birthday not yet reached -> age 2025-2003-1 = 21 (20-29)
#   "10/03/2001" -> birthday not yet reached -> age 2025-2001-1 = 23 (20-29)
#   "01/05/1988" -> birthday not yet reached -> age 2025-1988-1 = 36 (30-39)
#   "20/11/1992" -> birthday not yet reached -> age 2025-1992-1 = 32 (30-39)
#   "01/01/1979" -> birthday == reference_date -> already occurred -> age 46 (40-49)

_HAND_BUILT_CASES = [
    _case("kyc_001", "01/01/2000", _TAMIL_NADU_PIN),
    _case("kyc_002", "15/06/2003", _TAMIL_NADU_PIN),
    _case("kyc_003", "10/03/2001", _TAMIL_NADU_PIN),
    _case("kyc_004", "01/05/1988", _TAMIL_NADU_PIN),
    _case("kyc_005", "20/11/1992", _TAMIL_NADU_PIN),
    _case("kyc_006", "01/01/1979", _DELHI_PIN),
]


def test_hand_built_dataset_produces_known_equivalence_classes() -> None:
    result = compute_k_anonymity(
        _HAND_BUILT_CASES,
        _age_state_config(),
        derive_attribute=DeriveAttribute.STATE,
        threshold=MIN_K_THRESHOLD,
    )

    classes_by_key = {ec.exposed_attributes: ec for ec in result.equivalence_classes}
    assert classes_by_key[(("age_band", "20-29"), ("state", "TAMIL NADU"))].k == 3
    assert classes_by_key[(("age_band", "30-39"), ("state", "TAMIL NADU"))].k == 2
    assert classes_by_key[(("age_band", "40-49"), ("state", "DELHI"))].k == 1


def test_hand_built_dataset_summary_statistics_match_hand_computation() -> None:
    result = compute_k_anonymity(
        _HAND_BUILT_CASES,
        _age_state_config(),
        derive_attribute=DeriveAttribute.STATE,
        threshold=MIN_K_THRESHOLD,
    )

    assert result.total_cases == 6
    assert result.total_equivalence_classes == 3
    assert result.minimum_k == 1
    assert result.median_k == 2  # sorted class sizes [1, 2, 3] -> median 2
    assert result.percentage_singletons == (1 / 6) * 100
    assert result.meets_threshold is False  # minimum_k (1) < MIN_K_THRESHOLD (5)


def test_hand_built_dataset_under_strict_config_has_one_class_covering_everyone() -> None:
    result = compute_k_anonymity(
        _HAND_BUILT_CASES,
        _strict_config(),
        derive_attribute=None,
        threshold=MIN_K_THRESHOLD,
    )

    assert result.total_equivalence_classes == 1
    assert result.minimum_k == 6
    assert result.median_k == 6
    assert result.percentage_singletons == 0.0
    assert result.meets_threshold is True


def test_hand_built_dataset_grouping_is_deterministic_across_repeated_calls() -> None:
    first = compute_k_anonymity(_HAND_BUILT_CASES, _age_state_config(), derive_attribute=DeriveAttribute.STATE)
    second = compute_k_anonymity(_HAND_BUILT_CASES, _age_state_config(), derive_attribute=DeriveAttribute.STATE)
    assert first == second


# ---------------------------------------------------------------------------
# group_into_equivalence_classes (grouping logic in isolation)
# ---------------------------------------------------------------------------


def test_group_into_equivalence_classes_groups_by_exact_exposed_tuple() -> None:
    from eval.harness.k_anonymity import CaseExposure

    exposures = [
        CaseExposure(case_id="a", exposed_attributes=(("age_band", "20-29"),)),
        CaseExposure(case_id="b", exposed_attributes=(("age_band", "20-29"),)),
        CaseExposure(case_id="c", exposed_attributes=(("age_band", "30-39"),)),
    ]
    classes = group_into_equivalence_classes(exposures)
    sizes = sorted(ec.k for ec in classes)
    assert sizes == [1, 2]


# ---------------------------------------------------------------------------
# consistency against the real committed evaluation dataset
# ---------------------------------------------------------------------------


def test_real_dataset_loads_and_produces_internally_consistent_results_for_every_named_config() -> None:
    cases = load_dataset_cases()
    configs = load_named_configs()
    results = compute_k_anonymity_for_named_configs(cases, configs)

    assert set(results) == set(NAMED_CONFIGS)

    for result in results.values():
        assert result.total_cases == len(cases)
        assert sum(ec.k for ec in result.equivalence_classes) == result.total_cases
        assert result.total_equivalence_classes == len(result.equivalence_classes)
        assert 1 <= result.minimum_k <= result.median_k <= result.total_cases
        assert 0.0 <= result.percentage_singletons <= 100.0
        assert result.meets_threshold == (result.minimum_k >= MIN_K_THRESHOLD)


def test_real_dataset_strict_config_exposes_nothing_so_every_case_shares_one_class() -> None:
    # strict.json declares no generalize/derive field actions at all (app/config/
    # policy_configs/strict.json) -- every case's exposed tuple is empty, so the entire
    # dataset collapses into exactly one equivalence class.
    cases = load_dataset_cases()
    configs = load_named_configs()
    result = compute_k_anonymity_for_named_configs(cases, {"strict": configs["strict"]})["strict"]

    assert result.total_equivalence_classes == 1
    assert result.minimum_k == result.total_cases
    assert result.percentage_singletons == 0.0
    assert result.meets_threshold is True


def test_real_dataset_computation_is_deterministic_across_repeated_calls() -> None:
    cases = load_dataset_cases()
    configs = load_named_configs()
    first = compute_k_anonymity_for_named_configs(cases, configs)
    second = compute_k_anonymity_for_named_configs(cases, configs)
    assert first == second


# ---------------------------------------------------------------------------
# result_to_dict (CLI-facing serialization)
# ---------------------------------------------------------------------------


def test_result_to_dict_is_json_serializable_and_matches_the_result() -> None:
    import json

    result = compute_k_anonymity(
        _HAND_BUILT_CASES,
        _age_state_config(),
        derive_attribute=DeriveAttribute.STATE,
        threshold=MIN_K_THRESHOLD,
    )
    payload = result_to_dict(result)
    json.dumps(payload)  # must not raise

    assert payload["config_name"] == "test_age_state"
    assert payload["minimum_k"] == result.minimum_k
    assert payload["median_k"] == result.median_k
    assert payload["percentage_singletons"] == result.percentage_singletons
    assert payload["total_equivalence_classes"] == 3
    assert sum(entry["k"] for entry in payload["equivalence_classes"]) == result.total_cases
