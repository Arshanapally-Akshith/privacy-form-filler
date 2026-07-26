import json
import logging

# Attribute names present on every LogRecord, used to isolate caller-supplied
# `extra=` fields so they can be surfaced as first-class JSON keys instead of
# being silently dropped.
_STANDARD_RECORD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
}


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_RECORD_ATTRS
        }
        payload.update(extra)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(log_level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(log_level)
