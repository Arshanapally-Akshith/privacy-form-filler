"""Pinned PDF layout constants (BUILD.md Phase 2, task 6).

Cosmetic only -- no accuracy or privacy consequence, unlike DECISIONS.md-pinned values
(e.g. E13 chunk size, E17 retrieval top_k) whose values a measurement later validates.
Nothing will ever measure whether 54pt is the "right" margin, so these are named constants
here (CLAUDE.md §6: no magic numbers) rather than DECISIONS.md entries.
"""

# A4, in points (1/72 inch).
PAGE_WIDTH_PT = 595.0
PAGE_HEIGHT_PT = 842.0
MARGIN_PT = 54.0

TITLE_FONT_SIZE = 14.0
LABEL_FONT_SIZE = 11.0
PROVENANCE_FONT_SIZE = 9.0

TITLE_LINE_HEIGHT_PT = 24.0
LABEL_LINE_HEIGHT_PT = 16.0
PROVENANCE_LINE_HEIGHT_PT = 13.0
FIELD_SPACING_PT = 8.0  # extra gap after each field's provenance line

PROVENANCE_INDENT_PT = 12.0
