"""Embedding provider wrapper (BUILD.md Phase 1, task 3).

Provider-neutral by design: callers depend only on embed_texts(texts) -> vectors, and on
EmbeddingProviderError for failure handling. The OpenAI SDK -- including its exception
hierarchy -- is an implementation detail contained entirely in this module; nothing
upstream (app.retrieval.store, app.api.debug, or anything built on either) knows or
should know which provider is behind this call.

DECISIONS.md C5: this call sends raw, un-pseudonymized text to a third-party hosted
provider. This is a deliberate, documented trust-boundary scope decision, not an
oversight -- see DECISIONS.md C5 for the full reasoning. The Privacy Policy Engine does
not gate this call.
"""

from openai import OpenAI, OpenAIError

from app.retrieval.constants import EMBEDDING_MODEL


class EmbeddingProviderError(Exception):
    """Raised when the embedding provider call fails (network, auth, rate limit, etc).
    Wraps the provider SDK's own exception so callers never need to know which SDK is in
    use -- see the module docstring."""


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    client = OpenAI()
    try:
        response = client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    except OpenAIError as exc:
        raise EmbeddingProviderError(str(exc)) from exc
    return [item.embedding for item in response.data]
