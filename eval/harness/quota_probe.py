"""Standalone Gemini quota probe (Phase 7 Commit 0, per the approved Phase 7 plan's D1).

**Purpose.** Before scheduling any Phase 7 live measurement, get a small, honest signal on
whether `gemini-3.5-flash-lite` clears more real requests per day than the pinned generation
model (DECISIONS.md E9, `gemini-3.5-flash`), whose 20-requests/day/model free-tier cap was
already confirmed during Phase 4/5 (429 `RESOURCE_EXHAUSTED`, `quotaId:
GenerateRequestsPerDayPerProjectPerModel-FreeTier`, `quotaValue: 20`). This script does not
decide anything by itself -- it only produces a stored, reviewable artifact. DECISIONS.md is
updated to note this tool's existence and where its output lives, not to record a model
substitution; that decision is still open pending a look at this (and, if needed, a later)
run's results.

**Why a standalone script, not a change to an existing harness.** Every existing evaluation
script under eval/harness/ (measure_recall.py, measure_dev_case_accuracy.py, run_matrix.py)
is scoped to a specific, already-reviewed measurement. This probe measures something
different -- provider-side quota behavior, not extraction/retrieval quality -- and needs a
different model than any of them calls (they all use, directly or via
app.extraction.llm_client, the pinned GENERATION_MODEL). Bolting a second model onto an
existing script would blur what each script measures; a small new file keeps the boundary
clean and leaves every existing harness untouched, as instructed.

**Why this calls google.genai directly instead of app.extraction.llm_client.** That module's
generate_structured() hardcodes DECISIONS.md's pinned GENERATION_MODEL with no model
parameter -- correct for production code, which must only ever call the one pinned model, but
exactly why it cannot be reused here without changing it (which is out of scope for this
probe). This script calls the Gemini SDK directly instead, entirely within eval/, so app/ is
untouched.

**Why the probe call uses structured-output mode.** Gemini quotas can be tracked per exact
API configuration, not just per model name -- the production call this probe is trying to
size (app.extraction.llm_client.generate_structured) uses
`response_mime_type="application/json"` with a Pydantic `response_schema`. A probe using a
plain text call could consume a different quota bucket and give a misleading answer, so this
probe deliberately mirrors that call shape with its own minimal schema.

**Call budget and pacing.** MAX_PROBE_REQUESTS is deliberately small (10, well under the
already-known 20/day cap for the sibling model) -- Commit 0's job is a first sanity signal,
not an exhaustive limit-finding run that would needlessly spend whatever daily quota this
model has. MIN_CALL_INTERVAL_SECONDS paces requests the same way
measure_dev_case_accuracy.py's own _throttle does (its docstring: "15/min, under the observed
20/min free-tier ceiling") so that a failure recorded here is attributable to a request- or
day-level cap, not to bursting past a per-minute rate limit.

**Termination policy.** The probe stops at the first failed attempt -- continuing past a
failure would not add information (the cap has already been found) and would spend calls for
nothing. It otherwise runs until MAX_PROBE_REQUESTS successes. Exactly one of three
`termination_reason` values results, computed by determine_termination_reason() below:
  - "request_budget_reached"     -- every attempt up to the configured budget succeeded
  - "stopped_after_first_failure" -- some attempt failed; the probe stopped immediately
  - "aborted_before_first_attempt" -- no attempt was made at all (e.g. client construction
    itself failed before any request could be sent)

**Determinism and test scope.** The network-calling loop (probe(), _single_probe_call()) is
inherently non-deterministic (it depends on live provider/quota state) and is not unit
tested, per this commit's own instructions. determine_termination_reason() and
build_probe_record() are pure functions of an in-memory attempt list and are fully
deterministic -- tests/eval/test_quota_probe.py exercises those directly with synthetic
ProbeAttemptResult values, never a real network call.

Run manually, from the project root:

    uv run python -u -m eval.harness.quota_probe

Writes eval/harness/results/quota_probe_result.json.
"""

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import APIError
from pydantic import BaseModel

PROBE_MODEL = "gemini-3.5-flash-lite"
MAX_PROBE_REQUESTS = 10
MIN_CALL_INTERVAL_SECONDS = 4.0  # mirrors measure_dev_case_accuracy.py's own pacing constant
RESULT_PATH = Path(__file__).resolve().parent / "results" / "quota_probe_result.json"

_PROBE_PROMPT = "Reply with the single word: ack"


class _ProbeResponseSchema(BaseModel):
    """Minimal structured-output schema -- exists only so this probe's call shape matches
    app.extraction.llm_client.generate_structured's real, quota-relevant configuration
    (structured output via response_schema), not to carry any meaningful content."""

    acknowledgment: str


@dataclass(frozen=True)
class ProbeAttemptResult:
    attempt_number: int
    succeeded: bool
    error_message: str | None


def _single_probe_call(client: genai.Client, model: str, attempt_number: int) -> ProbeAttemptResult:
    """One real request. Never raises -- every outcome (success, provider error, or an
    empty/unparseable response) is captured into a ProbeAttemptResult so the caller's loop
    never needs its own try/except."""
    try:
        response = client.models.generate_content(
            model=model,
            contents=_PROBE_PROMPT,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_ProbeResponseSchema,
            ),
        )
    except APIError as exc:
        return ProbeAttemptResult(attempt_number=attempt_number, succeeded=False, error_message=str(exc))

    if response.parsed is None or not isinstance(response.parsed, _ProbeResponseSchema):
        return ProbeAttemptResult(
            attempt_number=attempt_number,
            succeeded=False,
            error_message=f"empty or unparseable structured response: {response.parsed!r}",
        )
    return ProbeAttemptResult(attempt_number=attempt_number, succeeded=True, error_message=None)


def probe(
    *,
    model: str = PROBE_MODEL,
    max_requests: int = MAX_PROBE_REQUESTS,
    min_call_interval_seconds: float = MIN_CALL_INTERVAL_SECONDS,
) -> list[ProbeAttemptResult]:
    """Runs up to max_requests real, paced requests against `model`, stopping at the first
    failure. Requires GEMINI_API_KEY in the process environment (DECISIONS.md R15); this
    function does not load .env itself -- main() does, matching every other live-calling
    harness script's own division of that responsibility."""
    client = genai.Client()
    attempts: list[ProbeAttemptResult] = []
    last_call_at = 0.0

    for attempt_number in range(1, max_requests + 1):
        wait = min_call_interval_seconds - (time.monotonic() - last_call_at)
        if wait > 0:
            time.sleep(wait)
        last_call_at = time.monotonic()

        result = _single_probe_call(client, model, attempt_number)
        attempts.append(result)
        if not result.succeeded:
            break

    return attempts


def determine_termination_reason(attempts: list[ProbeAttemptResult], max_requests: int) -> str:
    """Pure function of the attempt list -- see the module docstring for the three possible
    values. Raises if the loop's own stopping invariant (stop on first failure, or exhaust
    max_requests) is ever violated, rather than silently mislabeling an outcome this function
    was not written to expect (CLAUDE.md §5: no silent fallback)."""
    if not attempts:
        return "aborted_before_first_attempt"
    if not attempts[-1].succeeded:
        return "stopped_after_first_failure"
    if len(attempts) == max_requests:
        return "request_budget_reached"
    raise AssertionError(
        "unreachable: probe() only stops on a failed attempt or after max_requests successes"
    )


def build_probe_record(
    *,
    model: str,
    max_requests: int,
    attempts: list[ProbeAttemptResult],
    started_at: datetime,
    finished_at: datetime,
) -> dict[str, Any]:
    """Pure function turning one probe run's attempts into the committed artifact's shape.
    Records full error messages for every failure (never truncated or summarized), per this
    commit's own requirement."""
    successes = [a for a in attempts if a.succeeded]
    failures = [a for a in attempts if not a.succeeded]
    return {
        "timestamp": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "model": model,
        "max_requests_configured": max_requests,
        "successful_requests": len(successes),
        "failed_requests": len(failures),
        "termination_reason": determine_termination_reason(attempts, max_requests),
        "failures": [
            {"attempt_number": failure.attempt_number, "error_message": failure.error_message}
            for failure in failures
        ],
    }


def main() -> None:
    load_dotenv()  # GEMINI_API_KEY (DECISIONS.md R15), same as every other live-calling
    # harness script's own main() -- see e.g. measure_dev_case_accuracy.py.
    started_at = datetime.now(UTC)
    attempts = probe()
    finished_at = datetime.now(UTC)

    record = build_probe_record(
        model=PROBE_MODEL,
        max_requests=MAX_PROBE_REQUESTS,
        attempts=attempts,
        started_at=started_at,
        finished_at=finished_at,
    )

    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(
        f"model={record['model']} "
        f"successes={record['successful_requests']} "
        f"failures={record['failed_requests']} "
        f"termination={record['termination_reason']}"
    )
    print(f"Results written to {RESULT_PATH}")


if __name__ == "__main__":
    main()
