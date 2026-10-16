"""Ground heights for a course, for the uphill / downhill numbers in the book.

The open elevation models are surface models: over trees and roofs they measure the top, not the ground.
Parkland courses are full of trees, so instead of throwing those samples away the fit models them:

    sample height = ground(x, y) + beta * canopy share of the sample + noise

  * samples: the Copernicus DEM GLO-30 (courses/dem/<slug>-cop30.json; TanDEM-X radar, about 30 m apart)
  * canopy share: how much of each sample's footprint (plus a margin) is under the tree crowns measured from
    the photo (courses/edits/<slug>.json); buildings (map footprints) and anything off the course are left out
  * ground: a smooth surface (thin-plate spline); samples under more canopy are trusted less
  * beta (how far the radar sits above the ground per unit of canopy) is estimated from the samples
    themselves, alternating with the surface fit
The smoothing and the canopy margin are chosen by cross-validation on open-ground samples (predicting samples
the fit didn't see). Output: courses/terrain/<slug>.json on a 5 m grid (the format the app reads).
Usage: python3 scripts/ground_model.py <slug> [qa_dir]
"""
import base64, json, math, os, sys
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage
from scipy.interpolate import RBFInterpolator


def ellipsoid_k(lat0):
    sn = math.sin(math.radians(lat0)); w = 1 - 0.00669437999014 * sn * sn
    kx = 6378137 / math.sqrt(w) * math.cos(math.radians(lat0)) * math.pi / 180
    ky = 6378137 * (1 - 0.00669437999014) / w ** 1.5 * math.pi / 180
    return kx, ky


class Course:
    def __init__(self, slug):
        self.slug = slug
        dem = json.load(open(f"courses/dem/{slug}-cop30.json"))
        a, b, c, d, e, f = dem["transform"]
        Wd, Hd = dem["width"], dem["height"]
        self.Z = np.frombuffer(base64.b64decode(dem["data"]), "<f4").reshape(Hd, Wd).astype(float)
        cols, rows = np.meshgrid(np.arange(Wd) + 0.5, np.arange(Hd) + 0.5)
        self.LON = a * cols + b * rows + c; self.LAT = d * cols + e * rows + f
        self.lat0, self.lon0 = float(self.LAT.mean()), float(self.LON.mean())
        self.kx, self.ky = ellipsoid_k(self.lat0)
        self.X, self.Y = self.fx(self.LON), self.fy(self.LAT)
        self.px_w, self.px_h = abs(a) * self.kx, abs(e) * self.ky
        self.ed = json.load(open(f"courses/edits/{slug}.json"))
        self.base = json.load(open(f"courses/{slug}.json"))
        xp = f"courses/osm-extra/{slug}.json"
        self.extra = json.load(open(xp)) if os.path.exists(xp) else {"elements": []}
        self._masks()

    fx = lambda self, lo: (lo - self.lon0) * self.kx
    fy = lambda self, la: (la - self.lat0) * self.ky

    def _masks(self):
        X, Y = self.X, self.Y
        self.x0, x1 = X.min() - self.px_w, X.max() + self.px_w
        y0, self.y1 = Y.min() - self.px_h, Y.max() + self.px_h
        self.gw, self.gh = int(x1 - self.x0) + 1, int(self.y1 - y0) + 1
        to_px = lambda x, y: (x - self.x0, self.y1 - y)
        def raster(polys, circles=()):
            im = Image.new("L", (self.gw, self.gh), 0); dr = ImageDraw.Draw(im)
            for P in polys:
                if len(P) > 2: dr.polygon([to_px(self.fx(lo), self.fy(la)) for la, lo in P], fill=1)
            for la, lo, r in circles:
                cx, cy = to_px(self.fx(lo), self.fy(la)); R = r + 1.0
                dr.ellipse([cx - R, cy - R, cx + R, cy + R], fill=1)
            return np.asarray(im, dtype=np.float32)
        self.canopy = raster([], self.ed.get("trees", []))
        self.water = raster([s["coords"] for s in self.ed["shapes"] if s["kind"] == "water"])
        blds = [[(p["lat"], p["lon"]) for p in el.get("geometry") or [] if p] for el in self.extra["elements"] if (el.get("tags") or {}).get("building")]
        self.building = raster(blds)
        bnd = next(([(p["lat"], p["lon"]) for p in el["geometry"]] for el in self.base["elements"]
                    if (el.get("tags") or {}).get("leisure") == "golf_course" and el.get("geometry")), None)
        course = raster([bnd] if bnd else [])
        play = raster([s["coords"] for s in self.ed["shapes"]])
        self.course = np.maximum(ndimage.binary_dilation(course > 0, iterations=30), ndimage.binary_dilation(play > 0, iterations=40)).astype(np.float32)

    def frac(self, mask, grow):
        S = np.pad(mask, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
        hw, hh = self.px_w / 2 + grow, self.px_h / 2 + grow
        cx, cy = self.X - self.x0, self.y1 - self.Y
        i0 = np.clip((cy - hh).astype(int), 0, self.gh); i1 = np.clip((cy + hh).astype(int), 0, self.gh)
        j0 = np.clip((cx - hw).astype(int), 0, self.gw); j1 = np.clip((cx + hw).astype(int), 0, self.gw)
        tot = S[i1, j1] - S[i0, j1] - S[i1, j0] + S[i0, j0]
        return tot / np.maximum((i1 - i0) * (j1 - j0), 1)

    def samples(self, grow, fmax):
        f_can = self.frac(self.canopy, grow)
        f_bld, f_crs = self.frac(self.building, 8), self.frac(self.course, 0)
        ok = (f_bld < 0.02) & (f_crs > 0.5) & np.isfinite(self.Z) & (f_can <= fmax)
        return np.column_stack([self.X[ok], self.Y[ok]]), self.Z[ok], f_can[ok]


def fit_ground(P, z, f, s0, k, iters=8, beta0=8.0):
    """alternate: (1) the ground surface through heights with the canopy effect taken off; (2) the canopy effect
    from how much higher than that surface each sample sits, against its canopy share (least squares)"""
    beta = beta0
    sm = s0 * (1 + k * f)                      # samples under more canopy are trusted less
    for _ in range(iters):
        g = RBFInterpolator(P, z - beta * f, kernel="thin_plate_spline", smoothing=sm, degree=1)
        r = z - g(P)                           # = beta_true * f + noise, if the surface is right
        nb = float(np.clip(np.sum(f * r) / max(np.sum(f * f), 1e-9), 0.0, 25.0))
        if abs(nb - beta) < 0.05: beta = nb; break
        beta = nb
    g = RBFInterpolator(P, z - beta * f, kernel="thin_plate_spline", smoothing=sm, degree=1)
    return g, beta


def solve(c, grow, s0, k, fmax, folds=5, seed=3):
    P, z, f = c.samples(grow, fmax)
    open_ = f < 0.15
    rng = np.random.default_rng(seed); fold = rng.integers(0, folds, len(z))
    errs = []
    for q in range(folds):
        tr, te = fold != q, (fold == q) & open_
        if te.sum() == 0: continue
        g, beta = fit_ground(P[tr], z[tr], f[tr], s0, k)
        errs.append(g(P[te]) + beta * f[te] - z[te])
    cv = float(np.sqrt(np.mean(np.concatenate(errs) ** 2))) if errs else float("nan")
    g, beta = fit_ground(P, z, f, s0, k)
    return g, beta, cv, len(z), int(open_.sum())


def write_terrain(c, g, note):
    idx = {cc["slug"]: cc for cc in json.load(open("courses/index.json"))["courses"]}
    s, n, w, e_ = idx[c.slug]["bbox"]; pad = 0.003
    s, n, w, e_ = s - pad, n + pad, w - pad, e_ + pad
    step = 5.0
    dlat, dlon = step / c.ky, step / c.kx
    ny, nx = int((n - s) / dlat) + 1, int((e_ - w) / dlon) + 1
    lats = n - np.arange(ny) * dlat; lons = w + np.arange(nx) * dlon
    GX, GY = np.meshgrid(c.fx(lons), c.fy(lats))
    Q = np.column_stack([GX.ravel(), GY.ravel()])
    out = np.concatenate([g(Q[i:i + 6000]) for i in range(0, len(Q), 6000)]).reshape(ny, nx)
    base_h = float(np.floor(out.min())); scale = 0.02
    q = np.clip(np.round((out - base_h) / scale), 0, 65535).astype("<u2")
    json.dump({"s": s, "w": w, "n": n, "e": e_, "nx": nx, "ny": ny, "step_m": step, "dlat": dlat, "dlon": dlon, "base": base_h, "scale": scale,
               "source": note, "data": base64.b64encode(q.tobytes()).decode()}, open(f"courses/terrain/{c.slug}.json", "w"))
    return out, (s, n, w, e_, dlat, dlon)


def main(slug, qa_dir=None):
    c = Course(slug)
    best = None
    for grow in (5.0, 10.0):
        for fmax in (0.6, 0.9):
            for s0 in (20.0, 60.0, 200.0, 600.0):
                for k in (15.0, 50.0):
                    g, beta, cv, n_s, n_open = solve(c, grow, s0, k, fmax)
                    print(f"  margin {grow:>3.0f} m  max canopy {fmax:.1f}  smoothing {s0:>4.0f} x(1+{k:>2.0f}f)   beta {beta:5.1f} m   cv {cv:.2f} m   ({n_s} samples, {n_open} open)", flush=True)
                    if best is None or cv < best[0]: best = (cv, grow, fmax, s0, k, g, beta, n_s, n_open)
    cv, grow, fmax, s0, k, g, beta, n_s, n_open = best
    print(f"{slug}: best margin {grow} m, max canopy {fmax}, smoothing {s0}x(1+{k}f); trees raise the radar {beta:.1f} m per unit of canopy; "
          f"error predicting open ground it didn't see: {cv:.2f} m rms ({n_s} samples)")
    out, grid = write_terrain(c, g, f"Ground fitted to {n_s} samples of the Copernicus DEM GLO-30 with tree cover modelled out "
                                    f"(+{beta:.1f} m per unit canopy) and roofs left out; error on unseen open ground {cv:.1f} m rms")
    if qa_dir:
        os.makedirs(qa_dir, exist_ok=True)
        json.dump({"cv": cv, "beta": beta, "samples": n_s, "open": n_open, "grow": grow, "fmax": fmax, "s0": s0, "k": k},
                  open(os.path.join(qa_dir, f"{slug}-ground.json"), "w"))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
