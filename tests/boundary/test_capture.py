"""Tests for app.boundary.capture and generate_structured_protected's `capture` parameter
(BUILD.md Phase 4, commit 7).
"""

import pytest
from pydantic import BaseModel

from app.boundary.capture import CapturedPayload
from app.boundary.llm import generate_structured_protected
from app.boundary.mode import PrivacyMode


class _Response(BaseModel):
    value: str | None


def test_capture_defaults_to_none_and_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.boundary.llm.generate_structured", lambda p, s: _Response(value="ok"))

    result = generate_structured_protected("hello", _Response, session_id="s1", privacy_mode=PrivacyMode.NONE)

    assert result.value == "ok"


def test_capture_records_the_raw_prompt_under_none_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.boundary.llm.generate_structured", lambda p, s: _Response(value="ok"))
    captured: list[CapturedPayload] = []

    generate_structured_protected(
        "hello world", _Response, session_id="s1", privacy_mode=PrivacyMode.NONE, capture=captured.append
    )

    assert captured == [CapturedPayload(session_id="s1", privacy_mode=PrivacyMode.NONE, prompt="hello world")]


def test_capture_records_protected_text_not_the_raw_prompt_under_full_tokenize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.boundary.llm.generate_structured", lambda p, s: _Response(value=p))
    captured: list[CapturedPayload] = []

    generate_structured_protected(
        "PAN: ABCDE1234F",
        _Response,
        session_id="s1",
        privacy_mode=PrivacyMode.FULL_TOKENIZE,
        capture=captured.append,
    )

    assert len(captured) == 1
    assert "ABCDE1234F" not in captured[0].prompt
    assert captured[0].privacy_mode is PrivacyMode.FULL_TOKENIZE


def test_capture_fires_exactly_once_per_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.boundary.llm.generate_structured", lambda p, s: _Response(value="ok"))
    captured: list[CapturedPayload] = []

    generate_structured_protected(
        "hello", _Response, session_id="s1", privacy_mode=PrivacyMode.NONE, capture=captured.append
    )

    assert len(captured) == 1


def test_capture_is_request_scoped_not_shared_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two independent capture lists, two independent calls -- proves capture is supplied
    per call, not accumulated anywhere inside the boundary itself (app.boundary.capture's
    own reason for existing: no process-global capture store)."""
    monkeypatch.setattr("app.boundary.llm.generate_structured", lambda p, s: _Response(value="ok"))
    captured_a: list[CapturedPayload] = []
    captured_b: list[CapturedPayload] = []

    generate_structured_protected(
        "first", _Response, session_id="s1", privacy_mode=PrivacyMode.NONE, capture=captured_a.append
    )
    generate_structured_protected(
        "second", _Response, session_id="s2", privacy_mode=PrivacyMode.NONE, capture=captured_b.append
    )

    assert [c.prompt for c in captured_a] == ["first"]
    assert [c.prompt for c in captured_b] == ["second"]
