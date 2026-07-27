"""One-time generator for the Phase 1 retrieval evaluation fixtures (BUILD.md Phase 1
tasks 7-8). The committed documents in fixtures/phase1_retrieval/ are the evaluation
fixtures -- this script is provenance for how they were produced, not something the
harness re-runs. Re-run it only if the fixture content itself is deliberately revised
(see PHASE1_RETRIEVAL_LABELING_METHODOLOGY.md).
"""

import io
from pathlib import Path

import fitz
from PIL import Image, ImageDraw, ImageFont

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "phase1_retrieval"

PAGE_WIDTH = 612
PAGE_HEIGHT = 792

ID_PROOF_TEXT = """GOVERNMENT OF INDIA
IDENTITY PROOF DOCUMENT

Full Name: Ananya Sharma
Date of Birth: 14-03-1990
PAN Number: BXTPS4021K
Aadhaar Number: 4521 7896 3312

This document certifies the identity of the above named individual for the
purpose of account opening and KYC verification."""

ADDRESS_PROOF_TEXT = """ELECTRICITY BILL - ADDRESS PROOF

Consumer Name: Ananya Sharma
Address: 42 MG Road, Andheri West
City: Mumbai
State: Maharashtra
PIN Code: 400058

Billing Period: January 2026"""

INSURANCE_TEXT = """INSURANCE POLICY APPLICATION FORM

Policy Holder Name: Ananya Sharma
Sum Insured: INR 5,00,000
Annual Premium: INR 12,500
Phone Number: 9876543210
Email Address: ananya.sharma@example.com

Nominee Name: Rohan Sharma
Relationship: Spouse"""

INCOME_PROOF_TEXT = """SALARY CERTIFICATE

Employee Name: Ananya Sharma
Employer: TechCorp Solutions Pvt Ltd
Designation: Senior Software Engineer
Monthly Gross Salary: INR 1,20,000
Bank Account Number: 002401123456789
Bank Name: HDFC Bank

This certifies that the above named individual is employed with our
organization and draws the stated monthly salary."""


def _write_native_pdf(path: Path, text: str) -> None:
    doc = fitz.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_textbox(fitz.Rect(50, 50, PAGE_WIDTH - 50, PAGE_HEIGHT - 50), text, fontsize=12)
    doc.save(path)
    doc.close()


def _write_scanned_pdf(path: Path, text: str) -> None:
    """Renders text onto an image, matched to the PDF page's aspect ratio so
    insert_image doesn't distort it, then embeds the image as the page's only
    content -- no text layer, forcing the OCR fallback path. Embedded as JPEG bytes
    (quality 90) rather than a raw filename: PyMuPDF re-encodes a PNG source
    uncompressed, which bloated this single page to several megabytes; JPEG bytes are
    embedded as-is."""
    scale = 2
    image = Image.new("RGB", (PAGE_WIDTH * scale, PAGE_HEIGHT * scale), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=34)
    draw.multiline_text((70, 70), text, fill="black", font=font, spacing=22)

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)

    doc = fitz.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_image(page.rect, stream=buffer.getvalue())
    doc.save(path)
    doc.close()


def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    _write_native_pdf(FIXTURES_DIR / "doc-kyc-id-proof.pdf", ID_PROOF_TEXT)
    _write_scanned_pdf(FIXTURES_DIR / "doc-kyc-address-proof.pdf", ADDRESS_PROOF_TEXT)
    _write_native_pdf(FIXTURES_DIR / "doc-insurance-application.pdf", INSURANCE_TEXT)
    _write_native_pdf(FIXTURES_DIR / "doc-income-proof.pdf", INCOME_PROOF_TEXT)


if __name__ == "__main__":
    main()
