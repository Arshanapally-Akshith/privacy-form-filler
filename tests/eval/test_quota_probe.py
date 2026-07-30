"""Unit tests for eval.harness.quota_probe's pure result-recording logic
(determine_termination_reason / build_probe_record). Per this commit's own scope, network
calls (probe(), _single_probe_call()) are never exercised here -- these tests only feed
synthetic ProbeAttemptResult values through the parsing/aggregation logic, offline and
deterministic per CLAUDE.md §4.
"""

from datetime import UTC, datetime

import pytest

from eval.harness.quota_probe import (
    ProbeAttemptResult,
    build_probe_record,
    determine_termination_reason,
)

_STARTED_AT = datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC)
_FINISHED_AT = datetime(2026, 7, 30, 12, 1, 0, tzinfo=UTC)


def _success(attempt_number: int) -> ProbeAttemptResult:
    return ProbeAttemptResult(attempt_number=attempt_number, succeeded=True, error_message=None)


def _failure(attempt_number: int, message: str) -> ProbeAttemptResult:
    return ProbeAttemptResult(attempt_number=attempt_number, succeeded=False, error_message=message)


# ---------------------------------------------------------------------------
# determine_termination_reason
# ---------------------------------------------------------------------------


def test_termination_reason_when_budget_fully_consumed_without_failure() -> None:
    attempts = [_success(1), _success(2), _success(3)]
    assert determine_termination_reason(attempts, max_requests=3) == "request_budget_reached"


def test_termination_reason_when_last_attempt_failed() -> None:
    attempts = [_success(1), _success(2), _failure(3, "429 RESOURCE_EXHAUSTED")]
    assert determine_termination_reason(attempts, max_requests=10) == "stopped_after_first_failure"


def test_termination_reason_when_first_attempt_fails_immediately() -> None:
    attempts = [_failure(1, "401 UNAUTHENTICATED")]
    assert determine_termination_reason(attempts, max_requests=10) == "stopped_after_first_failure"


def test_termination_reason_when_no_attempt_was_made() -> None:
    assert determine_termination_reason([], max_requests=10) == "aborted_before_first_attempt"


def test_termination_reason_raises_on_a_stopping_invariant_violation() -> None:
    # Successes short of the budget with no failure is not a state probe()'s own loop can
    # produce -- asserts this function fails loudly rather than silently mislabeling it.
    attempts = [_success(1), _success(2)]
    with pytest.raises(AssertionError):
        determine_termination_reason(attempts, max_requests=10)


# ---------------------------------------------------------------------------
# build_probe_record
# ---------------------------------------------------------------------------


def test_build_probe_record_all_successes() -> None:
    attempts = [_success(1), _success(2)]
    record = build_probe_record(
        model="gemini-3.5-flash-lite",
        max_requests=2,
        attempts=attempts,
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )

    assert record["model"] == "gemini-3.5-flash-lite"
    assert record["max_requests_configured"] == 2
    assert record["successful_requests"] == 2
    assert record["failed_requests"] == 0
    assert record["termination_reason"] == "request_budget_reached"
    assert record["failures"] == []
    assert record["timestamp"] == _STARTED_AT.isoformat()
    assert record["finished_at"] == _FINISHED_AT.isoformat()


def test_build_probe_record_records_full_error_messages_for_failures() -> None:
    long_message = "429 RESOURCE_EXHAUSTED: quotaId=GenerateRequestsPerDayPerProjectPerModel-FreeTier, quotaValue=20"
    attempts = [_success(1), _failure(2, long_message)]
    record = build_probe_record(
        model="gemini-3.5-flash-lite",
        max_requests=10,
        attempts=attempts,
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )

    assert record["successful_requests"] == 1
    assert record["failed_requests"] == 1
    assert record["termination_reason"] == "stopped_after_first_failure"
    assert record["failures"] == [{"attempt_number": 2, "error_message": long_message}]


def test_build_probe_record_with_no_attempts() -> None:
    record = build_probe_record(
        model="gemini-3.5-flash-lite",
        max_requests=10,
        attempts=[],
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )

    assert record["successful_requests"] == 0
    assert record["failed_requests"] == 0
    assert record["termination_reason"] == "aborted_before_first_attempt"
    assert record["failures"] == []


def test_build_probe_record_is_json_serializable() -> None:
    import json

    attempts = [_success(1), _failure(2, "some error")]
    record = build_probe_record(
        model="gemini-3.5-flash-lite",
        max_requests=10,
        attempts=attempts,
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )

    json.dumps(record)  # must not raise
