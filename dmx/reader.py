"""Reader model: lighting, optics, sensor, a simple locator, module sampling and decode.

The locator is deliberately simple and is NOT the ISO/IEC 16022 reference decode
algorithm: it assumes the symbol is roughly axis-aligned in the image, finds the
outer edges of the solid "L" from threshold crossings and puts a uniform grid
between them. All reader numbers are ILLUSTRATIVE.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from . import ecc200
from .mark import Geometry


@dataclass(frozen=True)
class Reader:
    name: str
    ppm: float               # image pixels per module
    blur: float              # optical blur, Gaussian sigma in modules
    noise: float             # sensor noise, 1 sigma in reflectance units
    gradient: float          # relative lighting fall-off across the field (0.2 = +-10 %)
    glare: float             # additive specular highlight, peak in reflectance units
    glare_sigma: float       # modules
    view_angle: float        # rad, 1 sigma of the viewing tilt (foreshortening along x)
    threshold: str           # "minvar" (ISO/IEC 29158 style) or "midrange" (ISO/IEC 15415 style)
    aperture: float = 0.5    # sampling aperture, fraction of the module


# Station reader: fixed mount, diffuse dome light, part in a fixture.
STATION = Reader("station", ppm=10.0, blur=0.12, noise=0.006, gradient=0.06, glare=0.0, glare_sigma=4.0,
                 view_angle=np.radians(1.0), threshold="minvar")
# Later reader: lower resolution, more blur, directional light on a curved glossy part, hand or line presentation.
DOWNSTREAM = Reader("downstream", ppm=5.0, blur=0.28, noise=0.02, gradient=0.30, glare=0.06, glare_sigma=3.5,
                    view_angle=np.radians(8.0), threshold="midrange")


def acquire(scene: np.ndarray, geo: Geometry, reader: Reader, rng: np.random.Generator) -> np.ndarray:
    """Image in reflectance units (a calibrated reader), rows top to bottom."""
    size = scene.shape[0]
    u = (np.arange(size) - (size - 1) / 2.0) / size
    ang = rng.uniform(0.0, 2.0 * np.pi)
    g = reader.gradient * rng.uniform(0.3, 1.0)
    light = 1.0 + g * (u[None, :] * np.cos(ang) + u[:, None] * np.sin(ang))
    img = scene * light
    if reader.glare > 0.0:
        cx, cy = rng.uniform(-0.35, 0.35, 2) * size
        s = reader.glare_sigma * geo.ppm
        xx = (np.arange(size) - (size - 1) / 2.0)
        img = img + reader.glare * rng.uniform(0.0, 1.0) * np.exp(-((xx[None, :] - cx) ** 2 + (xx[:, None] - cy) ** 2) / (2 * s * s))
    img = ndimage.gaussian_filter(img, reader.blur * geo.ppm, mode="nearest")
    k = geo.ppm / reader.ppm                                   # scene pixels per image pixel
    img = ndimage.uniform_filter(img, size=max(1, int(round(k))), mode="nearest")
    cosv = np.cos(rng.normal(0.0, reader.view_angle))
    out = int(round(size / k))
    sx, sy = rng.uniform(-0.5, 0.5, 2)
    yi = (np.arange(out) + 0.5 + sy) * k - 0.5
    xi = ((np.arange(out) + 0.5 + sx) * k - 0.5 - (size - 1) / 2.0) / cosv + (size - 1) / 2.0
    img = ndimage.map_coordinates(img, np.meshgrid(yi, xi, indexing="ij"), order=1, mode="nearest")
    img = img + rng.normal(0.0, reader.noise, img.shape)
    return np.clip(img, 0.0, 1.0)


def minvar_threshold(v: np.ndarray) -> float:
    """Threshold that minimises the summed variance of the two groups (Otsu)."""
    s = np.sort(np.asarray(v, dtype=float).ravel())
    n = len(s)
    c1, c2 = np.cumsum(s), np.cumsum(s * s)
    k = np.arange(1, n)
    v_lo = c2[k - 1] - c1[k - 1] ** 2 / k
    v_hi = (c2[-1] - c2[k - 1]) - (c1[-1] - c1[k - 1]) ** 2 / (n - k)
    i = int(np.argmin(v_lo + v_hi))
    return 0.5 * (s[i] + s[i + 1])


def global_threshold(v: np.ndarray, method: str) -> float:
    if method == "minvar":
        return minvar_threshold(v)
    return 0.5 * (float(np.max(v)) + float(np.min(v)))


def _crossings(profile: np.ndarray, t: float) -> np.ndarray:
    """Sub-pixel positions where the profile crosses t (pixel-centre coordinates)."""
    d = profile - t
    i = np.nonzero(np.signbit(d[:-1]) != np.signbit(d[1:]))[0]
    return i + d[i] / (d[i] - d[i + 1])


@dataclass
class Located:
    x0: float
    x1: float
    y0: float
    y1: float

    def pitch(self, n: int) -> tuple[float, float]:
        return (self.x1 - self.x0) / n, (self.y1 - self.y0) / n


def locate(img: np.ndarray, n: int, t: float) -> Located | None:
    dark = img < t
    cols, rows = dark.sum(axis=0), dark.sum(axis=1)
    if cols.max() < 4 or rows.max() < 4:
        return None
    xs = np.nonzero(cols >= 0.3 * cols.max())[0]
    ys = np.nonzero(rows >= 0.3 * rows.max())[0]
    x0, x1, y0, y1 = xs[0] - 0.5, xs[-1] + 0.5, ys[0] - 0.5, ys[-1] + 0.5
    for _ in range(2):                               # refine on the solid bars of the "L"
        px, py = (x1 - x0) / n, (y1 - y0) / n
        if px < 1.5 or py < 1.5:
            return None
        ra, rb = int(max(0, np.floor(y0 + 2 * py))), int(min(img.shape[0], np.ceil(y1 - 2 * py)))
        ca, cb = int(max(0, np.floor(x0 + 2 * px))), int(min(img.shape[1], np.ceil(x1 - 2 * px)))
        if rb - ra < 2 or cb - ca < 2:
            return None
        cx = _crossings(img[ra:rb, :].mean(axis=0), _bar_level(img[ra:rb, :].mean(axis=0), x0, px))
        cy = _crossings(img[:, ca:cb].mean(axis=1), _bar_level(img[:, ca:cb].mean(axis=1), y1 - py, py))
        bl, bb = int(max(0, np.floor(y1 - 0.8 * py))), int(np.ceil(y1 - 0.2 * py))
        ll, lr = int(max(0, np.floor(x0 + 0.2 * px))), int(np.ceil(x0 + 0.8 * px))
        if bb - bl < 1 or lr - ll < 1:
            return None
        ex = _crossings(img[bl:bb, :].mean(axis=0), t)
        ey = _crossings(img[:, ll:lr].mean(axis=1), t)
        if not len(cx) or not len(cy) or not len(ex) or not len(ey):
            return None
        x0 = float(cx[np.argmin(np.abs(cx - x0))])
        y1 = float(cy[np.argmin(np.abs(cy - y1))])
        x1 = float(ex[np.argmin(np.abs(ex - x1))])
        y0 = float(ey[np.argmin(np.abs(ey - y0))])
        if x1 - x0 < 2 * n or y1 - y0 < 2 * n:
            return None
    return Located(x0, x1, y0, y1)


def _bar_level(profile: np.ndarray, start: float, pitch: float) -> float:
    """Half-way level between a solid bar (one module wide from `start`) and the light side next to it."""
    a = int(np.clip(round(start + 0.25 * pitch), 0, len(profile) - 1))
    b = int(np.clip(round(start + 0.75 * pitch), a + 1, len(profile)))
    bar = float(profile[a:b].mean())
    return 0.5 * (bar + float(np.percentile(profile, 95)))


def sample_modules(img: np.ndarray, loc: Located, n: int, aperture: float = 0.5) -> np.ndarray:
    """Mean over the aperture at every module centre, including a one-module quiet-zone ring."""
    px, py = loc.pitch(n)
    idx = np.arange(-1, n + 1) + 0.5
    sub = (np.arange(3) + 0.5) / 3.0 - 0.5
    ys = loc.y0 + (idx[:, None] + sub[None, :] * aperture) * py      # edges are already in pixel-centre coordinates
    xs = loc.x0 + (idx[:, None] + sub[None, :] * aperture) * px
    yy = np.broadcast_to(ys[:, None, :, None], (n + 2, n + 2, 3, 3))
    xx = np.broadcast_to(xs[None, :, None, :], (n + 2, n + 2, 3, 3))
    return ndimage.map_coordinates(img, [yy, xx], order=1, mode="nearest").mean(axis=(2, 3))


@dataclass
class Read:
    ok: bool
    text: str = ""
    decoded: ecc200.Decoded | None = None
    samples: np.ndarray | None = None      # (n+2, n+2) module reflectances with the quiet-zone ring
    threshold: float = 0.0
    located: Located | None = None
    image: np.ndarray | None = None
    reason: str = ""


def read(img: np.ndarray, n: int, reader: Reader) -> Read:
    t_img = global_threshold(img, reader.threshold)
    loc = locate(img, n, t_img)
    if loc is None:
        return Read(False, image=img, reason="finder not located")
    s = sample_modules(img, loc, n, reader.aperture)
    t = global_threshold(s[1:-1, 1:-1], reader.threshold)
    dec = ecc200.decode(s[1:-1, 1:-1] < t)
    return Read(dec.ok, dec.text, dec, s, t, loc, img, dec.reason)
