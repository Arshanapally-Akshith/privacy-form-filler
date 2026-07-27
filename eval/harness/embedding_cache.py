"""Disciplined embedding cache for the Phase 1 recall@5 evaluation only (BUILD.md Phase 1
tasks 7-8). Stores exactly the embeddings this evaluation touches, so the regression test
(tests/eval/test_recall_regression.py) can run fully offline (CLAUDE.md §4) without a live
call to the embedding provider.

This is not a general-purpose caching layer. BUILD.md Phase 6's R10 ("cache LLM responses
by input hash for evaluation runs") is where a broader mechanism belongs, if and when
Phase 6 needs one -- this module is intentionally narrower and does not generalize it.

Regenerated in full by eval/harness/measure_recall.py on every live run; never merged with
stale entries from a previous run.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.retrieval.constants import EMBEDDING_MODEL

CACHE_PATH = Path(__file__).resolve().parent / "fixtures" / "phase1_embedding_cache.json"


class CacheMissError(Exception):
    """Raised when a replayed text has no cached embedding. A miss means the cache needs
    regenerating by re-running the live measurement -- never silently falls back to a
    live API call (CLAUDE §5)."""


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_cache() -> dict[str, Any]:
    if not CACHE_PATH.exists():
        return {}
    payload: dict[str, Any] = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return payload.get("entries", {})


def save_cache(vectors_by_text: dict[str, list[float]]) -> None:
    """Overwrites the cache with exactly the given (text -> vector) entries -- Phase 1
    evaluation fixture embeddings only, tagged with the model name and a generation
    timestamp so the cache's provenance/version is explicit (not just its content hash)."""
    entries = {
        hash_text(text): {"text": text, "vector": vector} for text, vector in vectors_by_text.items()
    }
    payload = {
        "embedding_model": EMBEDDING_MODEL,
        "generated_at": datetime.now(UTC).isoformat(),
        "entries": entries,
    }
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def cached_embed_texts(texts: list[str]) -> list[list[float]]:
    """Offline stand-in for app.retrieval.embedder.embed_texts, used only by the Phase 1
    recall regression test."""
    entries = load_cache()
    vectors: list[list[float]] = []
    for text in texts:
        entry = entries.get(hash_text(text))
        if entry is None:
            raise CacheMissError(
                f"No cached embedding for text: {text!r}. Re-run "
                "`uv run python -m eval.harness.measure_recall` to regenerate the cache."
            )
        vectors.append(entry["vector"])
    return vectors
