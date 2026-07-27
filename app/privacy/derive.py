"""Derive action: PIN code -> district/state (BUILD.md Phase 3, task 4).

A deterministic, trusted lookup against the committed, versioned PIN code dataset
(DECISIONS.md P6-P9) -- no network fetch at runtime, reproducible from the input and the
pinned dataset version alone (ARCHITECTURE.md §5.1's constraint on Generalize/Derive).

ARCHITECTURE.md/DECISIONS.md refer to "city," but the committed CSV's actual columns are
pincode, district, statename -- there is no separate city field. "District" is what this
project's data provides at that granularity, implemented and named as such throughout.

Duplicate/contradictory PIN handling -- the real complexity here, verified against the
committed dataset, not assumed: 1,478 of 19,586 unique pincodes map to more than one
distinct (district, state) pair; of those, 52 are contradictory even at the state level
(after dropping raw "NA" placeholder rows -- see app.privacy.pincode_dataset). One
consistent rule, applied independently per derived attribute: if the dataset does not
unambiguously agree on a value, it is not derived -- fail closed, extending DECISIONS.md
P10's "unknown PIN -> explicit fallback, never silent" to the "contradictory PIN" case.
District is treated conservatively (any disagreement fails it) rather than picking a
"first" or "most common" value by convention -- a specific-but-possibly-wrong sub-location
is a worse failure mode than abstaining, the same "abstain, never invent" reasoning
Invariant I5 already established for extraction, applied here to a lookup instead of an
LLM call. State is therefore derivable far more often than district -- a direct, honest
consequence of the data, not a design flaw to paper over.

derive_state() and derive_district() are the primary, independent public API -- there is
deliberately no derive_location() combining both in this commit: a naive composition would
discard a state that resolved successfully whenever district happened to be ambiguous (or
vice versa), silently throwing away a real, independently-derivable result. Callers that
want both call both.

Not reversible (ARCHITECTURE.md §5.1's action table marks Derive "No"), and no probabilistic
inference -- a pure dictionary lookup against a fixed, versioned file.
"""

from app.privacy.constants import PIN_CODE_LENGTH
from app.privacy.pincode_dataset import PINCODE_DISTRICT_STATE_INDEX, PincodeEntry


class DerivationError(ValueError):
    """Base class for Derive-action failures."""


class InvalidPinCodeFormatError(DerivationError):
    """Raised when the input is not a well-formed 6-digit PIN code -- a caller-bug failure
    mode, checked before any dataset lookup."""


class UnknownPinCodeError(DerivationError):
    """Raised when a well-formed PIN code has no resolvable entry in the committed dataset
    (absent entirely, or present only via raw "NA"-placeholder rows)."""


class AmbiguousPinCodeError(DerivationError):
    """Raised when a PIN code resolves to more than one distinct value for the requested
    attribute -- the dataset does not agree with itself, so nothing is derived."""


def _entries_for(pin_code: str) -> frozenset[PincodeEntry]:
    if len(pin_code) != PIN_CODE_LENGTH or not pin_code.isdigit():
        raise InvalidPinCodeFormatError(f"{pin_code!r} is not a valid {PIN_CODE_LENGTH}-digit PIN code")

    entries = PINCODE_DISTRICT_STATE_INDEX.get(pin_code)
    if not entries:
        raise UnknownPinCodeError(f"Unknown PIN code: {pin_code!r}")
    return entries


def derive_state(pin_code: str) -> str:
    entries = _entries_for(pin_code)
    states = {entry.state for entry in entries}
    if len(states) > 1:
        raise AmbiguousPinCodeError(f"PIN code {pin_code!r} maps to multiple states: {sorted(states)}")
    return next(iter(states))


def derive_district(pin_code: str) -> str:
    entries = _entries_for(pin_code)
    districts = {entry.district for entry in entries}
    if len(districts) > 1:
        raise AmbiguousPinCodeError(f"PIN code {pin_code!r} maps to multiple districts: {sorted(districts)}")
    return next(iter(districts))
