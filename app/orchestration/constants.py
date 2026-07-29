"""Pinned orchestration constants. See DECISIONS.md §1 (E6, E18) for provenance."""

# DECISIONS.md E6 -- pinned. Max retries per field: 1 (max 2 extraction attempts total:
# initial + 1 retry). Grounded in the Phase 4 dev-case measurement showing the pinned
# Gemini free-tier quota is fragile, plus BUILD.md's own Phase 5 risk table naming quota
# burn from retry loops directly -- see the E6 change-log entry.
RETRY_BUDGET = 1

# DECISIONS.md E18 -- pinned. Retrieval fan-in used on a field's re-retrieve attempt after
# verifier rejection -- double E17's baseline (app.extraction.constants
# .DEFAULT_RETRIEVAL_TOP_K). The concrete meaning this project gives ARCH §7's "re-retrieve
# with adjusted query": a wider evidence set on retry, not a rewritten query string. Used
# only on the retry path (app.orchestration.nodes.retry_current_field) -- the initial
# extraction attempt always uses DEFAULT_RETRIEVAL_TOP_K.
RETRY_RETRIEVAL_TOP_K = 10
