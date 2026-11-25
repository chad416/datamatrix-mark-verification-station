"""ECC 200 encoder and decoder against published vectors and an independent library."""

import random

import numpy as np
import pytest

from dmx import ecc200

# Published example (docs/SOURCES.md S4): "Wikipedia" in a 16x16 symbol.
WIKI_DATA = [88, 106, 108, 106, 113, 102, 101, 106, 98, 129, 251, 147]
WIKI_ECC = [104, 216, 88, 39, 233, 202, 71, 217, 26, 92, 25, 232]


def test_field_and_generator_polynomial():
    # x^8 + x^5 + x^3 + x^2 + 1 = 301; published g(x) for five check codewords
    assert ecc200.PRIM == 301
    assert ecc200.rs_generator(5) == [1, 62, 111, 15, 48, 228]
    for a in (1, 2, 77, 200, 255):
        assert ecc200.gf_mul(a, ecc200.gf_inv(a)) == 1


def test_published_codewords():
    enc = ecc200.encode("Wikipedia", 16)
    assert enc.codewords[:12] == WIKI_DATA          # ASCII values + 1, pad 129, two randomised pads
    assert enc.codewords[12:] == WIKI_ECC


def test_digit_pairs_and_padding():
    assert ecc200.encode_ascii("0099") == [130, 229]
    assert ecc200.encode_ascii("A1") == [66, 50]
    assert ecc200.decode_ascii(ecc200.pad(ecc200.encode_ascii("12345X"), 12)) == "12345X"


@pytest.mark.parametrize("size", sorted(ecc200.SIZES))
def test_round_trip_and_error_correction(size):
    sz = ecc200.SIZES[size]
    rnd = random.Random(size)
    text = "".join(rnd.choice("ABCDEFGHxyz0123456789 -/") for _ in range(sz.n_data // 2 + 1))
    enc = ecc200.encode(text, size)
    assert enc.matrix.shape == (size, size)
    assert len(enc.codewords) == sz.n_data + sz.n_ecc
    assert ecc200.matrix_to_codewords(enc.matrix, sz) == enc.codewords
    d = ecc200.decode(enc.matrix)
    assert d.ok and d.text == text and d.errors == 0 and d.uec == 1.0
    t = sz.n_ecc // 2
    for trial in range(5):
        cw = list(enc.codewords)
        for p in rnd.sample(range(len(cw)), t):
            cw[p] ^= rnd.randrange(1, 256)
        d = ecc200.decode(ecc200.codewords_to_matrix(cw, sz))
        assert d.ok and d.text == text and d.errors == t
        assert d.uec == pytest.approx(1.0 - 2.0 * t / sz.n_ecc)
        cw = list(enc.codewords)
        for p in rnd.sample(range(len(cw)), t + 1):          # one more than can be corrected
            cw[p] ^= rnd.randrange(1, 256)
        d = ecc200.decode(ecc200.codewords_to_matrix(cw, sz))
        assert not (d.ok and d.text == text)


def test_finder_and_clock_track():
    m = ecc200.encode("8370205117000012345", 16).matrix
    assert m[:, 0].all() and m[-1, :].all()                   # solid "L": left column and bottom row
    assert list(m[0, :]) == [True, False] * 8                 # top clock track
    assert list(m[:, -1]) == [False, True] * 8                # right clock track


def test_every_module_belongs_to_one_codeword_bit():
    for size, sz in ecc200.SIZES.items():
        pl = ecc200.placement(sz.mapping)
        used = pl[pl >= 0]
        assert len(used) == len(set(used.tolist())) == 8 * (sz.n_data + sz.n_ecc), size


def test_unsupported_content_is_refused():
    with pytest.raises(ValueError):
        ecc200.encode("x" * 200)
    cw = ecc200.pad([231, 5, 66], 12)                         # 231 latches to Base 256: not implemented
    full = cw + ecc200.rs_encode(cw, 12)
    assert not ecc200.decode(ecc200.codewords_to_matrix(full, ecc200.SIZES[16])).ok


# --- independent library, used only here -----------------------------------
zx = pytest.importorskip("zxingcpp")


def _image(matrix, quiet=4, scale=8):
    n = matrix.shape[0]
    img = np.full(((n + 2 * quiet) * scale,) * 2, 255, np.uint8)
    img[quiet * scale:(quiet + n) * scale, quiet * scale:(quiet + n) * scale] = np.kron(
        np.where(matrix, 0, 255).astype(np.uint8), np.ones((scale, scale), np.uint8))
    return img


@pytest.mark.parametrize("size", sorted(ecc200.SIZES))
def test_independent_reader_decodes_our_symbols(size):
    sz = ecc200.SIZES[size]
    rnd = random.Random(100 + size)
    text = "".join(rnd.choice("ABCDEFGHxyz0123456789 -/") for _ in range(sz.n_data // 2 + 1))
    res = zx.read_barcodes(_image(ecc200.encode(text, size).matrix), formats=zx.BarcodeFormat.DataMatrix)
    assert res and res[0].text == text


def test_our_decoder_reads_the_independent_writer():
    if not hasattr(zx, "create_barcode"):
        pytest.skip("this zxing-cpp version has no create_barcode")
    for text in ("8370205117000012345", "0000000000000000000", "9999999999999999999", "83702052040000000017"):
        bc = zx.create_barcode(text, zx.BarcodeFormat.DataMatrix, force_square=True)
        m = np.array(zx.write_barcode_to_image(bc, scale=1, add_quiet_zones=False)) < 128
        d = ecc200.decode(m)
        assert d.ok and d.text == text
        assert (ecc200.encode(text, m.shape[0]).matrix == m).all()      # same symbol, module for module
