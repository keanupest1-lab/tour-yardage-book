"""Ground heights for a course, for the uphill / downhill numbers in the book.

Every source of heights we have for a course goes into one bare-ground surface:

  * laser ground returns, the most direct measurement: ICESat-2 ground photons fitted in 20 m segments, and GEDI
    lowest-mode footprints (courses/heights/<slug>-icesat2.json, <slug>-gedi.json; scripts/fetch_heights.py)
  * surface models about 30 m apart: Copernicus GLO-30 (TanDEM-X radar), ALOS AW3D30 (optical stereo) and NASADEM
    (SRTM radar, 2000) (courses/dem/<slug>-<model>.json; scripts/fetch_dem.py, scripts/fetch_heights.py)

The surface models measure the top of whatever stands there, so over trees they read high. Instead of throwing those
samples away the fit models them:

    model pixel = mean ground over the pixel + offset(model) + beta(model) * canopy share of the pixel + noise

with the canopy share measured from the tree crowns traced on the photo (courses/edits/<slug>.json). Pixels with roofs
on them, mostly water, or off the course are left out. The ground is a grid held smooth by a thin-plate penalty; the
offsets (each source has its own height datum), the betas and each source's noise level are estimated together with
it, and samples that disagree badly with the rest are down-weighted (iterated).

How smooth the ground is allowed to be, and which sources earn their place, is decided by cross-validation: whole
laser passes are held out and predicted, and the slopes along them (height differences 60-300 m apart, what the
book's numbers are) are compared.

Output: courses/terrain/<slug>.json on a 5 m grid (the format the app reads: src/terrain.js).
Usage: python3 scripts/ground_model.py <slug> [--cv] [--qa dir] [--sources cop30,aw3d30,...]
"""
import argparse, base64, collections, json, math, os, sys, time
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage, sparse
from scipy.sparse.linalg import splu

DEMS = ("cop30", "aw3d30", "nasadem")


def ellipsoid_k(lat0):
    sn = math.sin(math.radians(lat0)); w = 1 - 0.00669437999014 * sn * sn
    kx = 6378137 / math.sqrt(w) * math.cos(math.radians(lat0)) * math.pi / 180
    ky = 6378137 * (1 - 0.00669437999014) / w ** 1.5 * math.pi / 180
    return kx, ky


def load_dem(path):
    d = json.load(open(path))
    a, b, c, dd, e, f = d["transform"]
    Z = np.frombuffer(base64.b64decode(d["data"]), "<f4").reshape(d["height"], d["width"]).astype(float)
    cols, rows = np.meshgrid(np.arange(d["width"]) + 0.5, np.arange(d["height"]) + 0.5)
    return {"Z": Z, "lon": a * cols + b * rows + c, "lat": dd * cols + e * rows + f, "dlon": abs(a), "dlat": abs(e), "source": d.get("source", "")}


def vals(d, k):
    v = d.get(k)
    return None if v is None else np.array([np.nan if x is None else x for x in v], float)


class Course:
    """Everything about the course the fit needs, on local metres (x east, y north)."""

    MASK_RES = 2.0      # metres per pixel of the land-cover masks

    def __init__(self, slug):
        self.slug = slug
        idx = {c["slug"]: c for c in json.load(open("courses/index.json"))["courses"]}
        s, n, w, e = idx[slug]["bbox"]
        self.bbox = (s, n, w, e)
        pad = 0.004
        self.S, self.N, self.W, self.E = s - pad, n + pad, w - pad, e + pad
        self.lat0, self.lon0 = (s + n) / 2, (w + e) / 2
        self.kx, self.ky = ellipsoid_k(self.lat0)
        self.ed = json.load(open(f"courses/edits/{slug}.json"))
        self.base = json.load(open(f"courses/{slug}.json"))
        xp = f"courses/osm-extra/{slug}.json"
        self.extra = json.load(open(xp)) if os.path.exists(xp) else {"elements": []}
        self.x0, self.x1 = self.fx(self.W), self.fx(self.E)
        self.y0, self.y1 = self.fy(self.S), self.fy(self.N)
        self._masks()

    fx = lambda self, lo: (np.asarray(lo, float) - self.lon0) * self.kx
    fy = lambda self, la: (np.asarray(la, float) - self.lat0) * self.ky

    def _masks(self):
        r = self.MASK_RES
        self.mw, self.mh = int((self.x1 - self.x0) / r) + 1, int((self.y1 - self.y0) / r) + 1
        to_px = lambda x, y: ((x - self.x0) / r, (self.y1 - y) / r)

        def raster(polys, circles=()):
            im = Image.new("L", (self.mw, self.mh), 0); dr = ImageDraw.Draw(im)
            for P in polys:
                if len(P) > 2: dr.polygon([to_px(self.fx(lo), self.fy(la)) for la, lo in P], fill=1)
            for la, lo, rad in circles:
                cx, cy = to_px(self.fx(lo), self.fy(la)); R = (rad + 1.0) / r
                dr.ellipse([cx - R, cy - R, cx + R, cy + R], fill=1)
            return np.asarray(im, dtype=np.uint8)

        self.canopy = raster([], self.ed.get("trees", []))
        self.water = raster([s["coords"] for s in self.ed["shapes"] if s["kind"] == "water"])
        blds = [[(p["lat"], p["lon"]) for p in el.get("geometry") or [] if p] for el in self.extra["elements"] if (el.get("tags") or {}).get("building")]
        self.building = raster(blds)
        bnd = next(([(p["lat"], p["lon"]) for p in el["geometry"]] for el in self.base["elements"]
                    if (el.get("tags") or {}).get("leisure") == "golf_course" and el.get("geometry")), None)
        course = raster([bnd] if bnd else [])
        play = raster([s["coords"] for s in self.ed["shapes"]])
        self.course = np.maximum(ndimage.binary_dilation(course > 0, iterations=int(30 / r)),
                                 ndimage.binary_dilation(play > 0, iterations=int(40 / r))).astype(np.uint8)
        self._sat = {}

    def frac(self, name, X, Y, hw, hh, grow):
        """share of each footprint (centre X, Y; half sizes hw, hh, plus a margin) covered by a mask"""
        if name not in self._sat:
            m = getattr(self, name).astype(np.int32)
            self._sat[name] = np.pad(m, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
        S, r = self._sat[name], self.MASK_RES
        cx, cy = (np.asarray(X) - self.x0) / r, (self.y1 - np.asarray(Y)) / r
        ax, ay = (hw + grow) / r, (hh + grow) / r
        i0 = np.clip(np.floor(cy - ay).astype(int), 0, self.mh); i1 = np.clip(np.ceil(cy + ay).astype(int), 0, self.mh)
        j0 = np.clip(np.floor(cx - ax).astype(int), 0, self.mw); j1 = np.clip(np.ceil(cx + ax).astype(int), 0, self.mw)
        tot = S[i1, j1] - S[i0, j1] - S[i1, j0] + S[i0, j0]
        return tot / np.maximum((i1 - i0) * (j1 - j0), 1)

    # ---- the data
    def dem_samples(self, name, grow):
        p = f"courses/dem/{self.slug}-{name}.json"
        if not os.path.exists(p): return None
        d = load_dem(p)
        X, Y = self.fx(d["lon"]).ravel(), self.fy(d["lat"]).ravel(); Z = d["Z"].ravel()
        hw, hh = d["dlon"] * self.kx / 2, d["dlat"] * self.ky / 2
        inside = (X > self.x0 + hw) & (X < self.x1 - hw) & (Y > self.y0 + hh) & (Y < self.y1 - hh) & np.isfinite(Z)
        X, Y, Z = X[inside], Y[inside], Z[inside]
        f_can = self.frac("canopy", X, Y, hw, hh, grow)
        f_bld, f_crs, f_wat = self.frac("building", X, Y, hw, hh, 8), self.frac("course", X, Y, hw, hh, 0), self.frac("water", X, Y, hw, hh, 0)
        ok = (f_bld < 0.02) & (f_crs > 0.5) & (f_wat < 0.6)
        return {"kind": "dem", "name": name, "x": X[ok], "y": Y[ok], "z": Z[ok], "f": f_can[ok], "hw": hw, "hh": hh, "group": None}

    def icesat2(self):
        p = f"courses/heights/{self.slug}-icesat2.json"
        if not os.path.exists(p): return None
        d = json.load(open(p))
        la, lo, h, nph, rms = vals(d, "lat"), vals(d, "lon"), vals(d, "h"), vals(d, "n"), vals(d, "rms")
        X, Y = self.fx(lo), self.fy(la)
        ok = np.isfinite(h) & (nph >= 5) & (rms < 0.8) & (X > self.x0) & (X < self.x1) & (Y > self.y0) & (Y < self.y1)
        ok &= self.frac("course", X, Y, 1, 1, 0) > 0.5
        under = self.frac("canopy", X, Y, 1, 1, 0) > 0.3
        grp = np.array([f"is2 {int(a)}-{int(b)}" for a, b in zip(vals(d, "rgt"), vals(d, "cycle"))])
        beam = np.array([f"{g}-{int(s)}" for g, s in zip(grp, vals(d, "spot"))])
        return {"kind": "laser", "name": "icesat2", "x": X[ok], "y": Y[ok], "z": h[ok], "f": under[ok].astype(float), "r": 0.0,
                "group": grp[ok], "beam": beam[ok], "rms": rms[ok]}

    def gedi(self):
        p = f"courses/heights/{self.slug}-gedi.json"
        if not os.path.exists(p): return None
        d = json.load(open(p))
        la, lo, h = vals(d, "lat"), vals(d, "lon"), vals(d, "elev_lm")
        sens, flags = vals(d, "sensitivity"), vals(d, "flags")
        X, Y = self.fx(lo), self.fy(la)
        fl = np.nan_to_num(flags).astype(int)
        good = ((fl & 0x01) == 0) & ((fl & 0x02) != 0)          # not degraded, L2 quality flag set
        ok = np.isfinite(h) & good & (sens >= 0.9) & (X > self.x0) & (X < self.x1) & (Y > self.y0) & (Y < self.y1)
        ok &= self.frac("course", X, Y, 1, 1, 0) > 0.5
        under = self.frac("canopy", X, Y, 12.5, 12.5, 0)
        grp = np.array([f"gedi {int(o)}" for o in vals(d, "orbit")])
        beam = np.array([f"{g}-{int(b)}" for g, b in zip(grp, vals(d, "beam"))])
        return {"kind": "laser", "name": "gedi", "x": X[ok], "y": Y[ok], "z": h[ok], "f": under[ok], "r": 11.0,
                "group": grp[ok], "beam": beam[ok]}


class Grid:
    def __init__(self, c, cell):
        self.h = cell
        self.gx0, self.gy0 = c.x0, c.y0
        self.nx, self.ny = int((c.x1 - c.x0) / cell) + 2, int((c.y1 - c.y0) / cell) + 2
        self.n = self.nx * self.ny

    def bil(self, X, Y):
        """bilinear weights: rows of (cell index, weight), 4 per point"""
        fx = (np.asarray(X) - self.gx0) / self.h; fy = (np.asarray(Y) - self.gy0) / self.h
        i0 = np.clip(np.floor(fx).astype(int), 0, self.nx - 2); j0 = np.clip(np.floor(fy).astype(int), 0, self.ny - 2)
        tx, ty = fx - i0, fy - j0
        cols = np.stack([j0 * self.nx + i0, j0 * self.nx + i0 + 1, (j0 + 1) * self.nx + i0, (j0 + 1) * self.nx + i0 + 1], 1)
        w = np.stack([(1 - tx) * (1 - ty), tx * (1 - ty), (1 - tx) * ty, tx * ty], 1)
        return cols, w

    def footprint(self, X, Y, hw, hh, k=5):
        """weights averaging the ground over a rectangle (k x k samples, each bilinear)"""
        cs, ws = [], []
        for a in range(k):
            for b in range(k):
                ox = -hw + hw * (2 * a + 1) / k; oy = -hh + hh * (2 * b + 1) / k
                c_, w_ = self.bil(np.asarray(X) + ox, np.asarray(Y) + oy)
                cs.append(c_); ws.append(w_ / (k * k))
        return np.concatenate(cs, 1), np.concatenate(ws, 1)

    def disc(self, X, Y, r):
        if r <= 0: return self.bil(X, Y)
        cs, ws = [], []
        offs = [(0, 0)] + [(r * 0.6 * math.cos(a), r * 0.6 * math.sin(a)) for a in np.linspace(0, 2 * math.pi, 7)[:-1]]
        for ox, oy in offs:
            c_, w_ = self.bil(np.asarray(X) + ox, np.asarray(Y) + oy)
            cs.append(c_); ws.append(w_ / len(offs))
        return np.concatenate(cs, 1), np.concatenate(ws, 1)

    def smooth_rows(self):
        """thin-plate penalty: g_xx, 2 g_xy, g_yy as second differences (scaled 1/h)"""
        nx, ny, h = self.nx, self.ny, self.h
        I, J = np.meshgrid(np.arange(nx), np.arange(ny))
        rows, cols, vals_ = [], [], []
        r0 = 0
        def add(cells, coef):
            nonlocal r0
            m = cells[0].size
            for c_, k in zip(cells, coef):
                rows.append(np.arange(r0, r0 + m)); cols.append(c_.ravel()); vals_.append(np.full(m, k / h))
            r0 += m
        idx = lambda i, j: j * nx + i
        add([idx(I[:, :-2], J[:, :-2]), idx(I[:, 1:-1], J[:, 1:-1]), idx(I[:, 2:], J[:, 2:])], [1, -2, 1])
        add([idx(I[:-2, :], J[:-2, :]), idx(I[1:-1, :], J[1:-1, :]), idx(I[2:, :], J[2:, :])], [1, -2, 1])
        s2 = math.sqrt(2)
        add([idx(I[:-1, :-1], J[:-1, :-1]), idx(I[:-1, 1:], J[:-1, 1:]), idx(I[1:, :-1], J[1:, :-1]), idx(I[1:, 1:], J[1:, 1:])], [s2, -s2, -s2, s2])
        return sparse.csr_matrix((np.concatenate(vals_), (np.concatenate(rows), np.concatenate(cols))), shape=(r0, self.n))


def build_design(grid, sets):
    """one block per data set: the ground weights plus columns for the set's offset and canopy coefficient"""
    blocks = []
    for d in sets:
        if d["kind"] == "dem": cols, w = grid.footprint(d["x"], d["y"], d["hw"], d["hh"])
        else: cols, w = grid.disc(d["x"], d["y"], d["r"])
        m = len(d["z"])
        A = sparse.csr_matrix((w.ravel(), (np.repeat(np.arange(m), cols.shape[1]), cols.ravel())), shape=(m, grid.n))
        A.sum_duplicates()
        blocks.append(A)
    return blocks


SIGMA = {"cop30": 1.0, "aw3d30": 1.0, "nasadem": 2.5, "icesat2": 0.35, "gedi": 1.5}   # noise of each source, metres


def fit(grid, sets, blocks, lam, kappa=2.0, iters=3, ref=None, verbose=False, sigma=None):
    """weighted least squares for the ground grid, per-set offsets (one set is the datum) and per-model canopy
    coefficients; samples far off the fit are down-weighted (Huber) and the fit repeated.
    Returns the ground grid (ny, nx), the parameters, and the datum set."""
    sigma = dict(SIGMA, **(sigma or {}))
    names = [d["name"] for d in sets]
    ref = ref or next((nm for nm in names if nm in DEMS), names[0])
    extra = []                                   # extra unknowns: ('off', set) and ('beta', set)
    for d in sets:
        if d["name"] != ref: extra.append(("off", d["name"]))
        if d["kind"] == "dem": extra.append(("beta", d["name"]))
    k = len(extra); col = {e: i for i, e in enumerate(extra)}
    E = []
    for d in sets:
        m = len(d["z"]); Ed = np.zeros((m, k))
        if ("off", d["name"]) in col: Ed[:, col[("off", d["name"])]] = 1.0
        if ("beta", d["name"]) in col: Ed[:, col[("beta", d["name"])]] = d["f"]
        E.append(Ed)
    if getattr(grid, "_RtR", None) is None:
        Rs = grid.smooth_rows(); grid._RtR = (Rs.T @ Rs).tocsc()
    s_base = [sigma[d["name"]] * (1 + (kappa if d["kind"] == "dem" else 2.0) * d["f"]) for d in sets]
    robust = [np.ones(len(d["z"])) for d in sets]
    for it in range(iters):
        Ngg = grid._RtR * lam; Nge = np.zeros((grid.n, k)); Nee = np.zeros((k, k)); rg = np.zeros(grid.n); re = np.zeros(k)
        for d, A, Ed, sb, rw in zip(sets, blocks, E, s_base, robust):
            w2 = (rw / sb) ** 2
            AW = A.T.multiply(w2).tocsr() if False else (A.T @ sparse.diags(w2)).tocsr()
            Ngg = Ngg + AW @ A; Nge += AW @ Ed; rg += AW @ d["z"]
            Nee += Ed.T @ (Ed * w2[:, None]); re += Ed.T @ (w2 * d["z"])
        Ngg = (Ngg + sparse.identity(grid.n, format="csc") * 1e-8).tocsc()
        lu = splu(Ngg, permc_spec="MMD_ATA")
        Y = lu.solve(np.column_stack([rg, Nge]))
        y0, Yg = Y[:, 0], Y[:, 1:]
        if k:
            S = Nee - Nge.T @ Yg
            e = np.linalg.solve(S + np.eye(k) * 1e-9, re - Nge.T @ y0)
            g = y0 - Yg @ e
        else:
            e = np.zeros(0); g = y0
        for j, (d, A, Ed, sb) in enumerate(zip(sets, blocks, E, s_base)):
            r = d["z"] - (A @ g + Ed @ e)
            z_ = np.abs(r) / sb
            robust[j] = np.where(z_ > 2.5, 2.5 / np.maximum(z_, 1e-9), 1.0)
            d["resid"] = r
        if verbose:
            print(f"    pass {it}: " + "  ".join(f"{d['name']} rms {np.sqrt(np.mean(d['resid'] ** 2)):.2f} (down-weighted {np.mean(rw < 1) * 100:.0f}%)" for d, rw in zip(sets, robust))
                  + "  " + "  ".join(f"{x[0]}[{x[1]}] {e[col[x]]:+.2f}" for x in extra), flush=True)
    params = {f"{x[0]}:{x[1]}": float(e[col[x]]) for x in extra}
    return g.reshape(grid.ny, grid.nx), params, ref


def predict(grid, G, X, Y, r=0.0):
    cols, w = grid.disc(X, Y, r)
    return (G.ravel()[cols] * w).sum(1)


def pair_stats(x, y, z_true, z_pred, beams, dmin=60, dmax=300):
    """height differences between points on the same beam, dmin-dmax metres apart"""
    dt, dp = [], []
    for bm in np.unique(beams):
        k = np.where(beams == bm)[0]
        if len(k) < 2: continue
        X, Y = x[k], y[k]
        for a in range(len(k)):
            L = np.hypot(X[a + 1:] - X[a], Y[a + 1:] - Y[a])
            sel = np.where((L >= dmin) & (L <= dmax))[0] + a + 1
            for b in sel:
                dt.append(z_true[k[b]] - z_true[k[a]]); dp.append(z_pred[k[b]] - z_pred[k[a]])
    return np.array(dt), np.array(dp)


def cross_validate(c, grid, sets_all, use, lam, kappa, folds=6, seed=7, sigma=None, keep_points=None):
    """laser passes (ICESat-2 passes, GEDI orbits) split into folds; each fold held out in turn, the ground fitted to
    everything else in `use`, and the held-out passes predicted. Scored per laser kind: height (each pass on its own
    datum, so only shapes count) and the slopes along each beam (height differences 60-300 m apart)."""
    lasers = [d for d in sets_all if d["kind"] == "laser"]
    groups = sorted({g for d in lasers for g in d["group"]})
    rng = np.random.default_rng(seed); order = rng.permutation(len(groups))
    fold_of = {groups[i]: k % folds for k, i in enumerate(order)}
    acc = collections.defaultdict(lambda: {"r": [], "dt": [], "dp": [], "passes": 0})
    for q in range(folds):
        held = {g for g in groups if fold_of[g] == q}
        train = []
        for d in sets_all:
            if d["name"] not in use: continue
            if d["kind"] == "laser":
                keep = np.array([g not in held for g in d["group"]])
                if keep.sum() == 0: continue
                train.append({**d, **{k: d[k][keep] for k in ("x", "y", "z", "f", "group", "beam")}})
            else: train.append(d)
        if not train: continue
        G, params, ref = fit(grid, train, build_design(grid, train), lam, kappa, iters=2, sigma=sigma)
        for d in lasers:
            for g in held:
                m = d["group"] == g
                if m.sum() < 8: continue
                zp = predict(grid, G, d["x"][m], d["y"][m], d["r"])
                r = zp - d["z"][m]; r = r - np.median(r)
                if keep_points is not None:
                    keep_points.extend({"set": d["name"], "pass": g, "x": float(x), "y": float(y), "h": float(h), "pred": float(p_ - np.median(zp - d["z"][m]))}
                                       for x, y, h, p_ in zip(d["x"][m], d["y"][m], d["z"][m], zp))
                a = acc[d["name"]]; a["r"].append(r); a["passes"] += 1
                dt, dp = pair_stats(d["x"][m], d["y"][m], d["z"][m], zp, d["beam"][m])
                a["dt"].append(dt); a["dp"].append(dp)
    out = {}
    for nm, a in acc.items():
        r = np.concatenate(a["r"]); dt = np.concatenate(a["dt"]); dp = np.concatenate(a["dp"])
        s1, s2 = np.abs(dt) > 1, np.abs(dt) > 2
        out[nm] = {"rms": float(np.sqrt(np.mean(r ** 2))), "mad": float(1.4826 * np.median(np.abs(r))), "n": int(len(r)), "passes": a["passes"],
                   "pair_rms": float(np.sqrt(np.mean((dp - dt) ** 2))), "pair_mad": float(1.4826 * np.median(np.abs(dp - dt))), "pairs": int(len(dt)),
                   "sign1": float(np.mean(np.sign(dt[s1]) == np.sign(dp[s1])) * 100) if s1.any() else float("nan"),
                   "sign2": float(np.mean(np.sign(dt[s2]) == np.sign(dp[s2])) * 100) if s2.any() else float("nan")}
    return out


def write_terrain(c, grid, G, note, offset=0.0, out_path=None):
    s, n, w, e_ = c.bbox; pad = 0.003
    s, n, w, e_ = s - pad, n + pad, w - pad, e_ + pad
    step = 5.0
    dlat, dlon = step / c.ky, step / c.kx
    ny, nx = int((n - s) / dlat) + 1, int((e_ - w) / dlon) + 1
    lats = n - np.arange(ny) * dlat; lons = w + np.arange(nx) * dlon
    GX, GY = np.meshgrid(c.fx(lons), c.fy(lats))
    out = predict(grid, G, GX.ravel(), GY.ravel()).reshape(ny, nx) + offset
    base_h = float(np.floor(out.min())); scale = 0.02
    q = np.clip(np.round((out - base_h) / scale), 0, 65535).astype("<u2")
    json.dump({"s": s, "w": w, "n": n, "e": e_, "nx": nx, "ny": ny, "step_m": step, "dlat": dlat, "dlon": dlon, "base": base_h, "scale": scale,
               "source": note, "data": base64.b64encode(q.tobytes()).decode()}, open(out_path or f"courses/terrain/{c.slug}.json", "w"))
    return out


def parse_use(use):
    """['cop30@1.5', 'aw3d30'] -> (['cop30', 'aw3d30'], {'cop30': 1.5}): sources, and noise levels other than the defaults"""
    names, sig = [], {}
    for u in use:
        nm, _, sg = u.partition("@"); names.append(nm)
        if sg: sig[nm] = float(sg)
    return names, sig


def load_sets(c, grow):
    sets = []
    for nm in DEMS:
        d = c.dem_samples(nm, grow)
        if d is not None and len(d["z"]): sets.append(d)
    for d in (c.icesat2(), c.gedi()):
        if d is not None and len(d["z"]): sets.append(d)
    return sets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug"); ap.add_argument("--cv", action="store_true"); ap.add_argument("--qa")
    ap.add_argument("--sources"); ap.add_argument("--lam", type=float); ap.add_argument("--grow", type=float)
    ap.add_argument("--kappa", type=float); ap.add_argument("--cell", type=float, default=10.0)
    ap.add_argument("--no-write", action="store_true"); ap.add_argument("--out", help="write the terrain here instead")
    a = ap.parse_args()
    t0 = time.time()
    c = Course(a.slug); grid = Grid(c, a.cell)
    print(f"{a.slug}: ground grid {grid.nx} x {grid.ny} at {grid.h:.0f} m", flush=True)
    results = []
    if a.cv:
        configs = []
        for grow in ((a.grow,) if a.grow is not None else (5.0, 10.0)):
            sets_all = load_sets(c, grow)
            names = [d["name"] for d in sets_all]
            print("  data: " + ", ".join(f"{d['name']} {len(d['z'])}" for d in sets_all), flush=True)
            dem_names = [n for n in names if n in DEMS]
            laser_names = [n for n in names if n not in DEMS]
            combos = [s.split("+") for s in a.sources.split(";")] if a.sources else [
                ["aw3d30"], ["cop30", "aw3d30"], ["cop30@1.5", "aw3d30"], ["cop30@2", "aw3d30"],
                ["aw3d30", "icesat2"], ["cop30@1.5", "aw3d30", "icesat2"], ["cop30@1.5", "aw3d30", "icesat2", "gedi"],
                ["cop30@1.5", "aw3d30", "nasadem@3", "icesat2", "gedi"]]
            combos = [u for u in combos if all(x.partition("@")[0] in names for x in u)]
            for use in combos:
                for lam in ((a.lam,) if a.lam is not None else (100.0, 300.0, 1000.0, 3000.0)):
                    for kappa in ((a.kappa,) if a.kappa is not None else (2.0,)):
                        t = time.time()
                        names_, sig_ = parse_use(use)
                        r = cross_validate(c, grid, sets_all, names_, lam, kappa, sigma=sig_)
                        results.append({"grow": grow, "use": use, "lam": lam, "kappa": kappa, "scores": r})
                        line = "  ".join(f"[{nm}: {v['passes']} passes, rms {v['rms']:.2f} (mad {v['mad']:.2f}), slopes rms {v['pair_rms']:.2f} (mad {v['pair_mad']:.2f}), sign {v['sign1']:.1f}%/{v['sign2']:.1f}%]" for nm, v in sorted(r.items()))
                        print(f"  {'+'.join(use):<30} margin {grow:>3.0f} smooth {lam:>6.0f}  {line}  {time.time() - t:.0f}s", flush=True)
        if a.qa:
            os.makedirs(a.qa, exist_ok=True)
            json.dump(results, open(os.path.join(a.qa, f"{a.slug}-ground-cv.json"), "w"), indent=1)
        # the score: slope error on held-out ICESat-2 passes (the most exact heights), then on GEDI
        def score(r):
            sc = r["scores"]
            return (sc.get("icesat2", {}).get("pair_rms", 0) ** 2 + 0.5 * sc.get("gedi", {}).get("pair_mad", 0) ** 2)
        best = min(results, key=score)
        grow, use, lam, kappa = best["grow"], best["use"], best["lam"], best["kappa"]
        print(f"best: {'+'.join(use)} margin {grow} smooth {lam} κ {kappa}: " + "  ".join(f"{nm} slopes rms {v['pair_rms']:.2f} m, sign right {v['sign1']:.1f}%" for nm, v in best["scores"].items()))
        if a.qa:      # the held-out predictions of the chosen fit, point by point (for checking hole by hole)
            pts = []; names_, sig_ = parse_use(use)
            cross_validate(c, grid, load_sets(c, grow), names_, lam, kappa, sigma=sig_, keep_points=pts)
            for q in pts: q["lat"] = q["y"] / c.ky + c.lat0; q["lon"] = q["x"] / c.kx + c.lon0
            json.dump(pts, open(os.path.join(a.qa, f"{a.slug}-ground-heldout.json"), "w"))
    else:
        grow = a.grow if a.grow is not None else 5.0; lam = a.lam if a.lam is not None else 30.0; kappa = a.kappa if a.kappa is not None else 2.0
        use = a.sources.split("+") if a.sources else None
        best = None
    names_, sig_ = parse_use(use) if use else (None, {})
    sets = [d for d in load_sets(c, grow) if names_ is None or d["name"] in names_]
    G, params, ref = fit(grid, sets, build_design(grid, sets), lam, kappa, verbose=True, sigma=sig_)
    used = ", ".join(f"{d['name']} ({len(d['z'])})" for d in sets)
    betas = ", ".join(f"{k.split(':')[1]} +{v:.1f} m" for k, v in params.items() if k.startswith("beta"))
    note = (f"Bare ground fitted to {used}: surface models with tree cover modelled out (per unit canopy {betas}), roofs left out; "
            f"datum {ref}" + ("; on laser passes it didn't see: " + "; ".join(f"{nm} heights {v['rms']:.1f} m rms, slopes {v['pair_rms']:.1f} m rms, uphill/downhill right {v['sign1']:.0f}% (>1 m)"
                                                                       for nm, v in sorted(best["scores"].items())) if best else ""))
    print(note)
    if not a.no_write:
        write_terrain(c, grid, G, note, out_path=a.out)
        print(f"wrote {a.out or f'courses/terrain/{a.slug}.json'} in {time.time() - t0:.0f}s")
    if a.qa:
        os.makedirs(a.qa, exist_ok=True)
        json.dump({"params": params, "sigma": dict(SIGMA, **sig_), "ref": ref, "grow": grow, "lam": lam, "kappa": kappa, "use": [d["name"] for d in sets],
                   "cv": best}, open(os.path.join(a.qa, f"{a.slug}-ground.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
