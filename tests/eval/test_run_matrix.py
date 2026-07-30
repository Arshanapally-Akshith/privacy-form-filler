"""Integration tests for the Phase 6 full-matrix harness (eval/harness/run_matrix.py,
Commit 9). app.orchestration.graph.run_graph and eval.harness.run_matrix._ingest_case_documents
are monkeypatched at their call sites in every test -- these are integration tests of the
harness's own wiring (cache read/write flow, dataset iteration, scoring integration,
ordering), not of the orchestration graph's internal correctness (already covered
exhaustively under tests/orchestration/) or of a live LLM/embedding call (never allowed per
CLAUDE.md §4). Every test uses its own tmp_path cache file.
"""

from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app.api.models import FieldRecord, FieldState, Provenance
from app.boundary.mode import PrivacyMode
from app.orchestration.state import FieldGraphState, OrchestrationState
from eval.harness.response_cache import (
    get_cached_response,
    key_params_from_field,
    load_cache,
    put_cached_response,
)
from eval.harness.run_matrix import (
    _evaluate_field,
    execution_status_from_modes,
    run_matrix,
)

REFERENCE_DATE = date(2026, 7, 29)

_KYC_FIELD_NAMES = (
    "full_name",
    "date_of_birth",
    "pan_number",
    "aadhaar_number",
    "residential_address",
    "pin_code",
    "phone_number",
    "email_address",
    "linked_account_number",
    "annual_income",
)


def _kyc_case(case_id: str, ground_truth: dict[str, str | None]) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "form_schema_id": "kyc_account_opening",
        "reference_date": REFERENCE_DATE.isoformat(),
        "documents": [{"document_id": f"{case_id}__id_proof", "document_type": "id_proof", "text": "placeholder"}],
        "ground_truth": ground_truth,
    }


def _all_fields_ground_truth(value: str = "x") -> dict[str, str | None]:
    return {name: value for name in _KYC_FIELD_NAMES}


def _stub_run_graph(values_by_run_case_id: Mapping[str, Mapping[str, str | None]], calls: list[str]):
    """A run_graph stand-in: given the state's (mode-suffixed) case_id and its one pending
    field, returns a FILLED (or MISSING, if the mapped value is None) FieldRecord for it --
    no real graph execution, no LLM, no embedding."""

    def _run(state: OrchestrationState) -> OrchestrationState:
        calls.append(state.case_id)
        field_name = state.pending_field_names[0]
        value = values_by_run_case_id.get(state.case_id, {}).get(field_name)
        record = FieldRecord(
            name=field_name,
            label=field_name,
            value=value,
            confidence=(0.9 if value is not None else None),
            state=(FieldState.FILLED if value is not None else FieldState.MISSING),
            provenance=(Provenance(document_id="doc-1", page_number=1) if value is not None else None),
        )
        updated_fields = dict(state.fields)
        updated_fields[field_name] = FieldGraphState(record=record, retry_count=0, verifier_traces=[])
        return OrchestrationState(
            case_id=state.case_id,
            form_schema=state.form_schema,
            privacy_mode=state.privacy_mode,
            reference_date=state.reference_date,
            fields=updated_fields,
            pending_field_names=[],
            current_field_name=field_name,
        )

    return _run


def _must_not_be_called(*args: object, **kwargs: object) -> Any:
    raise AssertionError("this should never be called on an all-cache-hit run")


# ---------------------------------------------------------------------------
# Cache-miss population
# ---------------------------------------------------------------------------


def test_cache_miss_performs_the_live_evaluation_and_populates_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_path = tmp_path / "cache.json"
    case = _kyc_case("case-1", _all_fields_ground_truth("Rohan Mehta"))
    # run_matrix always sweeps every PrivacyMode, so a from-scratch cache misses (and must
    # ingest + run_graph) for all three, not just one.
    values_by_run_case_id = {
        f"case-1__{mode.value}": {name: "Rohan Mehta" for name in _KYC_FIELD_NAMES} for mode in PrivacyMode
    }

    ingest_calls: list[str] = []
    run_graph_calls: list[str] = []
    monkeypatch.setattr(
        "eval.harness.run_matrix._ingest_case_documents", lambda rid, docs: ingest_calls.append(rid)
    )
    monkeypatch.setattr(
        "eval.harness.run_matrix.run_graph", _stub_run_graph(values_by_run_case_id, run_graph_calls)
    )

    result = run_matrix([case], cache_path=cache_path)

    # Ingestion happened exactly once per (case, mode), not once per field.
    assert sorted(ingest_calls) == sorted(f"case-1__{mode.value}" for mode in PrivacyMode)
    assert len(run_graph_calls) == len(_KYC_FIELD_NAMES) * len(PrivacyMode)

    for mode in PrivacyMode:
        assert result[mode.value]["accuracy"] == 1.0

    # The cache is now populated for every field, for every mode.
    entries = load_cache(cache_path)
    assert len(entries) == len(_KYC_FIELD_NAMES) * len(PrivacyMode)


def test_cache_miss_response_is_retrievable_afterward_without_a_live_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_path = tmp_path / "cache.json"
    case = _kyc_case("case-1", _all_fields_ground_truth("Rohan Mehta"))
    run_case_id = f"case-1__{PrivacyMode.NONE.value}"

    monkeypatch.setattr("eval.harness.run_matrix._ingest_case_documents", lambda rid, docs: None)
    monkeypatch.setattr(
        "eval.harness.run_matrix.run_graph",
        _stub_run_graph({run_case_id: {name: "Rohan Mehta" for name in _KYC_FIELD_NAMES}}, []),
    )
    run_matrix([case], cache_path=cache_path)

    from app.config.form_schema import load_form_schemas

    schema = next(s for s in load_form_schemas() if s.id == "kyc_account_opening")
    field = next(f for f in schema.fields if f.name == "full_name")
    params = key_params_from_field(
        case_id="case-1", field=field, privacy_mode=PrivacyMode.NONE, reference_date=REFERENCE_DATE, top_k=5
    )
    assert get_cached_response(params, path=cache_path) == {
        "value": "Rohan Mehta",
        "confidence": 0.9,
        "state": "filled",
        "provenance": {"document_id": "doc-1", "page_number": 1},
    }


# ---------------------------------------------------------------------------
# All-cache-hit execution
# ---------------------------------------------------------------------------


def test_all_cache_hit_across_every_mode_never_calls_run_graph_or_ingestion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_matrix always sweeps all three privacy modes, so a genuinely all-cache-hit run
    requires every (field, mode) combination pre-populated -- otherwise the harness would
    correctly (and loudly) hit the "must not be called" stub for whichever mode was left
    uncached. Both monkeypatches raise if invoked at all, for any mode."""
    cache_path = tmp_path / "cache.json"
    case = _kyc_case("case-1", _all_fields_ground_truth("Rohan Mehta"))

    from app.config.form_schema import load_form_schemas

    schema = next(s for s in load_form_schemas() if s.id == "kyc_account_opening")
    for mode in PrivacyMode:
        for field in schema.fields:
            params = key_params_from_field(
                case_id="case-1", field=field, privacy_mode=mode, reference_date=REFERENCE_DATE, top_k=5
            )
            put_cached_response(
                params,
                {"value": "Rohan Mehta", "confidence": 0.9, "state": "filled", "provenance": None},
                path=cache_path,
            )

    monkeypatch.setattr("eval.harness.run_matrix._ingest_case_documents", _must_not_be_called)
    monkeypatch.setattr("eval.harness.run_matrix.run_graph", _must_not_be_called)

    result = run_matrix([case], cache_path=cache_path)

    for mode in PrivacyMode:
        assert result[mode.value]["accuracy"] == 1.0


# ---------------------------------------------------------------------------
# Deterministic output ordering
# ---------------------------------------------------------------------------


def test_field_results_are_ordered_by_case_then_by_schema_field_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_path = tmp_path / "cache.json"
    cases = [
        _kyc_case("case-A", _all_fields_ground_truth("A-value")),
        _kyc_case("case-B", _all_fields_ground_truth("B-value")),
    ]
    values_by_run_case_id = {
        f"case-A__{PrivacyMode.NONE.value}": {name: "A-value" for name in _KYC_FIELD_NAMES},
        f"case-B__{PrivacyMode.NONE.value}": {name: "B-value" for name in _KYC_FIELD_NAMES},
    }
    monkeypatch.setattr("eval.harness.run_matrix._ingest_case_documents", lambda rid, docs: None)
    monkeypatch.setattr("eval.harness.run_matrix.run_graph", _stub_run_graph(values_by_run_case_id, []))

    result = run_matrix(cases, cache_path=cache_path)

    none_field_results = result[PrivacyMode.NONE.value]["field_results"]
    expected_order = [(case_id, field_name) for case_id in ("case-A", "case-B") for field_name in _KYC_FIELD_NAMES]
    actual_order = [(r["case_id"], r["field_name"]) for r in none_field_results]
    assert actual_order == expected_order


def test_output_ordering_is_identical_across_repeated_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cases = [
        _kyc_case("case-A", _all_fields_ground_truth("A-value")),
        _kyc_case("case-B", _all_fields_ground_truth("B-value")),
    ]
    values_by_run_case_id = {
        f"case-A__{mode.value}": {name: "A-value" for name in _KYC_FIELD_NAMES} for mode in PrivacyMode
    } | {f"case-B__{mode.value}": {name: "B-value" for name in _KYC_FIELD_NAMES} for mode in PrivacyMode}
    monkeypatch.setattr("eval.harness.run_matrix._ingest_case_documents", lambda rid, docs: None)
    monkeypatch.setattr("eval.harness.run_matrix.run_graph", _stub_run_graph(values_by_run_case_id, []))

    # Two independent cache files (mirroring eval/dataset/generate_ocr_subset.py's own
    # test_generate_is_deterministic pattern) -- proves the ordering is a property of the
    # code and inputs, not an artifact of one accumulated cache file's insertion order.
    first = run_matrix(cases, cache_path=tmp_path / "first.json")
    second = run_matrix(cases, cache_path=tmp_path / "second.json")

    for mode in PrivacyMode:
        first_order = [(r["case_id"], r["field_name"]) for r in first[mode.value]["field_results"]]
        second_order = [(r["case_id"], r["field_name"]) for r in second[mode.value]["field_results"]]
        assert first_order == second_order


def test_mode_order_in_the_result_dict_matches_the_declared_modes_tuple(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from eval.harness.run_matrix import MODES

    cache_path = tmp_path / "cache.json"
    case = _kyc_case("case-1", _all_fields_ground_truth("x"))
    values_by_run_case_id = {f"case-1__{mode.value}": {name: "x" for name in _KYC_FIELD_NAMES} for mode in PrivacyMode}
    monkeypatch.setattr("eval.harness.run_matrix._ingest_case_documents", lambda rid, docs: None)
    monkeypatch.setattr("eval.harness.run_matrix.run_graph", _stub_run_graph(values_by_run_case_id, []))

    result = run_matrix([case], cache_path=cache_path)

    assert list(result.keys()) == [mode.value for mode in MODES]


# ---------------------------------------------------------------------------
# Summary metric correctness
# ---------------------------------------------------------------------------


def test_summary_metrics_reflect_a_mix_of_real_outcomes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ground truth and stubbed graph output are deliberately mismatched per-field to
    exercise every outcome classify_outcome/summarize_field_results (Commit 7) can
    produce, proving the harness wires ground truth and response through to scoring
    correctly end to end -- not re-testing the scoring arithmetic itself, which is
    tests/eval/test_scoring.py's job."""
    cache_path = tmp_path / "cache.json"
    ground_truth = {
        "full_name": "Rohan Mehta",  # will be extracted correctly -> correct
        "date_of_birth": "12/03/1988",  # will be extracted wrong -> incorrect_value
        "pan_number": None,  # genuinely absent, correctly abstained -> correct_abstention
        "aadhaar_number": "234567890123",  # abstained despite having a value -> incorrect_abstention
        "residential_address": None,  # hallucinated a value -> hallucination
        "pin_code": "122001",
        "phone_number": "9812345678",
        "email_address": "rohan@example.com",
        "linked_account_number": "987654321012",
        "annual_income": "950000",
    }
    case = _kyc_case("case-1", ground_truth)
    run_case_id = f"case-1__{PrivacyMode.NONE.value}"
    stub_values = {
        "full_name": "Rohan Mehta",
        "date_of_birth": "01/01/1999",
        "pan_number": None,
        "aadhaar_number": None,
        "residential_address": "Invented Address",
        "pin_code": "122001",
        "phone_number": "9812345678",
        "email_address": "rohan@example.com",
        "linked_account_number": "987654321012",
        "annual_income": "950000",
    }
    monkeypatch.setattr("eval.harness.run_matrix._ingest_case_documents", lambda rid, docs: None)
    monkeypatch.setattr("eval.harness.run_matrix.run_graph", _stub_run_graph({run_case_id: stub_values}, []))

    result = run_matrix([case], cache_path=cache_path)
    summary = result[PrivacyMode.NONE.value]

    assert summary["total_fields"] == 10
    assert summary["outcome_counts"]["correct"] == 6  # full_name + 4 fields with matching stub values
    assert summary["outcome_counts"]["incorrect_value"] == 1  # date_of_birth
    assert summary["outcome_counts"]["correct_abstention"] == 1  # pan_number
    assert summary["outcome_counts"]["incorrect_abstention"] == 1  # aadhaar_number
    assert summary["outcome_counts"]["hallucination"] == 1  # residential_address
    # correct + correct_abstention = 7 out of 10.
    assert summary["accuracy"] == pytest.approx(7 / 10)


def test_error_from_run_graph_is_recorded_as_an_error_outcome_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_path = tmp_path / "cache.json"
    case = _kyc_case("case-1", _all_fields_ground_truth("x"))

    def _raising_run_graph(state: OrchestrationState) -> OrchestrationState:
        raise RuntimeError("provider failure")

    monkeypatch.setattr("eval.harness.run_matrix._ingest_case_documents", lambda rid, docs: None)
    monkeypatch.setattr("eval.harness.run_matrix.run_graph", _raising_run_graph)

    result = run_matrix([case], cache_path=cache_path)
    summary = result[PrivacyMode.NONE.value]

    assert summary["outcome_counts"]["error"] == len(_KYC_FIELD_NAMES)
    assert summary["accuracy"] == 0.0
    # A failed field is never cached -- there is nothing correct to replay.
    assert load_cache(cache_path) == {}


def test_error_from_ingestion_is_recorded_as_an_error_outcome_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discovered live (Phase 7 Commit 7): an embedding-provider failure during ingestion
    previously propagated uncaught all the way out of run_matrix(), crashing the whole
    process and losing every already-computed in-memory result. Ingestion failures must be
    recorded per-field, exactly like run_graph failures already are -- run_graph must never
    even be reached once ingestion itself has failed."""
    cache_path = tmp_path / "cache.json"
    case = _kyc_case("case-1", _all_fields_ground_truth("x"))

    def _raising_ingest(run_case_id: str, documents: list[dict[str, Any]]) -> None:
        raise RuntimeError("embedding provider rate limit")

    monkeypatch.setattr("eval.harness.run_matrix._ingest_case_documents", _raising_ingest)
    monkeypatch.setattr("eval.harness.run_matrix.run_graph", _must_not_be_called)

    result = run_matrix([case], cache_path=cache_path)
    summary = result[PrivacyMode.NONE.value]

    assert summary["outcome_counts"]["error"] == len(_KYC_FIELD_NAMES)
    assert summary["accuracy"] == 0.0
    assert load_cache(cache_path) == {}


def test_ingestion_is_retried_on_the_next_field_after_a_transient_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed ingestion attempt must not be marked 'ingested' -- self-healing behavior for
    a transient failure (e.g. a rate limit with its own stated retry delay): the very next
    field for the same (case, mode) gets a fresh attempt, which can succeed."""
    cache_path = tmp_path / "cache.json"
    case = _kyc_case("case-1", _all_fields_ground_truth("Rohan Mehta"))
    run_case_id = f"case-1__{PrivacyMode.NONE.value}"

    ingest_attempts: list[str] = []

    def _fails_once_then_succeeds(rid: str, documents: list[dict[str, Any]]) -> None:
        ingest_attempts.append(rid)
        if len(ingest_attempts) == 1:
            raise RuntimeError("embedding provider rate limit")

    monkeypatch.setattr("eval.harness.run_matrix._ingest_case_documents", _fails_once_then_succeeds)
    monkeypatch.setattr(
        "eval.harness.run_matrix.run_graph",
        _stub_run_graph({run_case_id: {name: "Rohan Mehta" for name in _KYC_FIELD_NAMES}}, []),
    )

    result = run_matrix([case], cache_path=cache_path)
    summary = result[PrivacyMode.NONE.value]

    # Exactly one field absorbed the transient failure; every other field of this same
    # (case, mode) succeeded once ingestion was retried and worked.
    assert summary["outcome_counts"]["error"] == 1
    assert summary["outcome_counts"]["correct"] == len(_KYC_FIELD_NAMES) - 1
    # run_matrix sweeps every PrivacyMode, each with its own fresh `ingested` check: the
    # first attempt (case-1__none) fails and is retried once more for the same run_case_id;
    # full_tokenize/policy_engine each ingest successfully on their own first try.
    assert ingest_attempts == [
        "case-1__none",
        "case-1__none",
        "case-1__full_tokenize",
        "case-1__policy_engine",
    ]


# ---------------------------------------------------------------------------
# _evaluate_field, directly (the cache/ingest/run_graph wiring in isolation)
# ---------------------------------------------------------------------------


def test_execution_status_is_complete_when_no_mode_has_any_error_outcome() -> None:
    modes_result = {
        "none": {"outcome_counts": {"correct": 10}},
        "full_tokenize": {"outcome_counts": {"correct": 8, "correct_abstention": 2}},
    }
    assert execution_status_from_modes(modes_result) == "complete"


def test_execution_status_is_partial_when_any_mode_has_at_least_one_error_outcome() -> None:
    modes_result = {
        "none": {"outcome_counts": {"correct": 10}},
        "full_tokenize": {"outcome_counts": {"correct": 5, "error": 3}},
    }
    assert execution_status_from_modes(modes_result) == "partial_errors_present"


def test_execution_status_treats_a_zero_error_count_as_complete() -> None:
    """A mode summary can legitimately carry an explicit 'error': 0 key (scoring.py's own
    outcome_counts only includes outcomes that actually occurred, but a caller constructing
    a summary by hand -- as this test does -- might still include a zero) -- must not be
    mistaken for a partial run."""
    modes_result = {"none": {"outcome_counts": {"correct": 10, "error": 0}}}
    assert execution_status_from_modes(modes_result) == "complete"


def test_evaluate_field_returns_the_cached_response_without_ingesting_or_running_the_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config.form_schema import load_form_schemas

    cache_path = tmp_path / "cache.json"
    schema = next(s for s in load_form_schemas() if s.id == "kyc_account_opening")
    field = next(f for f in schema.fields if f.name == "full_name")
    case = _kyc_case("case-1", _all_fields_ground_truth("Rohan Mehta"))
    params = key_params_from_field(
        case_id="case-1", field=field, privacy_mode=PrivacyMode.NONE, reference_date=REFERENCE_DATE, top_k=5
    )
    cached_response = {"value": "Rohan Mehta", "confidence": 0.9, "state": "filled", "provenance": None}
    put_cached_response(params, cached_response, path=cache_path)

    monkeypatch.setattr("eval.harness.run_matrix._ingest_case_documents", _must_not_be_called)
    monkeypatch.setattr("eval.harness.run_matrix.run_graph", _must_not_be_called)

    response, error = _evaluate_field(
        case=case,
        run_case_id="case-1__none",
        schema=schema,
        field=field,
        privacy_mode=PrivacyMode.NONE,
        reference_date=REFERENCE_DATE,
        cache_path=cache_path,
        ingested=set(),
    )

    assert error is None
    assert response == cached_response
