"""Forward error correction backends.

* ``rs``   – Reed-Solomon over GF(256) (via :mod:`reedsolo`), with erasure
             support so the demodulator can hand over "I am not sure about
             these bytes" information (soft decision).
* ``ldpc`` – a compact regular LDPC (Gallager construction) with min-sum
             belief propagation, operating on hard bits.
* ``none`` – pass-through, used for benchmark baselines.

A backend exposes ``encode(bytes) -> bytes`` and
``decode(bytes, erasures=None) -> (bytes, n_corrected, ok)``.
"""
from __future__ import annotations

import numpy as np

KIND_BY_ID = {0: "none", 1: "rs", 2: "rs", 3: "rs", 4: "rs", 5: "ldpc", 6: "ldpc"}


# --------------------------------------------------------------------------- #
# Reed-Solomon
# --------------------------------------------------------------------------- #
class ReedSolomon:
    def __init__(self, nsym: int = 16):
        import reedsolo

        self.rs = reedsolo
        self.nsym = int(nsym)
        self.codec = reedsolo.RSCodec(self.nsym)

    # ---- block splitting: reedsolo works on <=255 byte chunks -------------
    def _blocks(self, data: bytes):
        k = 255 - self.nsym
        for i in range(0, len(data), k):
            yield data[i : i + k]

    def encode(self, data: bytes) -> bytes:
        return b"".join(bytes(self.codec.encode(bytes(b))) for b in self._blocks(data))

    def decode(self, data: bytes, erasures: list[int] | None = None):
        out = bytearray()
        ncorr = 0
        ok = True
        n = 255
        k = n - self.nsym
        blk = 0
        for i in range(0, len(data), n):
            chunk = bytes(data[i : i + n])
            blk = i
            er = None
            if erasures:
                er = sorted({p - blk for p in erasures if blk <= p < blk + len(chunk)})
                # reedsolo needs erasures inside the block
                if len(er) >= self.nsym:
                    er = er[: self.nsym - 1]
            try:
                dec, _, errata = self.codec.decode(chunk, erase_pos=er)
                ncorr += len(errata)
                out.extend(dec)
            except Exception:
                ok = False
                # keep whatever we can: strip parity heuristically
                out.extend(chunk[: min(k, len(chunk))])
        return bytes(out), ncorr, ok

    # ---- capacity bookkeeping -------------------------------------------
    def encoded_len(self, k_bytes: int) -> int:
        k = 255 - self.nsym
        n = (k_bytes + k - 1) // k
        return k_bytes + n * self.nsym


# --------------------------------------------------------------------------- #
# LDPC (hard decision, min-sum)
# --------------------------------------------------------------------------- #
class LDPC:
    """Systematic LDPC with a sparse parity-check matrix ``H = [A | I]``.

    ``A`` is a random ``m x k`` binary matrix with column weight ``dv``.  Using
    an identity parity block keeps ``H`` full rank (no Gaussian elimination at
    build time) and makes encoding a single GF(2) matrix-vector product while
    remaining a perfectly valid, decodable LDPC code.  Decoding uses sum-product
    belief propagation on hard bits.
    """

    _cache: dict[tuple[int, int], "LDPC"] = {}

    def __init__(self, n: int = 648, k: int = 324, seed: int = 7, dv: int = 3):
        self.n, self.k = int(n), int(k)
        self.m = self.n - self.k
        A = self._build_A(self.m, self.k, dv, seed)
        self.A = A
        self.H = np.concatenate([A, np.eye(self.m, dtype=np.uint8)], axis=1)
        self.var_neighbors = [np.nonzero(self.H[:, c])[0] for c in range(self.n)]

    @staticmethod
    def _build_A(m: int, k: int, dv: int, seed: int) -> np.ndarray:
        """Quasi-cyclic ``m x k`` parity matrix ``A`` (used as ``H = [A | I]``).

        ``A`` is built from ``z x z`` circulant permutation blocks: a random
        ``dv``-regular base graph of size ``(m/z) x (k/z)`` is expanded with
        random circulant shifts.  The construction is cheap, keeps the graph
        free of trivial weight-2 codewords (no duplicated rows/columns) and
        mirrors how real standards (5G NR, Wi-Fi) build LDPC matrices.
        """
        rng = np.random.default_rng(seed)
        z = 1
        for cand in (27, 24, 16, 12, 9, 8, 6, 4, 3, 2, 1):
            if m % cand == 0 and k % cand == 0:
                z = cand
                break
        mb, kb = m // z, k // z
        n_edges = dv * max(mb, kb)
        for _attempt in range(200):
            # random bipartite base graph: every node gets >= 1 edge
            left = np.arange(n_edges) % mb
            right_pat = np.arange(n_edges) % kb
            for _shuffle in range(2000):
                right = rng.permutation(right_pat)
                pairs = set(zip(left.tolist(), right.tolist()))
                if len(pairs) == n_edges:
                    break
            else:
                continue
            base = {pr: int(rng.integers(z)) for pr in pairs}
            A = np.zeros((m, k), dtype=np.uint8)
            for (i, j), sh in base.items():
                for u in range(z):
                    A[i * z + u, j * z + (u + sh) % z] = 1
            row_keys = {np.packbits(A[r]).tobytes() for r in range(m)}
            col_keys = {np.packbits(A[:, c]).tobytes() for c in range(k)}
            if np.all(A.sum(axis=1)) and np.all(A.sum(axis=0)):
                if len(row_keys) == m and len(col_keys) == k:
                    return A
        raise RuntimeError("could not build a duplicate-free LDPC matrix")

    # ---- bit helpers ----------------------------------------------------
    @staticmethod
    def _bytes_to_bits(data: bytes) -> np.ndarray:
        return np.unpackbits(np.frombuffer(data, dtype=np.uint8))

    @staticmethod
    def _bits_to_bytes(bits: np.ndarray) -> bytes:
        return np.packbits(bits.astype(np.uint8)).tobytes()

    def encode(self, data: bytes) -> bytes:
        bits = self._bytes_to_bits(data)
        blocks = (len(bits) + self.k - 1) // self.k
        padded = np.zeros(blocks * self.k, dtype=np.uint8)
        padded[: len(bits)] = bits
        out = []
        for b in range(blocks):
            info = padded[b * self.k : (b + 1) * self.k]
            par = (self.A @ info) % 2
            out.append(np.concatenate([info, par]))
        return self._bits_to_bytes(np.concatenate(out))

    def decode(self, data: bytes, erasures=None):
        bits = self._bytes_to_bits(data)
        nblk = len(bits) // self.n
        if nblk == 0:
            return b"", 0, False
        out, ok, ncorr = [], True, 0
        erased_pos = set()
        if erasures:
            erased_pos = {p for p in erasures}
        for b in range(nblk):
            y = bits[b * self.n : (b + 1) * self.n].copy()
            x = self._decode_block(y)
            if not self._syndrome_ok(x):
                ok = False
            ncorr += int((x[: self.k] != y[: self.k]).sum())
            out.append(x[: self.k])
        return self._bits_to_bytes(np.concatenate(out)), ncorr, ok

    def _syndrome_ok(self, x: np.ndarray) -> bool:
        return not np.any((self.H @ x) % 2)

    def _decode_block(self, y: np.ndarray, iters: int = 50) -> np.ndarray:
        """Sum-product (tanh) belief propagation, hard bits in / out."""
        L = np.where(y == 0, 3.0, -3.0)
        post = L.copy()
        msg = np.zeros((self.m, self.n))
        rows = [np.nonzero(self.H[r])[0] for r in range(self.m)]
        for _ in range(iters):
            x = (post < 0).astype(np.uint8)
            if self._syndrome_ok(x):
                return x
            new = np.zeros_like(msg)
            for r, cols in enumerate(rows):
                v2c = post[cols] - msg[r, cols]
                t = np.tanh(np.clip(v2c, -12.0, 12.0) / 2.0)
                prod = np.prod(t)
                for j, v in enumerate(cols):
                    tj = t[j]
                    oth = prod / tj if tj != 0 else np.prod(np.delete(t, j))
                    new[r, v] = 2.0 * np.arctanh(np.clip(oth, -0.999999, 0.999999)) * 0.95
            msg = new
            post = L + msg.sum(axis=0)
        return (post < 0).astype(np.uint8)

    @classmethod
    def get(cls, n: int, k: int) -> "LDPC":
        key = (n, k)
        if key not in cls._cache:
            cls._cache[key] = cls(n, k)
        return cls._cache[key]


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #
class NullFEC:
    nsym = 0
    kind = "none"

    def encode(self, data: bytes) -> bytes:
        return bytes(data)

    def decode(self, data: bytes, erasures=None):
        return bytes(data), 0, True

    def encoded_len(self, k: int) -> int:
        return k


def make_fec(profile):
    """Build the FEC backend for an :class:`~optical_modem.config.ECCProfile`."""
    if profile.kind == "rs":
        return ReedSolomon(profile.parity)
    if profile.kind == "ldpc":
        # scale the mother code to a practical block
        return LDPC.get(profile.ldpc_n, profile.ldpc_k)
    return NullFEC()
