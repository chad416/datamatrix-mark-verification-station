"""Bookkeeping of the Monte Carlo study."""

import numpy as np
import pytest

from dmx import study


def _pop():
    return {"st_ok": np.array([1.0, 1.0, 1.0, 0.0]), "dpm": np.array([4, 4, 2, 0]),
            "dn_read": np.array([1.0, 1.0, 1 / 3, 0.0]), "v_UEC": np.array([1.0, 1.0, 0.5, np.nan]),
            "v_CC": np.array([0.9, 0.88, 0.6, np.nan])}


def test_rates():
    pop = _pop()
    r = study.rates(pop, study.rule_decode(pop))
    assert r["accept_share"] == 0.75
    assert r["escape_per_marked"] == pytest.approx((2 / 3) / 4)
    assert r["escape_per_released"] == pytest.approx((2 / 3) / 3)
    assert r["false_reject_of_readable"] == 0.0
    r = study.rates(pop, study.rule_grade(pop, 3))
    assert r["escape_per_released"] == 0.0
    assert r["false_reject_of_readable"] == pytest.approx((1 / 3) / (7 / 3))
    assert study.rates(pop, study.rule_grade_uec(pop, 2, 0.8)) == r
    assert study.rates(pop, study.rule_value(pop, "CC", 0.7)) == r
    lim, _ = study.smallest_limit_for_escape(pop, "CC", 0.0)
    assert lim == 0.88


def test_auc():
    bad = np.array([1.0, 1.0, 0.0, 0.0, 0.0])
    assert study.auc(np.array([0.1, 0.2, 0.8, 0.9, 0.7]), bad) == 1.0       # low score = later no-read
    assert study.auc(np.array([0.9, 0.8, 0.1, 0.2, 0.3]), bad) == 0.0
    assert study.auc(np.full(5, 0.5), bad) == 0.5
    assert np.isnan(study.auc(np.array([0.1, 0.2]), np.array([0.0, 0.0])))


def test_process_levels():
    p0, p1 = study.process_at({}, None), study.process_at({k: 1.0 for k in study.DRIFT_FULL}, None)
    assert (p0.focus_offset, p0.energy_factor, p0.window_transmission, p0.paint_thickness) == (0.0, 1.0, 1.0, 20e-6)
    assert p1.focus_offset == pytest.approx(4e-3) and p1.energy_factor == pytest.approx(0.5)
    assert p1.window_transmission == pytest.approx(0.5) and p1.paint_thickness == pytest.approx(40e-6)


def test_run_is_reproducible_and_nominal_parts_read():
    a = study.run(6, 5, mode="fixed", levels={}, workers=1, chunk=3)
    b = study.run(6, 5, mode="fixed", levels={}, workers=1, chunk=3)
    assert all(np.array_equal(a[k], b[k], equal_nan=True) for k in a if a[k].dtype.kind in "fi")
    assert a["st_ok"].all() and (a["dpm"] == 4).all() and a["dn_read"].mean() > 0.9
    dead = study.run(4, 6, mode="fixed", levels={"paint": 1.0, "energy": 1.0}, workers=1)
    assert not dead["st_ok"].any() and dead["dn_read"].max() == 0.0
