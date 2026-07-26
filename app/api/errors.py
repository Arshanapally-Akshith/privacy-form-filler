from typing import Any

STANDARD_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"description": "Bad Request"},
    404: {"description": "Not Found"},
    422: {"description": "Validation Error"},
    500: {"description": "Internal Server Error"},
    501: {"description": "Not Implemented"},
    502: {"description": "Bad Gateway"},
}
