"""Phase 6 Commit 10: end-to-end determinism / self-validation for the full evaluation
harness (eval/harness/run_matrix.py). BUILD.md Phase 6's own exit criteria name this
directly -- "harness self-test... determinism verified" -- as work distinct from Commit 9's
own (narrower, per-behavior) integration tests: this file validates the harness as a whole,
against a real slice of the committed dataset, run repeatedly and under both a cold and a
warm cache, and checks that its own JSON serialization is actually reproducible, not just
its in-memory return value.

app.orchestration.graph.run_graph and eval.harness.run_matrix._ingest_case_documents are
monkeypatched in every test, exactly as in test_run_matrix.py -- these tests validate the
harness's own wiring and reproducibility guarantees, not the orchestration graph's internal
correctness (tests/orchestration/) or a live LLM/embedding call (never allowed, CLAUDE.md
§4). Cases come from eval.harness.run_matrix.load_dataset() -- the real committed artifact,
sliced to a small prefix for test speed -- rather than a hand-built fixture, so these tests
exercise the harness against its actual production input shape, not a stand-in that could
quietly drift from it.

**Limitation discovered while writing these tests, worth stating plainly:** the response
cache *file's own bytes* are not fully reproducible across independent runs --
eval.harness.response_cache.put_cached_response stamps a top-level "generated_at" timestamp
on every write, so the file's raw bytes differ between two separately-timed runs even when
every cached entry's content is identical. Determinism claims in this file (and, by
extension, DECISIONS.md's "every measured value must be reproducible from a stored run")
apply to the cache's *entries* content and to the harness's *result* output -- never to the
cache file's own byte-for-byte representation, which was never a reproducibility target to
begin with (it is a mutable, accumulating scratch file, not a stored result).
test_cache_entries_are_reproducible_but_the_cache_files_own_bytes_are_not makes this
distinction a checked property instead of just an assertion in prose.

**Deterministic dataset requirement, made explicit.** Every determinism claim this file
checks assumes the *input case list is itself fixed* between the two runs being compared --
this module always calls _dataset_subset() fresh each time rather than reusing one Python
list object, specifically so "the same dataset" means "the same committed JSON content
independently reloaded," not "the same in-memory object." If the committed dataset
(eval/dataset/phase6_eval_cases.json) were regenerated with a different seed or composition
between two runs, results would legitimately differ -- that would be a *dataset* change, not
a harness determinism failure, and is exactly why eval/dataset/generate_phase6_dataset.py's
own test (test_committed_dataset_matches_the_generator) guards the dataset's own
reproducibility separately, upstream of anything tested here.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.api.models import FieldRecord, FieldState, Provenance
from app.boundary.mode import PrivacyMode
from app.config.form_schema import FormFieldSpec, load_form_schemas
from app.orchestration.state import FieldGraphState, OrchestrationState
from eval.harness.run_matrix import MODES, load_dataset, run_matrix

_SUBSET_SIZE = 2  # the dataset's first two entries are phase6_ocr_kyc/phase6_ocr_insurance
# (Commit 5) -- one of each form schema, kept small deliberately: each cold run below does
# O(n^2) cache I/O (Commit 8's put_cached_response rewrites the whole file on every call),
# which is a fine trade-off against real LLM latency in a live run but dominates wall-clock
# time in these fast, stub-only tests if the subset grows much larger.


def _dataset_subset() -> list[dict[str, Any]]:
    """A real prefix of the committed Phase 6 dataset (Commit 5) -- not a hand-built
    fixture -- kept small for test speed. Order is whatever the committed file's own JSON
    array order is; these tests do not depend on which specific cases are first, only that
    the same prefix is used consistently within a given test."""
    return load_dataset()[:_SUBSET_SIZE]


def _perfect_extraction_stub(cases: list[dict[str, Any]]):
    """A run_graph stand-in that always 'extracts' exactly the case's own ground-truth
    value for whichever field is pending -- deterministic, and correct by construction, so
    these tests can focus purely on reproducibility rather than on scoring correctness
    (already covered by test_scoring.py and test_run_matrix.py's own summary-metrics test).
    """
    ground_truth_by_run_case_id = {
        f"{case['case_id']}__{mode.value}": case["ground_truth"] for case in cases for mode in MODES
    }

    def _run(state: OrchestrationState) -> OrchestrationState:
        field_name = state.pending_field_names[0]
        value = ground_truth_by_run_case_id[state.case_id].get(field_name)
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
    raise AssertionError("this should never be called against an already-warm cache")


def _run_cold(cases: list[dict[str, Any]], cache_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr("eval.harness.run_matrix._ingest_case_documents", lambda rid, docs: None)
    monkeypatch.setattr("eval.harness.run_matrix.run_graph", _perfect_extraction_stub(cases))
    return run_matrix(cases, cache_path=cache_path)


# ---------------------------------------------------------------------------
# Repeated full-matrix execution
# ---------------------------------------------------------------------------


def test_repeated_full_matrix_execution_is_deterministic(tmp_path: Path) -> None:
    """Independent cold runs (fresh dataset load, fresh cache each time) of the same subset
    must produce identical results -- not merely 'the same object returns the same thing
    twice', but genuinely independent executions converging."""
    results = []
    for i in range(2):
        cases = _dataset_subset()
        with pytest.MonkeyPatch.context() as mp:
            results.append(_run_cold(cases, tmp_path / f"run_{i}.json", mp))

    assert results[0] == results[1]


def test_repeated_execution_preserves_field_result_ordering(tmp_path: Path) -> None:
    results = []
    for i in range(2):
        cases = _dataset_subset()
        with pytest.MonkeyPatch.context() as mp:
            results.append(_run_cold(cases, tmp_path / f"order_{i}.json", mp))

    orders = [
        [(r["case_id"], r["field_name"]) for r in result[PrivacyMode.NONE.value]["field_results"]]
        for result in results
    ]
    assert orders[0] == orders[1]


# ---------------------------------------------------------------------------
# Cache warm/cold equivalence
# ---------------------------------------------------------------------------


def test_cache_cold_and_cache_warm_executions_converge_to_identical_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The core convergence claim: a cold run (empty cache, real -- here, stubbed --
    evaluation) and a subsequent warm run of the *identical* matrix (cache fully populated
    by the cold run, live evaluation forbidden) must produce exactly the same result --
    checked both as Python values and, more strictly, byte-for-byte after serialization
    (excluding the timestamp `main()` wraps results in for a real run, which
    run_matrix()'s own return value never includes -- see this module's docstring on why
    the cache *file's* own bytes are the one place a timestamp does leak in, deliberately
    excluded from this comparison)."""
    cases = _dataset_subset()
    cache_path = tmp_path / "cache.json"

    cold_result = _run_cold(cases, cache_path, monkeypatch)

    monkeypatch.setattr("eval.harness.run_matrix._ingest_case_documents", _must_not_be_called)
    monkeypatch.setattr("eval.harness.run_matrix.run_graph", _must_not_be_called)
    warm_result = run_matrix(cases, cache_path=cache_path)

    assert cold_result == warm_result
    cold_json = json.dumps(cold_result, indent=2, sort_keys=True)
    warm_json = json.dumps(warm_result, indent=2, sort_keys=True)
    assert cold_json == warm_json


def test_cache_warm_execution_does_not_touch_ingestion_or_the_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same claim as the convergence test above, asserted the direct way: count calls
    instead of relying on a 'raise if called' stub, as a second, independent proof."""
    cases = _dataset_subset()
    cache_path = tmp_path / "cache.json"
    _run_cold(cases, cache_path, monkeypatch)

    ingest_calls: list[str] = []
    run_graph_calls: list[str] = []

    def _counting_run_graph(state: OrchestrationState) -> OrchestrationState:
        run_graph_calls.append(state.case_id)
        raise AssertionError("run_graph must not run against a fully warm cache")

    monkeypatch.setattr(
        "eval.harness.run_matrix._ingest_case_documents", lambda rid, docs: ingest_calls.append(rid)
    )
    monkeypatch.setattr("eval.harness.run_matrix.run_graph", _counting_run_graph)

    run_matrix(cases, cache_path=cache_path)

    assert ingest_calls == []
    assert run_graph_calls == []


# ---------------------------------------------------------------------------
# Deterministic summary metrics
# ---------------------------------------------------------------------------


def test_summary_metrics_are_identical_and_correct_across_independent_cold_runs(tmp_path: Path) -> None:
    cases = _dataset_subset()
    results = []
    for i in range(2):
        with pytest.MonkeyPatch.context() as mp:
            results.append(_run_cold(cases, tmp_path / f"summary_{i}.json", mp))

    for mode in MODES:
        summary_a = results[0][mode.value]
        summary_b = results[1][mode.value]
        assert summary_a["accuracy"] == summary_b["accuracy"]
        assert summary_a["outcome_counts"] == summary_b["outcome_counts"]
        assert summary_a["accuracy_by_field_type"] == summary_b["accuracy_by_field_type"]
        assert summary_a["total_fields"] == summary_b["total_fields"]

        # The stub always "extracts" the case's own ground truth exactly, so every field
        # is correct or a correct abstention -- accuracy must be a perfect 1.0, not just
        # internally consistent between the two runs.
        assert summary_a["accuracy"] == 1.0
        non_correct_outcomes = set(summary_a["outcome_counts"]) - {"correct", "correct_abstention"}
        assert not non_correct_outcomes, f"unexpected non-correct outcomes: {non_correct_outcomes}"


# ---------------------------------------------------------------------------
# Deterministic output serialization
# ---------------------------------------------------------------------------


def test_serialized_output_is_byte_identical_across_independent_cold_runs(tmp_path: Path) -> None:
    """The reproducibility claim that actually matters for a stored run (DECISIONS.md:
    'every measured value must be reproducible from a stored run'): the *serialized* JSON
    two independently computed, equal results produce must be byte-identical -- not just
    the in-memory dicts comparing equal, which would tolerate e.g. float-formatting or
    key-ordering differences that a naive re-serialization could still introduce."""
    cases = _dataset_subset()
    serialized = []
    for i in range(2):
        with pytest.MonkeyPatch.context() as mp:
            result = _run_cold(cases, tmp_path / f"serialize_{i}.json", mp)
        serialized.append(json.dumps(result, indent=2, sort_keys=True))

    assert serialized[0] == serialized[1]


def test_serializing_the_same_result_object_twice_is_stable() -> None:
    result = {"none": {"accuracy": 1.0, "outcome_counts": {"correct": 3}, "field_results": [{"b": 1, "a": 2}]}}
    first = json.dumps(result, indent=2, sort_keys=True)
    second = json.dumps(result, indent=2, sort_keys=True)
    assert first == second


def test_cache_entries_are_reproducible_but_the_cache_files_own_bytes_are_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The limitation documented in this module's own docstring, made into a checked
    property: two independent cold runs produce cache files whose *entries* are identical
    (same keys, same responses) but whose raw file bytes differ, because
    put_cached_response stamps a fresh 'generated_at' on every write."""
    cases = _dataset_subset()
    cache_path_a = tmp_path / "cache_a.json"
    cache_path_b = tmp_path / "cache_b.json"

    with pytest.MonkeyPatch.context() as mp:
        _run_cold(cases, cache_path_a, mp)
    with pytest.MonkeyPatch.context() as mp:
        _run_cold(cases, cache_path_b, mp)

    payload_a = json.loads(cache_path_a.read_text(encoding="utf-8"))
    payload_b = json.loads(cache_path_b.read_text(encoding="utf-8"))

    assert payload_a["entries"] == payload_b["entries"]
    # Not asserted equal, deliberately: each write stamps its own real generation time.
    assert "generated_at" in payload_a and "generated_at" in payload_b


# ---------------------------------------------------------------------------
# Output ordering -- pinned regression guards (cases, modes, fields, serialized output)
#
# Unlike the "repeated execution" tests above, which only prove two runs agree *with each
# other*, the tests below pin the ordering against an independently-known expectation (the
# input case list's own order, the schema's own declared field order, MODES' own declared
# order) -- so a future change to run_matrix.py's iteration order would fail here even if
# it were internally self-consistent across repeated runs.
# ---------------------------------------------------------------------------


def test_case_order_in_field_results_matches_the_order_cases_were_passed_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases = list(reversed(_dataset_subset()))  # deliberately not the dataset's own file order
    result = _run_cold(cases, tmp_path / "cache.json", monkeypatch)

    none_field_results = result[PrivacyMode.NONE.value]["field_results"]
    expected_case_id_sequence = [case["case_id"] for case in cases for _ in _fields_for(case)]
    assert [r["case_id"] for r in none_field_results] == expected_case_id_sequence


def test_field_order_within_each_case_matches_the_schema_declaration_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases = _dataset_subset()
    result = _run_cold(cases, tmp_path / "cache.json", monkeypatch)
    none_field_results = result[PrivacyMode.NONE.value]["field_results"]

    for case in cases:
        expected_field_order = [field.name for field in _fields_for(case)]
        actual_field_order = [r["field_name"] for r in none_field_results if r["case_id"] == case["case_id"]]
        assert actual_field_order == expected_field_order


def test_mode_order_in_the_result_matches_the_declared_modes_tuple_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases = _dataset_subset()
    result = _run_cold(cases, tmp_path / "cache.json", monkeypatch)

    assert list(result.keys()) == [mode.value for mode in MODES]
    assert [mode.value for mode in MODES] == ["none", "full_tokenize", "policy_engine"]


def test_serialized_field_results_preserve_list_order_independent_of_sort_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sort_keys=True (used throughout this harness's own JSON output, matching
    main()/eval.dataset.generate_phase6_dataset's own convention) sorts *dict* keys at
    every nesting level; it must never be mistaken for something that also reorders a
    *list* like field_results. Round-tripping through JSON must preserve list order
    exactly, independent of key sorting."""
    cases = _dataset_subset()
    result = _run_cold(cases, tmp_path / "cache.json", monkeypatch)

    original_order = [(r["case_id"], r["field_name"]) for r in result[PrivacyMode.NONE.value]["field_results"]]
    round_tripped = json.loads(json.dumps(result, indent=2, sort_keys=True))
    round_tripped_order = [
        (r["case_id"], r["field_name"]) for r in round_tripped[PrivacyMode.NONE.value]["field_results"]
    ]

    assert round_tripped_order == original_order


def _fields_for(case: dict[str, Any]) -> list[FormFieldSpec]:
    schema = next(s for s in load_form_schemas() if s.id == case["form_schema_id"])
    return schema.fields
