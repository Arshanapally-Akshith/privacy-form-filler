"""Privacy engine CLI tests (BUILD.md Phase 3, task 9).

Drives app.privacy.cli.main() directly rather than via subprocess -- fast, deterministic,
and lets pytest's capsys capture stdout without shelling out. Everything here exercises the
existing, unmodified detection/dispatch/tokenize/policy_config modules; no new behavior is
introduced anywhere except this module's own orchestration.
"""

import json
from datetime import date

import pytest

from app.privacy.cli import EntityResult, _reconstruct_protected_text, main
from app.privacy.detection import DetectedEntity, EntityType
from app.privacy.dispatch import PolicyAction
from app.privacy.generalize import generalize_dob

# --- protect: default fail-closed behavior -----------------------------------------------


def test_protect_default_tokenizes_a_supported_identifier(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["protect", "PAN: ABCDE1234F"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "ABCDE1234F" not in out.split("Map:")[0]  # not present in the protected text section
    assert "action=tokenize" in out
    assert "reverses ok: True" in out


def test_protect_no_entities_detected(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["protect", "Nothing sensitive here."])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "no entities detected" in out


# --- protect: --action overrides ----------------------------------------------------------


def test_protect_generalize_override(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(
        ["protect", "DOB: 15/06/1990", "--action", "date=generalize", "--reference-date", "2026-07-28"]
    )
    out = capsys.readouterr().out
    expected = generalize_dob("15/06/1990", date(2026, 7, 28))

    assert exit_code == 0
    assert expected in out


def test_protect_derive_override(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["protect", "PIN: 110001", "--action", "pin_code=derive:state"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "'DELHI'" in out


def test_protect_pass_through_override(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["protect", "Phone: 9876543210", "--action", "phone=pass_through"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "9876543210" in out.split("Map:")[0]


def test_protect_unsupported_entity_type_reports_error_not_crash(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["protect", "Applicant Full Name Asha Rao Kumar"])
    out = capsys.readouterr().out

    assert exit_code == 1
    assert "ERROR" in out


def test_protect_invalid_derive_attribute_reports_error(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["protect", "PIN: 110001", "--action", "pin_code=derive:city"])
    out = capsys.readouterr().out

    assert exit_code == 1
    assert "Invalid derive attribute" in out


# --- protect: input sources ----------------------------------------------------------------


def test_protect_reads_from_file(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    input_file = tmp_path / "sample.txt"
    input_file.write_text("Phone: 9876543210", encoding="utf-8")

    exit_code = main(["protect", "--file", str(input_file)])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "action=tokenize" in out


def test_protect_reads_from_stdin(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("Phone: 9876543210"))
    exit_code = main(["protect"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "action=tokenize" in out


# --- protect: --json output -----------------------------------------------------------------


def test_protect_json_output_shape(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["protect", "PAN: ABCDE1234F", "--json"])
    out = capsys.readouterr().out
    payload = json.loads(out)

    assert exit_code == 0
    assert set(payload.keys()) == {"session_id", "input_text", "protected_text", "entities"}
    assert len(payload["entities"]) == 1
    entity = payload["entities"][0]
    assert entity["entity_type"] == "pan"
    assert entity["action"] == "tokenize"
    assert entity["error"] is None
    assert entity["reversed_ok"] is True


# --- protect: usage errors -------------------------------------------------------------------


def test_protect_malformed_action_flag_exits_with_usage_error() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["protect", "text", "--action", "not-valid"])
    assert exc_info.value.code == 2


def test_protect_unknown_entity_type_in_action_flag_exits_with_usage_error() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["protect", "text", "--action", "bogus_type=tokenize"])
    assert exc_info.value.code == 2


def test_protect_bad_reference_date_exits_with_usage_error() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["protect", "text", "--reference-date", "not-a-date"])
    assert exc_info.value.code == 2


# --- span reconstruction (overlap handling) ---------------------------------------------------


def test_reconstruct_protected_text_replaces_non_overlapping_entities_in_place() -> None:
    text = "A B C"
    entities = [
        DetectedEntity(EntityType.PAN, "A", 0, 1),
        DetectedEntity(EntityType.PHONE, "C", 4, 5),
    ]
    results = [
        EntityResult(EntityType.PAN, "A", PolicyAction.PASS_THROUGH, result="X"),
        EntityResult(EntityType.PHONE, "C", PolicyAction.PASS_THROUGH, result="Y"),
    ]
    protected, used = _reconstruct_protected_text(text, entities, results)
    assert protected == "X B Y"
    assert used == [True, True]


def test_reconstruct_protected_text_skips_entities_nested_in_a_larger_substituted_span() -> None:
    text = "123 MG Road 110001 end"
    # ADDRESS (the larger, outer span) and a PIN_CODE nested inside it -- mirrors
    # detection.py's one documented overlap exception.
    address = DetectedEntity(EntityType.ADDRESS, text[0:18], 0, 18)
    pin = DetectedEntity(EntityType.PIN_CODE, "110001", 12, 18)
    entities = [pin, address]
    results = [
        EntityResult(EntityType.PIN_CODE, "110001", PolicyAction.TOKENIZE, result="999999"),
        EntityResult(EntityType.ADDRESS, address.text, PolicyAction.TOKENIZE, result="<ADDRESS>"),
    ]
    protected, used = _reconstruct_protected_text(text, entities, results)

    assert protected == "<ADDRESS> end"
    assert used == [False, True]  # the nested PIN_CODE was not separately substituted


# --- demo -------------------------------------------------------------------------------------


def test_demo_touches_all_four_actions(capsys: pytest.CaptureFixture[str]) -> None:
    main(["demo"])
    out = capsys.readouterr().out

    assert "action=tokenize" in out
    assert "action=generalize" in out
    assert "action=derive" in out
    assert "action=pass_through" in out


def test_demo_shows_named_configs_section(capsys: pytest.CaptureFixture[str]) -> None:
    main(["demo"])
    out = capsys.readouterr().out

    assert "strict:" in out
    assert "age_state:" in out
    assert "ageband_city:" in out


def test_demo_honestly_shows_the_strict_dob_gap(capsys: pytest.CaptureFixture[str]) -> None:
    """`strict` omits date_of_birth (Commit 6); it falls to the fail-closed default
    (tokenize), which cannot execute for DATE. The demo should show this live, not hide it."""
    main(["demo"])
    out = capsys.readouterr().out
    strict_section = out.split("strict:")[1].split("age_state:")[0]

    assert "date_of_birth: action=tokenize -> ERROR" in strict_section


def test_demo_age_state_and_ageband_city_derive_different_attributes(capsys: pytest.CaptureFixture[str]) -> None:
    main(["demo"])
    out = capsys.readouterr().out
    age_state_section = out.split("age_state:")[1].split("ageband_city:")[0]
    ageband_city_section = out.split("ageband_city:")[1]

    assert "'DELHI'" in age_state_section.split("pin_code")[1].split("\n")[0]
    assert "'NEW DELHI'" in ageband_city_section.split("pin_code")[1].split("\n")[0]


def test_demo_exits_nonzero_because_it_honestly_demonstrates_the_strict_dob_gap() -> None:
    """Not a bug: `demo` deliberately shows strict's date_of_birth failing (see
    test_demo_honestly_shows_the_strict_dob_gap), so its exit code reflects that, matching
    `protect`'s own "1 if any entity errored" rule."""
    assert main(["demo"]) == 1
