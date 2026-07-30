"""OCR-path accuracy measurement (Phase 7 Commit 6; BUILD.md Phase 7 task 1, DECISIONS.md
V18: "accuracy on OCR-derived text reported separately from native text").

**Why this is a separate script, not a flag on run_matrix.py.** run_matrix.py's own
`_ingest_case_documents` renders every case's documents -- including the two OCR-designated
cases ("phase6_ocr_kyc", "phase6_ocr_insurance") -- as synthetic, clean, text-layer PDFs from
each document's stored `text` field. That is deliberate and correct for what run_matrix.py
measures (native-text extraction accuracy across the full 56-case matrix), but it means the
20 real rendered images already committed under eval/dataset/fixtures/phase6_ocr/ (tracked by
that directory's own manifest.json) are never actually pushed through Tesseract by any
existing harness -- the OCR path itself has never been exercised end to end by an evaluation
script. This script exists to close exactly that gap, and only that gap.

**The only architectural difference from run_matrix.py is the document source.** Everything
else -- per-field graph execution via app.orchestration.graph.run_graph, the response cache's
read/write split, and outcome scoring -- is reused unchanged:
  - eval.harness.run_matrix.load_dataset() loads the committed phase6_eval_cases.json;
    this script filters it down to the two OCR-designated case_ids rather than re-deriving
    dataset access of its own.
  - eval.harness.response_cache's public API (CacheKeyParams, key_params_from_field,
    get_cached_response, put_cached_response, CacheMissError) is reused directly, against
    its own explicit, overridable `path` parameter -- see OCR_CACHE_PATH below for why this
    script uses a distinct cache file rather than run_matrix.py's.
  - eval.harness.scoring's classify_outcome / summarize_field_results is reused directly and
    unmodified -- an OCR-derived field result has exactly the same shape as a native-text
    one (ExtractionResult in, outcome out), so nothing about scoring needs to know or care
    which path produced the evidence.

**What is genuinely new here (and only here):** resolving each case document to its real
rendered image for a given OCR condition (build_document_image_index below, driven by the
committed manifest.json -- never a second, hand-maintained mapping, per
eval/dataset/generate_phase6_dataset.py's own precedent for the same manifest), and
ingesting via app.ingest.parser.parse_document on that real image path -- which actually
invokes Tesseract (app.ingest.parser's own `_parse_image`), unlike run_matrix.py's synthetic-
PDF route, which never touches OCR at all regardless of which case it's given.

**Why the single-field graph trick, the ingestion glue, and the response/result conversion
helpers below are small private copies of run_matrix.py's own, rather than shared imports.**
run_matrix.py's own docstring already establishes this precedent explicitly (Commit 9's
"kept as its own small copy here rather than imported across script boundaries... this
commit's requirements do not ask for refactoring it", inherited from
measure_dev_case_accuracy.py before it): a private, underscore-prefixed helper in one eval
script is that script's own implementation detail, not a dependency surface for another
script to reach into. This script follows the same convention rather than promoting those
helpers to public names in run_matrix.py, which would be an unrequested change to
already-reviewed Phase 6 code for this commit's sake alone.

**Cache isolation (important correctness point, not just tidiness).**
eval.harness.response_cache.CacheKeyParams hashes on `case_id` as a literal string. If this
script used the *same* bare case_id run_matrix.py uses ("phase6_ocr_kyc") for the *same*
field/mode/reference_date/top_k, the two scripts' cache keys would collide even though they
represent completely different evidence (a synthetic clean-text PDF vs. a real, possibly
OCR-noisy, rendered image) -- a stale cross-script cache hit would silently return the wrong
script's answer. This script's run_case_id is therefore namespaced per condition
(f"{case_id}__ocr__{condition}__{mode.value}"), and it writes to its own OCR_CACHE_PATH file
entirely, not run_matrix.py's DEFAULT_CACHE_PATH -- belt and suspenders, but the distinct
run_case_id alone is what actually prevents the collision.

**Scope of today's live run, and why.** Two independent constraints bound what this script's
own `main()` actually attempts by default:
  1. Every real LLM generation call in this project shares the same pinned model's small
     daily quota (DECISIONS.md E9, confirmed 20 requests/day/model) -- already partly spent
     today by other Phase 7 commits. `run_graph` invokes the verifier on every field
     unconditionally (app.orchestration.nodes.verify_current_field), so each field-run costs
     at least 2 real calls, not 1.
  2. Tesseract itself is not installed in every environment this script might run in (it is
     in CI/Docker, per .github/workflows/ci.yml and DECISIONS.md E10) -- checked defensively
     at the start of main() via shutil.which("tesseract"), mirroring tests/conftest.py's own
     existing skip mechanism, so a missing binary produces one clear, honest
     "environment_blocked" artifact rather than a raw traceback or a silent partial file.
  MODES_TO_RUN therefore defaults to (PrivacyMode.NONE,) only -- the mode with no protect()
  call at all, avoiding both the quota cost of protecting/reversing every field and the
  already-documented Phase 4 finding that FULL_TOKENIZE/POLICY_ENGINE abort entirely on any
  document containing a detected NAME (which these real, unmodifiable KYC/insurance document
  renders certainly do). Both are overridable parameters on run_matrix_for_ocr(), not hard
  limits on what this script *can* measure -- just what it measures by default, today.
"""

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from shutil import which
from typing import Any

from app.api.models import FieldRecord
from app.boundary.mode import PrivacyMode
from app.config.form_schema import FormFieldSpec, FormSchema, load_form_schemas
from app.extraction.constants import DEFAULT_RETRIEVAL_TOP_K
from app.extraction.extractor import ExtractionProvenance, ExtractionResult
from app.ingest.chunker import chunk_pages
from app.ingest.parser import parse_document
from app.orchestration.graph import run_graph
from app.orchestration.state import OrchestrationState, new_orchestration_state
from app.retrieval.store import case_index_registry, embed_chunks
from eval.harness.response_cache import (
    CacheMissError,
    get_cached_response,
    key_params_from_field,
    put_cached_response,
)
from eval.harness.run_matrix import load_dataset
from eval.harness.scoring import classify_outcome, summarize_field_results

OCR_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "dataset" / "fixtures" / "phase6_ocr"
OCR_MANIFEST_PATH = OCR_FIXTURES_DIR / "manifest.json"
OCR_CACHE_PATH = Path(__file__).resolve().parent / "fixtures" / "phase6_ocr_response_cache.json"
RESULT_PATH = Path(__file__).resolve().parent / "results" / "phase6_ocr_matrix_result.json"

# The two cases whose documents were also rendered as real, committed images (see this
# dataset's own generation commit -- eval/dataset/generate_ocr_subset.py's
# build_representative_cases(), reused verbatim by generate_phase6_dataset.py rather than
# re-derived, so these case_ids are guaranteed identical to the manifest's own coverage).
OCR_CASE_IDS: tuple[str, ...] = ("phase6_ocr_kyc", "phase6_ocr_insurance")
OCR_CONDITIONS: tuple[str, ...] = ("clean_scan", "photo_like")
MODES_TO_RUN: tuple[PrivacyMode, ...] = (PrivacyMode.NONE,)


class OcrImageNotFoundError(LookupError):
    """Raised when the manifest has no rendered image for a (case_id, document_id,
    condition) this script needs -- a gap here means the manifest and the dataset have
    silently drifted apart, and must fail loudly rather than skip the field (CLAUDE.md §5).
    """


@dataclass(frozen=True)
class ManifestEntry:
    source_case_id: str
    source_document_id: str
    condition: str
    image_file: str


def load_ocr_manifest(path: Path = OCR_MANIFEST_PATH) -> list[ManifestEntry]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        ManifestEntry(
            source_case_id=entry["source_case_id"],
            source_document_id=entry["source_document_id"],
            condition=entry["condition"],
            image_file=entry["image_file"],
        )
        for entry in raw
    ]


def build_document_image_index(manifest: list[ManifestEntry]) -> dict[tuple[str, str, str], str]:
    """Pure: (case_id, document_id, condition) -> image filename. The manifest is the sole
    authoritative record of this mapping (eval/dataset/generate_phase6_dataset.py's own
    docstring already states this for the dataset side); this function does not duplicate
    or second-guess it, only reshapes it into a lookup."""
    return {
        (entry.source_case_id, entry.source_document_id, entry.condition): entry.image_file
        for entry in manifest
    }


def resolve_image_path(
    image_index: dict[tuple[str, str, str], str], case_id: str, document_id: str, condition: str
) -> Path:
    image_file = image_index.get((case_id, document_id, condition))
    if image_file is None:
        raise OcrImageNotFoundError(
            f"No rendered image for case_id={case_id!r}, document_id={document_id!r}, "
            f"condition={condition!r} in {OCR_MANIFEST_PATH}"
        )
    return OCR_FIXTURES_DIR / image_file


def select_ocr_cases(all_cases: list[dict[str, Any]], case_ids: tuple[str, ...] = OCR_CASE_IDS) -> list[dict[str, Any]]:
    """Pure filter over eval.harness.run_matrix.load_dataset()'s own output -- reuses that
    function rather than re-implementing dataset loading, and only selects the OCR-
    designated cases, preserving `case_ids`' order rather than the dataset's own order."""
    by_id = {case["case_id"]: case for case in all_cases}
    missing = [case_id for case_id in case_ids if case_id not in by_id]
    if missing:
        raise OcrImageNotFoundError(f"OCR case_id(s) not found in the committed dataset: {missing}")
    return [by_id[case_id] for case_id in case_ids]


def _single_field_state(
    case_id: str,
    form_schema: FormSchema,
    field_name: str,
    privacy_mode: PrivacyMode,
    reference_date: date,
) -> OrchestrationState:
    """Identical in behavior to run_matrix.py's own private helper of the same name -- see
    this module's own docstring for why it is a small, separate copy rather than a shared
    import."""
    full_state = new_orchestration_state(case_id, form_schema, privacy_mode, reference_date)
    return OrchestrationState(
        case_id=full_state.case_id,
        form_schema=full_state.form_schema,
        privacy_mode=full_state.privacy_mode,
        reference_date=full_state.reference_date,
        fields={field_name: full_state.fields[field_name]},
        pending_field_names=[field_name],
    )


def _record_to_response(record: FieldRecord) -> dict[str, Any]:
    return {
        "value": record.value,
        "confidence": record.confidence,
        "state": record.state.value,
        "provenance": (
            {"document_id": record.provenance.document_id, "page_number": record.provenance.page_number}
            if record.provenance is not None
            else None
        ),
    }


def _response_to_extraction_result(response: dict[str, Any]) -> ExtractionResult:
    provenance = response["provenance"]
    return ExtractionResult(
        value=response["value"],
        confidence=response["confidence"],
        provenance=(
            ExtractionProvenance(document_id=provenance["document_id"], page_number=provenance["page_number"])
            if provenance is not None
            else None
        ),
    )


def ingest_case_documents_from_images(
    run_case_id: str,
    documents: list[dict[str, Any]],
    *,
    case_id: str,
    condition: str,
    image_index: dict[tuple[str, str, str], str],
) -> None:
    """The one genuinely new ingestion path this script adds: each document is parsed from
    its *real, committed rendered image* for `condition` (invoking actual OCR via
    app.ingest.parser's image branch), not from a synthetic text-layer PDF. `documents`'
    own `text` field is deliberately never read here -- only `document_id`, to resolve which
    image file stands in for it."""
    all_chunks = []
    for document in documents:
        image_path = resolve_image_path(image_index, case_id, document["document_id"], condition)
        pages = parse_document(image_path, document_id=document["document_id"])
        all_chunks.extend(chunk_pages(pages))
    case_index_registry.get_or_create(run_case_id).add(embed_chunks(all_chunks))


def _evaluate_field(
    *,
    case: dict[str, Any],
    run_case_id: str,
    schema: FormSchema,
    field: FormFieldSpec,
    privacy_mode: PrivacyMode,
    reference_date: date,
    condition: str,
    image_index: dict[tuple[str, str, str], str],
    cache_path: Path,
    ingested: set[str],
    ingestion_error: Exception | None,
) -> tuple[dict[str, Any] | None, Exception | None]:
    """Mirrors run_matrix.py's own _evaluate_field exactly in structure (cache-first, ingest-
    on-miss, run_graph, cache the result) -- the only addition is `ingestion_error`: if this
    (case, condition)'s image ingestion already failed once (e.g. Tesseract unavailable),
    every field short-circuits to that same error immediately rather than re-attempting (and
    re-failing) ingestion once per field."""
    params = key_params_from_field(
        case_id=run_case_id,
        field=field,
        privacy_mode=privacy_mode,
        reference_date=reference_date,
        top_k=DEFAULT_RETRIEVAL_TOP_K,
    )
    try:
        return get_cached_response(params, path=cache_path), None
    except CacheMissError:
        pass

    if ingestion_error is not None:
        return None, ingestion_error

    if run_case_id not in ingested:
        ingest_case_documents_from_images(
            run_case_id, case["documents"], case_id=case["case_id"], condition=condition, image_index=image_index
        )
        ingested.add(run_case_id)

    state = _single_field_state(run_case_id, schema, field.name, privacy_mode, reference_date)
    try:
        result_state = run_graph(state)
    except Exception as exc:  # noqa: BLE001 -- a real, measured outcome to record, not retry
        return None, exc

    record = result_state.fields[field.name].record
    response = _record_to_response(record)
    put_cached_response(params, response, path=cache_path)
    return response, None


def run_matrix_for_ocr(
    cases: list[dict[str, Any]],
    *,
    conditions: tuple[str, ...] = OCR_CONDITIONS,
    modes: tuple[PrivacyMode, ...] = MODES_TO_RUN,
    image_index: dict[tuple[str, str, str], str] | None = None,
    cache_path: Path = OCR_CACHE_PATH,
) -> dict[str, Any]:
    schemas_by_id = {schema.id: schema for schema in load_form_schemas()}
    resolved_image_index = image_index if image_index is not None else build_document_image_index(load_ocr_manifest())
    ingested: set[str] = set()

    per_case: dict[str, Any] = {}
    for case in cases:
        schema = schemas_by_id[case["form_schema_id"]]
        reference_date = date.fromisoformat(case["reference_date"])
        per_condition: dict[str, Any] = {}

        for condition in conditions:
            per_mode: dict[str, Any] = {}
            for mode in modes:
                run_case_id = f"{case['case_id']}__ocr__{condition}__{mode.value}"

                # Attempt ingestion exactly once per (case, condition, mode) run_case_id --
                # if it fails (e.g. Tesseract missing), every field in this slice records
                # the same error rather than each re-attempting and re-failing ingestion.
                ingestion_error: Exception | None = None
                if run_case_id not in ingested:
                    try:
                        ingest_case_documents_from_images(
                            run_case_id, case["documents"], case_id=case["case_id"], condition=condition,
                            image_index=resolved_image_index,
                        )
                        ingested.add(run_case_id)
                    except Exception as exc:  # noqa: BLE001 -- recorded per field below, not retried
                        ingestion_error = exc

                field_results: list[dict[str, Any]] = []
                for field in schema.fields:
                    ground_truth_value = case["ground_truth"].get(field.name)
                    response, error = _evaluate_field(
                        case=case,
                        run_case_id=run_case_id,
                        schema=schema,
                        field=field,
                        privacy_mode=mode,
                        reference_date=reference_date,
                        condition=condition,
                        image_index=resolved_image_index,
                        cache_path=cache_path,
                        ingested=ingested,
                        ingestion_error=ingestion_error,
                    )
                    result = _response_to_extraction_result(response) if response is not None else None
                    outcome = classify_outcome(ground_truth_value, result, error)
                    field_results.append(
                        {
                            "case_id": case["case_id"],
                            "field_name": field.name,
                            "field_type": field.type.value,
                            "ground_truth": ground_truth_value,
                            "extracted_value": result.value if result is not None else None,
                            "field_state": response["state"] if response is not None else None,
                            "outcome": outcome,
                            "error": str(error) if error is not None else None,
                        }
                    )

                per_mode[mode.value] = summarize_field_results(field_results)
            per_condition[condition] = per_mode
        per_case[case["case_id"]] = per_condition

    return per_case


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()  # GEMINI_API_KEY (DECISIONS.md R15), same convention as every other
    # live-calling harness script.

    if which("tesseract") is None:
        # Mirrors tests/conftest.py's own detection exactly -- an honest, structured
        # "blocked" artifact instead of letting TesseractNotFoundError crash uncleanly or
        # silently writing a partial/empty file (CLAUDE.md §5: no silent fallback).
        blocked_payload: dict[str, Any] = {
            "measured_at": datetime.now(UTC).isoformat(),
            "status": "environment_blocked",
            "reason": (
                "tesseract binary not found on PATH in this environment (DECISIONS.md E10); "
                "CI and Docker install it (.github/workflows/ci.yml) -- rerun there, or on any "
                "machine with tesseract installed, to produce a real measurement."
            ),
            "attempted_cases": list(OCR_CASE_IDS),
            "attempted_conditions": list(OCR_CONDITIONS),
            "attempted_modes": [mode.value for mode in MODES_TO_RUN],
        }
        RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULT_PATH.write_text(json.dumps(blocked_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("tesseract not found -- wrote environment_blocked result to", RESULT_PATH)
        return

    all_cases = load_dataset()
    cases = select_ocr_cases(all_cases)
    result = run_matrix_for_ocr(cases)

    result_payload = {"measured_at": datetime.now(UTC).isoformat(), "status": "measured", "cases": result}
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(result_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    for case_id, per_condition in result.items():
        for condition, per_mode in per_condition.items():
            for mode_name, summary in per_mode.items():
                print(
                    f"{case_id}/{condition}/{mode_name}: accuracy={summary['accuracy']:.3f} "
                    f"({summary['total_fields']} fields)"
                )
    print(f"Results written to {RESULT_PATH}")


if __name__ == "__main__":
    main()
