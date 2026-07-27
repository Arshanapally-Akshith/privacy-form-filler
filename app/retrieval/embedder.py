"""Embedding provider wrapper (BUILD.md Phase 1, task 3).

Provider-neutral by design: callers depend only on embed_texts(texts) -> vectors, and on
EmbeddingProviderError for failure handling. The Gemini SDK -- including its exception
hierarchy -- is an implementation detail contained entirely in this module; nothing
upstream (app.retrieval.store, app.api.debug, or anything built on either) knows or
should know which provider is behind this call.

DECISIONS.md C5: this call sends raw, un-pseudonymized text to a third-party hosted
provider. This is a deliberate, documented trust-boundary scope decision, not an
oversight -- see DECISIONS.md C5 for the full reasoning. The Privacy Policy Engine does
not gate this call.

DECISIONS.md E8: originally OpenAI (text-embedding-3-small); migrated to Gemini
(gemini-embedding-001) after the OpenAI account had no usable quota. See the E8
change-log entry for the full reasoning, including why the prior OpenAI implementation
was removed rather than kept as a dormant alternative.
"""

from google import genai
from google.genai.errors import APIError

from app.retrieval.constants import EMBEDDING_MODEL


class EmbeddingProviderError(Exception):
    """Raised when the embedding provider call fails (network, auth, rate limit, etc).
    Wraps the provider SDK's own exception so callers never need to know which SDK is in
    use -- see the module docstring."""


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    client = genai.Client()
    try:
        # The SDK's overloaded `contents` type is broader than list[str]; mypy's
        # invariant list check flags it even though a plain string list is accepted at
        # runtime (verified against the live API).
        response = client.models.embed_content(model=EMBEDDING_MODEL, contents=texts)  # type: ignore[arg-type]
    except APIError as exc:
        raise EmbeddingProviderError(str(exc)) from exc

    if response.embeddings is None:
        raise EmbeddingProviderError("Embedding provider returned no embeddings")

    vectors: list[list[float]] = []
    for embedding in response.embeddings:
        if embedding.values is None:
            raise EmbeddingProviderError("Embedding provider returned an embedding with no values")
        vectors.append(embedding.values)
    return vectors
