"""Chunking (BUILD.md Phase 1, task 2). Splits parsed page text into fixed-size, overlapping
windows, carrying `document_id` and `page_number` on every chunk. This is not metadata
decoration: provenance display (G1) depends on it (ARCHITECTURE.md §6.2).

Pure and deterministic: `list[ParsedPage] -> list[Chunk]`. No embeddings, no vector store,
no retrieval, no tokenizer dependency, no LLM calls.
"""

from dataclasses import dataclass

from app.ingest.constants import CHUNK_OVERLAP_CHARS, CHUNK_SIZE_CHARS
from app.ingest.parser import ParsedPage


@dataclass(frozen=True)
class Chunk:
    document_id: str
    page_number: int
    chunk_index: int  # 0-indexed, scoped to the page
    text: str


def chunk_pages(pages: list[ParsedPage]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for page in pages:
        chunks.extend(_chunk_page(page))
    return chunks


def _chunk_page(page: ParsedPage) -> list[Chunk]:
    text = page.text
    if not text.strip():
        return []

    chunks: list[Chunk] = []
    text_length = len(text)
    start = 0
    chunk_index = 0
    while start < text_length:
        end = min(start + CHUNK_SIZE_CHARS, text_length)
        chunks.append(
            Chunk(
                document_id=page.document_id,
                page_number=page.page_number,
                chunk_index=chunk_index,
                text=text[start:end],
            )
        )
        chunk_index += 1
        if end == text_length:
            break
        start = end - CHUNK_OVERLAP_CHARS
    return chunks
