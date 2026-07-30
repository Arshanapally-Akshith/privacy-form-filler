"""Evaluation response cache for the Phase 6 harness (BUILD.md Phase 6 risk table: "cache
LLM responses by input hash so re-runs are cheap"; DECISIONS.md R10). Anticipated
explicitly by eval/harness/embedding_cache.py's own docstring: "BUILD.md Phase 6's R10 ...
is where a broader mechanism belongs, if and when Phase 6 needs one -- this module is
intentionally narrower and does not generalize it." This is that broader mechanism.

**Read/write split, mirroring embedding_cache.py's own precedent.** get_cached_response()
never calls a live model -- a cache miss raises CacheMissError, full stop (CLAUDE.md §5: no
silent fallback to a live call). Whether to then make a real call and populate the cache
with put_cached_response() is entirely the *caller's* decision -- typically a live-running
harness script (Commit 9), never this module itself.

**Unlike embedding_cache.py, this cache accumulates rather than being regenerated in full.**
embedding_cache.py's own docstring deliberately regenerates its (small, fixed, 20-pair)
cache from scratch on every live run -- correct for that narrow case, but wrong here: R10's
whole point is that repeated full-matrix re-runs (dozens of cases x several fields x three
privacy modes) should get *cheaper* over time, which requires accumulating entries across
runs, not discarding yesterday's on every new one. put_cached_response() therefore loads the
existing file, merges in the one new entry, and persists immediately -- crash-safe (a long
harness run dying partway through, as already happened during this project's own Phase 4/5
live measurements, loses at most the one in-flight call, not the whole run's progress so
far), at the cost of one extra read+write per call, negligible next to LLM call latency.

**Cache key.** CacheKeyParams is a plain, fully JSON-native frozen dataclass covering every
parameter that can change an extraction's outbound payload or response: which case, which
field (by its full definition -- name, label, type, expected format -- not just its bare
name, in case two same-named fields across schemas ever meant different things), which
privacy mode, which reference date (age-sensitive under Generalize), and the retrieval
fan-in (top_k, since a wider retry fan-in can surface different evidence). Privacy mode is
deliberately its own explicit field, not folded into anything else: the exact failure mode
this project's own Phase 6 planning named was a cache key that ignored privacy_mode and so
would silently replay a `none`-mode response for a `policy_engine`-mode request.
key_params_from_field() builds one from the real objects a harness has in hand (a
FormFieldSpec, a PrivacyMode, a date) without this module needing to depend on their exact
representations for hashing -- only on their already-stable string/primitive projections.

**`path` is an explicit, overridable parameter on every function here**, unlike
embedding_cache.py's single hardcoded CACHE_PATH -- that module serves exactly one fixture
file for exactly one regression test; this one is meant to be exercised by many tests (and,
later, by a real harness run) against independent cache files, so a hardcoded path would
make the two impossible to keep isolated from each other.

Lives entirely in eval/ -- wraps the harness's own call into app/ (extract_field today; the
orchestration graph once Commit 9 exists); it does not add a second LLM call site inside
app/, so Invariant I3 is untouched. No file under app/ is imported for anything beyond
read-only type references (FormFieldSpec, PrivacyMode), and none is modified.
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from app.boundary.mode import PrivacyMode
from app.config.form_schema import FormFieldSpec

DEFAULT_CACHE_PATH = Path(__file__).resolve().parent / "fixtures" / "phase6_response_cache.json"


class CacheMissError(Exception):
    """Raised when no cached response exists for a given key. Never silently triggers a
    live call -- whether to make one and then cache it is entirely the caller's decision."""


class CorruptCacheEntryError(Exception):
    """Raised when a cache entry exists but is structurally incomplete (not a dict, or
    missing 'key_params'/'response'). A corrupted cache file is a loud, specific failure,
    not a silent miss and not an unrelated KeyError surfacing deep inside caller code
    (CLAUDE.md §5)."""


@dataclass(frozen=True)
class CacheKeyParams:
    case_id: str
    field_name: str
    field_label: str
    field_type: str
    expected_format: str | None
    privacy_mode: str
    reference_date: str
    top_k: int


def key_params_from_field(
    *,
    case_id: str,
    field: FormFieldSpec,
    privacy_mode: PrivacyMode,
    reference_date: date,
    top_k: int,
) -> CacheKeyParams:
    return CacheKeyParams(
        case_id=case_id,
        field_name=field.name,
        field_label=field.label,
        field_type=field.type.value,
        expected_format=field.expected_format,
        privacy_mode=privacy_mode.value,
        reference_date=reference_date.isoformat(),
        top_k=top_k,
    )


def compute_cache_key(params: CacheKeyParams) -> str:
    canonical = json.dumps(asdict(params), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_cache(path: Path = DEFAULT_CACHE_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("entries", {})


def _validate_entry(entry: Any, cache_key: str) -> dict[str, Any]:
    if not isinstance(entry, dict) or "key_params" not in entry or "response" not in entry:
        raise CorruptCacheEntryError(
            f"Cache entry {cache_key!r} is malformed -- expected a dict with 'key_params' "
            f"and 'response' keys; got: {entry!r}"
        )
    return entry


def get_cached_response(params: CacheKeyParams, *, path: Path = DEFAULT_CACHE_PATH) -> dict[str, Any]:
    entries = load_cache(path)
    cache_key = compute_cache_key(params)
    entry = entries.get(cache_key)
    if entry is None:
        raise CacheMissError(
            f"No cached response for {params!r} (key {cache_key}). Run the live harness to "
            "populate the cache -- this function never falls back to a live call."
        )
    validated = _validate_entry(entry, cache_key)
    return validated["response"]


def put_cached_response(params: CacheKeyParams, response: dict[str, Any], *, path: Path = DEFAULT_CACHE_PATH) -> None:
    entries = load_cache(path)
    cache_key = compute_cache_key(params)
    entries[cache_key] = {"key_params": asdict(params), "response": response}

    payload = {"generated_at": datetime.now(UTC).isoformat(), "entries": entries}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
