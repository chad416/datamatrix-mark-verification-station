"""Mark physics against closed forms; reader and grading against the published tables."""

from dataclasses import replace

import numpy as np
import pytest

from dmx import ecc200, grading, mark, reader, study

ENC = ecc200.encode(study.CONTENT, study.N_SYM)
GEO = mark.Geometry()


def test_gaussian_beam_relations():
    laser = mark.Laser()
    w0, zr = mark.waist(laser), mark.rayleigh(laser)
    assert w0 == pytest.approx(laser.m2 * laser.wavelength * laser.focal / (np.pi * laser.w_in))
    assert float(mark.spot_radius(laser, zr)) == pytest.approx(np.sqrt(2.0) * w0)
    assert float(mark.spot_radius(laser, -zr)) == pytest.approx(np.sqrt(2.0) * w0)
    ideal = replace(laser, m2=1.0)
    assert mark.rayleigh(ideal) == pytest.approx(np.pi * mark.waist(ideal) ** 2 / laser.wavelength)
    # a worse beam focuses to a larger spot but the far field (w0 / zR) grows with M^2 as well
    assert mark.waist(laser) / mark.rayleigh(laser) == pytest.approx(laser.m2 * laser.wavelength / (np.pi * mark.waist(laser)))


def test_ablated_diameter_follows_the_d2_relation():
    w, fth = 30e-6, 1.0e4
    e_th = fth * np.pi * w ** 2 / 2.0                       # pulse energy at threshold
    assert float(mark.ablated_diameter(e_th * 0.99, w, fth)) == 0.0
    d1 = float(mark.ablated_diameter(e_th * np.e, w, fth))
    d2 = float(mark.ablated_diameter(e_th * np.e ** 2, w, fth))
    assert d1 ** 2 == pytest.approx(2.0 * w ** 2)           # D^2 = 2 w^2 ln(E / Eth)
    assert d2 ** 2 == pytest.approx(2.0 * d1 ** 2)


def test_crater_volume_and_fill_depth():
    laser, paint = mark.Laser(), mark.Paint()
    w = mark.waist(laser)
    k = mark._crater_kernel(laser.pulse_energy, w, paint, GEO.px)
    ln0 = np.log(float(mark.peak_fluence(laser.pulse_energy, w)) / paint.f_th)
    volume = 0.5 * np.pi * (w ** 2 * ln0 / 2.0) * paint.delta * ln0      # paraboloid: 1/2 * area * peak depth
    assert k.sum() == pytest.approx(volume, rel=0.03)
    # the centre of a large filled area reaches the closed-form fill depth
    full = np.ones((16, 16), dtype=bool)
    m = mark.make_mark(full, replace(mark.nominal_process(), paint_thickness=1.0), rng=np.random.default_rng(0))
    assert m.info["fill_depth_um"] == pytest.approx(float(mark.mean_fill_depth(laser.pulse_energy, w, laser, paint)) * 1e6)
    assert m.exposed.max() < 1e-6                           # a 1 m thick "paint" is never opened


def test_nominal_mark_reads_and_grades():
    rng = np.random.default_rng(3)
    m = mark.make_mark(ENC.matrix, mark.nominal_process(), rng=rng)
    rs = reader.read(reader.acquire(m.reflectance, GEO, reader.STATION, rng), 16, reader.STATION)
    g = grading.grade(rs, 16)
    assert rs.ok and rs.text == study.CONTENT
    assert g.dpm == 4 and g.grades["CC"] == 4 and g.grades["UEC"] == 4
    assert g.grades["SC"] <= 2          # painted part: symbol contrast is capped by the paint reflectance
    assert abs(g.values["PG"]) < 0.08 and g.values["AN"] < 0.02
    dn = reader.read(reader.acquire(m.reflectance, GEO, reader.DOWNSTREAM, rng), 16, reader.DOWNSTREAM)
    assert dn.ok and dn.text == study.CONTENT


def test_far_out_of_focus_is_not_readable():
    rng = np.random.default_rng(4)
    m = mark.make_mark(ENC.matrix, replace(mark.nominal_process(), focus_offset=5e-3), rng=rng)
    rs = reader.read(reader.acquire(m.reflectance, GEO, reader.STATION, rng), 16, reader.STATION)
    assert not rs.ok and grading.grade(rs, 16).dpm == 0


def test_contrast_falls_when_the_paint_is_not_opened():
    rng = np.random.default_rng(5)
    cc = []
    for thick in (20e-6, 38e-6, 42e-6):
        m = mark.make_mark(ENC.matrix, replace(mark.nominal_process(), paint_thickness=thick), rng=rng)
        rs = reader.read(reader.acquire(m.reflectance, GEO, reader.STATION, rng), 16, reader.STATION)
        cc.append(grading.grade(rs, 16).values.get("CC", 0.0))
    assert cc[0] > cc[1] > cc[2]


def test_grade_tables_as_published():
    # ISO/IEC 15415 tables and the ISO/IEC 29158 cell-contrast table as printed in S2 and S3
    assert [grading.grade_ge(v, grading.SC_T) for v in (0.70, 0.69, 0.55, 0.40, 0.20, 0.19)] == [4, 3, 3, 2, 1, 0]
    assert [grading.grade_ge(v, grading.MOD_T) for v in (0.50, 0.40, 0.30, 0.20, 0.19)] == [4, 3, 2, 1, 0]
    assert [grading.grade_ge(v, grading.UEC_T) for v in (0.62, 0.50, 0.37, 0.25, 0.24)] == [4, 3, 2, 1, 0]
    assert [grading.grade_ge(v, grading.CC_T) for v in (0.30, 0.25, 0.20, 0.15, 0.14)] == [4, 3, 2, 1, 0]
    assert [grading.grade_le(v, grading.AN_T) for v in (0.06, 0.08, 0.10, 0.12, 0.13)] == [4, 3, 2, 1, 0]
    assert [grading.grade_le(v, grading.GN_T) for v in (0.38, 0.50, 0.63, 0.75, 0.76)] == [4, 3, 2, 1, 0]
    assert [grading.grade_le(v, grading.DMG_T) for v in (0.0, 0.09, 0.13, 0.17, 0.18)] == [4, 3, 2, 1, 0]
    assert [grading.grade_ge(v, grading.AG_T) for v in (4.0, 3.5, 3.0, 2.5, 2.4)] == [4, 3, 2, 1, 0]


def _ideal_read(flip=()):
    """A perfect image (light 0.55, dark 0.06) with some modules painted the wrong way."""
    m = ENC.matrix.copy()
    for r, c in flip:
        m[r, c] = ~m[r, c]
    scene = np.full((22 * 10, 22 * 10), 0.55)
    scene[30:190, 30:190] = np.where(np.kron(m, np.ones((10, 10), dtype=bool)), 0.06, 0.55)
    return reader.read(scene, 16, reader.STATION)


def test_grading_of_a_perfect_and_a_damaged_symbol():
    g = grading.grade(_ideal_read(), 16)
    assert g.dpm == 4 and g.values["UEC"] == 1.0
    assert g.values["CC"] == pytest.approx((0.55 - 0.06) / 0.55, abs=0.01)
    assert g.values["SC"] == pytest.approx(0.49, abs=0.01) and g.grades["SC"] == 2
    assert g.values["GN"] < 0.02 and abs(g.values["PG"]) < 0.02
    idx = ecc200.module_codeword_index(ecc200.SIZES[16])
    flips = []
    for k in (0, 5, 11, 17):                                   # one wrong module in each of four codewords
        r, c = np.argwhere(idx == k)[0]
        flips.append((int(r), int(c)))
    g = grading.grade(_ideal_read(flips), 16)
    assert g.values["UEC"] == pytest.approx(1.0 - 2.0 * 4 / 12)   # 0.33: grade 1
    assert g.grades["UEC"] == 1 and g.dpm <= 1
    # a damaged finder bar lowers the fixed-pattern grade, not the error correction
    g = grading.grade(_ideal_read([(4, 0), (5, 0), (9, 0)]), 16)
    assert g.values["UEC"] == 1.0 and g.grades["FPD"] < 4 and g.values["FPD_segments"]["LLS"] < 4


def test_minimum_variance_threshold():
    v = np.r_[np.full(50, 0.1), np.full(70, 0.5)] + np.random.default_rng(0).normal(0, 0.01, 120)
    assert 0.15 < reader.minvar_threshold(v) < 0.45
    assert reader.global_threshold(np.array([0.1, 0.5, 0.2]), "midrange") == pytest.approx(0.3)
