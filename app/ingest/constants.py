"""Pinned ingestion constants. See DECISIONS.md §1 (E10-E12) for provenance.

E11 and E12 are provisional: validated only against the golden-file fixtures in
tests/ingest/, not yet against the broader Phase 1 hand-labeled document set (BUILD.md
Phase 1 tasks 7-8). Do not treat them as final until DECISIONS.md marks them measured.
"""

# DECISIONS.md E10 — pinned. CPU-only per the free-tier deployment constraint (ARCH §9).
OCR_ENGINE = "tesseract"

# DPI used to rasterize a PDF page before handing it to the OCR engine.
OCR_RENDER_DPI = 300

# Native page text shorter than this is treated as an absent/insufficient text layer and
# triggers OCR fallback (DECISIONS.md E11 — provisional).
TEXT_LAYER_SUFFICIENCY_THRESHOLD_CHARS = 20

# Standalone images with either dimension below this are rejected outright rather than
# sent through OCR (DECISIONS.md E12 — provisional).
MIN_IMAGE_DIMENSION_PX = 600
