"""Pinned retrieval constants. See DECISIONS.md §1 (E8) for provenance."""

# DECISIONS.md E8 — pinned embedding provider/model. See DECISIONS.md C5 for the
# trust-boundary scope of the outbound call this model name is used for, and the E8
# change-log entry for why this is Gemini rather than the originally-pinned OpenAI model.
EMBEDDING_MODEL = "gemini-embedding-001"
