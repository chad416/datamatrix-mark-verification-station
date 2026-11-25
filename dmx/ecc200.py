"""Data Matrix ECC 200 encoder and decoder (ISO/IEC 16022 symbol structure).

Written from the public description of the symbology (docs/SOURCES.md, S4):
finder "L" and alternating clock track, ASCII encodation, Reed-Solomon over
GF(256) with field polynomial x^8+x^5+x^3+x^2+1 (301) and first root alpha^1,
and the diagonal codeword placement with its four corner cases.

Scope (stated, not hidden):
* square symbols 10x10 to 26x26 (one data region) and 32x32 to 48x48 (four
  data regions), all of which use a single Reed-Solomon block;
* ASCII encodation only (ASCII values, digit pairs, pad). Latches to C40, Text,
  X12, EDIFACT and Base 256 are not implemented: the decoder reports them as
  an unsupported codeword instead of guessing;
* errors-only decoding (no erasures).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# --------------------------------------------------------------------------
# GF(256), field polynomial 301 = 0x12D
# --------------------------------------------------------------------------
PRIM = 0x12D
EXP = [0] * 512
LOG = [0] * 256
_x = 1
for _i in range(255):
    EXP[_i] = _x
    LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= PRIM
for _i in range(255, 512):
    EXP[_i] = EXP[_i - 255]


def gf_mul(a: int, b: int) -> int:
    return 0 if a == 0 or b == 0 else EXP[LOG[a] + LOG[b]]


def gf_div(a: int, b: int) -> int:
    if b == 0:
        raise ZeroDivisionError("division by zero in GF(256)")
    return 0 if a == 0 else EXP[(LOG[a] - LOG[b]) % 255]


def gf_inv(a: int) -> int:
    return EXP[255 - LOG[a]]


def poly_eval(p: list[int], x: int) -> int:
    """p[0] is the highest-order coefficient."""
    y = 0
    for c in p:
        y = gf_mul(y, x) ^ c
    return y


def rs_generator(n_ecc: int) -> list[int]:
    """g(x) = (x - a^1)(x - a^2)...(x - a^n), highest order first."""
    g = [1]
    for i in range(1, n_ecc + 1):
        root = EXP[i]
        g = [a ^ gf_mul(b, root) for a, b in zip(g + [0], [0] + g)]
    return g


def rs_encode(data: list[int], n_ecc: int) -> list[int]:
    g = rs_generator(n_ecc)
    rem = [0] * n_ecc
    for d in data:
        fb = d ^ rem[0]
        rem = rem[1:] + [0]
        if fb:
            for j in range(n_ecc):
                rem[j] ^= gf_mul(g[j + 1], fb)
    return rem


class RSError(Exception):
    pass


def rs_decode(code: list[int], n_ecc: int) -> tuple[list[int], int]:
    """Correct up to n_ecc // 2 codeword errors. Returns (corrected codeword list, errors)."""
    n = len(code)
    synd = [poly_eval(code, EXP[i]) for i in range(1, n_ecc + 1)]
    if not any(synd):
        return list(code), 0
    # Berlekamp-Massey: error locator sigma(x), lowest order first
    sigma, prev = [1], [1]
    L, m, b = 0, 1, 1
    for r in range(n_ecc):
        d = synd[r]
        for i in range(1, L + 1):
            if i < len(sigma):
                d ^= gf_mul(sigma[i], synd[r - i])
        if d == 0:
            m += 1
            continue
        coef = gf_div(d, b)
        cand = sigma + [0] * max(0, len(prev) + m - len(sigma))
        for i, c in enumerate(prev):
            cand[i + m] ^= gf_mul(coef, c)
        if 2 * L <= r:
            prev, b, L, m = sigma, d, r + 1 - L, 1
        else:
            m += 1
        sigma = cand
    while len(sigma) > 1 and sigma[-1] == 0:
        sigma.pop()
    v = len(sigma) - 1
    if v != L or v == 0 or 2 * v > n_ecc:
        raise RSError("too many errors")
    # Chien search: position p (power of x, 0 = last codeword) is in error if sigma(a^-p) = 0
    pos = [p for p in range(n) if poly_eval(sigma[::-1], EXP[(255 - p) % 255]) == 0]
    if len(pos) != v:
        raise RSError("error locator has wrong number of roots")
    # error values from the first v syndromes: sum_j e_j X_j^i = S_i, i = 1..v
    X = [EXP[p] for p in pos]
    A = [[EXP[(LOG[x] * i) % 255] for x in X] + [synd[i - 1]] for i in range(1, v + 1)]
    for c in range(v):
        piv = next((r for r in range(c, v) if A[r][c]), None)
        if piv is None:
            raise RSError("singular error system")
        A[c], A[piv] = A[piv], A[c]
        inv = gf_inv(A[c][c])
        A[c] = [gf_mul(a, inv) for a in A[c]]
        for r in range(v):
            if r != c and A[r][c]:
                f = A[r][c]
                A[r] = [a ^ gf_mul(f, bb) for a, bb in zip(A[r], A[c])]
    out = list(code)
    for p, row in zip(pos, A):
        out[n - 1 - p] ^= row[v]
    if any(poly_eval(out, EXP[i]) for i in range(1, n_ecc + 1)):
        raise RSError("correction failed the syndrome check")
    return out, v


# --------------------------------------------------------------------------
# symbol attributes: size -> (data region size, regions per side, data cw, ecc cw)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class SymbolSize:
    size: int
    region: int
    regions: int
    n_data: int
    n_ecc: int

    @property
    def mapping(self) -> int:
        return self.region * self.regions


SIZES = {s.size: s for s in (
    SymbolSize(10, 8, 1, 3, 5), SymbolSize(12, 10, 1, 5, 7), SymbolSize(14, 12, 1, 8, 10),
    SymbolSize(16, 14, 1, 12, 12), SymbolSize(18, 16, 1, 18, 14), SymbolSize(20, 18, 1, 22, 18),
    SymbolSize(22, 20, 1, 30, 20), SymbolSize(24, 22, 1, 36, 24), SymbolSize(26, 24, 1, 44, 28),
    SymbolSize(32, 14, 2, 62, 36), SymbolSize(36, 16, 2, 86, 42), SymbolSize(40, 18, 2, 114, 48),
    SymbolSize(44, 20, 2, 144, 56), SymbolSize(48, 22, 2, 174, 68),
)}


# --------------------------------------------------------------------------
# ASCII encodation
# --------------------------------------------------------------------------
def encode_ascii(text: str) -> list[int]:
    cw: list[int] = []
    i = 0
    while i < len(text):
        if i + 1 < len(text) and text[i].isdigit() and text[i + 1].isdigit() and text[i:i + 2].isascii():
            cw.append(130 + int(text[i:i + 2]))
            i += 2
        else:
            o = ord(text[i])
            if o > 127:
                raise ValueError("only 7-bit ASCII is supported")
            cw.append(o + 1)
            i += 1
    return cw


def pad(cw: list[int], n_data: int) -> list[int]:
    """First pad is 129; later pads are randomised with the 253-state algorithm."""
    out = list(cw)
    if len(out) < n_data:
        out.append(129)
    while len(out) < n_data:
        r = ((149 * (len(out) + 1)) % 253) + 1
        v = 129 + r
        out.append(v if v <= 254 else v - 254)
    return out


def decode_ascii(cw: list[int]) -> str:
    out = []
    for c in cw:
        if c == 129:
            break
        if 1 <= c <= 128:
            out.append(chr(c - 1))
        elif 130 <= c <= 229:
            out.append(f"{c - 130:02d}")
        else:
            raise ValueError(f"codeword {c} needs an encodation scheme that is not implemented")
    return "".join(out)


def pick_size(n_cw: int, min_size: int = 10) -> SymbolSize:
    for s in sorted(SIZES):
        if s >= min_size and SIZES[s].n_data >= n_cw:
            return SIZES[s]
    raise ValueError("message too long for the supported symbol sizes")


# --------------------------------------------------------------------------
# codeword placement in the mapping matrix
# --------------------------------------------------------------------------
def placement(n: int) -> np.ndarray:
    """Mapping matrix n x n. Entry = 8 * codeword_index + bit (bit 0 = MSB), -1 = fixed
    light, -2 = fixed dark (the unused lower-right corner pattern)."""
    arr = np.full((n, n), -9, dtype=int)

    def module(r, c, k, bit):
        if r < 0:
            r += n
            c += 4 - ((n + 4) % 8)
        if c < 0:
            c += n
            r += 4 - ((n + 4) % 8)
        arr[r, c] = 8 * k + bit

    def utah(r, c, k):
        for bit, (dr, dc) in enumerate(((-2, -2), (-2, -1), (-1, -2), (-1, -1), (-1, 0), (0, -2), (0, -1), (0, 0))):
            module(r + dr, c + dc, k, bit)

    corners = {
        1: ((n - 1, 0), (n - 1, 1), (n - 1, 2), (0, n - 2), (0, n - 1), (1, n - 1), (2, n - 1), (3, n - 1)),
        2: ((n - 3, 0), (n - 2, 0), (n - 1, 0), (0, n - 4), (0, n - 3), (0, n - 2), (0, n - 1), (1, n - 1)),
        3: ((n - 3, 0), (n - 2, 0), (n - 1, 0), (0, n - 2), (0, n - 1), (1, n - 1), (2, n - 1), (3, n - 1)),
        4: ((n - 1, 0), (n - 1, n - 1), (0, n - 3), (0, n - 2), (0, n - 1), (1, n - 3), (1, n - 2), (1, n - 1)),
    }

    def corner(which, k):
        for bit, (r, c) in enumerate(corners[which]):
            module(r, c, k, bit)

    k, r, c = 0, 4, 0
    while True:
        if r == n and c == 0:
            corner(1, k)
            k += 1
        if r == n - 2 and c == 0 and n % 4:
            corner(2, k)
            k += 1
        if r == n - 2 and c == 0 and n % 8 == 4:
            corner(3, k)
            k += 1
        if r == n + 4 and c == 2 and not n % 8:
            corner(4, k)
            k += 1
        while True:
            if r < n and c >= 0 and arr[r, c] == -9:
                utah(r, c, k)
                k += 1
            r -= 2
            c += 2
            if not (r >= 0 and c < n):
                break
        r += 1
        c += 3
        while True:
            if r >= 0 and c < n and arr[r, c] == -9:
                utah(r, c, k)
                k += 1
            r += 2
            c -= 2
            if not (r < n and c >= 0):
                break
        r += 3
        c += 1
        if not (r < n or c < n):
            break
    if arr[n - 1, n - 1] == -9:
        arr[n - 1, n - 1] = arr[n - 2, n - 2] = -2
        arr[n - 1, n - 2] = arr[n - 2, n - 1] = -1
    return arr


_PLACEMENT: dict[int, np.ndarray] = {}


def _place(n: int) -> np.ndarray:
    if n not in _PLACEMENT:
        _PLACEMENT[n] = placement(n)
    return _PLACEMENT[n]


def _mapping_coords(sz: SymbolSize) -> tuple[np.ndarray, np.ndarray]:
    """Symbol row/column of every mapping-matrix row/column (skips finder and clock lines)."""
    idx = np.arange(sz.mapping)
    return idx + 1 + 2 * (idx // sz.region), idx + 1 + 2 * (idx // sz.region)


def codewords_to_matrix(cw: list[int], sz: SymbolSize) -> np.ndarray:
    """Full symbol, True = dark. Row 0 is the top row."""
    pl = _place(sz.mapping)
    bits = np.zeros((sz.mapping, sz.mapping), dtype=bool)
    for r in range(sz.mapping):
        for c in range(sz.mapping):
            v = pl[r, c]
            bits[r, c] = (v == -2) if v < 0 else bool((cw[v // 8] >> (7 - v % 8)) & 1)
    sym = np.zeros((sz.size, sz.size), dtype=bool)
    step = sz.region + 2
    for k in range(sz.regions):
        o = k * step
        sym[:, o] = True                      # left finder bar of each region
        sym[o + step - 1, :] = True           # bottom finder bar of each region
    for k in range(sz.regions):
        o = k * step
        sym[o, 0::2] = True                   # top clock track
        sym[1::2, o + step - 1] = True        # right clock track
    for k in range(sz.regions):               # finder bars win over clock tracks where they cross
        o = k * step
        sym[:, o] = True
        sym[o + step - 1, :] = True
    rr, cc = _mapping_coords(sz)
    sym[np.ix_(rr, cc)] = bits
    return sym


def matrix_to_codewords(sym: np.ndarray, sz: SymbolSize) -> list[int]:
    pl = _place(sz.mapping)
    rr, cc = _mapping_coords(sz)
    bits = np.asarray(sym, dtype=bool)[np.ix_(rr, cc)]
    cw = [0] * (sz.n_data + sz.n_ecc)
    for r in range(sz.mapping):
        for c in range(sz.mapping):
            v = pl[r, c]
            if v >= 0 and bits[r, c]:
                cw[v // 8] |= 1 << (7 - v % 8)
    return cw


def module_codeword_index(sz: SymbolSize) -> np.ndarray:
    """Codeword index of every symbol module (-1 for finder, clock and fixed modules)."""
    pl = _place(sz.mapping)
    out = np.full((sz.size, sz.size), -1, dtype=int)
    rr, cc = _mapping_coords(sz)
    out[np.ix_(rr, cc)] = np.where(pl >= 0, pl // 8, -1)
    return out


def fixed_pattern(sz: SymbolSize) -> tuple[np.ndarray, np.ndarray]:
    """(is_fixed, expected_dark) for finder and clock modules."""
    blank = codewords_to_matrix([0] * (sz.n_data + sz.n_ecc), sz)
    return module_codeword_index(sz) < 0, blank


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------
@dataclass
class Encoded:
    text: str
    size: SymbolSize
    codewords: list[int]
    matrix: np.ndarray


def encode(text: str, size: int | None = None) -> Encoded:
    cw = encode_ascii(text)
    sz = SIZES[size] if size else pick_size(len(cw))
    if len(cw) > sz.n_data:
        raise ValueError(f"{len(cw)} codewords do not fit a {sz.size}x{sz.size} symbol")
    data = pad(cw, sz.n_data)
    full = data + rs_encode(data, sz.n_ecc)
    return Encoded(text, sz, full, codewords_to_matrix(full, sz))


@dataclass
class Decoded:
    ok: bool
    text: str = ""
    errors: int = 0            # codewords corrected by Reed-Solomon
    raw: list[int] | None = None
    corrected: list[int] | None = None
    reason: str = ""

    @property
    def uec(self) -> float:
        """Unused error correction, 1 - 2 t / n_ecc (errors only, no erasures)."""
        if not self.ok or self.raw is None or self.corrected is None:
            return 0.0
        return 1.0 - 2.0 * self.errors / self._n_ecc

    _n_ecc: int = 1


def decode(sym: np.ndarray) -> Decoded:
    sym = np.asarray(sym, dtype=bool)
    if sym.shape[0] != sym.shape[1] or sym.shape[0] not in SIZES:
        return Decoded(False, reason="unsupported symbol size")
    sz = SIZES[sym.shape[0]]
    raw = matrix_to_codewords(sym, sz)
    try:
        fixed, n_err = rs_decode(raw, sz.n_ecc)
        text = decode_ascii(fixed[:sz.n_data])
    except (RSError, ValueError) as e:
        return Decoded(False, raw=raw, reason=str(e), _n_ecc=sz.n_ecc)
    return Decoded(True, text, n_err, raw, fixed, _n_ecc=sz.n_ecc)
