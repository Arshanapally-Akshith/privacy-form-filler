"""Shared field-outcome classification and summary-metric aggregation for evaluation
harnesses (BUILD.md Phase 6 task 8: "separate accounting for correct abstentions versus
hallucinations").

Extracted verbatim from eval/harness/measure_dev_case_accuracy.py's own Phase 4
`_classify`/`_summarize` -- behavior is unchanged, only the module boundary moved.
That script now imports from here instead of keeping its own copy, so Phase 6's harness and
Phase 4's script share one implementation rather than two that could quietly drift apart
(CLAUDE.md §6: no duplicated logic). test_scoring.py is this module's own test coverage;
Phase 4's script no longer needs (or has) any test of this logic itself.

Independent of the dataset generator: nothing here imports eval.dataset.* -- classifying an
outcome is a pure function of (ground truth, extraction result, error), never of how the
case that produced them was assembled.
"""

from typing import Any

from app.extraction.extractor import ExtractionResult

OUTCOME_CORRECT = "correct"
OUTCOME_INCORRECT_VALUE = "incorrect_value"
OUTCOME_CORRECT_ABSTENTION = "correct_abstention"
OUTCOME_INCORRECT_ABSTENTION = "incorrect_abstention"
OUTCOME_HALLUCINATION = "hallucination"
OUTCOME_ERROR = "error"

# The two outcomes counted as "correct" for accuracy purposes -- a field that is genuinely
# absent and correctly left empty counts the same as a field correctly filled (DECISIONS.md
# V13: correct abstentions are accounted for, not just hallucinations).
_CORRECT_OUTCOMES = (OUTCOME_CORRECT, OUTCOME_CORRECT_ABSTENTION)


def classify_outcome(
    ground_truth_value: str | None, result: ExtractionResult | None, error: Exception | None
) -> str:
    if error is not None:
        return OUTCOME_ERROR
    assert result is not None
    if result.value is None:
        return OUTCOME_CORRECT_ABSTENTION if ground_truth_value is None else OUTCOME_INCORRECT_ABSTENTION
    if ground_truth_value is None:
        return OUTCOME_HALLUCINATION
    return (
        OUTCOME_CORRECT
        if result.value.strip().casefold() == ground_truth_value.strip().casefold()
        else OUTCOME_INCORRECT_VALUE
    )


def summarize_field_results(field_results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(field_results)
    outcome_counts: dict[str, int] = {}
    for r in field_results:
        outcome_counts[r["outcome"]] = outcome_counts.get(r["outcome"], 0) + 1

    correct = sum(outcome_counts.get(outcome, 0) for outcome in _CORRECT_OUTCOMES)

    by_field_type: dict[str, dict[str, Any]] = {}
    for r in field_results:
        bucket = by_field_type.setdefault(r["field_type"], {"total": 0, "correct": 0})
        bucket["total"] += 1
        if r["outcome"] in _CORRECT_OUTCOMES:
            bucket["correct"] += 1
    for bucket in by_field_type.values():
        bucket["accuracy"] = bucket["correct"] / bucket["total"] if bucket["total"] else 0.0

    return {
        "total_fields": total,
        "accuracy": (correct / total) if total else 0.0,
        "outcome_counts": outcome_counts,
        "accuracy_by_field_type": by_field_type,
        "field_results": field_results,
    }
