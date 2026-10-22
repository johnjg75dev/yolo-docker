"""Symbol masking ("whitening") for the coded frame.

Why
---
A frame's payload block is always the same size, so a short payload leaves the
tail zero-padded: ``k`` identical dark tiles in a row, and often hundreds of
them.  That is bad three times over:

* it wastes the most visible real estate on the screen on *nothing*;
* it breaks the receiver's *local* statistics — a background estimate, a
  vignetting model or a per-tile gain estimated on a dark slab is wrong for the
  rest of the frame (this was a real bug: a median-based background estimate
  landed on the foreground because most of the frame was padding);
* it makes the frame look obviously "half empty" to a human, which is exactly
  what cimbar-style codes avoid.

Masking fixes all three: every symbol value is XORed with a deterministic
pseudo-random value before it is drawn, so the *same* byte sequence produces a
uniformly busy frame and the zero tail looks like data.  XOR is self-inverse, so
the receiver applies the same mask.

Families
--------
Three cheap masks are provided, selected by a 2-bit family id:

``NONE``   no mask (diagnostics / A-B comparisons)
``PRNG``   SplitMix64 stream seeded from the header (the default)
``ALT``    alternating-value mask, ``(i + j) & 1`` selects one of two values
``RAMP``   index ramp, ``(7*i + 13*j + seed) mod n_values``

``PRNG`` gives the flat statistics; ``ALT``/``RAMP`` are free (no generator) and
still break up flat areas, which is enough when a device cannot afford even
that.  All three are *pure functions of the header*, so no side channel is
needed.

Random access / determinism
---------------------------
The generator is a hand-written SplitMix64 rather than ``numpy.random``: the
mask must be reproducible bit-for-bit by any receiver implementation and by any
future numpy version.  SplitMix64 is nine lines of integer arithmetic.
"""
from __future__ import annotations

import numpy as np

MASK_NONE, MASK_PRNG, MASK_ALT, MASK_RAMP = 0, 1, 2, 3
MASK_FAMILIES = {MASK_NONE: "none", MASK_PRNG: "prng", MASK_ALT: "alternating",
                 MASK_RAMP: "ramp"}

#: seed used for the *header* block.  The header has to be readable before the
#: mask byte inside it is known, so its mask is a public constant — the point is
#: only to keep the header rows from being a flat dark bar.
HEADER_MASK_BYTE = (MASK_PRNG << 6) | 0x2A
#: default for payload blocks: PRNG with a per-frame seed of 0
DEFAULT_MASK_BYTE = (MASK_PRNG << 6) | 0x00

_MASK64 = (1 << 64) - 1


def splitmix64(seed: int, n: int) -> np.ndarray:
    """First ``n`` outputs of a SplitMix64 stream as uint64."""
    x = np.uint64(seed & _MASK64)
    out = np.empty(n, dtype=np.uint64)
    for i in range(n):
        x = np.uint64((int(x) + 0x9E3779B97F4A7C15) & _MASK64)
        z = int(x)
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK64
        out[i] = np.uint64(z ^ (z >> 31))
    return out


def mask_values(mask_byte: int, positions, n_values: int) -> np.ndarray:
    """Mask values for symbols at ``positions`` (an ``(n, 2)`` array of i, j).

    Returns uint16 in ``[0, n_values)``; XOR it into the symbol values, or out
    again — it is self-inverse.
    """
    family = (int(mask_byte) >> 6) & 0x3
    seed = int(mask_byte) & 0x3F
    pos = np.asarray(positions, np.int32)
    n = len(pos)
    n_values = max(2, int(n_values))
    if family == MASK_NONE or n == 0:
        return np.zeros(n, np.uint16)
    if family == MASK_ALT:
        step = max(1, n_values // 2)
        vals = step * ((pos[:, 0] + pos[:, 1]) & 1)
    elif family == MASK_RAMP:
        vals = (7 * pos[:, 0] + 13 * pos[:, 1] + seed) % n_values
    else:
        # one 64-bit draw per symbol, folded into the value range.  A modulo
        # bias of at most n_values / 2**64 is irrelevant here.
        r = splitmix64((seed << 32) ^ 0xC0FFEE, n)
        vals = (r % np.uint64(n_values)).astype(np.uint16)
    return np.asarray(vals, np.uint16) % np.uint16(n_values)


def mask_info(mask_byte: int) -> str:
    family = (int(mask_byte) >> 6) & 0x3
    return f"{MASK_FAMILIES.get(family, '?')}(seed={int(mask_byte) & 0x3F})"
