import shutil

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if shutil.which("tesseract"):
        return
    skip_ocr = pytest.mark.skip(
        reason="tesseract binary not installed locally; CI and Docker install it (DECISIONS.md E10)"
    )
    for item in items:
        if "requires_tesseract" in item.keywords:
            item.add_marker(skip_ocr)
