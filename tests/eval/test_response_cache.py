"""Tests for the Phase 6 evaluation response cache (BUILD.md Phase 6 risk table, R10).
Pure file-based key/value store -- no I/O beyond a tmp_path-scoped JSON file, no LLM, no
dataset generator involved, offline per CLAUDE.md §4 by construction. Every test uses its
own tmp_path cache file, never eval/harness/response_cache.DEFAULT_CACHE_PATH.
"""

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app.boundary.mode import PrivacyMode
from app.config.form_schema import FieldType, FormFieldSpec
from eval.harness.response_cache import (
    CacheKeyParams,
    CacheMissError,
    CorruptCacheEntryError,
    compute_cache_key,
    get_cached_response,
    key_params_from_field,
    load_cache,
    put_cached_response,
)

REFERENCE_DATE = date(2026, 7, 29)

_FIELD = FormFieldSpec(
    name="full_name",
    label="Full Name",
    type=FieldType.NAME,
    required=True,
    expected_format="As it appears on the primary ID proof",
    policy_action_ref="name",
)


def _params(**overrides: Any) -> CacheKeyParams:
    defaults: dict[str, Any] = {
        "case_id": "kyc_clean_001",
        "field_name": "full_name",
        "field_label": "Full Name",
        "field_type": "name",
        "expected_format": "As it appears on the primary ID proof",
        "privacy_mode": PrivacyMode.NONE.value,
        "reference_date": REFERENCE_DATE.isoformat(),
        "top_k": 5,
    }
    defaults.update(overrides)
    return CacheKeyParams(**defaults)


# ---------------------------------------------------------------------------
# Cache hit / miss
# ---------------------------------------------------------------------------


def test_cache_hit_returns_the_stored_response(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    params = _params()
    response = {"value": "Rohan Mehta", "confidence": 0.95}

    put_cached_response(params, response, path=cache_path)

    assert get_cached_response(params, path=cache_path) == response


def test_cache_miss_on_an_empty_cache_raises_explicitly(tmp_path: Path) -> None:
    cache_path = tmp_path / "does_not_exist.json"

    with pytest.raises(CacheMissError):
        get_cached_response(_params(), path=cache_path)


def test_cache_miss_never_falls_back_to_any_default_value(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    put_cached_response(_params(case_id="case-A"), {"value": "A"}, path=cache_path)

    # A different case_id, everything else identical, must still be a genuine miss --
    # not silently resolved to the one entry that does exist.
    with pytest.raises(CacheMissError):
        get_cached_response(_params(case_id="case-B"), path=cache_path)


def test_load_cache_on_a_missing_file_returns_an_empty_dict_not_an_error(tmp_path: Path) -> None:
    assert load_cache(tmp_path / "nope.json") == {}


# ---------------------------------------------------------------------------
# Privacy-mode separation
# ---------------------------------------------------------------------------


def test_privacy_mode_separation_different_modes_get_different_keys() -> None:
    none_key = compute_cache_key(_params(privacy_mode=PrivacyMode.NONE.value))
    policy_key = compute_cache_key(_params(privacy_mode=PrivacyMode.POLICY_ENGINE.value))
    tokenize_key = compute_cache_key(_params(privacy_mode=PrivacyMode.FULL_TOKENIZE.value))

    assert len({none_key, policy_key, tokenize_key}) == 3


def test_privacy_mode_separation_a_response_cached_under_one_mode_is_not_visible_under_another(
    tmp_path: Path,
) -> None:
    cache_path = tmp_path / "cache.json"
    none_params = _params(privacy_mode=PrivacyMode.NONE.value)
    policy_params = _params(privacy_mode=PrivacyMode.POLICY_ENGINE.value)

    put_cached_response(none_params, {"value": "Rohan Mehta"}, path=cache_path)

    # Same case_id/field/reference_date/top_k, only privacy_mode differs -- must be a miss,
    # never a silent replay of the `none`-mode response under `policy_engine`.
    with pytest.raises(CacheMissError):
        get_cached_response(policy_params, path=cache_path)

    # The none-mode entry itself is of course still retrievable.
    assert get_cached_response(none_params, path=cache_path) == {"value": "Rohan Mehta"}


def test_both_privacy_modes_can_be_cached_independently_for_the_same_case_and_field(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    none_params = _params(privacy_mode=PrivacyMode.NONE.value)
    policy_params = _params(privacy_mode=PrivacyMode.POLICY_ENGINE.value)

    put_cached_response(none_params, {"value": "Rohan Mehta"}, path=cache_path)
    put_cached_response(policy_params, {"value": "TOKEN123"}, path=cache_path)

    assert get_cached_response(none_params, path=cache_path) == {"value": "Rohan Mehta"}
    assert get_cached_response(policy_params, path=cache_path) == {"value": "TOKEN123"}
    assert len(load_cache(cache_path)) == 2


# ---------------------------------------------------------------------------
# Deterministic key generation
# ---------------------------------------------------------------------------


def test_identical_params_produce_the_identical_key() -> None:
    assert compute_cache_key(_params()) == compute_cache_key(_params())


@pytest.mark.parametrize(
    "override",
    [
        {"case_id": "different-case"},
        {"field_name": "different_field"},
        {"field_label": "Different Label"},
        {"field_type": "date"},
        {"expected_format": None},
        {"privacy_mode": PrivacyMode.FULL_TOKENIZE.value},
        {"reference_date": "2026-01-01"},
        {"top_k": 10},
    ],
)
def test_changing_any_single_parameter_changes_the_key(override: dict[str, Any]) -> None:
    base_key = compute_cache_key(_params())
    changed_key = compute_cache_key(_params(**override))

    assert base_key != changed_key


def test_key_params_from_field_matches_manually_constructed_params() -> None:
    built = key_params_from_field(
        case_id="kyc_clean_001",
        field=_FIELD,
        privacy_mode=PrivacyMode.NONE,
        reference_date=REFERENCE_DATE,
        top_k=5,
    )
    assert compute_cache_key(built) == compute_cache_key(_params())


def test_key_generation_is_stable_across_repeated_calls_not_dict_ordering() -> None:
    keys = {compute_cache_key(_params()) for _ in range(10)}
    assert len(keys) == 1


# ---------------------------------------------------------------------------
# Corrupted / incomplete cache entries
# ---------------------------------------------------------------------------


def test_corrupted_entry_missing_response_key_fails_loudly(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    params = _params()
    cache_key = compute_cache_key(params)
    cache_path.write_text(
        f'{{"entries": {{"{cache_key}": {{"key_params": {{}}}}}}}}',
        encoding="utf-8",
    )

    with pytest.raises(CorruptCacheEntryError):
        get_cached_response(params, path=cache_path)


def test_corrupted_entry_missing_key_params_fails_loudly(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    params = _params()
    cache_key = compute_cache_key(params)
    cache_path.write_text(
        f'{{"entries": {{"{cache_key}": {{"response": {{"value": "x"}}}}}}}}',
        encoding="utf-8",
    )

    with pytest.raises(CorruptCacheEntryError):
        get_cached_response(params, path=cache_path)


def test_corrupted_entry_that_is_not_an_object_fails_loudly(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    params = _params()
    cache_key = compute_cache_key(params)
    cache_path.write_text(f'{{"entries": {{"{cache_key}": "not-a-dict"}}}}', encoding="utf-8")

    with pytest.raises(CorruptCacheEntryError):
        get_cached_response(params, path=cache_path)


def test_incomplete_cache_file_missing_entries_key_behaves_as_empty(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    cache_path.write_text('{"generated_at": "2026-07-29T00:00:00+00:00"}', encoding="utf-8")

    assert load_cache(cache_path) == {}
    with pytest.raises(CacheMissError):
        get_cached_response(_params(), path=cache_path)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_put_persists_to_disk_and_survives_a_fresh_load(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    put_cached_response(_params(), {"value": "Rohan Mehta"}, path=cache_path)

    reloaded_entries = load_cache(cache_path)
    assert len(reloaded_entries) == 1


def test_put_does_not_discard_previously_cached_entries(tmp_path: Path) -> None:
    """Accumulates rather than regenerating in full -- the deliberate difference from
    embedding_cache.py's save_cache documented in this module's own docstring."""
    cache_path = tmp_path / "cache.json"
    put_cached_response(_params(case_id="case-A"), {"value": "A"}, path=cache_path)
    put_cached_response(_params(case_id="case-B"), {"value": "B"}, path=cache_path)

    assert get_cached_response(_params(case_id="case-A"), path=cache_path) == {"value": "A"}
    assert get_cached_response(_params(case_id="case-B"), path=cache_path) == {"value": "B"}
