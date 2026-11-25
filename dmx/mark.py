"""Physical model of a laser mark on a painted part.

The laser removes a light paint layer and uncovers the dark plastic below (the
"remove a coating to get contrast" technique described in S1, section 3.3.4).

Relations and where they come from (docs/SOURCES.md):
* Gaussian beam: w(z) = w0 sqrt(1 + (z/zR)^2), zR = pi w0^2 / lambda, focused
  waist w0 = lambda f / (pi w_in) (S5). A beam with quality factor M^2 has M^2
  times the beam parameter product of the ideal beam (definition in S5), which
  gives w0 = M^2 lambda f / (pi w_in) and zR = pi w0^2 / (M^2 lambda).
* Peak fluence of a Gaussian pulse: F0 = 2 E / (pi w^2) (S5, same form as the
  peak intensity 2 P / (pi w^2)).
* Ablated diameter: D = w sqrt(2 ln(F0 / Fth)) (S6, Liu's D^2 relation, eq. 6.22
  with N = 1).
* Ablated depth of one pulse: d = delta ln(F / Fth) (S6, eq. 6.25, Beer's law).

Model built on them (my simplifications, not from a source):
* every pulse leaves the crater d(r) = delta (ln(F0/Fth) - 2 r^2 / w^2) >= 0;
  craters add linearly (no incubation, no plume shielding, no heat build-up);
* pulses are dense (pulse pitch and hatch smaller than the crater), so the
  removed depth is the fill mask convolved with the crater, divided by
  pitch * hatch;
* the paint is "open" where the removed depth exceeds the local paint thickness
  (a smooth step with the fine roughness); the film build also varies slowly
  across the symbol, so a marginal mark loses patches of modules first.

Every number in the dataclasses below is ILLUSTRATIVE unless its comment names a
source. None was measured.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
from scipy import ndimage, signal, special


@dataclass(frozen=True)
class Laser:
    wavelength: float = 1.062e-6     # m, S1 table 3: 1055-1070 nm, typically 1062 nm
    focal: float = 0.190             # m, S1 table 3: optics LF4, working distance 190 mm
    w_in: float = 3.75e-3            # m, beam radius at the lens; S1 table 3 gives a 7.5 mm beam diameter
    m2: float = 1.5                  # ILLUSTRATIVE beam quality factor
    pulse_energy: float = 2.0e-4     # J, ILLUSTRATIVE (10 W at 50 kHz, half of the 20 W unit in S1)
    rep_rate: float = 50e3           # Hz, ILLUSTRATIVE
    speed: float = 0.5               # m/s, S1 section 3.3.3 example: 500 mm/s
    hatch: float = 22e-6             # m, ILLUSTRATIVE line spacing of the fill
    scatter_gain: float = 1.0        # ILLUSTRATIVE: M^2 grows by this fraction per unit of window loss


@dataclass(frozen=True)
class Paint:
    thickness: float = 20e-6         # m, ILLUSTRATIVE top-coat thickness
    f_th: float = 1.0e4              # J/m^2 (1 J/cm^2), ILLUSTRATIVE ablation threshold
    delta: float = 2.0e-6            # m, ILLUSTRATIVE effective penetration depth
    roughness: float = 2.0e-6        # m, ILLUSTRATIVE fine thickness variation inside a module (1 sigma)
    waviness: float = 3.0e-6         # m, ILLUSTRATIVE film-build variation across the symbol (1 sigma)
    waviness_length: float = 0.6e-3  # m, ILLUSTRATIVE correlation length of that variation
    r_paint: float = 0.55            # ILLUSTRATIVE diffuse reflectance of the paint
    r_sub: float = 0.06              # ILLUSTRATIVE diffuse reflectance of the black plastic
    texture: float = 0.03            # ILLUSTRATIVE relative reflectance noise of the paint (flakes, orange peel)


@dataclass(frozen=True)
class Geometry:
    module: float = 0.25e-3          # m, ILLUSTRATIVE module size (16x16 symbol = 4 mm)
    quiet: int = 3                   # modules of margin kept around the symbol in the scene
    ppm: int = 12                    # scene pixels per module
    curvature_radius: float = 0.030  # m, ILLUSTRATIVE radius of the handle surface across the symbol

    @property
    def px(self) -> float:
        return self.module / self.ppm


@dataclass(frozen=True)
class Process:
    """State of the marking process for one part. Nominal = all defaults."""
    focus_offset: float = 0.0        # m, part surface relative to the focal plane at the symbol centre
    tilt: float = 0.0                # rad, surface tilt across the symbol (x direction)
    energy_factor: float = 1.0       # pulse energy relative to the job setting (laser ageing, set-point error)
    window_transmission: float = 1.0 # transmission of the protective window (contamination)
    paint_thickness: float = 20e-6   # m
    r_paint: float = 0.55


# --------------------------------------------------------------------------
# beam and single-line relations
# --------------------------------------------------------------------------
def effective_m2(laser: Laser, window_transmission: float) -> float:
    return laser.m2 * (1.0 + laser.scatter_gain * (1.0 - window_transmission))


def waist(laser: Laser, m2: float | None = None) -> float:
    m2 = laser.m2 if m2 is None else m2
    return m2 * laser.wavelength * laser.focal / (np.pi * laser.w_in)


def rayleigh(laser: Laser, m2: float | None = None) -> float:
    m2 = laser.m2 if m2 is None else m2
    return np.pi * waist(laser, m2) ** 2 / (m2 * laser.wavelength)


def spot_radius(laser: Laser, z, m2: float | None = None):
    return waist(laser, m2) * np.sqrt(1.0 + (np.asarray(z, dtype=float) / rayleigh(laser, m2)) ** 2)


def peak_fluence(energy: float, w):
    return 2.0 * energy / (np.pi * np.asarray(w, dtype=float) ** 2)


def ablated_diameter(energy: float, w, f_th: float):
    f0 = peak_fluence(energy, w)
    return np.where(f0 > f_th, np.asarray(w) * np.sqrt(2.0 * np.log(np.maximum(f0, f_th) / f_th)), 0.0)


def mean_fill_depth(energy: float, w, laser: Laser, paint: Paint):
    """Removed depth far inside a filled area: crater volume / (pulse pitch * hatch)."""
    f0 = peak_fluence(energy, w)
    ln = np.log(np.maximum(f0, paint.f_th) / paint.f_th)
    volume = 0.25 * np.pi * np.asarray(w) ** 2 * ln ** 2 * paint.delta   # paraboloid: 1/2 * pi r_a^2 * peak
    return volume / ((laser.speed / laser.rep_rate) * laser.hatch)


def _crater_kernel(energy: float, w: float, paint: Paint, px: float) -> np.ndarray:
    f0 = peak_fluence(energy, w)
    if f0 <= paint.f_th:
        return np.zeros((1, 1))
    ln0 = np.log(f0 / paint.f_th)
    r_a = w * np.sqrt(ln0 / 2.0)
    half = int(np.ceil(r_a / px)) + 1
    ax = (np.arange(-half, half + 1)) * px
    # sub-sample each pixel 4 x 4 so that a crater of one or two pixels keeps its volume
    sub = (np.arange(4) + 0.5) / 4.0 - 0.5
    xs = (ax[:, None] + sub[None, :] * px).ravel()
    r2 = xs[:, None] ** 2 + xs[None, :] ** 2
    k = paint.delta * np.maximum(ln0 - 2.0 * r2 / w ** 2, 0.0)
    n = len(ax)
    return k.reshape(n, 4, n, 4).mean(axis=(1, 3)) * px * px     # volume per pixel


# --------------------------------------------------------------------------
# the mark
# --------------------------------------------------------------------------
@dataclass
class Mark:
    reflectance: np.ndarray          # scene, rows top to bottom, values 0..1
    exposed: np.ndarray              # fraction of paint removed, 0..1
    info: dict


def fill_mask(matrix: np.ndarray, geo: Geometry) -> np.ndarray:
    n = matrix.shape[0]
    full = np.zeros(((n + 2 * geo.quiet) * geo.ppm,) * 2, dtype=bool)
    q = geo.quiet * geo.ppm
    full[q:q + n * geo.ppm, q:q + n * geo.ppm] = np.kron(matrix.astype(bool), np.ones((geo.ppm, geo.ppm), dtype=bool))
    return full


def make_mark(matrix: np.ndarray, proc: Process = Process(), laser: Laser = Laser(), paint: Paint = Paint(),
              geo: Geometry = Geometry(), rng: np.random.Generator | None = None) -> Mark:
    rng = rng or np.random.default_rng(0)
    mask = fill_mask(matrix, geo).astype(float)
    size = mask.shape[0]
    x = (np.arange(size) - (size - 1) / 2.0) * geo.px
    m2 = effective_m2(laser, proc.window_transmission)
    energy = laser.pulse_energy * proc.energy_factor * proc.window_transmission
    dose = 1.0 / ((laser.speed / laser.rep_rate) * laser.hatch)

    def z_at(xv):
        return proc.focus_offset + xv * np.tan(proc.tilt) + xv ** 2 / (2.0 * geo.curvature_radius)

    xs = np.array([x[0], 0.0, x[-1]])
    planes = []
    for xv in xs:                                 # three focus planes: left edge, centre, right edge
        w = float(spot_radius(laser, z_at(xv), m2))
        k = _crater_kernel(energy, w, paint, geo.px)
        planes.append(signal.fftconvolve(mask, k, mode="same") * dose if k.size > 1 else np.zeros_like(mask))
    wl = np.clip((0.0 - x) / (0.0 - x[0]), 0.0, 1.0)[None, :]
    wr = np.clip(x / x[-1], 0.0, 1.0)[None, :]
    depth = planes[0] * wl + planes[2] * wr + planes[1] * (1.0 - wl - wr)

    wav = ndimage.gaussian_filter(rng.standard_normal(mask.shape), paint.waviness_length / geo.px)
    thick = proc.paint_thickness + wav * (paint.waviness / max(wav.std(), 1e-12))
    exposed = 0.5 * (1.0 + special.erf((depth - thick) / (paint.roughness * np.sqrt(2.0))))
    tex = ndimage.gaussian_filter(rng.standard_normal(mask.shape), 2.0)
    tex *= paint.texture / max(tex.std(), 1e-12)
    refl = proc.r_paint * (1.0 + tex) * (1.0 - exposed) + paint.r_sub * exposed
    w_c = float(spot_radius(laser, proc.focus_offset, m2))
    info = {
        "w0_um": waist(laser, m2) * 1e6, "zR_mm": rayleigh(laser, m2) * 1e3, "w_um": w_c * 1e6,
        "F0_Jcm2": float(peak_fluence(energy, w_c)) * 1e-4,
        "D_um": float(ablated_diameter(energy, w_c, paint.f_th)) * 1e6,
        "fill_depth_um": float(mean_fill_depth(energy, w_c, laser, paint)) * 1e6,
    }
    return Mark(np.clip(refl, 0.0, 1.0), exposed, info)


def nominal_process(paint: Paint = Paint()) -> Process:
    return Process(paint_thickness=paint.thickness, r_paint=paint.r_paint)


__all__ = ["Laser", "Paint", "Geometry", "Process", "Mark", "make_mark", "waist", "rayleigh", "spot_radius",
           "peak_fluence", "ablated_diameter", "mean_fill_depth", "effective_m2", "fill_mask", "nominal_process",
           "replace"]
