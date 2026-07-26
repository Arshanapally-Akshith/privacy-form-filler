from app.ingest.chunker import chunk_pages
from app.ingest.constants import CHUNK_OVERLAP_CHARS, CHUNK_SIZE_CHARS
from app.ingest.parser import ParsedPage, TextSource


def _page(document_id: str, page_number: int, text: str, source: TextSource = TextSource.NATIVE) -> ParsedPage:
    return ParsedPage(document_id=document_id, page_number=page_number, text=text, source=source)


def test_provenance_is_preserved_on_every_chunk() -> None:
    page = _page("doc-1", page_number=3, text="short text well within one chunk")

    chunks = chunk_pages([page])

    assert len(chunks) == 1
    assert chunks[0].document_id == "doc-1"
    assert chunks[0].page_number == 3
    assert chunks[0].chunk_index == 0


def test_chunk_ordering_across_pages_and_documents() -> None:
    long_text = "x" * (CHUNK_SIZE_CHARS * 2)  # forces multiple chunks
    page_a = _page("doc-1", page_number=1, text=long_text)
    page_b = _page("doc-1", page_number=2, text="short second page")

    chunks = chunk_pages([page_a, page_b])

    # page_a produces several chunks with sequential, page-scoped indices...
    page_a_chunks = [c for c in chunks if c.page_number == 1]
    assert [c.chunk_index for c in page_a_chunks] == list(range(len(page_a_chunks)))
    assert len(page_a_chunks) > 1

    # ...and page_b's index resets to 0 rather than continuing page_a's count.
    page_b_chunks = [c for c in chunks if c.page_number == 2]
    assert [c.chunk_index for c in page_b_chunks] == [0]

    # Chunks appear in the same order as their source pages.
    assert chunks.index(page_a_chunks[0]) < chunks.index(page_b_chunks[0])


def test_overlap_matches_configured_window_exactly() -> None:
    text = "".join(str(i % 10) for i in range(CHUNK_SIZE_CHARS * 2 + 200))
    page = _page("doc-1", page_number=1, text=text)

    chunks = chunk_pages([page])

    assert len(chunks) == 3
    assert chunks[0].text == text[0:500]
    assert chunks[1].text == text[450:950]
    assert chunks[2].text == text[900:1200]

    # Adjacent chunks share exactly CHUNK_OVERLAP_CHARS of content.
    assert chunks[0].text[-CHUNK_OVERLAP_CHARS:] == chunks[1].text[:CHUNK_OVERLAP_CHARS]
    assert chunks[1].text[-CHUNK_OVERLAP_CHARS:] == chunks[2].text[:CHUNK_OVERLAP_CHARS]


def test_short_page_produces_a_single_chunk_with_no_overlap_logic() -> None:
    text = "a" * (CHUNK_SIZE_CHARS - 1)
    page = _page("doc-1", page_number=1, text=text)

    chunks = chunk_pages([page])

    assert len(chunks) == 1
    assert chunks[0].text == text


def test_empty_and_whitespace_only_pages_produce_zero_chunks() -> None:
    empty_page = _page("doc-1", page_number=1, text="")
    whitespace_page = _page("doc-1", page_number=2, text="   \n\t  ")

    assert chunk_pages([empty_page]) == []
    assert chunk_pages([whitespace_page]) == []
    assert chunk_pages([empty_page, whitespace_page]) == []
