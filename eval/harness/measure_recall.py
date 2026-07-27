"""Recall@5 measurement harness (BUILD.md Phase 1 tasks 7-8).

Pipeline: document -> parser -> chunker -> embed_chunks -> index -> build_query_text ->
embed query -> retrieve -> recall@5, computed against
eval/dataset/phase1_retrieval_pairs.json.

run_measurement() is the reusable pipeline core, shared by this script's live run (main(),
which calls the real embedding provider) and the offline regression test
(tests/eval/test_recall_regression.py, which monkeypatches embed_texts to replay the
committed cache -- the same monkeypatch-at-the-import-site pattern already used throughout
this project's test suite, rather than this function taking an injected embedding
callable). summarize_hits() is the pure recall@k arithmetic, tested in isolation against a
hand-computed fixture.

Run manually to reproduce a live measurement, from the project root:

    uv run python -m eval.harness.measure_recall

Writes:
  - eval/harness/results/phase1_recall_result.json -- the stored run every recall@5 number
    in DECISIONS.md traces back to.
  - eval/harness/fixtures/phase1_embedding_cache.json -- regenerated fresh, so the offline
    regression test can replay this exact run without a live API call.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.ingest.chunker import Chunk, chunk_pages
from app.ingest.parser import parse_document
from app.retrieval.embedder import embed_texts
from app.retrieval.query import build_query_text
from app.retrieval.store import CaseVectorIndex, embed_chunks
from eval.harness.embedding_cache import CACHE_PATH, save_cache

DATASET_DIR = Path(__file__).resolve().parent.parent / "dataset"
FIXTURES_DIR = DATASET_DIR / "fixtures" / "phase1_retrieval"
PAIRS_PATH = DATASET_DIR / "phase1_retrieval_pairs.json"
RESULT_PATH = Path(__file__).resolve().parent / "results" / "phase1_recall_result.json"

K = 5  # DECISIONS.md V19, fixed before this measurement was run

REQUIRED_PAIR_KEYS = {"pair_id", "field_label", "expected_document_id", "expected_page_number", "text_source"}


def load_pairs(path: Path = PAIRS_PATH) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    pairs: list[dict[str, Any]] = payload["pairs"]
    for pair in pairs:
        missing = REQUIRED_PAIR_KEYS - pair.keys()
        if missing:
            raise ValueError(f"Malformed ground truth pair {pair.get('pair_id', '<unknown>')!r}: missing {missing}")
    return pairs


def summarize_hits(hits_with_source: list[tuple[bool, str]]) -> dict[str, Any]:
    """Pure recall@k arithmetic -- no I/O, no embedding, no parsing. hits_with_source is a
    list of (hit, text_source) pairs; returns overall recall plus recall per source."""
    total = len(hits_with_source)
    hits_overall = sum(1 for hit, _source in hits_with_source if hit)

    count_by_source: dict[str, int] = {}
    hits_by_source: dict[str, int] = {}
    for hit, source in hits_with_source:
        count_by_source[source] = count_by_source.get(source, 0) + 1
        if hit:
            hits_by_source[source] = hits_by_source.get(source, 0) + 1

    recall_by_source = {
        source: (hits_by_source.get(source, 0) / count) for source, count in count_by_source.items()
    }

    return {
        "total": total,
        "hits_overall": hits_overall,
        "recall_overall": (hits_overall / total) if total else 0.0,
        "count_by_source": count_by_source,
        "recall_by_source": recall_by_source,
    }


def _ingest_all_documents(document_ids: set[str]) -> tuple[CaseVectorIndex, dict[str, list[float]]]:
    """Parses, chunks, and embeds every document referenced by the pairs into a single
    shared index -- mirroring a real case bundling multiple documents (ARCHITECTURE.md
    §6.2), which is a more realistic and harder test than one isolated index per
    document."""
    all_chunks: list[Chunk] = []
    for document_id in sorted(document_ids):
        path = FIXTURES_DIR / f"{document_id}.pdf"
        pages = parse_document(path, document_id=document_id)
        all_chunks.extend(chunk_pages(pages))

    embedded_chunks = embed_chunks(all_chunks)
    index = CaseVectorIndex()
    index.add(embedded_chunks)

    vectors_by_text = {ec.chunk.text: ec.vector for ec in embedded_chunks}
    return index, vectors_by_text


def run_measurement(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    """document -> parser -> chunker -> embed_chunks -> index -> build_query_text ->
    embed query -> retrieve -> recall@5. Calls embed_texts (via embed_chunks, and
    directly for each query) through their normal module imports."""
    document_ids = {pair["expected_document_id"] for pair in pairs}
    index, vectors_by_text = _ingest_all_documents(document_ids)

    per_pair_results = []
    hits_with_source: list[tuple[bool, str]] = []

    for pair in pairs:
        query_text = build_query_text(pair["field_label"])
        query_vector = embed_texts([query_text])[0]
        vectors_by_text[query_text] = query_vector

        results = index.query(query_vector=query_vector, top_k=K)
        retrieved = [
            {
                "document_id": embedded.chunk.document_id,
                "page_number": embedded.chunk.page_number,
                "chunk_index": embedded.chunk.chunk_index,
                "score": score,
            }
            for score, embedded in results
        ]
        hit = any(
            r["document_id"] == pair["expected_document_id"] and r["page_number"] == pair["expected_page_number"]
            for r in retrieved
        )
        hits_with_source.append((hit, pair["text_source"]))

        per_pair_results.append(
            {
                "pair_id": pair["pair_id"],
                "field_label": pair["field_label"],
                "query_text": query_text,
                "text_source": pair["text_source"],
                "hit": hit,
                "expected": {
                    "document_id": pair["expected_document_id"],
                    "page_number": pair["expected_page_number"],
                },
                "retrieved": retrieved,
            }
        )

    summary = summarize_hits(hits_with_source)
    recall_by_source = summary["recall_by_source"]
    count_by_source = summary["count_by_source"]

    return {
        "k": K,
        "total_pairs": summary["total"],
        "native_pair_count": count_by_source.get("native", 0),
        "ocr_pair_count": count_by_source.get("ocr", 0),
        "recall_at_5_overall": summary["recall_overall"],
        "recall_at_5_native": recall_by_source.get("native"),
        "recall_at_5_ocr": recall_by_source.get("ocr"),
        "per_pair_results": per_pair_results,
        "vectors_by_text": vectors_by_text,
    }


def main() -> None:
    pairs = load_pairs()
    result = run_measurement(pairs)
    vectors_by_text = result.pop("vectors_by_text")

    result_with_timestamp = {"measured_at": datetime.now(UTC).isoformat(), **result}
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(result_with_timestamp, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    save_cache(vectors_by_text)

    print(f"recall@{K} overall: {result['recall_at_5_overall']:.3f} ({result['total_pairs']} pairs)")
    print(f"recall@{K} native:  {result['recall_at_5_native']} ({result['native_pair_count']} pairs)")
    print(f"recall@{K} ocr:     {result['recall_at_5_ocr']} ({result['ocr_pair_count']} pairs)")
    print(f"Results written to {RESULT_PATH}")
    print(f"Embedding cache written to {CACHE_PATH}")


if __name__ == "__main__":
    main()
