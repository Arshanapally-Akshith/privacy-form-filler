import json
import logging

from app.config.logging import JSONFormatter


def _format_record(**extra: object) -> dict[str, object]:
    logger = logging.getLogger("test_logging")
    logger.setLevel(logging.INFO)
    record = logger.makeRecord(
        logger.name, logging.INFO, __file__, 0, "an event", (), None, extra=extra
    )
    return json.loads(JSONFormatter().format(record))


def test_standard_fields_present() -> None:
    payload = _format_record()
    assert payload["message"] == "an event"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test_logging"
    assert "timestamp" in payload


def test_extra_fields_are_surfaced_as_json_keys() -> None:
    payload = _format_record(document_id="doc-1", page_number=2, source="ocr")
    assert payload["document_id"] == "doc-1"
    assert payload["page_number"] == 2
    assert payload["source"] == "ocr"
