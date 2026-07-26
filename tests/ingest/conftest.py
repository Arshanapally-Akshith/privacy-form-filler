"""Synthetic golden-file document builders for parser tests.

No scanner or real-world corpus is available for a solo project at this stage, so these
fixtures build minimal representative documents (native-text PDF, image-only/"scanned" PDF,
a mixed PDF, and standalone images) deterministically at test time via PyMuPDF and Pillow.
This mirrors the semi-synthetic approach the project already takes for evaluation data
(ARCHITECTURE.md §8, D8), applied here at the unit-test scale.
"""

from pathlib import Path

import fitz
import pytest
from PIL import Image, ImageDraw, ImageFont

NATIVE_TEXT = "This page has native machine-readable text."
SCANNED_TEXT = "DOCUMENT"

PAGE_WIDTH = 612
PAGE_HEIGHT = 792


def _text_image(text: str, size: tuple[int, int] = (1200, 800)) -> Image.Image:
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=160)
    draw.text((60, size[1] // 2 - 80), text, fill="black", font=font)
    return image


def _new_pdf_with_native_text_page(text: str) -> fitz.Document:
    doc = fitz.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text((72, 72), text, fontsize=12)
    return doc


def _add_image_page(doc: fitz.Document, tmp_path: Path, image: Image.Image, name: str) -> None:
    image_path = tmp_path / name
    image.save(image_path)
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_image(page.rect, filename=str(image_path))


@pytest.fixture
def native_text_pdf(tmp_path: Path) -> Path:
    doc = _new_pdf_with_native_text_page(NATIVE_TEXT)
    out = tmp_path / "native.pdf"
    doc.save(out)
    doc.close()
    return out


@pytest.fixture
def scanned_pdf(tmp_path: Path) -> Path:
    doc = fitz.open()
    _add_image_page(doc, tmp_path, _text_image(SCANNED_TEXT), "scan.png")
    out = tmp_path / "scanned.pdf"
    doc.save(out)
    doc.close()
    return out


@pytest.fixture
def mixed_pdf(tmp_path: Path) -> Path:
    """Page 1: native text layer. Page 2: image-only, no text layer."""
    doc = _new_pdf_with_native_text_page(NATIVE_TEXT)
    _add_image_page(doc, tmp_path, _text_image(SCANNED_TEXT), "scan.png")
    out = tmp_path / "mixed.pdf"
    doc.save(out)
    doc.close()
    return out


@pytest.fixture
def standalone_image(tmp_path: Path) -> Path:
    out = tmp_path / "photo.png"
    _text_image(SCANNED_TEXT).save(out)
    return out


@pytest.fixture
def tiny_image(tmp_path: Path) -> Path:
    out = tmp_path / "tiny.png"
    Image.new("RGB", (100, 100), "white").save(out)
    return out
