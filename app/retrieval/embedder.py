"""Embedding provider wrapper (BUILD.md Phase 1, task 3).

Provider-neutral by design: callers depend only on embed_texts(texts) -> vectors. The
OpenAI SDK is an implementation detail contained entirely in this module -- nothing
upstream (app.retrieval.store or anything built on it) knows or should know which
provider is behind this call.

DECISIONS.md C5: this call sends raw, un-pseudonymized text to a third-party hosted
provider. This is a deliberate, documented trust-boundary scope decision, not an
oversight -- see DECISIONS.md C5 for the full reasoning. The Privacy Policy Engine does
not gate this call.
"""

from openai import OpenAI

from app.retrieval.constants import EMBEDDING_MODEL


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    client = OpenAI()
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [item.embedding for item in response.data]
