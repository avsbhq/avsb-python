"""
MurmurHash3_x86_32 — exact Python port of packages/avsb-core/src/hash.ts.

All arithmetic mirrors the TypeScript version:
  - Characters are read as their code-point (ord()) masked to 8 bits
  - All intermediate products are masked to 32 bits with & 0xFFFFFFFF
  - imul() semantics: signed 32-bit multiply whose result is used as unsigned
  - Final >>> 0 in TS ≡ & 0xFFFFFFFF in Python (both produce unsigned 32-bit)

The function is a pure hash of a Unicode string, treating each character as
its low-byte code-point value (consistent with the TS implementation which uses
charCodeAt & 0xff).  This means multi-byte Unicode characters above U+00FF will
have their high bytes silently dropped — the same behaviour as TS.
"""

from __future__ import annotations

_C1 = 0xCC9E2D51
_C2 = 0x1B873593
_MASK32 = 0xFFFFFFFF


def _imul32(a: int, b: int) -> int:
    """Signed 32-bit integer multiply, result returned as unsigned 32-bit."""
    # Python integers are arbitrary precision; we replicate Math.imul semantics
    # by masking both operands to 32 bits, computing the product, and keeping
    # the low 32 bits (unsigned).
    return ((a & _MASK32) * (b & _MASK32)) & _MASK32


def murmurhash3(key: str, seed: int = 0) -> int:
    """
    MurmurHash3_x86_32.

    Returns an unsigned 32-bit integer.  Accepts a Unicode string and processes
    each character as its low-byte code point (charCodeAt & 0xff) to match the
    TypeScript reference implementation exactly.
    """
    h1 = seed & _MASK32
    length = len(key)
    nblocks = length >> 2  # number of 4-byte blocks

    # ---- Body ----
    for i in range(nblocks):
        i4 = i * 4
        k1 = (
            (ord(key[i4]) & 0xFF)
            | ((ord(key[i4 + 1]) & 0xFF) << 8)
            | ((ord(key[i4 + 2]) & 0xFF) << 16)
            | ((ord(key[i4 + 3]) & 0xFF) << 24)
        )
        k1 &= _MASK32

        k1 = _imul32(k1, _C1)
        k1 = ((k1 << 15) | (k1 >> 17)) & _MASK32
        k1 = _imul32(k1, _C2)

        h1 ^= k1
        h1 = ((h1 << 13) | (h1 >> 19)) & _MASK32
        h1 = (_imul32(h1, 5) + 0xE6546B64) & _MASK32

    # ---- Tail ----
    tail = nblocks * 4
    k1 = 0

    remainder = length & 3
    if remainder == 3:
        k1 ^= (ord(key[tail + 2]) & 0xFF) << 16
    if remainder >= 2:
        k1 ^= (ord(key[tail + 1]) & 0xFF) << 8
    if remainder >= 1:
        k1 ^= ord(key[tail]) & 0xFF
        k1 = _imul32(k1, _C1)
        k1 = ((k1 << 15) | (k1 >> 17)) & _MASK32
        k1 = _imul32(k1, _C2)
        h1 ^= k1

    # ---- Finalization mix (fmix32) ----
    h1 ^= length
    h1 &= _MASK32
    h1 ^= h1 >> 16
    h1 &= _MASK32
    h1 = _imul32(h1, 0x85EBCA6B)
    h1 ^= h1 >> 13
    h1 &= _MASK32
    h1 = _imul32(h1, 0xC2B2AE35)
    h1 ^= h1 >> 16
    h1 &= _MASK32

    return h1
