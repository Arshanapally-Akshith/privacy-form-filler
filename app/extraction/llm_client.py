"""Generative LLM provider wrapper (BUILD.md Phase 2, task 3).

Provider-neutral by design, mirroring app.retrieval.embedder: callers depend only on
generate_structured(prompt, response_schema) -> an instance of response_schema, and on
LLMProviderError for failure handling. The Gemini SDK is an implementation detail contained
entirely in this module -- nothing in app.extraction.extractor knows or should know which
provider is behind this call.

This is a direct call, not routed through the Privacy Policy Engine -- per BUILD.md Phase 2
task 3, "direct LLM calls for now; the boundary layer arrives in Phase 4" (ARCHITECTURE.md
Invariant I3 only applies from Phase 4 onward).
"""

from typing import TypeVar

from google import genai
from google.genai import types
from google.genai.errors import APIError
from pydantic import BaseModel

from app.extraction.constants import GENERATION_MODEL

T = TypeVar("T", bound=BaseModel)


class LLMProviderError(Exception):
    """Raised when the generative LLM provider call fails (network, auth, rate limit, an
    empty/unparseable response, etc). Wraps the provider SDK's own exception so callers
    never need to know which SDK is in use -- see the module docstring."""


def generate_structured(prompt: str, response_schema: type[T]) -> T:
    client = genai.Client()
    try:
        response = client.models.generate_content(
            model=GENERATION_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=response_schema,
            ),
        )
    except APIError as exc:
        raise LLMProviderError(str(exc)) from exc

    if response.parsed is None:
        raise LLMProviderError("LLM provider returned no parsed structured output")
    if not isinstance(response.parsed, response_schema):
        raise LLMProviderError(
            f"LLM provider returned {type(response.parsed).__name__}, expected {response_schema.__name__}"
        )
    return response.parsed
