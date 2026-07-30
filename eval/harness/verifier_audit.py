"""Verifier audit — capture only (Phase 7 Commit 9; ARCHITECTURE.md §7.1/§8, DECISIONS.md
V11/V12).

**This script captures raw evidence. It does not judge it.** BUILD.md Phase 7 task 3 asks
for a *manual* review: precision on flagged cases, recall on should-have-been-flagged cases,
computed by a human reading real verifier reasoning against real ground truth. That judgment
step is deliberately a separate, later commit — this one only produces the real,
persisted `VerifierTrace` data that judgment needs. Nothing here labels a case "correctly
flagged," computes precision/recall, or asserts any conclusion about verifier quality.
`DECISIONS.md` and `RESULTS.md` are untouched by this commit, regardless of whether the live
run below succeeds or is quota-blocked -- there is nothing to record in either document
until a human has actually looked at the traces.

**Why this needs its own live run, not a replay of an existing artifact.**
eval/harness/run_matrix.py's own response cache stores only a field's final, settled
`FieldRecord` -- explicitly not the `VerifierTrace` objects a live `run_graph` call produces
internally (that module's own docstring, limitation 3). There is no existing artifact this
script can read the traces from; they only exist during a live call, gone once the graph
returns unless captured then, which is exactly what this script does.

**Case selection is representative, not engineered.** Per this commit's explicit
instruction, cases are not hand-picked or crafted to force a particular verifier decision
(e.g. re_retrieve) -- select_audit_cases() takes literally every adversarial case already
present in the committed, previously-approved eval/dataset/phase6_eval_cases.json (12 cases:
conflict/missing_field/near_duplicate_name/unusual_format, 3 each -- BUILD.md Phase 6's own
composition, not something this commit chose), plus a fixed, small number of clean cases
in the dataset's own order as negative controls (CLEAN_CASE_COUNT). Whichever verifier
decision paths do or do not occur across these 15 cases are reported honestly in the next
(interpretation) commit -- not engineered toward here.

select_audit_field() picks exactly one field per case, driven entirely by that case's own
already-recorded adversarial `detail` (the specific conflicting field for `conflict`, the
first omitted required field for `missing_field`, `full_name` for `near_duplicate_name`
since the ambiguity *is* the name field, the reformatted field for `unusual_format`) --
never a manual per-case-id pick.

**PrivacyMode.NONE only**, mirroring Phase 5's own manual verifier review precedent
exactly: isolates verifier reasoning quality from the already-documented, unrelated Phase 4
finding that Tokenize has no alphabet for NAME/ADDRESS/DATE, which would otherwise abort
every case before the verifier ever ran (exactly as it did in Phase 4's own dev-case
measurement). This means the audit says nothing about verifier behavior under
full_tokenize/policy_engine -- a scope limitation, not an oversight.

**No response cache.** Unlike run_matrix.py, this script never reuses eval.harness.
response_cache -- that cache's whole contract is "the final settled response," which is
exactly the piece that discards what this audit needs. Each of the 15 cases is small and
this script's scope is deliberately narrow, so no accumulating-cache/resume mechanism is
built for it (CLAUDE.md §9: no unrequested infrastructure) -- a future re-run simply repeats
whichever of the 15 cases didn't get a real trace last time.

**Per-case failure isolation, learned directly from Commit 7.** Both ingestion and the
run_graph call are wrapped per case: an embedding- or generation-quota failure on one case
must not abort the remaining 14 (Commit 7 found exactly this class of bug in run_matrix.py
and fixed it there; this script is written with that lesson already applied, not repeating
the mistake).
"""

import json
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import fitz

from app.boundary.mode import PrivacyMode
from app.config.form_schema import FormSchema, load_form_schemas
from app.extraction.constants import GENERATION_MODEL
from app.ingest.chunker import chunk_pages
from app.ingest.parser import parse_document
from app.orchestration.graph import run_graph
from app.orchestration.state import (
    OrchestrationState,
    VerifierTrace,
    new_orchestration_state,
)
from app.retrieval.store import case_index_registry, embed_chunks
from eval.harness.run_matrix import load_dataset

RESULT_PATH = Path(__file__).resolve().parent / "results" / "verifier_audit_result.json"

CLEAN_CASE_COUNT = 3
CLEAN_CASE_FIELD = "full_name"


class UnknownAdversarialTypeError(ValueError):
    """Raised for an adversarial_type this module has no field-selection rule for -- a
    silent default would risk reviewing the wrong field for a case type this script was
    never actually taught how to handle (CLAUDE.md §5)."""


def select_audit_field(case: dict[str, Any]) -> str:
    """Which single field to review for one case, derived entirely from that case's own
    recorded structure -- never a hand-picked, per-case-id choice."""
    adversarial = case.get("adversarial")
    if adversarial is None:
        return CLEAN_CASE_FIELD

    detail = adversarial["detail"]
    adversarial_type = adversarial["adversarial_type"]
    if adversarial_type in ("conflict", "unusual_format"):
        field_name = detail["field"]
        assert isinstance(field_name, str)
        return field_name
    if adversarial_type == "missing_field":
        first_field = detail["omitted_required_fields"].split(",")[0]
        assert isinstance(first_field, str)
        return first_field
    if adversarial_type == "near_duplicate_name":
        return "full_name"
    raise UnknownAdversarialTypeError(f"No field-selection rule for adversarial_type {adversarial_type!r}")


def select_audit_cases(all_cases: list[dict[str, Any]], *, clean_case_count: int = CLEAN_CASE_COUNT) -> list[dict[str, Any]]:
    """Every adversarial case in the committed dataset, plus a fixed number of clean cases
    in the dataset's own order -- representative of the dataset's real composition, not
    curated toward any particular verifier outcome."""
    adversarial_cases = [case for case in all_cases if case.get("adversarial") is not None]
    clean_cases = [case for case in all_cases if case.get("adversarial") is None][:clean_case_count]
    return adversarial_cases + clean_cases


def _pdf_bytes(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_textbox(fitz.Rect(50, 50, 550, 750), text, fontsize=11)
    pdf_bytes: bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def _ingest_case_documents(run_case_id: str, documents: list[dict[str, Any]]) -> None:
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
    case_id: str, form_schema: FormSchema, field_name: str, reference_date: date
) -> OrchestrationState:
    full_state = new_orchestration_state(case_id, form_schema, PrivacyMode.NONE, reference_date)
    return OrchestrationState(
        case_id=full_state.case_id,
        form_schema=full_state.form_schema,
        privacy_mode=full_state.privacy_mode,
        reference_date=full_state.reference_date,
        fields={field_name: full_state.fields[field_name]},
        pending_field_names=[field_name],
    )


def _trace_to_dict(trace: VerifierTrace) -> dict[str, Any]:
    return {
        "field_name": trace.field_name,
        "decision": trace.decision.value,
        "reasoning": trace.reasoning,
        "evidence": [
            {
                "document_id": evidence.document_id,
                "page_number": evidence.page_number,
                "chunk_index": evidence.chunk_index,
                "text": evidence.text,
                "score": evidence.score,
            }
            for evidence in trace.evidence
        ],
    }


@dataclass(frozen=True)
class AuditCaseRecord:
    case_id: str
    field_name: str
    adversarial_type: str | None
    adversarial_detail: dict[str, Any] | None
    ground_truth: str | None
    extracted_value: str | None
    confidence: float | None
    final_state: str | None
    retry_count: int | None
    verifier_traces: list[dict[str, Any]]
    error: str | None


def run_audit_case(case: dict[str, Any], schemas_by_id: dict[str, FormSchema]) -> AuditCaseRecord:
    """Ingests and runs exactly one (case, field) live, in PrivacyMode.NONE. Never raises --
    an ingestion or graph failure (e.g. a provider quota error) is recorded on this one
    case's own record, per Commit 7's lesson, so it can never abort the remaining cases."""
    field_name = select_audit_field(case)
    adversarial = case.get("adversarial")
    run_case_id = f"verifier-audit-{case['case_id']}"

    try:
        schema = schemas_by_id[case["form_schema_id"]]
        reference_date = date.fromisoformat(case["reference_date"])
        _ingest_case_documents(run_case_id, case["documents"])
        state = _single_field_state(run_case_id, schema, field_name, reference_date)
        result_state = run_graph(state)
    except Exception as exc:  # noqa: BLE001 -- a real, measured outcome to record for this
        # one case, per Commit 7's own lesson: never let one case's failure abort the rest.
        return AuditCaseRecord(
            case_id=case["case_id"],
            field_name=field_name,
            adversarial_type=adversarial["adversarial_type"] if adversarial else None,
            adversarial_detail=adversarial["detail"] if adversarial else None,
            ground_truth=case["ground_truth"].get(field_name),
            extracted_value=None,
            confidence=None,
            final_state=None,
            retry_count=None,
            verifier_traces=[],
            error=str(exc),
        )

    field_graph_state = result_state.fields[field_name]
    record = field_graph_state.record
    return AuditCaseRecord(
        case_id=case["case_id"],
        field_name=field_name,
        adversarial_type=adversarial["adversarial_type"] if adversarial else None,
        adversarial_detail=adversarial["detail"] if adversarial else None,
        ground_truth=case["ground_truth"].get(field_name),
        extracted_value=record.value,
        confidence=record.confidence,
        final_state=record.state.value,
        retry_count=field_graph_state.retry_count,
        verifier_traces=[_trace_to_dict(trace) for trace in field_graph_state.verifier_traces],
        error=None,
    )


def run_audit(cases: list[dict[str, Any]]) -> list[AuditCaseRecord]:
    schemas_by_id = {schema.id: schema for schema in load_form_schemas()}
    return [run_audit_case(case, schemas_by_id) for case in cases]


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()  # GEMINI_API_KEY (DECISIONS.md R15), same convention as every other
    # live-calling harness script.

    all_cases = load_dataset()
    selected_cases = select_audit_cases(all_cases)
    records = run_audit(selected_cases)

    real_trace_count = sum(1 for record in records if record.error is None)
    status = "measured" if real_trace_count > 0 else "blocked"

    payload = {
        "measured_at": datetime.now(UTC).isoformat(),
        "model": GENERATION_MODEL,
        "privacy_mode": PrivacyMode.NONE.value,
        "status": status,
        "case_count": len(records),
        "cases_with_real_traces": real_trace_count,
        "cases": [asdict(record) for record in records],
    }
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    for record in records:
        outcome = f"error: {record.error}" if record.error else f"state={record.final_state}"
        print(f"{record.case_id}/{record.field_name}: {outcome}")
    print(f"status={status} ({real_trace_count}/{len(records)} cases produced a real trace)")
    print(f"Results written to {RESULT_PATH}")


if __name__ == "__main__":
    main()
