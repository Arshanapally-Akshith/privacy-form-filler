"""OCR image-rendering primitives for the Phase 6 evaluation dataset (BUILD.md Phase 6
task 3a, DECISIONS.md V17). Turns already-generated document text (Commit 2/3) into a
standalone image, in the exact form app.ingest.parser's OCR path accepts directly --
JPG/PNG, no native text layer (DECISIONS.md R9a) -- so the pipeline's existing OCR fallback
(E10) is genuinely exercised by the Phase 6 dataset, not assumed to work.

Both variants render at (IMAGE_WIDTH, IMAGE_HEIGHT), comfortably above E12's pinned minimum
accepted resolution on both dimensions (checked directly against
app.ingest.constants.MIN_IMAGE_DIMENSION_PX in this module's tests, not just asserted here).

render_clean_scan_image has no random component: crisp black-on-white text, the "clean
scan" condition V17 asks for.

render_photo_like_image degrades that same base render along three axes -- a small
rotation, a brightness/contrast shift, and JPEG re-compression at a moderate-but-not-poor
quality -- each parameter drawn from a caller-supplied random.Random, so the degradation is
reproducible for a fixed seed rather than actually random from run to run. Degradation is
deliberately mild and bounded (ROTATION_DEGREES_RANGE, BRIGHTNESS_FACTOR_RANGE,
CONTRAST_FACTOR_RANGE, JPEG_QUALITY_RANGE below): ARCHITECTURE.md N3 explicitly excludes
poor-quality scans and handwriting from this project's scope, so "photo-like" here means
what a reasonably careful phone photo of a printed page looks like, not a stress test.

JPEG re-compression is used as the degradation/noise proxy rather than hand-rolled
per-pixel noise: PIL's own Image.effect_noise() is not seeded by Python's random module --
it reads the C library's own RNG state -- so it cannot be made reproducible from a
random.Random seed, and using it here would silently violate this module's own determinism
requirement. JPEG quantization, by contrast, is a deterministic function of its quality
parameter, and produces a comparably realistic degradation (blur, blocking) with no new
dependency.

Lives entirely in eval/ -- pure image generation for evaluation fixtures. No file under
app/ is imported for anything other than read-only reference in this module's tests, and
none is modified.
"""

import io
import random
import textwrap

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

IMAGE_WIDTH = 1200
IMAGE_HEIGHT = 1600
_MARGIN_PX = 60
_FONT_SIZE_PX = 28
_LINE_SPACING_PX = 14
# Conservative for this font/size/margin combination (roughly 77 characters would fit at
# IMAGE_WIDTH - 2*_MARGIN_PX) -- draw.multiline_text does not wrap long lines on its own,
# it only respects explicit "\n", so an unwrapped long line (e.g. a template's closing
# boilerplate sentence) would silently run off the right edge of the canvas.
_WRAP_WIDTH_CHARS = 65

# Deliberately mild -- see module docstring's ARCHITECTURE.md N3 note. A future change that
# widens any of these should be a conscious decision, not a silent drift, so this module's
# own tests assert against these constants directly rather than hardcoding a second copy of
# "mild" elsewhere.
ROTATION_DEGREES_RANGE = (-3.0, 3.0)
BRIGHTNESS_FACTOR_RANGE = (0.85, 1.15)
CONTRAST_FACTOR_RANGE = (0.85, 1.15)
JPEG_QUALITY_RANGE = (55, 80)


def _wrap_text(text: str) -> str:
    wrapped_lines: list[str] = []
    for line in text.split("\n"):
        if not line:
            wrapped_lines.append("")
        else:
            wrapped_lines.extend(textwrap.wrap(line, width=_WRAP_WIDTH_CHARS))
    return "\n".join(wrapped_lines)


def _base_render(text: str) -> Image.Image:
    image = Image.new("RGB", (IMAGE_WIDTH, IMAGE_HEIGHT), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=_FONT_SIZE_PX)
    draw.multiline_text((_MARGIN_PX, _MARGIN_PX), _wrap_text(text), fill="black", font=font, spacing=_LINE_SPACING_PX)
    return image


def render_clean_scan_image(text: str) -> Image.Image:
    return _base_render(text)


def render_photo_like_image(text: str, rng: random.Random) -> Image.Image:
    image = _base_render(text)

    angle = rng.uniform(*ROTATION_DEGREES_RANGE)
    image = image.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor="white")

    brightness = rng.uniform(*BRIGHTNESS_FACTOR_RANGE)
    image = ImageEnhance.Brightness(image).enhance(brightness)
    contrast = rng.uniform(*CONTRAST_FACTOR_RANGE)
    image = ImageEnhance.Contrast(image).enhance(contrast)

    quality = rng.randint(*JPEG_QUALITY_RANGE)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    buffer.seek(0)
    # .convert() forces a fresh, buffer-independent image (not a lazy view), since `buffer`
    # goes out of scope on return.
    return Image.open(buffer).convert("RGB")
