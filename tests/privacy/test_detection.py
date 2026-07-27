"""PII detection tests (BUILD.md Phase 3, task 1).

Structured entity types (PAN, Aadhaar, phone, email, PIN code, passport, date) have a
fully known contract -- format specs, not exploration -- so these are written before the
corresponding detector, per CLAUDE.md §4. Name and address are heuristic-based with no
fixed format to test against in advance; those are written alongside/iteratively.

No app.privacy content exists yet, so this is also the first exercise of the module.
"""

import pytest

from app.privacy.detection import EntityType, detect_entities

# Synthetically generated (base digits "12345678901" + Verhoeff check digit "0"),
# verified via a standalone script before being used here -- not a real Aadhaar number.
_VALID_AADHAAR = "123456789010"
_INVALID_CHECKSUM_AADHAAR = "123456789020"  # last-but-one digit tampered


def _entity_texts(text: str, entity_type: EntityType) -> list[str]:
    return [e.text for e in detect_entities(text) if e.entity_type == entity_type]


# --- PAN --------------------------------------------------------------------------------


def test_pan_valid_format_detected() -> None:
    assert "ABCDE1234F" in _entity_texts("PAN: ABCDE1234F", EntityType.PAN)


@pytest.mark.parametrize(
    "text",
    ["ABCD1234F", "ABCDE12345", "ABCDEF123F", "abcde1234f is lowercase but still matches"],
)
def test_pan_near_miss_formats(text: str) -> None:
    # The last case is intentionally a case-insensitivity check, not a negative case --
    # OCR output is inconsistently cased, so PAN matching is case-insensitive by design.
    if "lowercase" in text:
        assert _entity_texts(text, EntityType.PAN)
    else:
        assert not _entity_texts(text, EntityType.PAN)


# --- Aadhaar (with Verhoeff checksum) ----------------------------------------------------


def test_aadhaar_valid_checksum_detected() -> None:
    assert _VALID_AADHAAR in _entity_texts(f"Aadhaar: {_VALID_AADHAAR}", EntityType.AADHAAR)


def test_aadhaar_invalid_checksum_not_detected() -> None:
    assert not _entity_texts(f"Aadhaar: {_INVALID_CHECKSUM_AADHAAR}", EntityType.AADHAAR)


def test_aadhaar_grouped_with_spaces_detected() -> None:
    grouped = f"{_VALID_AADHAAR[:4]} {_VALID_AADHAAR[4:8]} {_VALID_AADHAAR[8:]}"
    assert _entity_texts(f"Aadhaar: {grouped}", EntityType.AADHAAR) == [grouped]


# --- Phone --------------------------------------------------------------------------------


@pytest.mark.parametrize("phone", ["9876543210", "+919876543210", "+91 9876543210"])
def test_phone_valid_formats_detected(phone: str) -> None:
    assert _entity_texts(f"Call: {phone}", EntityType.PHONE)


@pytest.mark.parametrize("phone", ["1876543210", "98765432"])
def test_phone_invalid_formats_not_detected(phone: str) -> None:
    # 1876543210 starts with 1, outside the TRAI 6-9 mobile numbering range.
    # 98765432 is too short.
    assert not _entity_texts(f"Call: {phone}", EntityType.PHONE)


# --- Email --------------------------------------------------------------------------------


def test_email_valid_format_detected() -> None:
    assert "asha.rao@example.com" in _entity_texts("Email: asha.rao@example.com", EntityType.EMAIL)


def test_email_malformed_not_detected() -> None:
    assert not _entity_texts("Email: not-an-email", EntityType.EMAIL)


# --- PIN code (dataset-validated) ---------------------------------------------------------


def test_pin_code_known_pincode_detected() -> None:
    assert "110001" in _entity_texts("PIN: 110001", EntityType.PIN_CODE)


def test_pin_code_unknown_six_digit_number_not_detected() -> None:
    assert not _entity_texts("Reference: 999999", EntityType.PIN_CODE)


# --- Passport -------------------------------------------------------------------------------


def test_passport_valid_format_detected() -> None:
    assert "A1234567" in _entity_texts("Passport: A1234567", EntityType.PASSPORT)


def test_passport_wrong_digit_count_not_detected() -> None:
    assert not _entity_texts("Passport: A123456", EntityType.PASSPORT)


# --- Date -----------------------------------------------------------------------------------


@pytest.mark.parametrize("date_text", ["01/02/1990", "1-2-1990", "1 January 1990"])
def test_date_formats_detected(date_text: str) -> None:
    assert _entity_texts(f"DOB: {date_text}", EntityType.DATE)


def test_non_date_number_not_detected_as_date() -> None:
    assert not _entity_texts("Amount: 100000", EntityType.DATE)


# --- Account number (catch-all) --------------------------------------------------------------


def test_long_unclaimed_digit_run_detected_as_account_number() -> None:
    assert "123456789012345" in _entity_texts("A/C: 123456789012345", EntityType.ACCOUNT_NUMBER)


def test_short_digit_run_not_detected_as_account_number() -> None:
    assert not _entity_texts("Count: 12345678", EntityType.ACCOUNT_NUMBER)


# --- Precedence / non-overlapping span ownership ----------------------------------------------


def test_valid_aadhaar_is_not_also_claimed_as_account_number() -> None:
    entities = detect_entities(f"Aadhaar: {_VALID_AADHAAR}")
    types = {e.entity_type for e in entities if e.text == _VALID_AADHAAR}
    assert types == {EntityType.AADHAAR}


def test_independent_non_overlapping_entities_are_all_detected_regardless_of_precedence_order() -> None:
    text = f"Email asha@example.com, PAN ABCDE1234F, Aadhaar {_VALID_AADHAAR}, PIN 110001"
    entities = detect_entities(text)
    found_types = {e.entity_type for e in entities}
    assert found_types == {EntityType.EMAIL, EntityType.PAN, EntityType.AADHAAR, EntityType.PIN_CODE}


def test_invalid_checksum_aadhaar_falls_back_to_account_number_not_left_unclassified() -> None:
    # Not Aadhaar (checksum fails), but still a 12-digit run -- account_number's catch-all
    # picks it up rather than leaving it undetected entirely.
    entities = detect_entities(f"Ref: {_INVALID_CHECKSUM_AADHAAR}")
    matching = [e for e in entities if e.text == _INVALID_CHECKSUM_AADHAAR]
    assert len(matching) == 1
    assert matching[0].entity_type == EntityType.ACCOUNT_NUMBER


# --- Name (heuristic, conservative by design -- adjustment 3) ---------------------------------


def test_two_word_title_case_name_detected() -> None:
    assert "Asha Rao" in _entity_texts("Applicant: Asha Rao", EntityType.NAME)


@pytest.mark.parametrize(
    "phrase",
    ["Full Name", "Date Of Birth", "Account Number", "Pin Code"],
)
def test_document_label_phrases_excluded_from_name_detection(phrase: str) -> None:
    assert phrase not in _entity_texts(f"{phrase}: Asha Rao", EntityType.NAME)


def test_address_suffix_phrase_excluded_from_name_detection() -> None:
    # "New Road" is a Title-Case two-word run but ends in a known address suffix.
    assert "New Road" not in _entity_texts("Address: New Road, Delhi", EntityType.NAME)


def test_month_name_phrase_excluded_from_name_detection() -> None:
    assert "January Report" not in _entity_texts("Subject: January Report", EntityType.NAME)


# --- Address (heuristic) -----------------------------------------------------------------------


def test_pin_code_with_nearby_address_keyword_detected_as_address() -> None:
    text = "123 MG Road, Sector 5, PIN 110001"
    assert _entity_texts(text, EntityType.ADDRESS)


def test_bare_pin_code_without_address_keyword_not_detected_as_address() -> None:
    text = "Reference PIN 110001 for invoice"
    assert not _entity_texts(text, EntityType.ADDRESS)


def test_address_detection_does_not_prevent_pin_code_from_also_being_detected() -> None:
    # Deliberate, documented exception to strict non-overlap: an address naturally
    # contains its own PIN code -- this is nesting, not competing reclassification.
    text = "123 MG Road, Sector 5, PIN 110001"
    entities = detect_entities(text)
    types = {e.entity_type for e in entities}
    assert EntityType.PIN_CODE in types
    assert EntityType.ADDRESS in types
