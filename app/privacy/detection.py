"""PII detection over free text (BUILD.md Phase 3, task 1).

Deterministic and heuristic-based, by design -- not a compromise. Every match is a fully
explainable regex hit or a documented heuristic rule, never a probabilistic or ML-based
inference: ARCHITECTURE.md §5.1 already bars probabilistic inference for Generalize/Derive,
and the same reasoning applies here. A DetectedEntity's entity_type and span fully explain
why it fired; there is no confidence score, because detection is not a self-assessment --
it is a deterministic pattern match, either it fires or it does not.

Scope: this module only identifies candidate PII spans in arbitrary text. It does not
protect, tokenize, or gate anything -- that starts at Commit 2. A value returned here is
not yet subject to Invariant I2 or the fail-closed default (I4); those require an action to
dispatch to, which does not exist yet. Nothing here sends anything anywhere.

Detection precedence and span ownership: detectors run in a fixed order, most
distinctive/constrained pattern first (email, PAN, Aadhaar, phone, PIN code, passport,
date, account number, name, address). A candidate match is only accepted if it does not
overlap a span already claimed by an earlier (higher-precedence) detector -- a claimed span
is never reclassified. All detectors still run to completion regardless of order, so
independent, non-overlapping entities anywhere in the text are detected no matter which
type they are.

The one deliberate exception is address, which runs last: its window is built around an
already-claimed PIN_CODE span and commonly spans other already-claimed entities too (e.g. a
place name misclassified as NAME). An address is a coarse, containing entity by nature, not
a same-level competing classification of the same text -- so it is exempt from the overlap
check entirely. Every other claimed entity, including any PIN_CODE or NAME it spatially
contains, keeps its own classification; nothing is reclassified.
"""

import re
from dataclasses import dataclass
from enum import Enum

from app.privacy.constants import (
    ACCOUNT_NUMBER_MAX_DIGITS,
    ACCOUNT_NUMBER_MIN_DIGITS,
    ADDRESS_SUFFIXES,
    ADDRESS_WINDOW_CHARS,
    FIELD_LABEL_SUFFIX_WORDS,
    MONTH_NAMES,
    NAME_EXCLUSION_PHRASES,
    WEEKDAY_NAMES,
)
from app.privacy.pincode_dataset import KNOWN_PINCODES


class EntityType(str, Enum):
    AADHAAR = "aadhaar"
    PAN = "pan"
    PASSPORT = "passport"
    ACCOUNT_NUMBER = "account_number"
    PHONE = "phone"
    EMAIL = "email"
    NAME = "name"
    DATE = "date"
    PIN_CODE = "pin_code"
    ADDRESS = "address"


@dataclass(frozen=True)
class DetectedEntity:
    entity_type: EntityType
    text: str
    start: int
    end: int


# --- Verhoeff checksum (used to validate Aadhaar candidates) -----------------------------
# Standard NIST-independent, well-known checksum algorithm (dihedral group D5) -- a
# deterministic arithmetic check, not a probabilistic or statistical technique. Self-
# verified against generated test vectors before use (round-trip: generate a check digit,
# confirm it validates; tamper one digit, confirm validation then fails).

_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def _verhoeff_checksum_valid(digits: str) -> bool:
    checksum = 0
    for i, digit in enumerate(reversed(digits)):
        checksum = _VERHOEFF_D[checksum][_VERHOEFF_P[i % 8][int(digit)]]
    return checksum == 0


# --- Per-type candidate detectors ----------------------------------------------------------
# Each returns raw (start, end, matched_text) candidates, before precedence/overlap
# resolution -- that happens once, centrally, in detect_entities().

_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PAN_PATTERN = re.compile(r"\b[A-Za-z]{5}[0-9]{4}[A-Za-z]\b")
_AADHAAR_PATTERN = re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b")
_PHONE_PATTERN = re.compile(r"\+91[-\s]?[6-9]\d{9}(?!\d)|(?<!\d)[6-9]\d{9}(?!\d)")
_PIN_CODE_PATTERN = re.compile(r"\b\d{6}\b")
_PASSPORT_PATTERN = re.compile(r"\b[A-Za-z]\d{7}\b")
_NUMERIC_DATE_PATTERN = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")
_TEXTUAL_DATE_PATTERN = re.compile(
    r"\b\d{1,2}\s+(?:" + "|".join(sorted(MONTH_NAMES)) + r")\s+\d{4}\b"
)
_ACCOUNT_NUMBER_PATTERN = re.compile(rf"\b\d{{{ACCOUNT_NUMBER_MIN_DIGITS},{ACCOUNT_NUMBER_MAX_DIGITS}}}\b")
_TITLE_CASE_WORD = r"[A-Z][a-z]+"
# [ \t]+, not \s+: word separator must stay on one line -- a form's "...Rao\nDate of..."
# must not merge across the line break into a single false-positive name candidate.
_NAME_PATTERN = re.compile(rf"\b{_TITLE_CASE_WORD}(?:[ \t]+{_TITLE_CASE_WORD})+\b")

_Candidate = tuple[int, int, str]


def _regex_candidates(pattern: re.Pattern[str], text: str) -> list[_Candidate]:
    return [(m.start(), m.end(), m.group()) for m in pattern.finditer(text)]


def _detect_email(text: str) -> list[_Candidate]:
    return _regex_candidates(_EMAIL_PATTERN, text)


def _detect_pan(text: str) -> list[_Candidate]:
    return _regex_candidates(_PAN_PATTERN, text)


def _detect_aadhaar(text: str) -> list[_Candidate]:
    candidates = []
    for start, end, matched in _regex_candidates(_AADHAAR_PATTERN, text):
        digits = re.sub(r"[\s-]", "", matched)
        if len(digits) == 12 and _verhoeff_checksum_valid(digits):
            candidates.append((start, end, matched))
    return candidates


def _detect_phone(text: str) -> list[_Candidate]:
    return _regex_candidates(_PHONE_PATTERN, text)


def _detect_pin_code(text: str) -> list[_Candidate]:
    return [
        (start, end, matched)
        for start, end, matched in _regex_candidates(_PIN_CODE_PATTERN, text)
        if matched in KNOWN_PINCODES
    ]


def _detect_passport(text: str) -> list[_Candidate]:
    return _regex_candidates(_PASSPORT_PATTERN, text)


def _detect_date(text: str) -> list[_Candidate]:
    return _regex_candidates(_NUMERIC_DATE_PATTERN, text) + _regex_candidates(_TEXTUAL_DATE_PATTERN, text)


def _detect_account_number(text: str) -> list[_Candidate]:
    return _regex_candidates(_ACCOUNT_NUMBER_PATTERN, text)


def _is_excluded_name_phrase(phrase: str) -> bool:
    """Adjustment 3: deliberately conservative. A false negative here is still caught by
    the fail-closed default once actions exist (Commit 2+); a false positive on a form
    label has no such safety net, so precision is favored over recall for names.

    Checks every contiguous word window of the phrase against NAME_EXCLUSION_PHRASES, not
    just the whole phrase -- a captured run like "Applicant Full Name" must still be
    excluded because it contains the exact label "Full Name", even though the full
    three-word phrase is not itself a listed exclusion. Also excludes any phrase whose
    last word is a common field-label suffix (FIELD_LABEL_SUFFIX_WORDS) -- "Policy Issue
    Date", "Nominee Phone", "Residential Address" are all "<prefix> <field word>" shaped,
    an unbounded prefix set but a small, predictable suffix set.
    """
    words = phrase.split()
    if words[-1] in ADDRESS_SUFFIXES or words[-1] in FIELD_LABEL_SUFFIX_WORDS:
        return True
    if any(word in MONTH_NAMES or word in WEEKDAY_NAMES for word in words):
        return True
    for window_size in range(2, len(words) + 1):
        for start in range(len(words) - window_size + 1):
            if " ".join(words[start : start + window_size]) in NAME_EXCLUSION_PHRASES:
                return True
    return False


def _detect_name(text: str) -> list[_Candidate]:
    candidates = []
    for start, end, matched in _regex_candidates(_NAME_PATTERN, text):
        if not _is_excluded_name_phrase(matched):
            candidates.append((start, end, matched))
    return candidates


def _detect_address(text: str, pin_code_spans: list[tuple[int, int]]) -> list[_Candidate]:
    """The window is bounded by ADDRESS_WINDOW_CHARS *and* never crosses a line break --
    without the line bound, a short-lined form (one field per line, the common case) lets
    the character-count window bleed into unrelated neighboring fields entirely."""
    candidates = []
    for pin_start, pin_end in pin_code_spans:
        line_start = text.rfind("\n", 0, pin_start) + 1  # rfind -1 (not found) + 1 == 0
        line_end = text.find("\n", pin_end)
        if line_end == -1:
            line_end = len(text)

        window_start = max(line_start, pin_start - ADDRESS_WINDOW_CHARS)
        window_end = min(line_end, pin_end + ADDRESS_WINDOW_CHARS)
        window = text[window_start:window_end]
        if any(keyword in window for keyword in ADDRESS_SUFFIXES):
            candidates.append((window_start, window_end, window))
    return candidates


# --- Precedence + non-overlapping span ownership --------------------------------------------


def _overlaps_claimed(start: int, end: int, claimed: list[DetectedEntity]) -> bool:
    return any(start < entity.end and end > entity.start for entity in claimed)


def detect_entities(text: str) -> list[DetectedEntity]:
    claimed: list[DetectedEntity] = []

    def _accept(entity_type: EntityType, candidates: list[_Candidate], *, check_overlap: bool = True) -> None:
        for start, end, matched_text in candidates:
            if check_overlap and _overlaps_claimed(start, end, claimed):
                continue
            claimed.append(DetectedEntity(entity_type=entity_type, text=matched_text, start=start, end=end))

    _accept(EntityType.EMAIL, _detect_email(text))
    _accept(EntityType.PAN, _detect_pan(text))
    _accept(EntityType.AADHAAR, _detect_aadhaar(text))
    _accept(EntityType.PHONE, _detect_phone(text))
    _accept(EntityType.PIN_CODE, _detect_pin_code(text))
    _accept(EntityType.PASSPORT, _detect_passport(text))
    _accept(EntityType.DATE, _detect_date(text))
    _accept(EntityType.ACCOUNT_NUMBER, _detect_account_number(text))
    _accept(EntityType.NAME, _detect_name(text))

    pin_code_spans = [(e.start, e.end) for e in claimed if e.entity_type == EntityType.PIN_CODE]
    # Deliberate exception, documented in the module docstring: address is a coarse,
    # containing entity by nature -- its window is expected to span other already-claimed
    # spans (its own PIN_CODE, a place name misclassified as NAME, etc). That is nesting,
    # not competing reclassification, so ADDRESS is the one detector not subject to the
    # overlap check. It runs last, so this cannot itself block any other detector.
    _accept(EntityType.ADDRESS, _detect_address(text, pin_code_spans), check_overlap=False)

    return sorted(claimed, key=lambda e: e.start)
