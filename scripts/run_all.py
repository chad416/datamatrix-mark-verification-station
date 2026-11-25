"""Run the whole study and rewrite results/ and docs/img/.

    python scripts/run_all.py            full study (a few minutes), figures, results/summary.json
    python scripts/run_all.py --quick    reduced study for CI, results/summary_quick.json, no figures

Every number in README.md and DOSSIER.md comes from results/summary.json or
results/handshake_exploration.md.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dmx import ecc200, explore, grading, mark, reader, study  # noqa: E402

TARGET_ESCAPE = 0.001          # share of released parts that a later reader cannot read
TARGET_FALSE_REJECT = 0.02     # share of later-readable parts that the station rejects
CAL_ALLOWANCE = 0.01           # ILLUSTRATIVE: drift of the station reader's contrast reading between checks
CANDIDATES = ("CC", "CM_min", "CM_p05", "UEC", "MOD_min", "SC")
HIGH_IS_BAD = ("AN", "GN")


def r4(x):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), 5)


def beam_section():
    laser, paint = mark.Laser(), mark.Paint()
    z = np.linspace(-5e-3, 5e-3, 201)
    w = mark.spot_radius(laser, z)
    out = {"w0_um": mark.waist(laser) * 1e6, "zR_mm": mark.rayleigh(laser) * 1e3,
           "F0_Jcm2": float(mark.peak_fluence(laser.pulse_energy, mark.waist(laser))) * 1e-4,
           "D_um": float(mark.ablated_diameter(laser.pulse_energy, mark.waist(laser), paint.f_th)) * 1e6,
           "fill_depth_um": float(mark.mean_fill_depth(laser.pulse_energy, mark.waist(laser), laser, paint)) * 1e6,
           "paint_um": paint.thickness * 1e6, "pulse_pitch_um": laser.speed / laser.rep_rate * 1e6,
           "hatch_um": laser.hatch * 1e6, "z_mm": (z * 1e3).tolist(), "w_um": (w * 1e6).tolist(), "curves": {}}
    for e in (1.0, 0.75, 0.5):
        en = laser.pulse_energy * e
        d = mark.mean_fill_depth(en, w, laser, paint) * 1e6
        out["curves"][str(e)] = {"D_um": (mark.ablated_diameter(en, w, paint.f_th) * 1e6).tolist(), "depth_um": d.tolist()}
        ok = z[(d >= paint.thickness * 1e6) & (z >= 0)]
        out["curves"][str(e)]["focus_window_mm"] = float(ok.max() * 1e3) if len(ok) else 0.0
    return {k: (r4(v) if isinstance(v, float) else v) for k, v in out.items()}


def summarise(pop):
    ok = pop["st_ok"] > 0
    med = {k: r4(np.nanmedian(pop["v_" + k][ok])) if ok.any() else None for k in study.VALUE_KEYS}
    return {"n": int(len(ok)), "station_decode": r4(ok.mean()), "later_read": r4(pop["dn_read"].mean()),
            "station_grade_mean": r4(pop["dpm"].mean()), "grade_counts": np.bincount(pop["dpm"], minlength=5).tolist(),
            "iso15415_grade_counts": np.bincount(pop["iso"], minlength=5).tolist(), "median": med,
            "station_misread": r4(pop["st_misread"].mean()), "later_misread": r4(pop["dn_misread"].mean())}


def sweeps(n_levels, n_parts, pool):
    out = {}
    futures = {(mech, j): pool.submit(study.run, n_parts, 1000 + 100 * i + j, "fixed", {mech: float(lv)}, 1)
               for i, mech in enumerate(study.DRIFT_FULL) for j, lv in enumerate(np.linspace(0.0, 1.0, n_levels))}
    for i, mech in enumerate(study.DRIFT_FULL):
        rows = []
        for j, lv in enumerate(np.linspace(0.0, 1.0, n_levels)):
            pop = futures[(mech, j)].result()
            proc = study.process_at({mech: float(lv)}, None)
            x = {"focus": proc.focus_offset * 1e3, "energy": proc.energy_factor, "window": proc.window_transmission,
                 "paint": proc.paint_thickness * 1e6}[mech]
            rows.append({"level": r4(lv), "x": r4(x), **summarise(pop)})
        out[mech] = rows
    return out


def repeatability(key, limit, n_marks=24, n_reads=10):
    """Spread of repeated station measurements of the same mark, for marks near the limit."""
    rng = np.random.default_rng(77)
    matrix = ecc200.encode(study.CONTENT, study.N_SYM).matrix
    geo = mark.Geometry()
    sds, means = [], []
    tries = 0
    while len(sds) < n_marks and tries < 150:
        tries += 1
        proc = study.process_at({k: rng.beta(1.0, 2.0) for k in study.DRIFT_FULL}, rng)
        m = mark.make_mark(matrix, proc, rng=rng)
        vals = []
        for _ in range(n_reads):
            rs = reader.read(reader.acquire(m.reflectance, geo, reader.STATION, rng), study.N_SYM, reader.STATION)
            g = grading.grade(rs, study.N_SYM)
            if rs.ok:
                vals.append(g.values[key])
        if len(vals) == n_reads and abs(np.mean(vals) - limit) < 0.15:
            sds.append(np.std(vals, ddof=1))
            means.append(np.mean(vals))
    return {"marks": len(sds), "reads_per_mark": n_reads, "u": r4(np.sqrt(np.mean(np.square(sds)))) if sds else None,
            "mean_value": r4(np.mean(means)) if means else None}


def mix_section(pop, quick):
    ok = pop["st_ok"] > 0
    p_fail = 1.0 - pop["dn_read"]
    out = {"population": summarise(pop), "targets": {"escape_per_released": TARGET_ESCAPE, "false_reject_of_readable": TARGET_FALSE_REJECT}}
    aucs = {}
    for k in study.VALUE_KEYS:
        a = study.auc(pop["v_" + k][ok], p_fail[ok])
        aucs[k] = r4(1.0 - a if k in HIGH_IS_BAD else a)
    out["auc"] = aucs
    key = max(CANDIDATES, key=lambda k: aucs[k])
    out["deciding_parameter"] = key
    out["limiting_grade_when_below_4"] = dict(Counter(str(x) for x in pop["limiting"][ok & (pop["dpm"] < 4)]))
    out["later_no_read_of_all_marked"] = r4(p_fail.mean())
    out["later_no_read_station_decoded"] = r4(p_fail[ok].mean())
    full4 = ok & (pop["dpm"] == 4) & (np.nan_to_num(pop["v_" + key], nan=0.0) >= np.nanpercentile(pop["v_" + key][ok], 50))
    out["later_no_read_floor"] = r4(p_fail[full4].mean())          # best half of the population: the reader's own share

    rules = {"A0 no read-back": study.rates(pop, np.ones(len(ok), dtype=bool)),
             "A1 decode only": study.rates(pop, study.rule_decode(pop))}
    for g in (1, 2, 3, 4):
        rules[f"A2 grade >= {g}"] = study.rates(pop, study.rule_grade(pop, g))
    for g, u in ((2, 0.67), (2, 0.84), (3, 0.84), (2, 1.0)):
        rules[f"A3 grade >= {g} and UEC >= {u}"] = study.rates(pop, study.rule_grade_uec(pop, g, u))

    lim_need, r_need = study.smallest_limit_for_escape(pop, key, TARGET_ESCAPE)
    out["limit_need"] = lim_need
    if lim_need is not None:
        rep = repeatability(key, lim_need, n_marks=6 if quick else 24)
        u = rep["u"] or 0.0
        lim_gb = round(lim_need + 2.0 * u + CAL_ALLOWANCE, 3)
        out["guard_band"] = {"two_u": r4(2.0 * u), "calibration_allowance": CAL_ALLOWANCE}
        out["repeatability"] = rep
        out["limit_guard_banded"] = lim_gb
        rules[f"A4 {key} >= {lim_need:.3f} (no guard band)"] = r_need
        rules[f"A5 {key} >= {lim_gb:.3f} (guard-banded)"] = study.rates(pop, study.rule_value(pop, key, lim_gb))
        # a station that reads the deciding value too high by the calibration allowance
        out["biased_station"] = {
            "bias": CAL_ALLOWANCE,
            "no_guard_band": {k: r4(v) for k, v in study.rates(pop, study.rule_value(pop, key, lim_need - CAL_ALLOWANCE)).items()},
            "guard_banded": {k: r4(v) for k, v in study.rates(pop, study.rule_value(pop, key, lim_gb - CAL_ALLOWANCE)).items()},
        }
        g4 = ok & (pop["g_" + key] == 4) if "g_" + key in pop else ok
        out["grade4_on_deciding_parameter"] = {"share_of_decoded": r4(g4.sum() / ok.sum()),
                                               "threshold_for_grade_4": grading.CC_T[0] if key == "CC" else None}
    out["rules"] = {k: {kk: r4(vv) for kk, vv in v.items()} for k, v in rules.items()}
    out["meets_targets"] = [k for k, v in rules.items() if v["escape_per_released"] <= TARGET_ESCAPE
                            and v["false_reject_of_readable"] <= TARGET_FALSE_REJECT]
    v = pop["v_" + key][ok]
    limits = np.round(np.linspace(np.nanpercentile(v, 0.5), np.nanpercentile(v, 60), 40), 4)
    out["limit_curve"] = [{k: r4(x) for k, x in row.items()} for row in study.limit_curve(pop, key, limits)]
    return out


def handshake_section(quick):
    out = {"parameters": explore.PARAMS, "mark_cycles": list(explore.MARK_CYCLES), "runs": [], "mutants": []}
    lines = ["# Handshake state-space exploration", "",
             "Generated by `python scripts/run_all.py` from `dmx/explore.py` with `plc/FB_MarkStation.scl` in the loop.", ""]
    last = None
    for n in ((2, 3) if quick else (2, 3, 4, 5, 6)):
        t = time.time()
        r = explore.explore(max_parts=n)
        out["runs"].append({"max_parts": n, "states": r.states, "transitions": r.transitions, "depth": r.depth,
                            "release_transitions": r.releases, "violations": len(r.violations), "complete": r.complete,
                            "seconds": round(time.time() - t, 2)})
        last = r
    out["states_by_fault"] = {k or "no fault yet": v for k, v in sorted(last.by_fault.items())}
    out["steps_reached"] = sorted(int(k) for k in last.steps_seen)
    oc = Counter()
    for (fault, exits), cnt in last.outcomes.items():
        oc[(fault, ", ".join(f"{n} {k}" for k, n in exits))] += cnt
    out["end_states"] = [{"fault": f, "parts": e, "end_states": c} for (f, e), c in sorted(oc.items())]
    lines += ["## The block as written", "", "| Parts per run | States | Transitions | Longest shortest path | Release transitions | Violations |", "|---|---|---|---|---|---|"]
    lines += [f"| {r['max_parts']} | {r['states']} | {r['transitions']} | {r['depth']} | {r['release_transitions']} | {r['violations']} |" for r in out["runs"]]
    lines += ["", "## The same search with one safeguard removed", ""]
    for name in explore.MUTANTS:
        r = explore.explore(explore.mutant_source(name), max_parts=3)
        v = r.violations[0] if r.violations else None
        out["mutants"].append({"mutant": name, "states_until_stop": r.states, "violation": v[0] if v else None,
                               "trace_length": len(v[1]) if v else 0})
        lines += [f"### {name}", ""]
        if v:
            lines += [f"Violated: **{v[0]}**. Shortest trace ({len(v[1])} controller cycles; event before the cycle, step after it, reaction):", "", "```"] + v[1] + ["```", ""]
        else:
            lines += [f"No property violated in {r.states} states. This safeguard is not needed for the five properties; see DOSSIER section 9.", ""]
    (ROOT / "results" / ("handshake_exploration_quick.md" if quick else "handshake_exploration.md")).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


# --------------------------------------------------------------------------
def figures(summary, pop):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    img = ROOT / "docs" / "img"
    img.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.size": 9, "axes.grid": True, "grid.alpha": 0.3, "savefig.dpi": 140, "figure.constrained_layout.use": True})
    enc = ecc200.encode(study.CONTENT, study.N_SYM)
    geo = mark.Geometry()
    nominal = mark.nominal_process()

    # fig 1: symbols
    cases = [("nominal", nominal), ("focus +3.7 mm", replace(nominal, focus_offset=3.7e-3)),
             ("pulse energy x 0.55", replace(nominal, energy_factor=0.55)), ("paint 38 um", replace(nominal, paint_thickness=38e-6)),
             ("window 60 %, tilt 6 deg", replace(nominal, window_transmission=0.6, tilt=np.radians(6.0)))]
    fig, ax = plt.subplots(len(cases), 4, figsize=(9.2, 2.25 * len(cases)))
    rng = np.random.default_rng(5)
    fig1 = []
    for i, (name, proc) in enumerate(cases):
        m = mark.make_mark(enc.matrix, proc, rng=rng)
        st_img = reader.acquire(m.reflectance, geo, reader.STATION, rng)
        rs = reader.read(st_img, study.N_SYM, reader.STATION)
        g = grading.grade(rs, study.N_SYM)
        dn_img = reader.acquire(m.reflectance, geo, reader.DOWNSTREAM, rng)
        rd = reader.read(dn_img, study.N_SYM, reader.DOWNSTREAM)
        later = float(np.mean([reader.read(reader.acquire(m.reflectance, geo, reader.DOWNSTREAM, rng), study.N_SYM, reader.DOWNSTREAM).ok for _ in range(200)]))
        fig1.append({"case": name, "station_decode": bool(rs.ok), "station_grade": g.dpm, "CC": r4(g.values.get("CC", float("nan"))),
                     "later_read_rate_200": r4(later), "fill_depth_um": r4(m.info["fill_depth_um"])})
        panels = [(~enc.matrix).astype(float), m.reflectance, st_img, dn_img]
        titles = ["encoded symbol (ECC 200, 16x16)", f"mark, {name}", f"station image: grade {g.dpm}" if rs.ok else "station image: no decode",
                  f"later reader: {'read' if rd.ok else 'no read'} ({100 * later:.0f} % of 200)"]
        for a, p, t in zip(ax[i], panels, titles):
            a.imshow(p, cmap="gray", vmin=0, vmax=0.7 if p is not panels[0] else 1, interpolation="nearest")
            a.set_title(t, fontsize=8)
            a.set_xticks([])
            a.set_yticks([])
            a.grid(False)
    fig.savefig(img / "fig1_symbols_nominal_and_degraded.png")
    plt.close(fig)
    summary["fig1_cases"] = fig1

    # fig 2: beam
    b = summary["beam"]
    fig, ax = plt.subplots(1, 3, figsize=(10, 3.1))
    ax[0].plot(b["z_mm"], b["w_um"], color="k")
    ax[0].set(xlabel="focus offset z (mm)", ylabel="beam radius w (um)", title=f"Gaussian beam: w0 = {b['w0_um']:.1f} um, zR = {b['zR_mm']:.2f} mm")
    for e, c in zip(b["curves"], ("C0", "C1", "C3")):
        ax[1].plot(b["z_mm"], b["curves"][e]["D_um"], color=c, label=f"energy x {e}")
        ax[2].plot(b["z_mm"], b["curves"][e]["depth_um"], color=c, label=f"energy x {e}")
    ax[1].set(xlabel="focus offset z (mm)", ylabel="ablated diameter D (um)", title="One pulse: D = w sqrt(2 ln(F0/Fth))")
    ax[2].axhline(b["paint_um"], color="k", ls="--", lw=1, label="paint thickness")
    ax[2].set(xlabel="focus offset z (mm)", ylabel="removed depth in a filled cell (um)", title="Fill: crater volume / (pitch x hatch)")
    ax[1].legend()
    ax[2].legend()
    fig.savefig(img / "fig2_beam_spot_and_removed_depth.png")
    plt.close(fig)

    # fig 3: grades versus drift
    xl = {"focus": "focus offset (mm)", "energy": "pulse energy (x nominal)", "window": "window transmission", "paint": "paint thickness (um)"}
    fig, ax = plt.subplots(2, 4, figsize=(12, 5.2), sharey="row")
    for j, mech in enumerate(study.DRIFT_FULL):
        rows = summary["sweeps"][mech]
        x = [r["x"] for r in rows]
        for k, c in (("CC", "C0"), ("CM_min", "C1"), ("UEC", "C2")):
            ax[0, j].plot(x, [r["median"][k] if r["median"][k] is not None else np.nan for r in rows], "o-", color=c, ms=3, label=f"{k} (median)")
        ax[0, j].set(title=xl[mech], ylim=(-0.03, 1.08))
        ax[1, j].plot(x, [r["station_grade_mean"] / 4 for r in rows], "s-", color="k", ms=3, label="station grade / 4 (mean)")
        ax[1, j].plot(x, [r["station_decode"] for r in rows], "o--", color="C0", ms=3, label="station decodes")
        ax[1, j].plot(x, [r["later_read"] for r in rows], "o-", color="C3", ms=3, label="later reader reads")
        ax[1, j].set(xlabel=xl[mech], ylim=(-0.03, 1.08))
    ax[0, 0].set_ylabel("value at the station")
    ax[1, 0].set_ylabel("share / grade")
    ax[0, 0].legend(fontsize=7)
    ax[1, 0].legend(fontsize=7)
    fig.savefig(img / "fig3_grades_vs_drift.png")
    plt.close(fig)

    # fig 4: escape and false reject versus the limit
    mx = summary["mix"]
    key = mx["deciding_parameter"]
    cur = mx["limit_curve"]
    fig, ax = plt.subplots(1, 2, figsize=(10, 3.6))
    L = [c["limit"] for c in cur]
    ax[0].semilogy(L, [max(c["escape_per_released"], 1e-5) * 100 for c in cur], "o-", ms=3, color="C3", label="not read later, % of released parts")
    ax[0].semilogy(L, [max(c["false_reject_of_readable"], 1e-5) * 100 for c in cur], "s-", ms=3, color="C0", label="rejected, % of later-readable parts")
    ax[0].axhline(TARGET_ESCAPE * 100, color="C3", ls=":", lw=1)
    ax[0].axhline(TARGET_FALSE_REJECT * 100, color="C0", ls=":", lw=1)
    if mx.get("limit_need") is not None:
        ax[0].axvline(mx["limit_need"], color="k", ls="--", lw=1, label=f"limit that meets the escape target: {mx['limit_need']:.3f}")
        ax[0].axvline(mx["limit_guard_banded"], color="k", lw=1, label=f"guard-banded limit: {mx['limit_guard_banded']:.3f}")
    ax[0].set(xlabel=f"station limit on {key}", ylabel="%", title=f"Limit on {key} (dotted: targets)")
    ax[0].legend(fontsize=7)
    names = ["A1 decode only"] + [f"A2 grade >= {g}" for g in (1, 2, 3, 4)]
    ax[1].semilogy(range(5), [max(mx["rules"][n]["escape_per_released"], 1e-5) * 100 for n in names], "o-", color="C3", label="not read later, % of released parts")
    ax[1].semilogy(range(5), [max(mx["rules"][n]["false_reject_of_readable"], 1e-5) * 100 for n in names], "s-", color="C0", label="rejected, % of later-readable parts")
    ax[1].axhline(TARGET_ESCAPE * 100, color="C3", ls=":", lw=1)
    ax[1].axhline(TARGET_FALSE_REJECT * 100, color="C0", ls=":", lw=1)
    ax[1].set_xticks(range(5))
    ax[1].set_xticklabels(["decode", ">= 1", ">= 2", ">= 3", ">= 4"])
    ax[1].set(xlabel="station limit on the overall grade", title="Limit on the overall grade")
    ax[1].legend(fontsize=7)
    fig.savefig(img / "fig4_escape_and_false_reject_vs_limit.png")
    plt.close(fig)

    # fig 5: trade study
    fig, ax = plt.subplots(figsize=(7.4, 4.4))
    for i, (name, r) in enumerate(mx["rules"].items()):
        mk = {"A0": "x", "A1": "o", "A2": "s", "A3": "^", "A4": "D", "A5": "D"}[name[:2]]
        col = {"A4": "0.5", "A5": "k"}.get(name[:2], f"C{i % 10}")
        ax.scatter(max(r["false_reject_of_readable"], 2e-5) * 100, max(r["escape_per_released"], 2e-5) * 100, color=col, marker=mk, s=40, label=name, zorder=3)
    ax.axhline(TARGET_ESCAPE * 100, color="k", ls=":", lw=1)
    ax.axvline(TARGET_FALSE_REJECT * 100, color="k", ls=":", lw=1)
    ax.set(xscale="log", yscale="log", xlabel="rejected at the station, % of later-readable parts", ylabel="not read later, % of released parts",
           title="Alternatives on the same simulated parts (dotted: targets)")
    ax.legend(fontsize=7, loc="upper right")
    fig.savefig(img / "fig5_trade_study.png")
    plt.close(fig)

    # fig 6: which parameter predicts a later no-read
    fig, ax = plt.subplots(1, 2, figsize=(10, 3.4))
    ks = sorted(mx["auc"], key=lambda k: mx["auc"][k] or 0)
    ax[0].barh(ks, [mx["auc"][k] or 0 for k in ks], color=["C3" if k == key else "C0" for k in ks])
    ax[0].axvline(0.5, color="k", lw=1, ls=":")
    ax[0].set(xlim=(0, 1), xlabel="area under the ROC curve (0.5 = no information)", title="Station value as predictor of a later no-read")
    ok = pop["st_ok"] > 0
    ax[1].scatter(pop["v_" + key][ok], 1 - pop["dn_read"][ok] + np.random.default_rng(0).normal(0, 0.012, ok.sum()), s=4, alpha=0.35, color="C0")
    if mx.get("limit_need") is not None:
        ax[1].axvline(mx["limit_need"], color="k", ls="--", lw=1)
        ax[1].axvline(mx["limit_guard_banded"], color="k", lw=1)
    ax[1].set(xlabel=f"{key} measured at the station", ylabel="share of later reads that fail (3 reads, jittered)", title="Every station-decoded part of the drift mix")
    fig.savefig(img / "fig6_deciding_parameter.png")
    plt.close(fig)

    # fig 7: handshake exploration
    hs = summary["handshake"]
    fig, ax = plt.subplots(1, 2, figsize=(10, 3.4))
    f = hs["states_by_fault"]
    ax[0].barh(list(f), list(f.values()), color="C0")
    ax[0].set(xlabel="reachable states", title=f"States by injected fault ({hs['runs'][-1]['max_parts']} parts per run, {hs['runs'][-1]['states']} states, 0 violations)")
    ax[1].plot([r["max_parts"] for r in hs["runs"]], [r["states"] for r in hs["runs"]], "o-", label="states")
    ax[1].plot([r["max_parts"] for r in hs["runs"]], [r["transitions"] for r in hs["runs"]], "s-", label="transitions")
    ax[1].set(xlabel="parts per run", title="Size of the explored state space")
    ax[1].legend()
    fig.savefig(img / "fig7_handshake_state_space.png")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--workers", type=int, default=None)
    a = ap.parse_args()
    t0 = time.time()
    (ROOT / "results").mkdir(exist_ok=True)
    n_mix, n_nom, n_lv, n_sw = (600, 100, 5, 25) if a.quick else (6000, 1000, 9, 75)
    summary = {"quick": a.quick, "content": study.CONTENT, "symbol": f"{study.N_SYM}x{study.N_SYM}",
               "parameters": {"laser": asdict(mark.Laser()), "paint": asdict(mark.Paint()), "geometry": asdict(mark.Geometry()),
                              "station_reader": asdict(reader.STATION), "later_reader": asdict(reader.DOWNSTREAM),
                              "drift_full_scale": study.DRIFT_FULL, "part_sigma": study.PART_SIGMA,
                              "later_reads_per_part": study.DOWNSTREAM_READS}}
    summary["beam"] = beam_section()
    summary["handshake"] = handshake_section(a.quick)
    print("handshake done", round(time.time() - t0, 1), flush=True)
    with ProcessPoolExecutor(max_workers=a.workers or 4) as pool:
        summary["nominal"] = summarise(study.run(n_nom, 11, mode="fixed", levels={}, pool=pool))
        summary["sweeps"] = sweeps(n_lv, n_sw, pool)
        print("sweeps done", round(time.time() - t0, 1), flush=True)
        pop = study.run(n_mix, 2026, mode="mix", pool=pool)
    summary["mix"] = mix_section(pop, a.quick)
    print("mix done", round(time.time() - t0, 1), flush=True)
    if not a.quick:
        figures(summary, pop)
    summary["runtime_s"] = round(time.time() - t0, 1)
    name = "summary_quick.json" if a.quick else "summary.json"
    (ROOT / "results" / name).write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print("wrote", name, summary["runtime_s"], "s")


if __name__ == "__main__":
    main()
