"""Synthetic identity generator for the Phase 6 evaluation dataset (BUILD.md Phase 6 tasks
1-2). Produces one internally-consistent synthetic person -- name, DOB, PIN code (plus its
human-readable city/state), PAN, Aadhaar, phone, email, linked account number, annual
income, residential address -- meant to be reused across every document in a case, so a
case's ground truth is exactly this identity's own fields. (Case assembly, and the
consistency check that a case's documents actually agree with it, are Commit 3/6 -- this
module only produces the identity itself.)

Deliberately lives in eval/, not app/: this is evaluation-fixture data generation, the same
category as eval/dataset/generate_phase1_fixtures.py, not a real ingestion or retrieval
path -- Invariant P1 has nothing to say about it.

**City pool is deliberately constrained** (DECISIONS.md V6) rather than drawn from the full
~19,586-pincode committed dataset (P9): identities generated uniformly at random across
India would make every case unique by construction and render the Phase 7 k-anonymity
analysis meaningless (ARCHITECTURE.md §8 Axis 3). Each CITY_POOL entry's pincode was
verified against the committed app/config/data/pincode_district_state.csv to resolve to
exactly one (district, state) pair via app.privacy.derive -- so Derive's ground truth for
every generated case is unambiguous by construction (see test_identity_generator.py, which
checks this directly rather than trusting the comment).

CITY_POOL.city is a human-readable label ("Bengaluru") used in generated document prose --
it is deliberately *not* required to match app.privacy.derive's own district-name spelling
("BENGALURU URBAN"). Those are two different data paths: document text is prose a person
would plausibly write on a form, while Derive resolves the case's pin_code value
independently at policy-engine time. What matters for k-anonymity grouping is that every
case sharing a pincode derives to the same district string, which holds regardless of the
prose label -- not that the two strings match each other.

All randomness flows through a caller-supplied `random.Random`, never a module-level
instance or `random.random()` directly -- CLAUDE.md §4's "no reliance on ... random seeds"
(implicitly: no *hidden* seed) and this module's own determinism test require a seed to
fully determine output.
"""

import random
import string
from dataclasses import dataclass
from datetime import date

# Bumped whenever a change to this module would alter what a fixed seed generates --
# Commit 5's dataset-build script embeds this in the committed dataset's metadata so a
# regenerated dataset's provenance is traceable (not yet consumed by any code in this
# commit).
IDENTITY_GENERATOR_VERSION = "1.0.0"


@dataclass(frozen=True)
class CityEntry:
    city: str
    state: str
    pincode: str


CITY_POOL: tuple[CityEntry, ...] = (
    CityEntry("New Delhi", "Delhi", "110001"),
    CityEntry("Mumbai", "Maharashtra", "400001"),
    CityEntry("Chennai", "Tamil Nadu", "600001"),
    CityEntry("Kolkata", "West Bengal", "700001"),
    CityEntry("Pune", "Maharashtra", "411001"),
    CityEntry("Hyderabad", "Telangana", "500001"),
    CityEntry("Jaipur", "Rajasthan", "302001"),
    CityEntry("Lucknow", "Uttar Pradesh", "226001"),
    CityEntry("Bengaluru", "Karnataka", "560001"),
    CityEntry("Ahmedabad", "Gujarat", "380001"),
)

FIRST_NAMES = (
    "Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Reyansh", "Ishaan", "Kabir",
    "Rohan", "Karan", "Nikhil", "Sanjay", "Ravi", "Deepak", "Arnav",
    "Ananya", "Diya", "Priya", "Saanvi", "Aadhya", "Meera", "Kavya", "Neha",
    "Pooja", "Anjali", "Sunita", "Ritu", "Divya", "Shreya",
)  # fmt: skip

LAST_NAMES = (
    "Sharma", "Verma", "Gupta", "Mehta", "Patel", "Reddy", "Iyer", "Nair",
    "Singh", "Kumar", "Rao", "Joshi", "Malhotra", "Chatterjee", "Bose",
    "Desai", "Kapoor", "Agarwal", "Menon", "Pillai",
)  # fmt: skip

STREET_NAMES = (
    "MG Road", "Lake View Apartments", "Church Street", "Station Road",
    "Gandhi Nagar", "Park Street", "Ring Road", "Model Colony", "Civil Lines",
    "Green Park",
)  # fmt: skip

LOCALITIES = (
    "Sector 12", "Andheri West", "Koramangala", "Salt Lake", "Banjara Hills",
    "Vaishali Nagar", "Hazratganj", "Satellite", "Kothrud", "Anna Nagar",
)  # fmt: skip

# Skewed toward working-age adults (KYC/insurance applicants), not a uniform 18-100 spread
# -- "realistic age distribution" per DECISIONS.md V6. rng.triangular gives that skew
# without pulling in numpy (E15's own no-unjustified-dependency precedent).
MIN_AGE_YEARS = 21
MAX_AGE_YEARS = 70
MODE_AGE_YEARS = 34

MIN_ANNUAL_INCOME = 300_000
MAX_ANNUAL_INCOME = 2_500_000
ANNUAL_INCOME_STEP = 10_000


@dataclass(frozen=True)
class Identity:
    full_name: str
    date_of_birth: date
    residential_address: str
    pin_code: str
    city: str
    state: str
    pan_number: str
    aadhaar_number: str
    phone_number: str
    email_address: str
    linked_account_number: str
    annual_income: int


def _random_digits(rng: random.Random, count: int) -> str:
    return "".join(rng.choice(string.digits) for _ in range(count))


def _random_letters(rng: random.Random, count: int) -> str:
    return "".join(rng.choice(string.ascii_uppercase) for _ in range(count))


def _generate_dob(rng: random.Random, reference_date: date) -> date:
    age_years = round(rng.triangular(MIN_AGE_YEARS, MAX_AGE_YEARS, MODE_AGE_YEARS))
    birth_year = reference_date.year - age_years
    month = rng.randint(1, 12)
    day = rng.randint(1, 28)  # avoids month-length/leap-day edge cases -- an approximate
    # birth year is what "realistic age distribution" needs, not an exact day.
    return date(birth_year, month, day)


def _generate_pan(rng: random.Random) -> str:
    # Format only (5 letters, 4 digits, 1 letter) -- matches app.privacy.detection's
    # _PAN_PATTERN and the form schemas' own expected_format; real PAN's semantic
    # structure (holder-type/surname-encoding letters) is not needed for this project.
    return f"{_random_letters(rng, 5)}{_random_digits(rng, 4)}{_random_letters(rng, 1)}"


def _generate_aadhaar(rng: random.Random) -> str:
    return _random_digits(rng, 12)


def _generate_phone(rng: random.Random) -> str:
    return f"{rng.choice('6789')}{_random_digits(rng, 9)}"


def _generate_email(rng: random.Random, first_name: str, last_name: str) -> str:
    suffix = rng.randint(10, 99)
    return f"{first_name.lower()}.{last_name.lower()}{suffix}@example.com"


def _generate_account_number(rng: random.Random) -> str:
    return _random_digits(rng, 12)


def _generate_address(rng: random.Random, city_entry: CityEntry) -> str:
    house_number = rng.randint(1, 199)
    street = rng.choice(STREET_NAMES)
    locality = rng.choice(LOCALITIES)
    return f"{house_number} {street}, {locality}, {city_entry.city}, {city_entry.state}"


def _generate_annual_income(rng: random.Random) -> int:
    steps = (MAX_ANNUAL_INCOME - MIN_ANNUAL_INCOME) // ANNUAL_INCOME_STEP
    return MIN_ANNUAL_INCOME + rng.randint(0, steps) * ANNUAL_INCOME_STEP


def generate_identity(rng: random.Random, reference_date: date) -> Identity:
    """One synthetic person, fully populated -- every field always has a value. Whether a
    given case's documents actually mention every field (genuine-absence cases need at
    least one field withheld) is a case-assembly decision, not this function's."""
    first_name = rng.choice(FIRST_NAMES)
    last_name = rng.choice(LAST_NAMES)
    city_entry = rng.choice(CITY_POOL)

    return Identity(
        full_name=f"{first_name} {last_name}",
        date_of_birth=_generate_dob(rng, reference_date),
        residential_address=_generate_address(rng, city_entry),
        pin_code=city_entry.pincode,
        city=city_entry.city,
        state=city_entry.state,
        pan_number=_generate_pan(rng),
        aadhaar_number=_generate_aadhaar(rng),
        phone_number=_generate_phone(rng),
        email_address=_generate_email(rng, first_name, last_name),
        linked_account_number=_generate_account_number(rng),
        annual_income=_generate_annual_income(rng),
    )
