"""Pinned Phase 1 recall@5 regression baseline. See DECISIONS.md §5 Phase 1 measurements
for the full measured run this was pinned from.

This is a Phase-1-scoped regression guard (BUILD.md Phase 1 testing requirements: "Recall@k
regression test pinned to the measured baseline"), distinct from the formal CI regression
threshold DECISIONS.md §5's "CI regression thresholds" table reserves for Phase 7.
"""

# Measured 2026-07-27 against gemini-embedding-001: 1.000 (20/20) on the committed Phase 1
# fixture set. Pinned exactly at the measured value -- this is a small, fixed, deterministic
# fixture set, not a statistical sample, so any drop below it means something that used to
# retrieve correctly no longer does and should be investigated, not a threshold to loosen
# (CLAUDE.md §4: do not lower a pinned regression threshold to make CI pass).
MIN_RECALL_AT_5 = 1.0
