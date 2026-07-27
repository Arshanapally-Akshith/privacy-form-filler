"""Offline recall@5 regression test (BUILD.md Phase 1 tasks 7-8): recall@5 on the
committed Phase 1 fixtures must not fall below the pinned baseline
(eval/harness/constants.py MIN_RECALL_AT_5, DECISIONS.md §5). Replays embeddings from the
committed cache (eval/harness/fixtures/phase1_embedding_cache.json) via the same
monkeypatch-at-the-import-site pattern used throughout this project's test suite, so no
live embedding-provider call happens here (CLAUDE.md §4).

Still parses the OCR fixture document, so this needs the tesseract binary -- same
requires_tesseract skip/marker convention as tests/ingest/test_parser.py: skipped locally
if tesseract isn't installed, run for real in CI and Docker.
"""

import pytest

from eval.harness.constants import MIN_RECALL_AT_5
from eval.harness.embedding_cache import cached_embed_texts
from eval.harness.measure_recall import load_pairs, run_measurement


@pytest.mark.requires_tesseract
def test_recall_at_5_does_not_regress_below_the_measured_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.retrieval.store.embed_texts", cached_embed_texts)
    monkeypatch.setattr("eval.harness.measure_recall.embed_texts", cached_embed_texts)

    pairs = load_pairs()
    result = run_measurement(pairs)

    assert result["recall_at_5_overall"] >= MIN_RECALL_AT_5, (
        f"recall@5 regressed to {result['recall_at_5_overall']} "
        f"(pinned baseline: {MIN_RECALL_AT_5}, DECISIONS.md §5)"
    )
