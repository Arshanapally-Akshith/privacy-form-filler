"""Phase 6 full-matrix evaluation harness (BUILD.md Phase 6 deliverable: "Harness running
the full matrix and writing results to disk"). Iterates (privacy mode x case x field) over
the committed Phase 6 dataset (eval/dataset/phase6_eval_cases.json, Commit 5) -- never
regenerates it -- routing every field through the real orchestration graph
(app.orchestration.graph.run_graph), not app.extraction.extractor.extract_field directly, so
this harness evaluates the actual production system: verification, bounded retries, and
routing (approved Phase 6 Commit 9 decision), unlike
eval/harness/measure_dev_case_accuracy.py's own pre-Phase-5 Phase 4 script.

**One run_graph call per field, not per case.** ARCH §7's graph processes whatever fields
are listed in OrchestrationState.pending_field_names/fields -- nothing in
app.orchestration.nodes assumes that set is the schema's *entire* field list (see
nodes.py's own `_field_spec_by_name`: "pending_field_names must always be a subset of the
schema's own field names"). Each field is therefore run through its own single-field
OrchestrationState (_single_field_state below), reusing new_orchestration_state's own
per-field FieldRecord initialization rather than duplicating it, then trimmed to one field.
This is what lets eval.harness.response_cache's per-field CacheKeyParams (Commit 8) apply
directly to a graph that is nominally whole-case: caching at the granularity of "this case's
this field under this privacy mode" is unaffected by whether other fields of the same case
were run in the same graph invocation or a separate one, since field processing has no
cross-field state (confirmed by reading every node function in app.orchestration.nodes: each
keys off current_field_name alone, never iterates every field).

**The cache is a passive dependency, never a decision-maker.** For every field: attempt
eval.harness.response_cache.get_cached_response(); on CacheMissError, lazily ingest the
case's documents (only once per (case, mode) run_case_id, and only if at least one field
actually misses, so an all-cache-hit run never touches ingestion or embedding at all), call
run_graph(), and put_cached_response() the result immediately. The cache module itself never
decides to call run_graph -- that decision belongs entirely to this harness, per its own
read/write split (Commit 8).

**Scoring is entirely eval.harness.scoring's** (Commit 7) classify_outcome /
summarize_field_results -- this file does not reclassify or re-aggregate anything of its
own. The graph's final FieldRecord (value/confidence/provenance) is converted to an
ExtractionResult (the shape classify_outcome already expects) regardless of the field's
final FieldState (FILLED/LOW_CONFIDENCE/CONFLICT/MISSING/HUMAN_REVIEWED): scoring is a
function of "what value ended up in the record," not of which routing path produced it. The
FieldState itself is still recorded on each field_results entry (a field the shared scoring
module doesn't have or need) so a *quantitative* count of which fields were flagged
(LOW_CONFIDENCE/CONFLICT) is available without re-deriving it -- but see the Commit 10
limitation below on what this does not give Phase 7's *qualitative* verifier audit.

**No retry/timeout/pacing robustness is added here.** measure_dev_case_accuracy.py's
elaborate throttling (_throttle, MAX_RETRIES, CALL_TIMEOUT_SECONDS) was added in response to
*measured* quota/hang problems during a live Phase 4 run, not built speculatively
(CLAUDE.md §9: no unrequested robustness). If a live Phase 6 run surfaces the same problems,
the same treatment can be added then; nothing in this commit's own requirements asks for it
now.

**Architectural assumptions and intentional limitations, consolidated
(Commit 10; regression-tested by tests/eval/test_harness_determinism.py):**

0. **Per-field graph execution** (detailed above, under "One run_graph call per field"):
   this harness deliberately does not run app.orchestration.graph.run_graph once per case
   the way app.api.cases does -- it runs it once per *field*, against a trimmed
   single-field OrchestrationState. Safe because no node in app.orchestration.nodes reads
   or mutates any field other than current_field_name.

0a. **Passive response cache** (detailed above, under "The cache is a passive
    dependency"): eval.harness.response_cache never decides to call run_graph on a miss --
    this module alone makes that call and alone decides to persist the result. The cache
    is pure storage, not an evaluation strategy.

1. **Deterministic dataset requirement.** Every determinism/reproducibility claim this
   harness or its tests make assumes the *input case list itself does not change* between
   the runs being compared -- this harness has no opinion on where `cases` came from, and
   does not re-validate that two calls received "the same" dataset beyond what Python
   equality already checks. eval/dataset/generate_phase6_dataset.py's own
   test_committed_dataset_matches_the_generator is what guards the *dataset's* own
   reproducibility (that the committed JSON matches what its generator would produce
   today); this harness's determinism claims are conditional on that upstream guarantee
   holding, not a replacement for it.

2. What "deterministic" means here is "given a fixed cache state, repeated harness runs
   converge to the identical result" -- not "the underlying LLM's own outputs are
   deterministic across independent live calls." They are not, in general (sampling,
   provider-side changes). The response cache is precisely the mechanism that neutralizes
   that: once a field's response is written, every later replay against that cache is
   pinned to the recorded value regardless of what a fresh live call might return next.
   Two *live* (uncached) runs of the same matrix are not claimed to produce identical
   results -- only two runs against the *same* cache state are.

3. The cache stores each field's final settled FieldRecord only -- not the verifier's
   reasoning-trace text, nor which retry attempt(s) preceded it. Replaying a cached matrix
   reproduces accuracy/scoring numbers exactly, but Phase 7's *qualitative* verifier audit
   (DECISIONS.md V11/V12 -- a human reading reasoning text to judge whether a flag was
   appropriate) needs the actual VerifierTrace objects a live app.orchestration.graph.run_graph
   call produces internally, which are discarded once this harness extracts the field's
   record from them. That audit must run against a live (or otherwise separately captured)
   run, not against this cache.

4. eval.harness.response_cache.put_cached_response stamps a fresh top-level "generated_at"
   timestamp on every write, so the cache *file's own bytes* differ between two
   independently-timed runs even when every cached entry's content is identical.
   Reproducibility claims apply to the cache's entries and to this harness's result output,
   never to the cache file's raw bytes -- it is an accumulating scratch file, not a stored
   result artifact.

Lives entirely in eval/ -- routes exclusively through app.orchestration.graph.run_graph,
never a second LLM call site (Invariant I3 untouched). No file under app/ is modified.
"""

import json
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import fitz

from app.api.models import FieldRecord
from app.boundary.mode import PrivacyMode
from app.boundary.policy_engine import ACTIVE_POLICY_CONFIG_NAME
from app.config.form_schema import FormFieldSpec, FormSchema, load_form_schemas
from app.extraction.constants import DEFAULT_RETRIEVAL_TOP_K, GENERATION_MODEL
from app.extraction.extractor import ExtractionProvenance, ExtractionResult
from app.ingest.chunker import chunk_pages
from app.ingest.parser import parse_document
from app.orchestration.graph import run_graph
from app.orchestration.state import OrchestrationState, new_orchestration_state
from app.retrieval.store import case_index_registry, embed_chunks
from eval.harness.response_cache import (
    DEFAULT_CACHE_PATH,
    CacheMissError,
    get_cached_response,
    key_params_from_field,
    put_cached_response,
)
from eval.harness.scoring import classify_outcome, summarize_field_results

DATASET_PATH = Path(__file__).resolve().parent.parent / "dataset" / "phase6_eval_cases.json"
RESULT_PATH = Path(__file__).resolve().parent / "results" / "phase6_matrix_result.json"

MODES = (PrivacyMode.NONE, PrivacyMode.FULL_TOKENIZE, PrivacyMode.POLICY_ENGINE)


def load_dataset() -> list[dict[str, Any]]:
    payload = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = payload["cases"]
    return cases


def _pdf_bytes(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_textbox(fitz.Rect(50, 50, 550, 750), text, fontsize=11)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def _ingest_case_documents(run_case_id: str, documents: list[dict[str, Any]]) -> None:
    """Same technique as eval/harness/measure_dev_case_accuracy.py's own private helper of
    the same purpose (render each document's text to a synthetic single-page PDF, then
    parse/chunk/embed it) -- kept as its own small copy here rather than imported across
    script boundaries: that function is private to Phase 4's own script, and this commit's
    requirements do not ask for refactoring it (Commit 7 already extracted the one piece --
    scoring -- both scripts needed identically; ingestion glue is not that)."""
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
    case_index_registry.get_or_create(run_case_id).add(embed_chunks(all_chunks))


def _single_field_state(
    case_id: str,
    form_schema: FormSchema,
    field_name: str,
    privacy_mode: PrivacyMode,
    reference_date: date,
) -> OrchestrationState:
    """A full-schema OrchestrationState restricted to one pending field. form_schema still
    declares every field (nodes look field specs up from it by name regardless of what's
    pending), but `fields`/`pending_field_names` carry only `field_name` -- see this
    module's own docstring for why that is a safe, correct way to drive this graph."""
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


def _evaluate_field(
    *,
    case: dict[str, Any],
    run_case_id: str,
    schema: FormSchema,
    field: FormFieldSpec,
    privacy_mode: PrivacyMode,
    reference_date: date,
    cache_path: Path,
    ingested: set[str],
) -> tuple[dict[str, Any] | None, Exception | None]:
    """Returns (response, error) -- exactly one is not None, mirroring
    measure_dev_case_accuracy.py's own (result, error) contract."""
    params = key_params_from_field(
        case_id=case["case_id"],
        field=field,
        privacy_mode=privacy_mode,
        reference_date=reference_date,
        top_k=DEFAULT_RETRIEVAL_TOP_K,
    )
    try:
        return get_cached_response(params, path=cache_path), None
    except CacheMissError:
        pass

    if run_case_id not in ingested:
        try:
            _ingest_case_documents(run_case_id, case["documents"])
        except Exception as exc:  # noqa: BLE001 -- discovered live (Phase 7 Commit 7): an
            # embedding-provider failure during ingestion (e.g. the embedding model's own,
            # separate per-minute rate limit -- a different quota entirely from the
            # generation model's daily cap) previously propagated uncaught, crashing the
            # whole run_matrix() call and losing every already-computed in-memory result
            # that had not yet reached RESULT_PATH. Mirrors the run_graph handling directly
            # below: a real, measured outcome to record for this one field, not a reason to
            # abort the entire matrix. `ingested` is deliberately NOT marked on failure, so
            # the very next field for this same (case, mode) naturally retries ingestion --
            # correct for a transient, explicitly time-bounded rate limit (the observed
            # error's own payload states a retry delay), not a permanent failure.
            return None, exc
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


def run_matrix(cases: list[dict[str, Any]], *, cache_path: Path = DEFAULT_CACHE_PATH) -> dict[str, Any]:
    schemas_by_id = {schema.id: schema for schema in load_form_schemas()}
    ingested: set[str] = set()

    per_mode: dict[str, Any] = {}
    for mode in MODES:
        field_results: list[dict[str, Any]] = []
        for case in cases:
            schema = schemas_by_id[case["form_schema_id"]]
            run_case_id = f"{case['case_id']}__{mode.value}"
            reference_date = date.fromisoformat(case["reference_date"])

            for field in schema.fields:
                ground_truth_value = case["ground_truth"].get(field.name)
                response, error = _evaluate_field(
                    case=case,
                    run_case_id=run_case_id,
                    schema=schema,
                    field=field,
                    privacy_mode=mode,
                    reference_date=reference_date,
                    cache_path=cache_path,
                    ingested=ingested,
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

    return per_mode


def execution_status_from_modes(modes_result: dict[str, Any]) -> str:
    """Pure summary of whether any field, in any mode, resolved to an `error` outcome
    (Phase 7 Commit 7: generated artifacts must record an explicit execution status, not
    just per-field outcome_counts a reader would otherwise have to aggregate by hand to
    notice a partial/quota-bounded run)."""
    any_errors = any(summary["outcome_counts"].get("error", 0) > 0 for summary in modes_result.values())
    return "partial_errors_present" if any_errors else "complete"


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()  # GEMINI_API_KEY (DECISIONS.md R15) -- see measure_dev_case_accuracy.py's
    # own main() for why this standalone, real-provider-calling script must load it itself.
    cases = load_dataset()
    result = run_matrix(cases)

    result_payload = {
        "measured_at": datetime.now(UTC).isoformat(),
        "model": GENERATION_MODEL,
        "configuration": {
            "dataset_case_count": len(cases),
            "top_k": DEFAULT_RETRIEVAL_TOP_K,
            "active_policy_config_name": ACTIVE_POLICY_CONFIG_NAME,
            "modes": [mode.value for mode in MODES],
        },
        "execution_status": execution_status_from_modes(result),
        "modes": result,
    }
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(result_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    for mode_name, summary in result.items():
        print(f"{mode_name}: accuracy={summary['accuracy']:.3f} ({summary['total_fields']} fields)")
        print(f"  outcomes: {summary['outcome_counts']}")
    print(f"execution_status={result_payload['execution_status']}")
    print(f"Results written to {RESULT_PATH}")


if __name__ == "__main__":
    main()
