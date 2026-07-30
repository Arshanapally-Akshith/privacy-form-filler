"""Unit tests for eval.harness.analyze_results (Phase 7 Commit 8). Every test uses small,
hand-built, synthetic result dicts -- never the real committed artifacts -- so these tests
pin the *analysis logic* against inputs whose correct answer is known by construction,
independent of whatever eval/harness/results/phase6_matrix_result.json happens to contain on
a given day. No LLM, no file I/O beyond the load_* wrappers' own thin JSON parsing (not
exercised here with real files).
"""

import json

from eval.harness.analyze_results import (
    ERROR_CATEGORY_OTHER,
    ERROR_CATEGORY_PROVIDER_QUOTA_EMBEDDING,
    ERROR_CATEGORY_PROVIDER_QUOTA_GENERATION,
    ERROR_CATEGORY_PROVIDER_QUOTA_OTHER,
    ERROR_CATEGORY_TOKENIZE_UNSUPPORTED_ENTITY,
    MIN_SAMPLE_SIZE_FOR_STATISTICAL_MEANING,
    analyze_field_results,
    analyze_field_type_breakdown,
    build_analysis,
    classify_error,
    summarize_ocr_result,
)

_EMBED_QUOTA_ERROR = (
    "429 RESOURCE_EXHAUSTED. {'error': {'message': 'Quota exceeded for metric: "
    "generativelanguage.googleapis.com/embed_content_free_tier_requests'}}"
)
_GENERATE_QUOTA_ERROR = (
    "429 RESOURCE_EXHAUSTED. {'error': {'message': 'Quota exceeded for metric: "
    "generativelanguage.googleapis.com/generate_content_free_tier_requests, "
    "quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier'}}"
)
_TOKENIZE_GAP_ERROR = "app.privacy.dispatch.UnsupportedActionForEntityTypeError: Tokenize has no defined alphabet for entity type 'name'"


def _result(outcome: str, *, field_type: str = "identifier", error: str | None = None) -> dict[str, object]:
    return {
        "case_id": "case-1",
        "field_name": "some_field",
        "field_type": field_type,
        "ground_truth": "x",
        "extracted_value": "x" if outcome == "correct" else None,
        "outcome": outcome,
        "error": error,
    }


# ---------------------------------------------------------------------------
# classify_error
# ---------------------------------------------------------------------------


def test_classify_error_recognizes_embedding_quota_failure() -> None:
    assert classify_error(_EMBED_QUOTA_ERROR) == ERROR_CATEGORY_PROVIDER_QUOTA_EMBEDDING


def test_classify_error_recognizes_generation_quota_failure() -> None:
    assert classify_error(_GENERATE_QUOTA_ERROR) == ERROR_CATEGORY_PROVIDER_QUOTA_GENERATION


def test_classify_error_recognizes_other_429_as_provider_quota_other() -> None:
    assert classify_error("429 RESOURCE_EXHAUSTED. some unrelated quota message") == ERROR_CATEGORY_PROVIDER_QUOTA_OTHER


def test_classify_error_recognizes_tokenize_unsupported_entity_gap() -> None:
    assert classify_error(_TOKENIZE_GAP_ERROR) == ERROR_CATEGORY_TOKENIZE_UNSUPPORTED_ENTITY


def test_classify_error_falls_back_to_other_for_an_unrecognized_message() -> None:
    assert classify_error("some completely unrelated runtime error") == ERROR_CATEGORY_OTHER


# ---------------------------------------------------------------------------
# analyze_field_results -- the core breakdown
# ---------------------------------------------------------------------------


def test_all_provider_failures_yields_zero_scoreable_and_no_accuracy() -> None:
    """Mirrors the real committed phase6_matrix_result.json's full_tokenize/policy_engine
    modes: every single field errored on a provider quota failure."""
    field_results = [_result("error", error=_EMBED_QUOTA_ERROR) for _ in range(5)]
    breakdown = analyze_field_results(field_results)

    assert breakdown.total_fields == 5
    assert breakdown.scoreable_fields == 0
    assert breakdown.provider_failure_count == 5
    assert breakdown.non_provider_error_count == 0
    assert breakdown.accuracy_over_scoreable is None
    assert breakdown.is_statistically_meaningful is False


def test_a_single_correct_result_among_provider_failures_is_not_misreported_as_full_accuracy() -> None:
    """Mirrors the real committed artifact's `none` mode almost exactly: 1 correct result
    buried among hundreds of provider failures. accuracy_over_scoreable is legitimately 1.0
    (the one real data point was correct), but is_statistically_meaningful must be False --
    this is the single most important behavior this module exists to get right."""
    field_results = [_result("correct")] + [_result("error", error=_GENERATE_QUOTA_ERROR) for _ in range(10)]
    breakdown = analyze_field_results(field_results)

    assert breakdown.total_fields == 11
    assert breakdown.scoreable_fields == 1
    assert breakdown.correct == 1
    assert breakdown.accuracy_over_scoreable == 1.0
    assert breakdown.is_statistically_meaningful is False  # 1 < MIN_SAMPLE_SIZE_FOR_STATISTICAL_MEANING


def test_scoreable_sample_at_the_meaningfulness_threshold_is_marked_meaningful() -> None:
    field_results = [_result("correct") for _ in range(MIN_SAMPLE_SIZE_FOR_STATISTICAL_MEANING)]
    breakdown = analyze_field_results(field_results)
    assert breakdown.scoreable_fields == MIN_SAMPLE_SIZE_FOR_STATISTICAL_MEANING
    assert breakdown.is_statistically_meaningful is True


def test_scoreable_sample_one_below_the_threshold_is_not_meaningful() -> None:
    field_results = [_result("correct") for _ in range(MIN_SAMPLE_SIZE_FOR_STATISTICAL_MEANING - 1)]
    breakdown = analyze_field_results(field_results)
    assert breakdown.is_statistically_meaningful is False


def test_extraction_correctness_abstention_and_hallucination_are_kept_separate() -> None:
    field_results = [
        _result("correct"),
        _result("incorrect_value"),
        _result("correct_abstention"),
        _result("incorrect_abstention"),
        _result("hallucination"),
    ]
    breakdown = analyze_field_results(field_results)

    assert breakdown.correct == 1
    assert breakdown.incorrect_value == 1
    assert breakdown.correct_abstention == 1
    assert breakdown.incorrect_abstention == 1
    assert breakdown.hallucination == 1
    assert breakdown.error_total == 0
    assert breakdown.scoreable_fields == 5
    # correct + correct_abstention = 2 of 5
    assert breakdown.accuracy_over_scoreable == 2 / 5


def test_error_breakdown_distinguishes_provider_failures_from_a_mechanism_level_gap() -> None:
    """A mix an execution could plausibly produce: some provider failures, and one instance
    of the already-known Tokenize-NAME-alphabet gap (a real mechanism finding, not a
    provider outage) -- these must never be merged into one undifferentiated error count."""
    field_results = [
        _result("error", error=_EMBED_QUOTA_ERROR),
        _result("error", error=_EMBED_QUOTA_ERROR),
        _result("error", error=_TOKENIZE_GAP_ERROR),
        _result("correct"),
    ]
    breakdown = analyze_field_results(field_results)

    assert breakdown.error_total == 3
    assert breakdown.provider_failure_count == 2
    assert breakdown.non_provider_error_count == 1
    assert breakdown.error_breakdown == {
        ERROR_CATEGORY_PROVIDER_QUOTA_EMBEDDING: 2,
        ERROR_CATEGORY_TOKENIZE_UNSUPPORTED_ENTITY: 1,
    }
    # Only the correct result and the tokenize-gap error are "scoreable" in the sense of not
    # being a provider outage -- but scoreable_fields is defined as total minus *all*
    # error outcomes (provider or not), since a non-provider error still isn't a value to
    # score for accuracy purposes either.
    assert breakdown.scoreable_fields == 1
    assert breakdown.accuracy_over_scoreable == 1.0


def test_a_null_error_message_is_classified_as_other_rather_than_raising() -> None:
    """Defensive: scoring.py's own field_results always set error=None for non-error
    outcomes, but a caller constructing a result by hand could plausibly pass "" for an
    error outcome. Must not crash."""
    field_results = [_result("error", error="")]
    breakdown = analyze_field_results(field_results)
    assert breakdown.error_breakdown == {ERROR_CATEGORY_OTHER: 1}


# ---------------------------------------------------------------------------
# analyze_field_type_breakdown
# ---------------------------------------------------------------------------


def test_field_type_breakdown_groups_and_analyzes_independently() -> None:
    field_results = [
        _result("correct", field_type="name"),
        _result("error", field_type="identifier", error=_EMBED_QUOTA_ERROR),
        _result("error", field_type="identifier", error=_EMBED_QUOTA_ERROR),
        _result("incorrect_value", field_type="date"),
    ]
    by_type = analyze_field_type_breakdown(field_results)

    assert set(by_type) == {"name", "identifier", "date"}
    assert by_type["name"].correct == 1
    assert by_type["name"].scoreable_fields == 1
    assert by_type["identifier"].scoreable_fields == 0
    assert by_type["identifier"].provider_failure_count == 2
    assert by_type["date"].incorrect_value == 1
    assert by_type["date"].accuracy_over_scoreable == 0.0


# ---------------------------------------------------------------------------
# summarize_ocr_result
# ---------------------------------------------------------------------------


def test_summarize_ocr_result_environment_blocked() -> None:
    payload = {
        "status": "environment_blocked",
        "reason": "tesseract binary not found on PATH in this environment",
    }
    summary = summarize_ocr_result(payload)
    assert summary == {
        "status": "environment_blocked",
        "reason": "tesseract binary not found on PATH in this environment",
        "measured_fields": 0,
    }


def test_summarize_ocr_result_measured_counts_every_case_condition_mode() -> None:
    payload = {
        "status": "measured",
        "cases": {
            "phase6_ocr_kyc": {
                "clean_scan": {"none": {"total_fields": 10}},
                "photo_like": {"none": {"total_fields": 10}},
            },
            "phase6_ocr_insurance": {
                "clean_scan": {"none": {"total_fields": 8}},
                "photo_like": {"none": {"total_fields": 8}},
            },
        },
    }
    summary = summarize_ocr_result(payload)
    assert summary == {"status": "measured", "reason": None, "measured_fields": 36}


# ---------------------------------------------------------------------------
# build_analysis -- end-to-end pure assembly
# ---------------------------------------------------------------------------


def test_build_analysis_reports_a_limitation_when_the_scoreable_sample_is_too_small() -> None:
    matrix_payload = {
        "measured_at": "2026-07-30T11:20:37+00:00",
        "model": "gemini-3.5-flash",
        "execution_status": "partial_errors_present",
        "modes": {
            "none": {"field_results": [_result("correct")] + [_result("error", error=_GENERATE_QUOTA_ERROR) for _ in range(5)]},
            "full_tokenize": {"field_results": [_result("error", error=_EMBED_QUOTA_ERROR) for _ in range(5)]},
            "policy_engine": {"field_results": [_result("error", error=_EMBED_QUOTA_ERROR) for _ in range(5)]},
        },
    }
    ocr_payload = {"status": "environment_blocked", "reason": "tesseract binary not found"}

    analysis = build_analysis(matrix_payload, ocr_payload)

    assert analysis["source_model"] == "gemini-3.5-flash"
    assert analysis["source_execution_status"] == "partial_errors_present"
    assert analysis["modes"]["none"]["overall"]["scoreable_fields"] == 1
    assert analysis["modes"]["none"]["overall"]["is_statistically_meaningful"] is False
    assert analysis["modes"]["full_tokenize"]["overall"]["scoreable_fields"] == 0
    assert analysis["ocr"]["status"] == "environment_blocked"
    assert any("statistically meaningful" in limitation for limitation in analysis["limitations"])
    assert any("OCR" in limitation for limitation in analysis["limitations"])
    assert any("Verifier audit" in limitation for limitation in analysis["limitations"])


def test_build_analysis_output_is_json_serializable() -> None:
    matrix_payload = {
        "measured_at": "2026-07-30T11:20:37+00:00",
        "model": "gemini-3.5-flash",
        "execution_status": "complete",
        "modes": {"none": {"field_results": [_result("correct")]}},
    }
    ocr_payload = {"status": "environment_blocked", "reason": "tesseract binary not found"}
    analysis = build_analysis(matrix_payload, ocr_payload)
    json.dumps(analysis)  # must not raise
