"""Phase 4 dev-case accuracy measurement (BUILD.md Phase 4, task 6): re-run the informal
dev cases (eval/dataset/phase4_dev_cases.json) in all three privacy modes and record
accuracy per mode -- the first real signal of the privacy layer's accuracy cost.

Calls the real pipeline end to end (parse -> chunk -> embed -> retrieve -> extract) through
app.extraction.extractor.extract_field directly, the same call app.api.cases uses, minus
the HTTP wrapping -- mirroring eval/harness/measure_recall.py's own precedent of reusing
pipeline pieces without going through FastAPI. This deliberately includes a live call to
the real, pinned generative model (DECISIONS.md E9, gemini-3.5-flash) -- an accuracy
measurement against a hand-scripted stub would only prove the stub matches itself, not
anything about the model. Unlike Phase 1's recall@5 harness, there is no offline replay
cache here: BUILD.md Phase 4 has no regression-test requirement for this specific number
(that arrives in Phase 7), so this script's output is the artifact, not a fixture another
test depends on.

Each (case, mode) pair runs against its own case_id (f"{case_id}__{mode.value}"), so the
three modes never share a retrieval index or an FF1 session -- case isolation (Invariant
I6) applies here exactly as it does to any two unrelated cases.

Outcomes per field, per mode:
  correct              -- extracted value matches ground truth
  incorrect_value       -- extracted value present but wrong
  correct_abstention    -- ground truth is null (genuinely absent) and the field abstained
  incorrect_abstention  -- ground truth has a value but the field abstained
  hallucination         -- ground truth is null but a value was extracted anyway
  error                 -- an exception propagated (provider failure, or -- specifically
                           relevant to full_tokenize/policy_engine -- a PolicyDispatchError
                           when the field's own evidence contains an entity type Tokenize
                           cannot execute, e.g. NAME; BUILD.md's own Phase 4 risk table
                           treats this as a finding to measure, not a bug to route around)

A transient provider error (LLMProviderError) is retried before being counted as `error` --
this is evaluation-harness robustness, not a change to the pinned retry policy
(DECISIONS.md E6 is still TBD -- Phase 5); app.extraction/app.boundary are untouched. Each
attempt also runs under a hard wall-clock timeout enforced from this script alone (a thread
with `future.result(timeout=...)`, since the live run showed the provider can hang rather
than fail fast) -- this bounds total runtime without touching app.extraction.llm_client,
which has no timeout parameter of its own to change.

The free-tier quota for the pinned model (DECISIONS.md E9, gemini-3.5-flash) turned out to
be far more restrictive than a simple per-minute ceiling: proactive pacing
(MIN_CALL_INTERVAL_SECONDS) and a single long wait-and-retry (RETRY_DELAY_SECONDS) were both
tried and neither cleared it -- a clean, isolated, single call with zero concurrent
processes still returned 429 RESOURCE_EXHAUSTED. This was confirmed to be a real quota
exhaustion, not a harness pacing bug, before this script's retry count was reduced back to
1 (no retry): retrying against an exhausted quota that a 65s wait could not clear is not
"transient" by this project's own definition, and burning more wall-clock time on retries
that cannot succeed serves nobody. See DECISIONS.md Phase 4 measurements for how this
affected the `none`-mode baseline specifically.

Progress is printed per (mode, case, field) as it runs, not only at the end -- a first
version of this script printed nothing until every mode finished, which made a slow live
run indistinguishable from a hung one.

Run manually, from the project root (unbuffered, so progress prints promptly):

    uv run python -u -m eval.harness.measure_dev_case_accuracy

Writes eval/harness/results/phase4_dev_case_result.json.
"""

import json
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import fitz
from dotenv import load_dotenv

from app.boundary.mode import PrivacyMode
from app.config.form_schema import FormFieldSpec, FormSchema, load_form_schemas
from app.extraction.extractor import ExtractionResult, extract_field
from app.extraction.llm_client import LLMProviderError
from app.ingest.chunker import chunk_pages
from app.ingest.parser import parse_document
from app.retrieval.store import case_index_registry, embed_chunks

DATASET_PATH = Path(__file__).resolve().parent.parent / "dataset" / "phase4_dev_cases.json"
RESULT_PATH = Path(__file__).resolve().parent / "results" / "phase4_dev_case_result.json"

MODES = (PrivacyMode.NONE, PrivacyMode.FULL_TOKENIZE, PrivacyMode.POLICY_ENGINE)
MAX_RETRIES = 1  # no retry: a 65s wait already failed to clear the observed quota
# exhaustion (see module docstring) -- retrying further just spends time on a call that
# cannot succeed until the quota resets outside this session.
RETRY_DELAY_SECONDS = 65
CALL_TIMEOUT_SECONDS = 30
MIN_CALL_INTERVAL_SECONDS = 4.0  # 15/min, under the observed 20/min free-tier ceiling

_last_call_at: float = 0.0


def _throttle() -> None:
    """Proactive pacing before every extract_field attempt, regardless of whether this
    particular field will actually reach the network -- full_tokenize/policy_engine calls
    that fail inside protect() before any request is sent are throttled too, which costs a
    few extra seconds but keeps this function simple and correct rather than trying to
    predict which calls will hit the network."""
    global _last_call_at
    wait = MIN_CALL_INTERVAL_SECONDS - (time.monotonic() - _last_call_at)
    if wait > 0:
        time.sleep(wait)
    _last_call_at = time.monotonic()


def load_dev_cases() -> dict[str, Any]:
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def _pdf_bytes(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_textbox(fitz.Rect(50, 50, 550, 750), text, fontsize=11)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def _ingest_case_documents(case_id: str, documents: list[dict[str, str]]) -> None:
    all_chunks = []
    for document in documents:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(_pdf_bytes(document["text"]))
            tmp_path = Path(tmp.name)
        try:
            pages = parse_document(tmp_path, document_id=document["document_id"])
        finally:
            tmp_path.unlink(missing_ok=True)
        all_chunks.extend(chunk_pages(pages))
    case_index_registry.get_or_create(case_id).add(embed_chunks(all_chunks))


def _extract_with_timeout(
    case_id: str, field: FormFieldSpec, privacy_mode: PrivacyMode, reference_date: date
) -> ExtractionResult:
    """extract_field, but bounded by CALL_TIMEOUT_SECONDS. The underlying thread cannot be
    force-killed if it truly hangs (a Python limitation, not something app.extraction can
    fix), but this lets the *script* move on and record a timeout instead of blocking
    forever -- which is what an unbounded live run just did."""
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            extract_field, case_id=case_id, field=field, privacy_mode=privacy_mode, reference_date=reference_date
        )
        try:
            return future.result(timeout=CALL_TIMEOUT_SECONDS)
        except FutureTimeoutError as exc:
            raise LLMProviderError(f"extract_field did not return within {CALL_TIMEOUT_SECONDS}s") from exc


def _extract_with_retry(
    case_id: str, field: FormFieldSpec, privacy_mode: PrivacyMode, reference_date: date
) -> tuple[ExtractionResult | None, Exception | None]:
    last_provider_error: LLMProviderError | None = None
    for attempt in range(MAX_RETRIES):
        _throttle()
        try:
            return _extract_with_timeout(case_id, field, privacy_mode, reference_date), None
        except LLMProviderError as exc:
            last_provider_error = exc
            if attempt < MAX_RETRIES - 1:
                print(f"    retry {attempt + 1}/{MAX_RETRIES} in {RETRY_DELAY_SECONDS}s after: {exc}", flush=True)
                time.sleep(RETRY_DELAY_SECONDS)
        except Exception as exc:  # noqa: BLE001 -- a real, non-transient outcome to record, not retry
            return None, exc
    return None, last_provider_error


def _classify(ground_truth_value: str | None, result: ExtractionResult | None, error: Exception | None) -> str:
    if error is not None:
        return "error"
    assert result is not None
    if result.value is None:
        return "correct_abstention" if ground_truth_value is None else "incorrect_abstention"
    if ground_truth_value is None:
        return "hallucination"
    return "correct" if result.value.strip().casefold() == ground_truth_value.strip().casefold() else "incorrect_value"


def run_measurement(payload: dict[str, Any]) -> dict[str, Any]:
    schemas_by_id = {schema.id: schema for schema in load_form_schemas()}
    reference_date = date.fromisoformat(payload["reference_date"])
    cases = payload["cases"]

    per_mode: dict[str, Any] = {}
    for mode in MODES:
        print(f"=== {mode.value} ===", flush=True)
        field_results = []
        for case in cases:
            schema: FormSchema = schemas_by_id[case["form_schema_id"]]
            run_case_id = f"{case['case_id']}__{mode.value}"
            print(f"  {case['case_id']}: ingesting...", flush=True)
            _ingest_case_documents(run_case_id, case["documents"])

            for field in schema.fields:
                ground_truth_value = case["ground_truth"].get(field.name)
                result, error = _extract_with_retry(run_case_id, field, mode, reference_date)
                outcome = _classify(ground_truth_value, result, error)
                print(f"    {field.name}: {outcome}", flush=True)
                field_results.append(
                    {
                        "case_id": case["case_id"],
                        "field_name": field.name,
                        "field_type": field.type.value,
                        "ground_truth": ground_truth_value,
                        "extracted_value": result.value if result is not None else None,
                        "outcome": outcome,
                        "error": str(error) if error is not None else None,
                    }
                )

        per_mode[mode.value] = _summarize(field_results)

    return per_mode


def _summarize(field_results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(field_results)
    outcome_counts: dict[str, int] = {}
    for r in field_results:
        outcome_counts[r["outcome"]] = outcome_counts.get(r["outcome"], 0) + 1

    correct = outcome_counts.get("correct", 0) + outcome_counts.get("correct_abstention", 0)

    by_field_type: dict[str, dict[str, Any]] = {}
    for r in field_results:
        bucket = by_field_type.setdefault(r["field_type"], {"total": 0, "correct": 0})
        bucket["total"] += 1
        if r["outcome"] in ("correct", "correct_abstention"):
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


def main() -> None:
    load_dotenv()  # GEMINI_API_KEY (DECISIONS.md R15) is read from the process
    # environment; nothing else in app/ loads .env itself, so this standalone,
    # real-provider-calling script must, or a live run has no key to call with.
    payload = load_dev_cases()
    result = run_measurement(payload)

    result_with_timestamp = {"measured_at": datetime.now(UTC).isoformat(), "modes": result}
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(result_with_timestamp, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    for mode_name, summary in result.items():
        print(f"{mode_name}: accuracy={summary['accuracy']:.3f} ({summary['total_fields']} fields)")
        print(f"  outcomes: {summary['outcome_counts']}")
    print(f"Results written to {RESULT_PATH}")


if __name__ == "__main__":
    main()
