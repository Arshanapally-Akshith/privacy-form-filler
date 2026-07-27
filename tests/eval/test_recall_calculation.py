"""Recall@k arithmetic tests (BUILD.md Phase 1 tasks 7-8). Pure function, no I/O, no
embedding calls -- tested against a small hand-computed fixture per CLAUDE.md's test-first
guidance for a fully-known contract. Also covers ground-truth loading: malformed entries
must fail loudly, not silently.
"""

import json
from pathlib import Path

import pytest

from eval.harness.measure_recall import load_pairs, summarize_hits


def test_recall_arithmetic_against_a_hand_computed_fixture() -> None:
    # 5 pairs: 3 native (2 hits, 1 miss), 2 ocr (1 hit, 1 miss).
    hits_with_source = [
        (True, "native"),
        (True, "native"),
        (False, "native"),
        (True, "ocr"),
        (False, "ocr"),
    ]

    summary = summarize_hits(hits_with_source)

    assert summary["total"] == 5
    assert summary["hits_overall"] == 3
    assert summary["recall_overall"] == pytest.approx(3 / 5)
    assert summary["recall_by_source"]["native"] == pytest.approx(2 / 3)
    assert summary["recall_by_source"]["ocr"] == pytest.approx(1 / 2)


def test_recall_is_zero_for_all_misses() -> None:
    summary = summarize_hits([(False, "native"), (False, "native")])
    assert summary["recall_overall"] == 0.0


def test_recall_is_one_for_all_hits() -> None:
    summary = summarize_hits([(True, "native"), (True, "ocr")])
    assert summary["recall_overall"] == 1.0


def test_empty_input_does_not_divide_by_zero() -> None:
    summary = summarize_hits([])
    assert summary["total"] == 0
    assert summary["recall_overall"] == 0.0


def test_committed_ground_truth_loads_and_has_the_expected_shape() -> None:
    pairs = load_pairs()

    assert len(pairs) == 20
    for pair in pairs:
        assert pair["text_source"] in {"native", "ocr"}
        assert pair["field_label"]
        assert pair["expected_document_id"]
        assert isinstance(pair["expected_page_number"], int)


def test_malformed_pair_missing_required_key_fails_loudly(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.json"
    malformed.write_text(
        json.dumps({"pairs": [{"pair_id": "bad-1", "field_label": "Name"}]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Malformed ground truth pair"):
        load_pairs(malformed)
