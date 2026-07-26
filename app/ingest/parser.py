"""Parser abstraction (BUILD.md Phase 1, task 1). Normalizes every supported input format
to a list of `ParsedPage`, each carrying `(text, page_number, document_id)`. OCR is a
strategy selected per page inside this abstraction, not a separate component (ARCH §6.1,
D11) — nothing downstream needs to know whether a page's text came from the PDF's own text
layer or from OCR.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import fitz
import pytesseract
from PIL import Image

from app.ingest.constants import (
    MIN_IMAGE_DIMENSION_PX,
    OCR_RENDER_DPI,
    TEXT_LAYER_SUFFICIENCY_THRESHOLD_CHARS,
)

logger = logging.getLogger(__name__)

_PDF_SUFFIX = ".pdf"
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}  # DECISIONS.md R9a


class TextSource(str, Enum):
    NATIVE = "native"
    OCR = "ocr"


@dataclass(frozen=True)
class ParsedPage:
    document_id: str
    page_number: int  # 1-indexed
    text: str
    source: TextSource


class UnsupportedDocumentTypeError(ValueError):
    """Raised for a file extension outside the accepted input formats (DECISIONS.md R9a)."""


class ImageResolutionError(ValueError):
    """Raised when a standalone image falls below the minimum accepted resolution (E12)."""


def parse_document(path: Path, document_id: str) -> list[ParsedPage]:
    suffix = path.suffix.lower()
    if suffix == _PDF_SUFFIX:
        return _parse_pdf(path, document_id)
    if suffix in _IMAGE_SUFFIXES:
        return [_parse_image(path, document_id)]
    raise UnsupportedDocumentTypeError(f"Unsupported document type: {suffix!r}")


def _parse_pdf(path: Path, document_id: str) -> list[ParsedPage]:
    pages: list[ParsedPage] = []
    with fitz.open(path) as pdf:
        for index, page in enumerate(pdf):
            page_number = index + 1
            native_text = page.get_text().strip()
            if len(native_text) >= TEXT_LAYER_SUFFICIENCY_THRESHOLD_CHARS:
                _log_page_source(document_id, page_number, TextSource.NATIVE)
                pages.append(ParsedPage(document_id, page_number, native_text, TextSource.NATIVE))
                continue

            pixmap = page.get_pixmap(dpi=OCR_RENDER_DPI)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            ocr_text = pytesseract.image_to_string(image).strip()
            _log_page_source(document_id, page_number, TextSource.OCR)
            pages.append(ParsedPage(document_id, page_number, ocr_text, TextSource.OCR))
    return pages


def _parse_image(path: Path, document_id: str) -> ParsedPage:
    page_number = 1
    with Image.open(path) as image:
        width, height = image.size
        if width < MIN_IMAGE_DIMENSION_PX or height < MIN_IMAGE_DIMENSION_PX:
            raise ImageResolutionError(
                f"Image resolution {width}x{height} is below the minimum accepted "
                f"{MIN_IMAGE_DIMENSION_PX}x{MIN_IMAGE_DIMENSION_PX} (DECISIONS.md E12)."
            )
        text = pytesseract.image_to_string(image).strip()
    _log_page_source(document_id, page_number, TextSource.OCR)
    return ParsedPage(document_id, page_number, text, TextSource.OCR)


def _log_page_source(document_id: str, page_number: int, source: TextSource) -> None:
    logger.info(
        "page_text_source",
        extra={"document_id": document_id, "page_number": page_number, "source": source.value},
    )
