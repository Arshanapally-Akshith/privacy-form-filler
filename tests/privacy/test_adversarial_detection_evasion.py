"""Adversarial mechanism tests: can PII be formatted so app.privacy.detection.detect_entities
misses it entirely? (Phase 7 Commit 3; ARCHITECTURE.md §8 "detection evasion via unusual
formatting", V14.)

**Why this matters more than an ordinary recall gap.** detect_entities() is not just measured
for its own sake -- app.boundary.payload.protect() (the real production boundary, Invariant
P1/I1) calls it directly over the *entire outbound prompt text* and only ever applies a
policy action (Tokenize/Generalize/Derive/Pass-through) to spans it actually returns. A value
detect_entities() never sees is not "unprotected by policy" -- it is never in `entities` at
all, so app.boundary.payload._reconstruct() copies that exact substring into the protected
text untouched (read the two modules directly; this is not an inference). In `full_tokenize`/
`policy_engine` modes, a detection miss on real PII therefore is a raw PII value reaching
`protected_text`, which is exactly what would be sent onward to the external LLM. This file
exists to find and report exactly that class of gap -- deliberately, adversarially -- for
entity types the project's own recall measurement (tests/privacy/test_detection_recall.py,
DECISIONS.md P12) currently pins at 1.0 recall.

**These are mechanism tests, not an evaluation script.** No dataset, no LLM, no case
fixtures -- one call each into the real, unmodified app.privacy.detection.detect_entities(),
the same function app.boundary.payload.protect() calls in production. Nothing here
reimplements or approximates detection logic.

**Per this commit's own instruction, nothing here is fixed.** Every confirmed bypass below is
reported as such -- asserted as *currently* evading detection -- not patched. That is a
deliberate choice, not an oversight: fixing detection regexes is out of this commit's scope,
and doing so quietly here would mask a finding this suite exists to surface.

**Three explicit buckets, kept separate throughout this file:**

1. **Expected failed attacks** -- formatting tricks against detection that do *not* work;
   the mechanism holds. Included for contrast, and because a "detection evasion" suite with
   no attempted-and-failed attacks would only be testing the easy, already-known-broken
   half of the story.

2. **Expected successful attacks -- already-documented limitations.** NAME/DATE/ADDRESS are
   heuristic detectors, already measured below 1.0 recall on purpose
   (tests/privacy/test_detection_recall.py's own pinned floors: NAME 0.4, DATE/ADDRESS 0.6;
   detection.py's own module docstring already states the precision-over-recall trade-off
   for NAME). Reproducing a fresh evasion example against these here is not a new finding --
   it is this suite explicitly acknowledging a limitation this project already knows about
   and has chosen to accept.

3. **Expected successful attacks -- newly confirmed, NOT previously documented at this
   recall level.** Aadhaar/PAN/phone/PIN-code/account-number are currently pinned at 1.0
   recall (test_detection_recall.py's own `_MINIMUM_RECALL_BY_TYPE`) -- these detectors are
   presently believed to catch every occurrence. The tests below demonstrate concrete,
   simple formatting variants (an alternate separator character, doubled whitespace, a
   common human email-obfuscation convention) that were not exercised by the existing
   labeled fixture set and that evade every one of these detectors completely. This is a
   materially more severe finding than bucket 2: it means Invariant I2 ("no raw PII value
   appears in any payload sent to an external LLM") can be defeated for these entity types by
   an unsophisticated formatting change, for input shapes the project's own recall
   measurement does not currently cover. **Reported here plainly, not fixed** -- per this
   commit's explicit instruction and per ARCHITECTURE.md §8/V15 ("unfixed bypasses are
   reported plainly"). Recommended follow-up (out of scope for this commit): extend the
   regex patterns in app/privacy/detection.py to admit these separators, and add the
   confirmed cases to tests/fixtures/pii_detection/labeled_samples.json so
   test_detection_recall.py's pinned floors reflect them.

**Whether any of this violates a protected CLAUDE.md invariant test.** No. Every existing
protected invariant test (tests/test_import_boundaries.py, tests/boundary/
test_outbound_pii_assertion.py, tests/boundary/test_state_isolation.py, and this project's
detection-recall regression test itself) remains green and unmodified by this commit --
verified by running the full suite alongside this file, not asserted. Those tests are scoped
to the project's own measured, committed evaluation dataset (ARCHITECTURE.md §8's own
distinction: "the re-identification analysis tests whether the policy design leaks even when
the mechanism works perfectly... the adversarial suite tests whether the mechanism can be
bypassed" -- two different failure classes, deliberately not conflated). The bypasses
confirmed below are real and are not hidden, but they do not make any *currently measured*
claim in this project false; they identify input shapes outside what has been measured so
far. That distinction is exactly what this file is for.
"""

from app.privacy.detection import EntityType, detect_entities


def _entity_texts(text: str, entity_type: EntityType) -> list[str]:
    return [e.text for e in detect_entities(text) if e.entity_type == entity_type]


def _nothing_detected_at_all(text: str) -> bool:
    """Stronger than "not detected as this type" -- confirms detect_entities() returns no
    entity of *any* type overlapping this text, i.e. the value is not merely misclassified,
    it is invisible to the mechanism entirely."""
    return detect_entities(text) == []


# ===========================================================================
# Bucket 1 -- expected failed attacks: formatting tricks that do NOT evade detection.
# ===========================================================================


def test_attack_aadhaar_mixed_separator_grouping_still_detected() -> None:
    # Attempt: mix hyphen and space between groups, hoping the detector expects one
    # separator style consistently. Fails -- [\s-]? is evaluated independently per gap.
    text = "Aadhaar: 1234-5678 9010"
    assert "1234-5678 9010" in _entity_texts(text, EntityType.AADHAAR)


def test_attack_phone_plus91_with_hyphen_still_detected() -> None:
    # Attempt: use a hyphen instead of a space after the country code. Fails.
    text = "Phone: +91-9876543210"
    assert _entity_texts(text, EntityType.PHONE)


def test_attack_pin_code_with_trailing_punctuation_still_detected() -> None:
    # Attempt: hide a PIN code by burying it against punctuation rather than whitespace,
    # hoping the word-boundary regex is fooled. Fails -- \b matches at a digit/punctuation
    # boundary just as well as at a digit/whitespace boundary.
    text = "Address line, PIN:110001."
    assert "110001" in _entity_texts(text, EntityType.PIN_CODE)


def test_attack_pan_lowercase_embedded_in_prose_still_detected() -> None:
    # Attempt: rely on case-folding to slip past a supposedly-strict format check. Fails --
    # the PAN pattern is deliberately case-insensitive (OCR output is inconsistently cased).
    text = "the applicant's pan is abcde1234f as printed on the card"
    assert _entity_texts(text, EntityType.PAN)


# ===========================================================================
# Bucket 2 -- expected successful attacks: already-documented, accepted limitations.
# NAME/DATE/ADDRESS are heuristic by design; recall < 1.0 is pinned and measured, not a
# surprise. Presented here under adversarial framing, not as a new finding.
# ===========================================================================


def test_attack_all_caps_name_evades_detection_known_accepted_limitation() -> None:
    # Known limitation: the name heuristic matches Title-Case runs specifically
    # (app.privacy.detection._TITLE_CASE_WORD); an all-caps name is a Title-Case-detector
    # blind spot by construction, already reflected in NAME's own pinned recall floor (0.4,
    # test_detection_recall.py) via the fixture set's own "known_miss" all-caps case.
    text = "Applicant: ASHA RAO"
    assert "ASHA RAO" not in _entity_texts(text, EntityType.NAME)
    assert _nothing_detected_at_all(text)


def test_attack_abbreviated_initial_name_evades_detection_known_accepted_limitation() -> None:
    # Known limitation: "A. Rao" is not two consecutive Title-Case *words* by the pattern's
    # own definition (a single letter followed by a period is not [A-Z][a-z]+) -- already
    # the same class of gap test_detection_recall.py's fixture set names explicitly.
    text = "Signed: A. Rao"
    assert "A. Rao" not in _entity_texts(text, EntityType.NAME)


def test_attack_address_without_recognized_suffix_keyword_evades_detection_known_accepted_limitation() -> None:
    # Known limitation: the address heuristic requires an ADDRESS_SUFFIXES keyword
    # (Road/Street/Nagar/...) within its window around a PIN code. A building-name-only
    # address with no such keyword is exactly the gap test_detection_recall.py's fixture
    # set already names ("a building-name address with no recognized street-suffix
    # keyword").
    text = "Shanti Bhavan, Near City Hospital, 560001"
    assert not _entity_texts(text, EntityType.ADDRESS)


# ===========================================================================
# Bucket 3 -- expected successful attacks: NEWLY confirmed, not previously documented at
# the currently-pinned 1.0 recall level for these entity types. Reported plainly, not fixed
# (see module docstring). Each assertion below was verified interactively against the real
# detector before being written -- these are not speculative.
# ===========================================================================


def test_attack_aadhaar_dot_separated_evades_detection_confirmed_new_bypass() -> None:
    # AADHAAR_PATTERN's separator class is [\s-]? -- a period is not in that class, so a
    # dot-grouped Aadhaar (a real, common human formatting choice) is invisible to the
    # detector entirely, not merely misclassified.
    assert _nothing_detected_at_all("Aadhaar: 1234.5678.9010")


def test_attack_aadhaar_doubled_whitespace_evades_detection_confirmed_new_bypass() -> None:
    # [\s-]? permits at most one separator character between groups -- two consecutive
    # spaces (a plausible copy-paste/OCR artifact, not an exotic attack) already breaks the
    # match.
    assert _nothing_detected_at_all("Aadhaar: 1234  5678  9010")


def test_attack_pan_hyphen_separated_evades_detection_confirmed_new_bypass() -> None:
    # PAN_PATTERN admits no separator at all between its three segments -- any character
    # inserted between them, including a hyphen a human might type for readability, defeats
    # it completely.
    assert _nothing_detected_at_all("PAN: ABCDE-1234-F")


def test_attack_pan_space_separated_evades_detection_confirmed_new_bypass() -> None:
    assert _nothing_detected_at_all("PAN: ABCDE 1234 F")


def test_attack_phone_hyphenated_5_5_grouping_evades_detection_confirmed_new_bypass() -> None:
    # PHONE_PATTERN requires 10 *contiguous* digits (no internal separator at all, unlike
    # Aadhaar) -- the extremely common Indian "XXXXX-XXXXX" phone display grouping breaks
    # it into two 5-digit runs, neither of which matches any pattern (too short for
    # ACCOUNT_NUMBER's 9-digit minimum too).
    assert _nothing_detected_at_all("Phone: 98765-43210")


def test_attack_phone_dotted_grouping_evades_detection_confirmed_new_bypass() -> None:
    assert _nothing_detected_at_all("Phone: 98765.43210")


def test_attack_pin_code_space_separated_evades_detection_confirmed_new_bypass() -> None:
    # PIN_CODE_PATTERN requires 6 contiguous digits -- "110 001" (a common human-readable
    # grouping of a 6-digit Indian PIN code) is two 3-digit runs, neither matching anything.
    assert _nothing_detected_at_all("PIN: 110 001")


def test_attack_account_number_hyphen_grouped_evades_detection_confirmed_new_bypass() -> None:
    # ACCOUNT_NUMBER_PATTERN requires one contiguous 9-18 digit run -- a bank-statement-
    # style hyphenated grouping (e.g. card/account numbers commonly shown "1234-5678-9012-
    # 345") splits it into chunks all below the 9-digit minimum.
    assert _nothing_detected_at_all("A/C: 1234-5678-9012-345")


def test_attack_email_human_obfuscation_convention_evades_detection_confirmed_new_bypass() -> None:
    # EMAIL_PATTERN requires a literal '@'. Writing "(at)" in place of '@' is a well-known,
    # long-standing human convention for evading exactly this kind of automated scanning
    # (originally popularized to dodge spam harvesters) -- it defeats this detector just as
    # effectively, and requires no technical sophistication to use.
    assert _nothing_detected_at_all("Email: asha.rao(at)example.com")
