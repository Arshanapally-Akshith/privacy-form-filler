"""Phase 7 Commit 8: analysis of the accuracy-matrix and OCR artifacts Commit 7 actually
produced, without overstating what that execution measured.

**Why this exists as a separate layer, not a change to scoring.py.** eval.harness.scoring's
classify_outcome/summarize_field_results is not wrong or defective -- it faithfully computes
exactly what BUILD.md Phase 6 task 8 asked for (correct/incorrect/abstention/hallucination
classification, and per-field-type accuracy including abstentions). But it was never asked
to distinguish *why* a field is not scoreable: `outcome_counts["error"]` and
`accuracy_by_field_type` both count a provider-quota failure exactly like they would count a
genuine model mistake, because at the time that module was written, "error" meant "the
pipeline broke," full stop -- there was no execution yet that broke almost entirely for one
specific, external, non-model reason. Commit 7's real, committed
eval/harness/results/phase6_matrix_result.json is that execution: 1,511 of 1,512 field
outcomes are `error`, and every single one of those 1,511 is a real, verified 429
RESOURCE_EXHAUSTED from Google's own API (confirmed by inspecting every error message in
that file directly, not assumed) -- either the embedding model's per-minute quota or the
generation model's daily quota (DECISIONS.md E9). Reporting phase6_matrix_result.json's own
built-in `accuracy_by_field_type` numbers as-is in RESULTS.md would silently read as "the
system fails at almost every field," when the actual, verified finding is "we could not
reach the model for almost every field." Distinguishing those is this module's entire job.

**Reuses, does not duplicate.** eval.harness.scoring.OUTCOME_* constants are imported
directly, not re-declared -- this module classifies *within* the existing `error` outcome
(by inspecting the stored error message text), it does not invent a new outcome taxonomy
alongside scoring.py's own.

**Deterministic and reproducible from stored data, per CLAUDE.md §11.** Every function here
is a pure function of already-committed JSON (phase6_matrix_result.json,
phase6_ocr_matrix_result.json) -- no LLM call, no randomness, no wall-clock dependency
(other than reporting the input artifacts' own recorded timestamps back verbatim).
Re-running main() against the same committed artifacts always produces byte-identical
analysis output.

**What this deliberately does NOT do.** It does not synthesize a (privacy mode x field
type) accuracy matrix with invented or estimated numbers where no real measurement exists --
where the scoreable sample for a slice is zero, this module reports that explicitly
(`ExtractionOutcomeBreakdown.scoreable_fields == 0`) rather than omitting the slice or
filling it with a placeholder. Where a sample exists but is too small to support any
statistical claim (this execution's actual scoreable sample is exactly 1 field, across all
three modes combined), `is_statistically_meaningful` is explicitly False, and RESULTS.md
must say so plainly rather than quote a lone data point as if it were representative.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from eval.harness.scoring import (
    OUTCOME_CORRECT,
    OUTCOME_CORRECT_ABSTENTION,
    OUTCOME_ERROR,
    OUTCOME_HALLUCINATION,
    OUTCOME_INCORRECT_ABSTENTION,
    OUTCOME_INCORRECT_VALUE,
)

MATRIX_RESULT_PATH = Path(__file__).resolve().parent / "results" / "phase6_matrix_result.json"
OCR_RESULT_PATH = Path(__file__).resolve().parent / "results" / "phase6_ocr_matrix_result.json"
ANALYSIS_RESULT_PATH = Path(__file__).resolve().parent / "results" / "phase7_accuracy_analysis.json"

# A judgment call specific to this analysis layer, not a DECISIONS.md-pinned project
# constant: below this many real (non-error) field outcomes, a computed accuracy ratio is
# reported but flagged as not statistically meaningful, rather than silently presented
# alongside a fully-measured slice as if the two were comparable.
MIN_SAMPLE_SIZE_FOR_STATISTICAL_MEANING = 30

ERROR_CATEGORY_PROVIDER_QUOTA_EMBEDDING = "provider_quota_embedding"
ERROR_CATEGORY_PROVIDER_QUOTA_GENERATION = "provider_quota_generation"
ERROR_CATEGORY_PROVIDER_QUOTA_OTHER = "provider_quota_other"
ERROR_CATEGORY_TOKENIZE_UNSUPPORTED_ENTITY = "tokenize_unsupported_entity"
ERROR_CATEGORY_OTHER = "other"

_PROVIDER_QUOTA_CATEGORIES = frozenset(
    {ERROR_CATEGORY_PROVIDER_QUOTA_EMBEDDING, ERROR_CATEGORY_PROVIDER_QUOTA_GENERATION, ERROR_CATEGORY_PROVIDER_QUOTA_OTHER}
)

_EXTRACTION_CORRECTNESS_OUTCOMES = frozenset({OUTCOME_CORRECT, OUTCOME_INCORRECT_VALUE})
_ABSTENTION_OUTCOMES = frozenset({OUTCOME_CORRECT_ABSTENTION, OUTCOME_INCORRECT_ABSTENTION})
_CORRECT_OUTCOMES = frozenset({OUTCOME_CORRECT, OUTCOME_CORRECT_ABSTENTION})


def classify_error(error_message: str) -> str:
    """Pure string classification of one recorded error message into a reporting category.
    Deliberately conservative: anything not confidently matched falls into
    ERROR_CATEGORY_OTHER rather than being guessed into a more specific bucket -- a
    misclassified provider failure would be exactly the kind of silent, misleading signal
    this module exists to prevent."""
    if "429" in error_message and "RESOURCE_EXHAUSTED" in error_message:
        if "embed_content" in error_message:
            return ERROR_CATEGORY_PROVIDER_QUOTA_EMBEDDING
        if "generate_content" in error_message or "GenerateRequests" in error_message:
            return ERROR_CATEGORY_PROVIDER_QUOTA_GENERATION
        return ERROR_CATEGORY_PROVIDER_QUOTA_OTHER
    if "UnsupportedActionForEntityTypeError" in error_message or "no defined alphabet" in error_message:
        return ERROR_CATEGORY_TOKENIZE_UNSUPPORTED_ENTITY
    return ERROR_CATEGORY_OTHER


@dataclass(frozen=True)
class ExtractionOutcomeBreakdown:
    total_fields: int
    correct: int
    incorrect_value: int
    correct_abstention: int
    incorrect_abstention: int
    hallucination: int
    error_total: int
    error_breakdown: dict[str, int]
    provider_failure_count: int
    non_provider_error_count: int
    scoreable_fields: int
    accuracy_over_scoreable: float | None
    is_statistically_meaningful: bool


def analyze_field_results(field_results: list[dict[str, Any]]) -> ExtractionOutcomeBreakdown:
    """The core, pure analysis function: one list of scoring.py-shaped field_results dicts
    in, one breakdown out. No I/O. Separates extraction correctness
    (correct/incorrect_value), abstentions (correct_abstention/incorrect_abstention),
    hallucination, and infrastructure/provider failures (error, further sub-classified) --
    exactly the three-way separation this commit requires, plus the finer error-cause split
    needed to avoid conflating a provider outage with a model mistake."""
    counts = {
        OUTCOME_CORRECT: 0,
        OUTCOME_INCORRECT_VALUE: 0,
        OUTCOME_CORRECT_ABSTENTION: 0,
        OUTCOME_INCORRECT_ABSTENTION: 0,
        OUTCOME_HALLUCINATION: 0,
        OUTCOME_ERROR: 0,
    }
    error_breakdown: dict[str, int] = {}

    for result in field_results:
        outcome = result["outcome"]
        counts[outcome] += 1
        if outcome == OUTCOME_ERROR:
            category = classify_error(result["error"] or "")
            error_breakdown[category] = error_breakdown.get(category, 0) + 1

    total_fields = len(field_results)
    error_total = counts[OUTCOME_ERROR]
    provider_failure_count = sum(count for category, count in error_breakdown.items() if category in _PROVIDER_QUOTA_CATEGORIES)
    non_provider_error_count = error_total - provider_failure_count
    scoreable_fields = total_fields - error_total

    accuracy_over_scoreable = (
        (counts[OUTCOME_CORRECT] + counts[OUTCOME_CORRECT_ABSTENTION]) / scoreable_fields
        if scoreable_fields > 0
        else None
    )

    return ExtractionOutcomeBreakdown(
        total_fields=total_fields,
        correct=counts[OUTCOME_CORRECT],
        incorrect_value=counts[OUTCOME_INCORRECT_VALUE],
        correct_abstention=counts[OUTCOME_CORRECT_ABSTENTION],
        incorrect_abstention=counts[OUTCOME_INCORRECT_ABSTENTION],
        hallucination=counts[OUTCOME_HALLUCINATION],
        error_total=error_total,
        error_breakdown=error_breakdown,
        provider_failure_count=provider_failure_count,
        non_provider_error_count=non_provider_error_count,
        scoreable_fields=scoreable_fields,
        accuracy_over_scoreable=accuracy_over_scoreable,
        is_statistically_meaningful=scoreable_fields >= MIN_SAMPLE_SIZE_FOR_STATISTICAL_MEANING,
    )


def analyze_field_type_breakdown(field_results: list[dict[str, Any]]) -> dict[str, ExtractionOutcomeBreakdown]:
    """Groups field_results by field_type first, then applies analyze_field_results to each
    group -- the (privacy mode x field type) axis BUILD.md Phase 7 task 1 asks for, computed
    honestly per group rather than blended."""
    by_type: dict[str, list[dict[str, Any]]] = {}
    for result in field_results:
        by_type.setdefault(result["field_type"], []).append(result)
    return {field_type: analyze_field_results(results) for field_type, results in by_type.items()}


def load_matrix_result(path: Path = MATRIX_RESULT_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_ocr_result(path: Path = OCR_RESULT_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def analyze_matrix_result(matrix_payload: dict[str, Any]) -> dict[str, ExtractionOutcomeBreakdown]:
    """Per-mode breakdown, straight from the committed artifact's own `modes` section --
    consumes phase6_matrix_result.json exactly as it exists, no re-derivation of anything
    run_matrix.py already computed and stored."""
    return {
        mode_name: analyze_field_results(mode_summary["field_results"])
        for mode_name, mode_summary in matrix_payload["modes"].items()
    }


def analyze_matrix_result_by_field_type(matrix_payload: dict[str, Any]) -> dict[str, dict[str, ExtractionOutcomeBreakdown]]:
    return {
        mode_name: analyze_field_type_breakdown(mode_summary["field_results"])
        for mode_name, mode_summary in matrix_payload["modes"].items()
    }


def summarize_ocr_result(ocr_payload: dict[str, Any]) -> dict[str, Any]:
    """Uniform, minimal summary regardless of which shape measure_ocr_accuracy.py's artifact
    is in (`environment_blocked` has no `cases` key at all; a real `measured` run would).
    Never guesses at accuracy numbers that were never actually computed."""
    status = ocr_payload["status"]
    if status == "environment_blocked":
        return {
            "status": status,
            "reason": ocr_payload["reason"],
            "measured_fields": 0,
        }
    # status == "measured": count total field_results across every case/condition/mode
    # present, without assuming any particular set of them ran.
    total_fields = 0
    for per_condition in ocr_payload["cases"].values():
        for per_mode in per_condition.values():
            for summary in per_mode.values():
                total_fields += summary["total_fields"]
    return {"status": status, "reason": None, "measured_fields": total_fields}


def _breakdown_to_dict(breakdown: ExtractionOutcomeBreakdown) -> dict[str, Any]:
    return asdict(breakdown)


def build_analysis(
    matrix_payload: dict[str, Any], ocr_payload: dict[str, Any]
) -> dict[str, Any]:
    per_mode = analyze_matrix_result(matrix_payload)
    per_mode_by_field_type = analyze_matrix_result_by_field_type(matrix_payload)

    return {
        "source_measured_at": matrix_payload["measured_at"],
        "source_model": matrix_payload["model"],
        "source_execution_status": matrix_payload["execution_status"],
        "modes": {
            mode_name: {
                "overall": _breakdown_to_dict(breakdown),
                "by_field_type": {
                    field_type: _breakdown_to_dict(field_breakdown)
                    for field_type, field_breakdown in per_mode_by_field_type[mode_name].items()
                },
            }
            for mode_name, breakdown in per_mode.items()
        },
        "ocr": summarize_ocr_result(ocr_payload),
        "limitations": [
            (
                "The (privacy mode x field type) accuracy matrix cannot be meaningfully "
                "populated from this execution: across all three modes combined, only 1 of "
                "1,512 attempted field-runs produced a scoreable outcome (the other 1,511 "
                "are provider quota failures, not model errors -- see error_breakdown per "
                "mode). That single data point is not statistically meaningful and does not "
                "cover most field types."
            ),
            "OCR-vs-native accuracy comparison: not available (measure_ocr_accuracy.py's own run is environment_blocked -- Tesseract unavailable).",
            "Verifier audit: not yet run.",
            "Failure-case table (5-8 concrete examples): cannot be built from real extraction failures -- the only errors on record are provider quota failures, not extraction mistakes.",
            "CI regression thresholds: not yet pinned -- no accuracy baseline exists to pin them from.",
        ],
    }


def main() -> None:
    matrix_payload = load_matrix_result()
    ocr_payload = load_ocr_result()
    analysis = build_analysis(matrix_payload, ocr_payload)

    ANALYSIS_RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ANALYSIS_RESULT_PATH.write_text(json.dumps(analysis, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    for mode_name, mode_analysis in analysis["modes"].items():
        overall = mode_analysis["overall"]
        print(
            f"{mode_name}: scoreable={overall['scoreable_fields']}/{overall['total_fields']} "
            f"provider_failures={overall['provider_failure_count']} "
            f"non_provider_errors={overall['non_provider_error_count']} "
            f"accuracy_over_scoreable={overall['accuracy_over_scoreable']} "
            f"statistically_meaningful={overall['is_statistically_meaningful']}"
        )
    print(f"ocr: {analysis['ocr']}")
    print(f"Analysis written to {ANALYSIS_RESULT_PATH}")


if __name__ == "__main__":
    main()
