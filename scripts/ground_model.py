"""Ground heights for a course, for the uphill / downhill numbers in the book.

The open elevation models are surface models: over trees and roofs they measure the top, not the ground. So:
  1. take the Copernicus DEM GLO-30 samples (courses/dem/<slug>-cop30.json, about 30 m apart, TanDEM-X radar),
  2. keep only samples whose footprint (plus 10 m around it) is open ground: no tree crowns (the crowns measured
     from the photo, courses/edits/<slug>.json), no buildings (map footprints, courses/osm-extra/<slug>.json),
     no water, and on or right beside the course,
  3. fit a smooth surface through them (thin-plate spline, smoothing chosen by cross-validation, outliers
     dropped and refitted),
  4. write it on a 5 m grid as courses/terrain/<slug>.json (the format the app reads) and report how well it
     predicts samples it wasn't fitted on, plus every hole's tee-to-green change against the old SRTM model.
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


def main(slug, qa_dir=None):
    dem = json.load(open(f"courses/dem/{slug}-cop30.json"))
    a, b, c, d, e, f = dem["transform"]
    Wd, Hd = dem["width"], dem["height"]
    Z = np.frombuffer(base64.b64decode(dem["data"]), "<f4").reshape(Hd, Wd).astype(float)
    cols, rows = np.meshgrid(np.arange(Wd) + 0.5, np.arange(Hd) + 0.5)
    LON = a * cols + b * rows + c; LAT = d * cols + e * rows + f
    lat0, lon0 = float(LAT.mean()), float(LON.mean())
    kx, ky = ellipsoid_k(lat0)
    fx = lambda lo: (lo - lon0) * kx
    fy = lambda la: (la - lat0) * ky
    X, Y = fx(LON), fy(LAT)
    px_w, px_h = abs(a) * kx, abs(e) * ky                # sample footprint, metres

    ed = json.load(open(f"courses/edits/{slug}.json"))
    base = json.load(open(f"courses/{slug}.json"))
    extra = json.load(open(f"courses/osm-extra/{slug}.json")) if os.path.exists(f"courses/osm-extra/{slug}.json") else {"elements": []}

    # 1 m masks over the DEM window: canopy, buildings, water, course
    x0, x1 = X.min() - px_w, X.max() + px_w; y0, y1 = Y.min() - px_h, Y.max() + px_h
    G = 1.0
    gw, gh = int((x1 - x0) / G) + 1, int((y1 - y0) / G) + 1
    to_px = lambda x, y: ((x - x0) / G, (y1 - y) / G)
    def raster(polys, circles=()):
        im = Image.new("L", (gw, gh), 0); dr = ImageDraw.Draw(im)
        for P in polys:
            if len(P) > 2: dr.polygon([to_px(fx(lo), fy(la)) for la, lo in P], fill=1)
        for la, lo, r in circles:
            cx, cy = to_px(fx(lo), fy(la)); R = (r + 2.0) / G       # crowns + 2 m: radar sees the canopy edge too
            dr.ellipse([cx - R, cy - R, cx + R, cy + R], fill=1)
        return np.asarray(im, dtype=np.float32)
    canopy = raster([], ed.get("trees", []))
    water = raster([s["coords"] for s in ed["shapes"] if s["kind"] == "water"])
    blds = [[(p["lat"], p["lon"]) for p in el.get("geometry") or [] if p] for el in extra["elements"] if (el.get("tags") or {}).get("building")]
    building = raster(blds)
    boundary = next(([(p["lat"], p["lon"]) for p in el["geometry"]] for el in base["elements"] if (el.get("tags") or {}).get("leisure") == "golf_course" and el.get("geometry")), None)
    course = raster([boundary] if boundary else [])
    play = raster([s["coords"] for s in ed["shapes"]])
    course = np.maximum(ndimage.binary_dilation(course > 0, iterations=30), ndimage.binary_dilation(play > 0, iterations=40)).astype(np.float32)

    # fraction of each sample's footprint (+10 m all round) covered by each mask: box sums on integral images
    def frac(mask, grow):
        S = np.pad(mask, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
        hw, hh = px_w / 2 + grow, px_h / 2 + grow
        cx, cy = (X - x0) / G, (y1 - Y) / G
        i0 = np.clip((cy - hh).astype(int), 0, gh); i1 = np.clip((cy + hh).astype(int), 0, gh)
        j0 = np.clip((cx - hw).astype(int), 0, gw); j1 = np.clip((cx + hw).astype(int), 0, gw)
        tot = S[i1, j1] - S[i0, j1] - S[i1, j0] + S[i0, j0]
        return tot / np.maximum((i1 - i0) * (j1 - j0), 1)
    f_can, f_bld, f_wat, f_crs = frac(canopy, 10), frac(building, 12), frac(water, 6), frac(course, 0)
    clean = (f_can < 0.04) & (f_bld < 0.01) & (f_wat < 0.05) & (f_crs > 0.5) & np.isfinite(Z)
    xs, ys, zs = X[clean], Y[clean], Z[clean]
    print(f"{slug}: {clean.sum()} open-ground samples of {Z.size} ({px_w:.1f} x {px_h:.1f} m each)")

    # smoothing by 5-fold cross-validation, then drop outliers (unmapped roofs / trees) and refit
    P = np.column_stack([xs, ys])
    rng = np.random.default_rng(3); fold = rng.integers(0, 5, len(zs))
    def cv(smooth, P, zs, fold):
        err = []
        for k in range(5):
            tr, te = fold != k, fold == k
            m = RBFInterpolator(P[tr], zs[tr], kernel="thin_plate_spline", smoothing=smooth, degree=1)
            err.append(m(P[te]) - zs[te])
        return np.sqrt(np.mean(np.concatenate(err) ** 2))
    cands = [0.3, 1, 3, 10, 30, 100]
    scores = {s_: cv(s_, P, zs, fold) for s_ in cands}
    sm = min(scores, key=scores.get)
    fit = RBFInterpolator(P, zs, kernel="thin_plate_spline", smoothing=sm, degree=1)
    res = fit(P) - zs
    keep = np.abs(res) < 3 * max(np.std(res), 0.3)
    if (~keep).sum():
        P, zs, fold = P[keep], zs[keep], fold[keep]
        scores2 = {s_: cv(s_, P, zs, fold) for s_ in cands}
        sm = min(scores2, key=scores2.get); scores = scores2
        fit = RBFInterpolator(P, zs, kernel="thin_plate_spline", smoothing=sm, degree=1)
    print(f"  smoothing {sm}; cross-validated error {scores[sm]:.2f} m (rms, predicting samples left out); {int((~keep).sum())} outliers dropped")

    # 5 m output grid in lat/lon over the course bbox (+ pad), same format as courses/terrain/<slug>.json
    idx = {cc["slug"]: cc for cc in json.load(open("courses/index.json"))["courses"]}
    s, n, w, e_ = idx[slug]["bbox"]; pad = 0.003
    s, n, w, e_ = s - pad, n + pad, w - pad, e_ + pad
    step = 5.0
    dlat, dlon = step / ky, step / kx
    ny, nx = int((n - s) / dlat) + 1, int((e_ - w) / dlon) + 1
    lats = n - np.arange(ny) * dlat; lons = w + np.arange(nx) * dlon
    GX, GY = np.meshgrid(fx(lons), fy(lats))
    Q = np.column_stack([GX.ravel(), GY.ravel()])
    out = np.concatenate([fit(Q[i:i + 6000]) for i in range(0, len(Q), 6000)]).reshape(ny, nx)
    base_h = float(np.floor(out.min())); scale = 0.02
    q = np.clip(np.round((out - base_h) / scale), 0, 65535).astype("<u2")
    old_path = f"courses/terrain/{slug}.json"
    old = json.load(open(old_path)) if os.path.exists(old_path) else None
    json.dump({"s": s, "w": w, "n": n, "e": e_, "nx": nx, "ny": ny, "step_m": step, "dlat": dlat, "dlon": dlon, "base": base_h, "scale": scale,
               "source": f"Ground fitted to {len(zs)} open-ground samples of the Copernicus DEM GLO-30 (trees, roofs and water left out); "
                         f"cross-validated error {scores[sm]:.1f} m",
               "data": base64.b64encode(q.tobytes()).decode()}, open(old_path, "w"))

    # report: every hole, old (SRTM, surface) against new (ground)
    def sampler(t):
        if t is None: return None
        A_ = np.frombuffer(base64.b64decode(t["data"]), "<u2").reshape(t["ny"], t["nx"]).astype(float) * t["scale"] + t["base"]
        return lambda la, lo: float(ndimage.map_coordinates(A_, [[(t["n"] - la) / t["dlat"]], [(lo - t["w"]) / t["dlon"]]], order=1)[0])
    new_at, old_at = sampler(json.load(open(old_path))), sampler(old)
    print("  hole   tee->green   new (ground)   old (SRTM surface)")
    rows_ = []
    for r in sorted(ed["holes"], key=int):
        L = ed["holes"][r]["line"]
        dn = new_at(*L[-1]) - new_at(*L[0])
        do = old_at(*L[-1]) - old_at(*L[0]) if old_at else float("nan")
        rows_.append((int(r), dn, do))
        print(f"  {r:>4}                  {dn:+6.1f} m        {do:+6.1f} m")

    if qa_dir:
        os.makedirs(qa_dir, exist_ok=True)
        # map: new ground surface hillshade + contours, open samples, holes
        sc = 2
        hs = np.zeros((ny, nx))
        gyy, gxx = np.gradient(out, step)
        hs = np.clip((-gxx * 0.5 + gyy * 0.5 + 1) / np.sqrt(gxx ** 2 + gyy ** 2 + 1), 0, 1.4)
        img = Image.fromarray((np.clip(hs / 1.4, 0, 1) * 200 + 40).astype(np.uint8)).convert("RGB").resize((nx * sc, ny * sc))
        dr = ImageDraw.Draw(img)
        lev = np.arange(math.floor(out.min()), math.ceil(out.max()) + 1, 1.0)
        # contour lines every 1 m (heavier every 5 m) by marching on the grid
        for L_ in lev:
            m_ = out > L_
            edge = m_ ^ ndimage.binary_erosion(m_)
            yy_, xx_ = np.nonzero(edge)
            col = (120, 70, 20) if int(L_) % 5 == 0 else (190, 160, 120)
            for y_, x_ in zip(yy_, xx_): dr.point((x_ * sc, y_ * sc), fill=col)
        to = lambda la, lo: ((lo - w) / dlon * sc, (n - la) / dlat * sc)
        for la, lo in zip(LAT[clean].ravel(), LON[clean].ravel()):
            x_, y_ = to(la, lo); dr.ellipse([x_ - 2, y_ - 2, x_ + 2, y_ + 2], fill=(30, 120, 255))
        for sh in ed["shapes"]:
            if sh["kind"] in ("water", "green"):
                dr.line([to(*p) for p in sh["coords"]] + [to(*sh["coords"][0])], fill=(0, 90, 200) if sh["kind"] == "water" else (40, 160, 40), width=2)
        for r, h in ed["holes"].items():
            pts = [to(*p) for p in h["line"]]; dr.line(pts, fill=(220, 30, 30), width=2); dr.text((pts[0][0] + 4, pts[0][1] - 12), r, fill=(0, 0, 0))
        img.save(os.path.join(qa_dir, f"{slug}-ground.png"))
        json.dump({"cv_rms": scores[sm], "samples": int(len(zs)), "smoothing": sm, "holes": rows_}, open(os.path.join(qa_dir, f"{slug}-ground.json"), "w"))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
