"""Builds a hand-verified course into the library from courses/verified/<slug>.json.

The spec holds every hole's line of play in pixels of the course's saved satellite imagery (checked by eye on the
imagery, tee box to green centre), plus the scorecard. This writes courses/edits/<slug>.json with:
  holes  : par, stroke index, scorecard length (back tees, metres), line of play (lat, lon)
  shapes : greens, tee boxes, fairways, bunkers, water traced by the shape model on the same imagery
  trees  : individual tree crowns [lat, lon, radius m] found in the imagery (for the canopy circles in the book)
Usage: python3 scripts/build_verified.py <slug>
"""
import datetime, json, math, os, sys
import numpy as np
from PIL import Image
from scipy import ndimage
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ml"))
import automap_lib as A
from automap import polygons

YD = 0.9144


def inside(pt, ring):
    x, y = pt; r = False
    for i in range(len(ring)):
        x1, y1 = ring[i]; x2, y2 = ring[i - 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1: r = not r
    return r


def dp(P, tol):
    """Douglas-Peucker: keep the turn points of a line of play, drop the wiggles"""
    if len(P) < 3: return P
    a, b = np.array(P[0]), np.array(P[-1]); ab = b - a; L = np.linalg.norm(ab) or 1
    d = [abs(ab[0] * (p[1] - a[1]) - ab[1] * (p[0] - a[0])) / L for p in P[1:-1]]
    i = int(np.argmax(d)) + 1
    if d[i - 1] <= tol: return [P[0], P[-1]]
    return dp(P[:i + 1], tol)[:-1] + dp(P[i:], tol)


def crowns(img, mpp, keep, not_tree, rmax=7.5):
    """individual tree crowns: dark green canopy (the trees here are far darker than any grass), packed with
    circles round by round: biggest free crown first, so isolated trees and big clumps both read as crowns"""
    a = np.asarray(img.convert("RGB"), np.float32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    lum = .3 * r + .59 * g + .11 * b
    sm = ndimage.uniform_filter(lum, 5)
    t = (sm < 78) & (g >= r * 0.95) & (g >= b * 0.95)
    t = ndimage.binary_opening(ndimage.binary_closing(t, iterations=2), iterations=2) & keep & ~not_tree
    t = ndimage.binary_fill_holes(t)
    lab, n = ndimage.label(t)
    sizes = ndimage.sum(t, lab, range(1, n + 1)) * mpp * mpp
    t &= np.isin(lab, np.nonzero(sizes >= 10)[0] + 1)
    left = t.copy(); out = []
    yy, xx = np.mgrid[0:t.shape[0], 0:t.shape[1]]
    for _ in range(8):
        d = ndimage.distance_transform_edt(left) * mpp
        cap = np.minimum(d, rmax)
        peaks = (cap == ndimage.maximum_filter(cap, size=int(rmax / mpp))) & (d >= 1.6)
        ys, xs = np.nonzero(peaks)
        if not len(ys): break
        order = np.argsort(-cap[ys, xs]); taken = np.zeros_like(t); added = 0
        for i in order:
            x, y = xs[i], ys[i]
            if taken[y, x]: continue
            rad = float(cap[y, x]) + 0.6       # crowns overlap a little, as real ones do
            R = int(rad / mpp) + 1
            y0, y1, x0, x1 = max(0, y - R), y + R + 1, max(0, x - R), x + R + 1
            disk = np.hypot(xx[y0:y1, x0:x1] - x, yy[y0:y1, x0:x1] - y) * mpp
            taken[y0:y1, x0:x1] |= disk <= rad * 1.6
            left[y0:y1, x0:x1] &= disk > rad * 0.85
            out.append((x, y, rad)); added += 1
        if not added: break
    return out


def tee_like(poly, mpp, starts):
    """tee boxes are compact mown pads; the model also marks odd patches of short grass as tee"""
    import cv2
    a = np.array(poly, np.float32)
    area = cv2.contourArea(a) * mpp * mpp
    (_, _), (w, h), _ = cv2.minAreaRect(a)
    rect = cv2.contourArea(a) / max(w * h, 1e-6)
    c = a.mean(0)
    near_start = min(math.dist(c, s) for s in starts) * mpp < 45
    return area <= (1500 if near_start else 700) and (rect >= 0.55 or near_start)


def main(slug):
    spec = json.load(open(f"courses/verified/{slug}.json"))
    meta = json.load(open(spec["imagery"]))[spec["level"]]
    img = Image.open(os.path.join(os.path.dirname(spec["imagery"]), f"{spec['level']}.jpg"))
    m = A.Mosaic(img, meta["zoom"], meta["tile_x0"], meta["tile_y0"]); mpp = m.mpp
    lab = np.array(Image.open(spec["mask"]))
    base = json.load(open(f"courses/{slug}.json"))
    bound = next((e for e in base["elements"] if (e.get("tags") or {}).get("leisure") == "golf_course"), None)
    ring = [m.ll2px(p["lat"], p["lon"]) for p in bound["geometry"]] if bound else None
    H, W = lab.shape
    keep = np.zeros((H, W), bool)
    if ring:
        from PIL import ImageDraw
        k = Image.new("L", (W, H), 0); ImageDraw.Draw(k).polygon(ring, fill=1)
        keep = ndimage.binary_dilation(np.asarray(k).astype(bool), iterations=int(25 / mpp))
    else: keep[:] = True

    holes = {}
    for h in spec["holes"]:
        P = dp([tuple(p) for p in h["px"]], 8 / mpp)
        back = h.get("yellow_yd") or h.get("white_yd")
        holes[str(h["hole"])] = {"par": h["par"], "si": h.get("si"), "dist": round(back * YD) if back else None,
                                 "line": [list(m.px2ll(x, y)) for x, y in P], "source": "verified"}
    starts = [tuple(h["px"][0]) for h in spec["holes"]]
    shapes = []; n = 0
    for kind, cls, min_m2 in (("fairway", 1, 250), ("green", 2, 120), ("tee", 3, 20), ("bunker", 4, 12), ("water", 5, 40)):
        # smooth each class before tracing: the model's pixel steps become clean, book-like outlines
        sm = ndimage.gaussian_filter((lab == cls).astype(np.float32), 1.6 / mpp if kind in ("fairway", "water") else 0.9 / mpp)
        cl = np.where(sm > 0.5, cls, 0).astype(np.uint8)
        for p in polygons(cl, cls, mpp, min_m2, eps=0.7):
            c = (sum(q[0] for q in p) / len(p), sum(q[1] for q in p) / len(p))
            if not keep[int(min(H - 1, max(0, c[1]))), int(min(W - 1, max(0, c[0])))]: continue
            if kind == "tee" and not tee_like(p, mpp, starts): continue
            shapes.append({"id": f"v{kind}{n}", "kind": kind, "coords": [list(m.px2ll(x, y)) for x, y in p], "source": "model"}); n += 1
    not_tree = ndimage.binary_dilation(np.isin(lab, [1, 2, 3, 4, 5]), iterations=int(2 / mpp))
    trees = [[*m.px2ll(x, y), round(float(r), 1)] for x, y, r in crowns(img, mpp, keep, not_tree)]
    trees = [[round(a, 7), round(b, 7), r] for a, b, r in trees]
    ed = {"version": 1, "slug": slug, "name": spec["name"], "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
          "holes": holes, "shapes": shapes, "trees": trees, "source": "verified",
          "card": spec.get("card"), "designer": spec.get("designer"), "verified": spec.get("verified")}
    json.dump(ed, open(f"courses/edits/{slug}.json", "w"), separators=(",", ":"))
    print(f"{slug}: {len(holes)} holes, {len(shapes)} shapes, {len(trees)} trees")


if __name__ == "__main__":
    main(sys.argv[1])
