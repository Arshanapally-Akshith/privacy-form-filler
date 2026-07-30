"""Unit tests for eval.harness.scoring (Phase 6 Commit 7): the shared
classify_outcome/summarize_field_results implementation extracted from
eval/harness/measure_dev_case_accuracy.py's own Phase 4 `_classify`/`_summarize`. Pure
functions of (ground truth, extraction result, error) / a list of already-classified field
results -- no I/O, no LLM, no dataset generator involved, offline per CLAUDE.md §4 by
construction.
"""

from typing import Any

import pytest

from app.extraction.extractor import ExtractionProvenance, ExtractionResult
from eval.harness.scoring import (
    OUTCOME_CORRECT,
    OUTCOME_CORRECT_ABSTENTION,
    OUTCOME_ERROR,
    OUTCOME_HALLUCINATION,
    OUTCOME_INCORRECT_ABSTENTION,
    OUTCOME_INCORRECT_VALUE,
    classify_outcome,
    summarize_field_results,
)

_PROVENANCE = ExtractionProvenance(document_id="doc-1", page_number=1)


def _filled(value: str) -> ExtractionResult:
    return ExtractionResult(value=value, provenance=_PROVENANCE, confidence=0.9)


_ABSTAINED = ExtractionResult(value=None, provenance=None, confidence=None)


# ---------------------------------------------------------------------------
# classify_outcome
# ---------------------------------------------------------------------------


def test_correct_extraction() -> None:
    assert classify_outcome("Rohan Mehta", _filled("Rohan Mehta"), None) == OUTCOME_CORRECT


def test_correct_extraction_is_case_and_whitespace_insensitive() -> None:
    assert classify_outcome("  Rohan Mehta  ", _filled("rohan mehta"), None) == OUTCOME_CORRECT


def test_incorrect_extraction() -> None:
    assert classify_outcome("Rohan Mehta", _filled("Someone Else"), None) == OUTCOME_INCORRECT_VALUE


def test_correct_abstention() -> None:
    assert classify_outcome(None, _ABSTAINED, None) == OUTCOME_CORRECT_ABSTENTION


def test_incorrect_abstention() -> None:
    assert classify_outcome("Rohan Mehta", _ABSTAINED, None) == OUTCOME_INCORRECT_ABSTENTION


def test_hallucination() -> None:
    assert classify_outcome(None, _filled("Invented Value"), None) == OUTCOME_HALLUCINATION


def test_error_takes_priority_over_any_result() -> None:
    assert classify_outcome("Rohan Mehta", None, ValueError("provider failure")) == OUTCOME_ERROR
    # Even a well-formed result must not override a recorded error.
    assert classify_outcome("Rohan Mehta", _filled("Rohan Mehta"), ValueError("boom")) == OUTCOME_ERROR


def test_classify_outcome_requires_a_result_when_there_is_no_error() -> None:
    with pytest.raises(AssertionError):
        classify_outcome("Rohan Mehta", None, None)


# ---------------------------------------------------------------------------
# summarize_field_results
# ---------------------------------------------------------------------------


def _result(outcome: str, field_type: str = "name") -> dict[str, Any]:
    return {
        "case_id": "case-1",
        "field_name": "full_name",
        "field_type": field_type,
        "ground_truth": "x",
        "extracted_value": "x",
        "outcome": outcome,
        "error": None,
    }


def test_summary_metric_aggregation() -> None:
    field_results = [
        _result(OUTCOME_CORRECT, field_type="name"),
        _result(OUTCOME_CORRECT_ABSTENTION, field_type="name"),
        _result(OUTCOME_INCORRECT_VALUE, field_type="date"),
        _result(OUTCOME_HALLUCINATION, field_type="date"),
        _result(OUTCOME_INCORRECT_ABSTENTION, field_type="location"),
        _result(OUTCOME_ERROR, field_type="location"),
    ]

    summary = summarize_field_results(field_results)

    assert summary["total_fields"] == 6
    # correct + correct_abstention = 2 out of 6.
    assert summary["accuracy"] == pytest.approx(2 / 6)
    assert summary["outcome_counts"] == {
        OUTCOME_CORRECT: 1,
        OUTCOME_CORRECT_ABSTENTION: 1,
        OUTCOME_INCORRECT_VALUE: 1,
        OUTCOME_HALLUCINATION: 1,
        OUTCOME_INCORRECT_ABSTENTION: 1,
        OUTCOME_ERROR: 1,
    }
    assert summary["accuracy_by_field_type"] == {
        "name": {"total": 2, "correct": 2, "accuracy": 1.0},
        "date": {"total": 2, "correct": 0, "accuracy": 0.0},
        "location": {"total": 2, "correct": 0, "accuracy": 0.0},
    }
    assert summary["field_results"] == field_results


def test_summarize_empty_field_results_does_not_divide_by_zero() -> None:
    summary = summarize_field_results([])

    assert summary["total_fields"] == 0
    assert summary["accuracy"] == 0.0
    assert summary["outcome_counts"] == {}
    assert summary["accuracy_by_field_type"] == {}


def test_summarize_all_correct_is_perfect_accuracy() -> None:
    field_results = [_result(OUTCOME_CORRECT), _result(OUTCOME_CORRECT_ABSTENTION)]
    summary = summarize_field_results(field_results)

    assert summary["accuracy"] == 1.0
    assert summary["accuracy_by_field_type"]["name"]["accuracy"] == 1.0
