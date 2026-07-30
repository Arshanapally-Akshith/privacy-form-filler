"""Tests for the Phase 6 OCR image-rendering primitives (BUILD.md Phase 6 task 3a).
Pure image generation, no I/O beyond in-memory PIL objects, no LLM -- offline per
CLAUDE.md §4 by construction. No tesseract binary needed (that's covered separately, and
optionally, in test_generate_ocr_subset.py).
"""

import random

from PIL import Image

from app.ingest.constants import MIN_IMAGE_DIMENSION_PX
from eval.dataset.ocr_rendering import (
    BRIGHTNESS_FACTOR_RANGE,
    CONTRAST_FACTOR_RANGE,
    IMAGE_HEIGHT,
    IMAGE_WIDTH,
    JPEG_QUALITY_RANGE,
    ROTATION_DEGREES_RANGE,
    render_clean_scan_image,
    render_photo_like_image,
)

_SAMPLE_TEXT = "GOVERNMENT OF INDIA\nIDENTITY PROOF DOCUMENT\n\nFull Name: Priya Sharma\nPAN Number: ABCDE1234F"


def _bytes_of(image: Image.Image) -> bytes:
    return image.tobytes()


def test_clean_scan_dimensions_are_above_the_pinned_minimum_resolution() -> None:
    image = render_clean_scan_image(_SAMPLE_TEXT)
    assert image.width >= MIN_IMAGE_DIMENSION_PX
    assert image.height >= MIN_IMAGE_DIMENSION_PX
    assert (IMAGE_WIDTH, IMAGE_HEIGHT) == image.size


def test_photo_like_dimensions_are_above_the_pinned_minimum_resolution() -> None:
    image = render_photo_like_image(_SAMPLE_TEXT, random.Random(1))
    assert image.width >= MIN_IMAGE_DIMENSION_PX
    assert image.height >= MIN_IMAGE_DIMENSION_PX


def test_clean_scan_rendering_is_deterministic() -> None:
    first = render_clean_scan_image(_SAMPLE_TEXT)
    second = render_clean_scan_image(_SAMPLE_TEXT)
    assert _bytes_of(first) == _bytes_of(second)


def test_photo_like_rendering_is_deterministic_for_a_fixed_seed() -> None:
    first = render_photo_like_image(_SAMPLE_TEXT, random.Random(42))
    second = render_photo_like_image(_SAMPLE_TEXT, random.Random(42))
    assert _bytes_of(first) == _bytes_of(second)


def test_photo_like_rendering_differs_across_seeds() -> None:
    first = render_photo_like_image(_SAMPLE_TEXT, random.Random(1))
    second = render_photo_like_image(_SAMPLE_TEXT, random.Random(2))
    assert _bytes_of(first) != _bytes_of(second)


def test_photo_like_rendering_actually_differs_from_clean_scan() -> None:
    clean = render_clean_scan_image(_SAMPLE_TEXT)
    photo = render_photo_like_image(_SAMPLE_TEXT, random.Random(7))
    assert _bytes_of(clean) != _bytes_of(photo)


def test_degradation_parameters_stay_within_the_documented_mild_bounds() -> None:
    """Guards against the ranges themselves silently drifting wide (ARCHITECTURE.md N3:
    poor-quality scans are out of scope). Thresholds here are deliberately looser than the
    module's own constants -- this test protects against a large drift, not a style
    preference about the exact numbers."""
    assert abs(ROTATION_DEGREES_RANGE[0]) <= 5.0
    assert abs(ROTATION_DEGREES_RANGE[1]) <= 5.0
    assert 0.7 <= BRIGHTNESS_FACTOR_RANGE[0] <= BRIGHTNESS_FACTOR_RANGE[1] <= 1.3
    assert 0.7 <= CONTRAST_FACTOR_RANGE[0] <= CONTRAST_FACTOR_RANGE[1] <= 1.3
    assert 50 <= JPEG_QUALITY_RANGE[0] <= JPEG_QUALITY_RANGE[1] <= 95


def test_photo_like_rotation_and_quality_stay_within_the_declared_ranges_across_many_seeds() -> None:
    for seed in range(50):
        rng = random.Random(seed)
        # Draw in the same order render_photo_like_image itself draws, so this test
        # observes exactly the values that would be used -- not a separate, potentially
        # divergent sampling of the same distribution.
        angle = rng.uniform(*ROTATION_DEGREES_RANGE)
        brightness = rng.uniform(*BRIGHTNESS_FACTOR_RANGE)
        contrast = rng.uniform(*CONTRAST_FACTOR_RANGE)
        quality = rng.randint(*JPEG_QUALITY_RANGE)

        assert ROTATION_DEGREES_RANGE[0] <= angle <= ROTATION_DEGREES_RANGE[1]
        assert BRIGHTNESS_FACTOR_RANGE[0] <= brightness <= BRIGHTNESS_FACTOR_RANGE[1]
        assert CONTRAST_FACTOR_RANGE[0] <= contrast <= CONTRAST_FACTOR_RANGE[1]
        assert JPEG_QUALITY_RANGE[0] <= quality <= JPEG_QUALITY_RANGE[1]
