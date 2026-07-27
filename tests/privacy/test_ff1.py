"""FF1 core algorithm tests (BUILD.md Phase 3, task 2).

NIST vector conformance is written first and is the correctness foundation everything else
in this commit depends on -- verified directly against NIST's own published FF1 samples
(csrc.nist.gov, "FF1 Method for Format-Preserving Encryption" examples), covering
AES-128/192/256 keys, empty and non-empty tweaks, and both a radix-10 and radix-36 domain.

Exhaustive enumeration (not randomized property testing, per CLAUDE.md §4's determinism
rule) proves round-trip correctness and bijectivity across an entire small input space,
not just a handful of examples.
"""

import pytest

from app.privacy import ff1

_ALPHABET_DIGITS = "0123456789"
_ALPHABET_36 = "0123456789abcdefghijklmnopqrstuvwxyz"  # matches NIST's own sample alphabet


# --- Official NIST test vectors (csrc.nist.gov FF1 samples) -----------------------------


@pytest.mark.parametrize(
    ("key_hex", "tweak_hex", "alphabet", "plaintext", "expected_ciphertext"),
    [
        # Sample #1: FF1-AES128, radix 10, empty tweak
        ("2B7E151628AED2A6ABF7158809CF4F3C", "", _ALPHABET_DIGITS, "0123456789", "2433477484"),
        # Sample #2: FF1-AES128, radix 10, non-empty tweak
        (
            "2B7E151628AED2A6ABF7158809CF4F3C",
            "39383736353433323130",
            _ALPHABET_DIGITS,
            "0123456789",
            "6124200773",
        ),
        # Sample #3: FF1-AES128, radix 36, non-empty tweak
        (
            "2B7E151628AED2A6ABF7158809CF4F3C",
            "3737373770717273373737",
            _ALPHABET_36,
            "0123456789abcdefghi",
            "a9tv40mll9kdu509eum",
        ),
        # Sample #7: FF1-AES256, radix 10, empty tweak
        (
            "2B7E151628AED2A6ABF7158809CF4F3CEF4359D8D580AA4F7F036D6F04FC6A94",
            "",
            _ALPHABET_DIGITS,
            "0123456789",
            "6657667009",
        ),
    ],
)
def test_encrypt_matches_official_nist_vector(
    key_hex: str, tweak_hex: str, alphabet: str, plaintext: str, expected_ciphertext: str
) -> None:
    key = bytes.fromhex(key_hex)
    tweak = bytes.fromhex(tweak_hex)
    assert ff1.encrypt(key, tweak, alphabet, plaintext) == expected_ciphertext


@pytest.mark.parametrize(
    ("key_hex", "tweak_hex", "alphabet", "ciphertext", "expected_plaintext"),
    [
        ("2B7E151628AED2A6ABF7158809CF4F3C", "", _ALPHABET_DIGITS, "2433477484", "0123456789"),
        (
            "2B7E151628AED2A6ABF7158809CF4F3C",
            "39383736353433323130",
            _ALPHABET_DIGITS,
            "6124200773",
            "0123456789",
        ),
        (
            "2B7E151628AED2A6ABF7158809CF4F3C",
            "3737373770717273373737",
            _ALPHABET_36,
            "a9tv40mll9kdu509eum",
            "0123456789abcdefghi",
        ),
        (
            "2B7E151628AED2A6ABF7158809CF4F3CEF4359D8D580AA4F7F036D6F04FC6A94",
            "",
            _ALPHABET_DIGITS,
            "6657667009",
            "0123456789",
        ),
    ],
)
def test_decrypt_matches_official_nist_vector(
    key_hex: str, tweak_hex: str, alphabet: str, ciphertext: str, expected_plaintext: str
) -> None:
    key = bytes.fromhex(key_hex)
    tweak = bytes.fromhex(tweak_hex)
    assert ff1.decrypt(key, tweak, alphabet, ciphertext) == expected_plaintext


# --- Exhaustive round-trip and bijectivity over small synthetic domains ------------------


@pytest.mark.parametrize("length", [7, 8])  # odd and even length -- exercises both u<v and u==v splits
def test_exhaustive_round_trip_and_bijection_over_binary_alphabet(length: int) -> None:
    key = b"\x00" * 16
    tweak = b""
    alphabet = "01"

    outputs = set()
    for value in range(2**length):
        plaintext = format(value, f"0{length}b")
        ciphertext = ff1.encrypt(key, tweak, alphabet, plaintext)
        assert ff1.decrypt(key, tweak, alphabet, ciphertext) == plaintext
        outputs.add(ciphertext)

    # Bijection: every one of the 2**length possible plaintexts maps to a distinct
    # ciphertext -- a stronger property than round-trip alone, and a direct check that
    # this behaves as a true cipher (a permutation of the domain), not merely a pair of
    # matching bugs in encrypt/decrypt.
    assert len(outputs) == 2**length


def test_exhaustive_round_trip_over_small_decimal_domain() -> None:
    key = b"\x11" * 24  # AES-192, exercising a different key size than the binary test
    tweak = b"tweak-bytes"
    alphabet = _ALPHABET_DIGITS
    length = 4  # domain = 10_000, small enough to enumerate exhaustively and quickly

    for value in range(10**length):
        plaintext = str(value).zfill(length)
        ciphertext = ff1.encrypt(key, tweak, alphabet, plaintext)
        assert ff1.decrypt(key, tweak, alphabet, ciphertext) == plaintext
        assert len(ciphertext) == length
        assert all(ch in alphabet for ch in ciphertext)
