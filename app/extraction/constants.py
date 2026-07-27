"""Pinned extraction constants. See DECISIONS.md §1 (E9) for provenance."""

# DECISIONS.md E9 -- pinned generative LLM provider/model. Verified live against the
# project's Gemini account before pinning (see the E9 change-log entry): gemini-2.5-flash
# returns 404 "no longer available to new users" on this account, and gemini-2.0-flash-001
# returned a transient 503 -- gemini-3.5-flash is the confirmed-working, structured-output
# capable choice.
GENERATION_MODEL = "gemini-3.5-flash"

# Retrieval fan-in for a single field's extraction prompt. Not a DECISIONS.md-pinned value
# (unlike the constants above) -- an evaluation-driven top_k tuning pass has no BUILD.md
# task attached to it yet, so this is a reasonable starting point, matching E13/E14/E16's
# treatment as unvalidated starting values.
DEFAULT_RETRIEVAL_TOP_K = 5
