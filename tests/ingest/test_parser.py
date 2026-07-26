"""Golden-file parser tests (BUILD.md Phase 1). Covers all three input forms — text PDF,
scanned PDF, JPG/PNG — plus per-page OCR routing and resolution validation.

Tests that only assert routing behavior (which path was taken, not what OCR produced) run
without a real OCR engine by monkeypatching pytesseract, so they hold regardless of
environment. Tests that assert actual OCR *content* require tesseract to be installed and
are marked accordingly.
"""

from pathlib import Path

import pytest

from app.ingest.parser import (
    ImageResolutionError,
    TextSource,
    UnsupportedDocumentTypeError,
    parse_document,
)
from tests.ingest.conftest import NATIVE_TEXT, SCANNED_TEXT


def test_native_text_pdf_extracts_text_with_correct_attribution(native_text_pdf: Path) -> None:
    pages = parse_document(native_text_pdf, document_id="doc-1")

    assert len(pages) == 1
    assert pages[0].document_id == "doc-1"
    assert pages[0].page_number == 1
    assert pages[0].source == TextSource.NATIVE
    assert NATIVE_TEXT in pages[0].text


def test_native_text_pdf_does_not_invoke_ocr(
    native_text_pdf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail_if_called(*args: object, **kwargs: object) -> str:
        raise AssertionError("OCR must not be invoked when the native text layer suffices")

    monkeypatch.setattr("app.ingest.parser.pytesseract.image_to_string", _fail_if_called)

    parse_document(native_text_pdf, document_id="doc-1")


def test_mixed_pdf_invokes_ocr_only_on_the_page_that_needs_it(
    mixed_pdf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ocr_calls = 0

    def _count_calls(*args: object, **kwargs: object) -> str:
        nonlocal ocr_calls
        ocr_calls += 1
        return "stubbed ocr text"

    monkeypatch.setattr("app.ingest.parser.pytesseract.image_to_string", _count_calls)

    pages = parse_document(mixed_pdf, document_id="doc-1")

    assert len(pages) == 2
    assert pages[0].source == TextSource.NATIVE
    assert pages[1].source == TextSource.OCR
    assert ocr_calls == 1


def test_unsupported_extension_raises(tmp_path: Path) -> None:
    bogus = tmp_path / "document.docx"
    bogus.write_text("not a supported format")

    with pytest.raises(UnsupportedDocumentTypeError):
        parse_document(bogus, document_id="doc-1")


def test_sub_minimum_resolution_image_rejected(tiny_image: Path) -> None:
    with pytest.raises(ImageResolutionError):
        parse_document(tiny_image, document_id="doc-1")


@pytest.mark.requires_tesseract
def test_scanned_pdf_ocr_produces_correct_attribution(scanned_pdf: Path) -> None:
    pages = parse_document(scanned_pdf, document_id="doc-1")

    assert len(pages) == 1
    assert pages[0].document_id == "doc-1"
    assert pages[0].page_number == 1
    assert pages[0].source == TextSource.OCR
    assert SCANNED_TEXT in pages[0].text.upper().replace(" ", "")


@pytest.mark.requires_tesseract
def test_standalone_image_ocr_produces_correct_attribution(standalone_image: Path) -> None:
    pages = parse_document(standalone_image, document_id="doc-2")

    assert len(pages) == 1
    assert pages[0].document_id == "doc-2"
    assert pages[0].page_number == 1
    assert pages[0].source == TextSource.OCR
    assert SCANNED_TEXT in pages[0].text.upper().replace(" ", "")
