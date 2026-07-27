"""FF1 format-preserving encryption (BUILD.md Phase 3, task 2).

A direct implementation of the FF1 algorithm as specified in NIST SP 800-38G
("Recommendation for Block Cipher Modes of Operation: Methods for Format-Preserving
Encryption"), built on PyCryptodome's AES primitive (AES-CBC-MAC for the round function,
AES-ECB for the block cipher used when the round output needs to be stretched beyond one
block). This is not novel cryptography -- it is a transcription of a published, standardized
algorithm, using an already-vetted AES implementation for every actual cryptographic
operation. See DECISIONS.md E3 for the research behind building this directly rather than
depending on a third-party FF1 package (every option found was either gated behind a
commercial API, a large multi-feature platform dependency, or implemented the wrong scheme
FF3/FF3-1 -- which NIST's own most recent draft revision is removing from the standard).

Correctness is not asserted, it is verified: this implementation reproduces NIST's own
published test vectors exactly (tests/privacy/test_ff1.py), covering AES-128/192/256 keys,
empty and non-empty tweaks, and both a radix-10 and a radix-36 domain -- the same
self-verification discipline used for the Verhoeff checksum in Commit 1, applied here
because the stakes are much higher.

Scope: this module is the cryptographic primitive only -- generic over any caller-supplied
alphabet and key. It has no notion of a "session," an "entity type," or a minimum domain
size policy; those belong to app.privacy.tokenize, the layer above this one.
"""

import math

from Crypto.Cipher import AES


def _num_radix(digits: str, alphabet: str) -> int:
    radix = len(alphabet)
    value = 0
    for ch in digits:
        value = value * radix + alphabet.index(ch)
    return value


def _str_radix(value: int, alphabet: str, length: int) -> str:
    radix = len(alphabet)
    chars = []
    for _ in range(length):
        chars.append(alphabet[value % radix])
        value //= radix
    return "".join(reversed(chars))


def _prf(key: bytes, block: bytes) -> bytes:
    # AES-CBC-MAC, zero IV: encrypt the (already block-aligned) input and keep only the
    # final ciphertext block, per NIST SP 800-38G's PRF definition.
    cipher = AES.new(key, AES.MODE_CBC, iv=b"\x00" * 16)
    return cipher.encrypt(block)[-16:]


def _ciph(key: bytes, block: bytes) -> bytes:
    return AES.new(key, AES.MODE_ECB).encrypt(block)


def _feistel_round_count() -> int:
    return 10  # NIST SP 800-38G fixes this at 10 for FF1.


def _build_prefix(radix: int, u: int, n: int, tweak_len: int) -> bytes:
    return (
        bytes([1, 2, 1])
        + radix.to_bytes(3, "big")
        + bytes([10])
        + (u % 256).to_bytes(1, "big")
        + n.to_bytes(4, "big")
        + tweak_len.to_bytes(4, "big")
    )


def _round_params(alphabet: str, n: int) -> tuple[int, int, int, int]:
    radix = len(alphabet)
    u = n // 2
    v = n - u
    b = math.ceil(math.ceil(v * math.log2(radix)) / 8)
    d = 4 * math.ceil(b / 4) + 4
    return u, v, b, d


def _round_key_stream(key: bytes, prefix: bytes, q: bytes, d: int) -> int:
    r = _prf(key, prefix + q)
    s = r
    j = 1
    while len(s) < d:
        block = (int.from_bytes(r, "big") ^ j).to_bytes(16, "big")
        s += _ciph(key, block)
        j += 1
    return int.from_bytes(s[:d], "big")


def encrypt(key: bytes, tweak: bytes, alphabet: str, plaintext: str) -> str:
    """plaintext must consist entirely of characters in `alphabet`. Returns a string of the
    same length, over the same alphabet -- format-preserving by construction."""
    n = len(plaintext)
    radix = len(alphabet)
    u, v, b, d = _round_params(alphabet, n)
    a, bb = plaintext[:u], plaintext[u:]
    prefix = _build_prefix(radix, u, n, len(tweak))
    pad_len = (-len(tweak) - b - 1) % 16

    for i in range(_feistel_round_count()):
        q = tweak + bytes(pad_len) + bytes([i]) + _num_radix(bb, alphabet).to_bytes(b, "big")
        y = _round_key_stream(key, prefix, q, d)
        m = u if i % 2 == 0 else v
        c = (_num_radix(a, alphabet) + y) % (radix**m)
        a, bb = bb, _str_radix(c, alphabet, m)

    return a + bb


def decrypt(key: bytes, tweak: bytes, alphabet: str, ciphertext: str) -> str:
    """Exact inverse of encrypt(): decrypt(key, tweak, alphabet, encrypt(key, tweak,
    alphabet, x)) == x for every x over `alphabet`."""
    n = len(ciphertext)
    radix = len(alphabet)
    u, v, b, d = _round_params(alphabet, n)
    a, bb = ciphertext[:u], ciphertext[u:]
    prefix = _build_prefix(radix, u, n, len(tweak))
    pad_len = (-len(tweak) - b - 1) % 16

    for i in reversed(range(_feistel_round_count())):
        q = tweak + bytes(pad_len) + bytes([i]) + _num_radix(a, alphabet).to_bytes(b, "big")
        y = _round_key_stream(key, prefix, q, d)
        m = u if i % 2 == 0 else v
        c = (_num_radix(bb, alphabet) - y) % (radix**m)
        bb, a = a, _str_radix(c, alphabet, m)

    return a + bb
