"""Symbol quality grading in the sense of ISO/IEC 15415 and ISO/IEC 29158 (AIM DPM).

Neither standard was read in full (both are paywalled). The formulas and grade
thresholds below are the ones printed in public reader-vendor documentation
(docs/SOURCES.md, S2 and S3). The table in DOSSIER.md section 7.3 lists, for each
parameter, what is implemented as published, what is simplified and what is
missing. The result is an in-line quality indication, not a formal verification.

Grades are 4 (A) to 0 (F). Values are fractions (0.30 = 30 %).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import ecc200
from .reader import Read, _crossings

# thresholds for grades 4, 3, 2, 1 (S2: 15415 tables, S3: cell contrast)
SC_T = (0.70, 0.55, 0.40, 0.20)          # symbol contrast, >=
MOD_T = (0.50, 0.40, 0.30, 0.20)         # modulation, >=  (also used for cell modulation, see DOSSIER 7.3)
UEC_T = (0.62, 0.50, 0.37, 0.25)         # unused error correction, >=
CC_T = (0.30, 0.25, 0.20, 0.15)          # cell contrast, >=
AN_T = (0.06, 0.08, 0.10, 0.12)          # axial non-uniformity, <=
GN_T = (0.38, 0.50, 0.63, 0.75)          # grid non-uniformity, <=
DMG_T = (0.0, 0.09, 0.13, 0.17)          # share of incorrect modules in a fixed-pattern segment, <=
AG_T = (4.0, 3.5, 3.0, 2.5)              # average grade of the fixed-pattern segments, >=


def grade_ge(v: float, t) -> int:
    return next((4 - i for i, lim in enumerate(t) if v >= lim), 0)


def grade_le(v: float, t) -> int:
    return next((4 - i for i, lim in enumerate(t) if v <= lim), 0)


def _mod_grades(v: np.ndarray) -> np.ndarray:
    g = np.zeros(v.shape, dtype=int)
    for lim in reversed(MOD_T):
        g += v >= lim
    return g


def _codeword_level_grade(module_grade: np.ndarray, cw_index: np.ndarray, n_ecc: int, errors: int) -> int:
    """Highest level whose low-modulation codewords still leave a notional UEC grade of that level (S2)."""
    cw_min = {}
    for g, k in zip(module_grade.ravel(), cw_index.ravel()):
        if k >= 0:
            cw_min[k] = min(cw_min.get(k, 4), int(g))
    best = 0
    for level in (4, 3, 2, 1):
        bad = sum(1 for g in cw_min.values() if g < level)
        notional = 1.0 - 2.0 * max(bad, errors) / n_ecc
        best = max(best, min(level, grade_ge(notional, UEC_T)))
    return best


def _segment_grade(module_grade: np.ndarray, clock: bool) -> int:
    best = 0
    for level in (4, 3, 2, 1):
        bad = module_grade < level
        g = grade_le(float(bad.mean()), DMG_T)
        if clock and len(bad) >= 5:      # S2: three of every five consecutive clock modules must be correct
            if np.convolve(bad.astype(int), np.ones(5, dtype=int), mode="valid").max() > 2:
                g = 0
        best = max(best, min(level, g))
    return best


@dataclass
class Grade:
    decode: int = 0
    values: dict = field(default_factory=dict)
    grades: dict = field(default_factory=dict)
    dpm: int = 0            # min of decode, CC, CM, FPD, UEC, AN, GN   (ISO/IEC 29158 style)
    iso15415: int = 0       # min of decode, SC, MOD, FPD, UEC, AN, GN  (ISO/IEC 15415 style)
    limiting: str = "decode"


def _track(profile: np.ndarray, t: float, start: float, pitch: float, dark_modules) -> tuple[float, float]:
    """(worst centre deviation, mean dark width) of the dark clock modules, in modules."""
    cr = _crossings(profile, t)
    dev, widths = 0.0, []
    for m in dark_modules:
        c = start + (m + 0.5) * pitch
        left, right = cr[cr < c], cr[cr > c]
        if not len(left) or not len(right) or right[0] - left[-1] > 2.5 * pitch:
            return 1.0, float("nan")
        a, b = left[-1], right[0]
        dev = max(dev, abs(0.5 * (a + b) - c) / pitch)
        widths.append((b - a) / pitch)
    return dev, float(np.mean(widths)) if widths else float("nan")


def grade(rd: Read, n: int) -> Grade:
    if not rd.ok or rd.decoded is None or rd.samples is None:
        return Grade()
    sz = ecc200.SIZES[n]
    dec, s, loc, img = rd.decoded, rd.samples, rd.located, rd.image
    inner = s[1:-1, 1:-1]
    truth = ecc200.codewords_to_matrix(dec.corrected, sz)          # the symbol as it should be
    cw_index = ecc200.module_codeword_index(sz)
    v, g = {}, {"decode": 4}

    # --- ISO/IEC 15415 style: symbol contrast and modulation around the mid-range threshold
    r_max, r_min = float(inner.max()), float(inner.min())
    v["SC"] = r_max - r_min
    gt15 = 0.5 * (r_max + r_min)
    mod = 2.0 * np.abs(inner - gt15) / max(v["SC"], 1e-9)
    v["MOD_min"] = float(mod[cw_index >= 0].min())
    g["SC"] = grade_ge(v["SC"], SC_T)
    g["MOD"] = _codeword_level_grade(_mod_grades(mod), cw_index, sz.n_ecc, dec.errors)

    # --- ISO/IEC 29158 style: cell contrast and cell modulation around the minimum-variance threshold
    t = rd.threshold
    ml, md = float(inner[~truth].mean()), float(inner[truth].mean())
    v["CC"] = (ml - md) / max(ml, 1e-9)
    g["CC"] = grade_ge(v["CC"], CC_T)

    def cell_mod(val, dark):
        cm = np.where(dark, (t - val) / max(t - md, 1e-9), (val - t) / max(ml - t, 1e-9))
        return np.maximum(cm, 0.0)                 # a module on the wrong side of the threshold counts as 0

    cm = cell_mod(inner, truth)
    data = cw_index >= 0
    v["CM_min"] = float(cm[data].min())
    v["CM_p05"] = float(np.percentile(cm[data], 5))
    v["CM_mean"] = float(cm[data].mean())
    g["CM"] = _codeword_level_grade(_mod_grades(cm), cw_index, sz.n_ecc, dec.errors)

    # --- unused error correction
    v["UEC"] = dec.uec
    g["UEC"] = grade_ge(dec.uec, UEC_T)

    # --- fixed pattern damage (simplified: no transition-ratio test, see DOSSIER 7.3)
    full_truth = np.zeros((n + 2, n + 2), dtype=bool)
    full_truth[1:-1, 1:-1] = truth
    mg = _mod_grades(cell_mod(s, full_truth))
    seg = {
        "LLS": _segment_grade(mg[1:-1, 1], False), "BLS": _segment_grade(mg[n, 1:-1], False),
        "TCT": _segment_grade(mg[1, 1:-1], True), "RCT": _segment_grade(mg[1:-1, n], True),
        "LQZ": _segment_grade(mg[1:-1, 0], False), "BQZ": _segment_grade(mg[n + 1, 1:-1], False),
        "TQZ": _segment_grade(mg[0, 1:-1], False), "RQZ": _segment_grade(mg[1:-1, n + 1], False),
    }
    avg = np.mean([min(seg["TCT"], seg["TQZ"], seg["RCT"], seg["RQZ"]), seg["LLS"], seg["BLS"], seg["LQZ"], seg["BQZ"]])
    seg["AG"] = grade_ge(float(avg), AG_T)
    v["FPD_segments"] = seg
    g["FPD"] = min(seg.values())

    # --- geometry from the image: axial and grid non-uniformity, print growth (not graded)
    px, py = loc.pitch(n)
    v["AN"] = abs(px - py) / (0.5 * (px + py))
    g["AN"] = grade_le(v["AN"], AN_T)
    r0, r1 = int(max(0, np.floor(loc.y0 + 0.25 * py))), int(np.ceil(loc.y0 + 0.75 * py))
    c0, c1 = int(np.floor(loc.x1 - 0.75 * px)), int(min(img.shape[1], np.ceil(loc.x1 - 0.25 * px)))
    dev_t, w_t = _track(img[r0:max(r1, r0 + 1), :].mean(axis=0), t, loc.x0, px, range(0, n, 2))
    dev_r, w_r = _track(img[:, c0:max(c1, c0 + 1)].mean(axis=1), t, loc.y0, py, range(1, n, 2))
    v["GN"] = max(dev_t, dev_r)
    g["GN"] = grade_le(v["GN"], GN_T)
    v["PG_x"], v["PG_y"] = w_t - 1.0, w_r - 1.0
    v["PG"] = float(np.nanmean([v["PG_x"], v["PG_y"]])) if not (np.isnan(w_t) and np.isnan(w_r)) else float("nan")

    dpm_keys = ("decode", "CC", "CM", "FPD", "UEC", "AN", "GN")
    out = Grade(4, v, g, min(g[k] for k in dpm_keys),
                min(g[k] for k in ("decode", "SC", "MOD", "FPD", "UEC", "AN", "GN")))
    out.limiting = min(dpm_keys, key=lambda k: g[k])
    return out
