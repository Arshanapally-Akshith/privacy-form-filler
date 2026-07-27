"""Pinned detection constants (BUILD.md Phase 3, task 1). Named, not duplicated inline,
per CLAUDE.md §6.
"""

from pathlib import Path

# Reuses the same committed, versioned dataset Derive will use (DECISIONS.md P6-P9) to
# validate PIN-code candidates by dataset membership rather than a bare 6-digit regex.
PINCODE_DATASET_PATH = Path(__file__).resolve().parent.parent / "config" / "data" / "pincode_district_state.csv"

# Account number is the catch-all for a long digit run not claimed by a more specific
# detector -- no universal format exists, so this is a documented approximation.
ACCOUNT_NUMBER_MIN_DIGITS = 9
ACCOUNT_NUMBER_MAX_DIGITS = 18

# Reused by both the name detector (excluding "<Word> Road"-shaped false positives, e.g.
# "New Road") and the address detector (as positive evidence of an address).
ADDRESS_SUFFIXES = frozenset(
    {
        "Road",
        "Street",
        "Nagar",
        "Colony",
        "Marg",
        "Sector",
        "Lane",
        "Avenue",
        "Society",
        "Apartment",
        "Layout",
        "Extension",
        "Cross",
        "Block",
        "Chowk",
        "Path",
    }
)

# How far around a detected PIN code to look for an address keyword. Deliberately narrow
# to keep the heuristic precision-favoring rather than recall-favoring.
ADDRESS_WINDOW_CHARS = 60

# Exact document/form-label phrases that are Title-Case multi-word runs but are never a
# person's name. Deliberately conservative (a precision trade-off, not an oversight): a
# missed name is a detection-recall gap the fail-closed default (Commit 2+) still protects
# against, whereas a form label misclassified as a name is a pure false positive with no
# compensating safeguard. Covers compound labels the broader FIELD_LABEL_SUFFIX_WORDS rule
# below does not catch (e.g. "Date Of Birth" ends in "Birth", not a listed suffix word).
NAME_EXCLUSION_PHRASES = frozenset(
    {
        "Date Of Birth",
        "Permanent Account",
        "Application Form",
        "Bank Account",
    }
)

# Broader, general rule: a form label is overwhelmingly "<Something> <FieldWord>" --
# "Policy Issue Date", "Nominee Phone", "Residential Address", "Account Number", etc. --
# a near-unbounded set of prefixes but a small, predictable set of trailing words. A
# Title-Case phrase whose *last* word is one of these is excluded, same conservative
# precision-over-recall reasoning as NAME_EXCLUSION_PHRASES. No real person's surname is
# any of these words in practice.
FIELD_LABEL_SUFFIX_WORDS = frozenset(
    {"Name", "Number", "No", "Date", "Phone", "Address", "Reference", "Code", "Account", "Amount", "Details", "Id"}
)

MONTH_NAMES = frozenset(
    {
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    }
)

WEEKDAY_NAMES = frozenset(
    {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}
)
