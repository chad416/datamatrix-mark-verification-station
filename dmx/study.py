"""Monte Carlo study: process drift, station grading, a weaker later reader, acceptance rules.

All distributions here are ILLUSTRATIVE assumptions (DOSSIER.md section 8). The study
compares acceptance rules on the same simulated parts; the absolute rates depend on
the assumed drift mix and are not a prediction for a real line.
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor

import numpy as np

from . import ecc200, grading, mark, reader

N_SYM = 16
CONTENT = "8370205117000012345"       # part number (8) + variant (2) + serial (9), an invented example
VALUE_KEYS = ("SC", "CC", "CM_min", "CM_p05", "CM_mean", "UEC", "AN", "GN", "PG", "MOD_min")
GRADE_KEYS = ("SC", "MOD", "CC", "CM", "FPD", "UEC", "AN", "GN")
DOWNSTREAM_READS = 3

# full-scale drift of each mechanism (level 0 = nominal, level 1 = this value)
DRIFT_FULL = {"focus": 4.0e-3, "energy": 0.50, "window": 0.50, "paint": 40e-6}
PART_SIGMA = {"focus": 0.25e-3, "energy": 0.03, "window": 0.01, "paint": 2.0e-6, "tilt": np.radians(3.0),
              "r_paint": 0.02}


def process_at(levels: dict, rng: np.random.Generator | None, paint: mark.Paint = mark.Paint()) -> mark.Process:
    """Process state for drift levels 0..1 per mechanism, with part-to-part scatter if rng is given."""

    def n(k):
        return rng.normal(0.0, PART_SIGMA[k]) if rng is not None else 0.0

    def lv(k):
        return float(levels.get(k, 0.0))

    return mark.Process(
        focus_offset=lv("focus") * DRIFT_FULL["focus"] + n("focus"),
        tilt=n("tilt"),
        energy_factor=max(0.05, 1.0 + lv("energy") * (DRIFT_FULL["energy"] - 1.0) + n("energy")),
        window_transmission=float(np.clip(1.0 + lv("window") * (DRIFT_FULL["window"] - 1.0) + n("window"), 0.05, 1.0)),
        paint_thickness=max(5e-6, paint.thickness + lv("paint") * (DRIFT_FULL["paint"] - paint.thickness) + n("paint")),
        r_paint=float(np.clip(paint.r_paint + n("r_paint"), 0.2, 0.9)),
    )


def simulate_part(proc: mark.Process, rng: np.random.Generator, matrix: np.ndarray,
                  downstream: reader.Reader = reader.DOWNSTREAM) -> dict:
    geo = mark.Geometry()
    m = mark.make_mark(matrix, proc, rng=rng)
    rs = reader.read(reader.acquire(m.reflectance, geo, reader.STATION, rng), N_SYM, reader.STATION)
    g = grading.grade(rs, N_SYM)
    ok_text = rs.ok and rs.text == CONTENT
    dn = [reader.read(reader.acquire(m.reflectance, geo, downstream, rng), N_SYM, downstream)
          for _ in range(DOWNSTREAM_READS)]
    rec = {"st_ok": float(ok_text), "dpm": g.dpm if ok_text else 0, "iso": g.iso15415 if ok_text else 0,
           "dn_read": float(np.mean([d.ok and d.text == CONTENT for d in dn])),
           "dn_misread": float(np.mean([d.ok and d.text != CONTENT for d in dn])),
           "st_misread": float(rs.ok and rs.text != CONTENT), "limiting": g.limiting if ok_text else "decode",
           "focus": proc.focus_offset, "energy": proc.energy_factor, "window": proc.window_transmission,
           "paint": proc.paint_thickness, "fill_depth_um": m.info["fill_depth_um"]}
    for k in VALUE_KEYS:
        rec["v_" + k] = float(g.values.get(k, np.nan)) if ok_text else np.nan
    for k in GRADE_KEYS:
        rec["g_" + k] = g.grades.get(k, 0) if ok_text else 0
    return rec


def _chunk(args):
    seed, n, mode, levels, downstream = args
    rng = np.random.default_rng(seed)
    matrix = ecc200.encode(CONTENT, N_SYM).matrix
    out = []
    for _ in range(n):
        if mode == "mix":       # every mechanism drifts on its own; most parts are close to nominal
            lv = {k: rng.beta(1.0, 5.0) for k in DRIFT_FULL}
        else:
            lv = levels
        out.append(simulate_part(process_at(lv, rng), rng, matrix, downstream))
    return out


def run(n: int, seed: int, mode: str = "mix", levels: dict | None = None, workers: int | None = None,
        downstream: reader.Reader = reader.DOWNSTREAM, chunk: int = 25, pool: ProcessPoolExecutor | None = None) -> dict:
    """Simulate n parts. Returns a dict of numpy arrays. The result depends on n, seed and chunk only,
    not on the number of worker processes."""
    seeds = np.random.SeedSequence(seed).spawn((n + chunk - 1) // chunk)
    jobs = [(s, min(chunk, n - i * chunk), mode, levels or {}, downstream) for i, s in enumerate(seeds)]
    if pool is not None:
        parts = [r for rs in pool.map(_chunk, jobs) for r in rs]
    elif workers == 1 or len(jobs) == 1:
        parts = [r for j in jobs for r in _chunk(j)]
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            parts = [r for rs in ex.map(_chunk, jobs) for r in rs]
    return {k: np.array([p[k] for p in parts]) for k in parts[0]}


# --------------------------------------------------------------------------
# acceptance rules and their two error rates
# --------------------------------------------------------------------------
def rates(pop: dict, accept: np.ndarray) -> dict:
    """escape = accepted at the station and not read later; false reject = rejected but readable later."""
    accept = np.asarray(accept, dtype=float)
    p_fail = 1.0 - pop["dn_read"]
    n_acc = accept.sum()
    readable = pop["dn_read"].sum()
    return {
        "accept_share": float(accept.mean()),
        "escape_per_marked": float((accept * p_fail).mean()),
        "escape_per_released": float((accept * p_fail).sum() / n_acc) if n_acc else 0.0,
        "false_reject_per_marked": float(((1 - accept) * pop["dn_read"]).mean()),
        "false_reject_of_readable": float(((1 - accept) * pop["dn_read"]).sum() / readable) if readable else 0.0,
    }


def rule_decode(pop):
    return pop["st_ok"] > 0


def rule_grade(pop, g):
    return (pop["st_ok"] > 0) & (pop["dpm"] >= g)


def rule_grade_uec(pop, g, uec):
    return rule_grade(pop, g) & (np.nan_to_num(pop["v_UEC"], nan=-1.0) >= uec)


def rule_value(pop, key, limit, g=0):
    return rule_grade(pop, g) & (np.nan_to_num(pop["v_" + key], nan=-1.0) >= limit)


def auc(score: np.ndarray, bad_weight: np.ndarray) -> float:
    """Area under the ROC curve of "a low score predicts a later no-read" (weighted by the no-read share)."""
    ok = ~np.isnan(score)
    s, w_bad = score[ok], bad_weight[ok]
    w_good = 1.0 - w_bad
    tot_b, tot_g = w_bad.sum(), w_good.sum()
    if tot_b == 0 or tot_g == 0:
        return float("nan")
    order = np.argsort(s, kind="stable")
    s, w_bad, w_good = s[order], w_bad[order], w_good[order]
    # P(score_bad < score_good) + 0.5 P(tie), ties handled by grouping equal scores
    _, start = np.unique(s, return_index=True)
    grp_bad = np.add.reduceat(w_bad, start)
    grp_good = np.add.reduceat(w_good, start)
    below = np.cumsum(grp_bad) - grp_bad
    return float((grp_good * (below + 0.5 * grp_bad)).sum() / (tot_b * tot_g))


def limit_curve(pop, key, limits, g=0):
    return [dict(limit=float(L), **rates(pop, rule_value(pop, key, L, g))) for L in limits]


def smallest_limit_for_escape(pop, key, target, g=0):
    """Smallest limit on a value whose escape share of released parts is at or below the target."""
    vals = np.unique(np.round(pop["v_" + key][~np.isnan(pop["v_" + key])], 3))
    for L in vals:
        r = rates(pop, rule_value(pop, key, L, g))
        if r["accept_share"] > 0 and r["escape_per_released"] <= target:
            return float(L), r
    return None, None
