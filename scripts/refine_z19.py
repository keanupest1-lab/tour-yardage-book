"""Sharpens greens and bunkers on the full-resolution photo (z19, ~0.27 m per pixel).

The shape model traces at z18; here each green and bunker outline is refined with GrabCut seeded from that
outline (inside = certain, a band around it = undecided), so the edge follows the real cut of the green or the
sand line. Greens keep the model's interior (pulled in 1.5 m) so tree shadows across a green don't bite into it.
Usage: python3 scripts/refine_z19.py <slug>   (rewrites the green/bunker shapes in courses/edits/<slug>.json)
"""
import json, math, os, sys
import numpy as np, cv2
from PIL import Image
from scipy import ndimage
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ml"))
import automap_lib as A
Image.MAX_IMAGE_PIXELS = None


def refine(full, M, coords, kind):
    mpp = M.mpp
    poly = np.array([M.ll2px(*p) for p in coords])
    c = poly.mean(0); ext = np.abs(poly - c).max() + (12 if kind == "green" else 8) / mpp
    R = int(ext)
    x0, y0 = int(c[0] - R), int(c[1] - R)
    if x0 < 0 or y0 < 0 or x0 + 2 * R > full.width or y0 + 2 * R > full.height: return None
    crop = np.asarray(full.crop((x0, y0, x0 + 2 * R, y0 + 2 * R)), np.uint8)
    P = (poly - [x0, y0]).astype(np.int32)
    mk = np.zeros(crop.shape[:2], np.uint8); cv2.fillPoly(mk, [P], 1); mk = mk.astype(bool)
    d_in = ndimage.distance_transform_edt(mk) * mpp; d_out = ndimage.distance_transform_edt(~mk) * mpp
    band, sure = {"green": (6, 3), "bunker": (3, 1.2), "tee": (4, 1.5)}[kind]
    gm = np.full(mk.shape, cv2.GC_BGD, np.uint8)
    gm[d_out < band] = cv2.GC_PR_BGD; gm[mk] = cv2.GC_PR_FGD; gm[mk & (d_in > sure)] = cv2.GC_FGD
    if not (gm == cv2.GC_FGD).any(): gm[mk] = cv2.GC_FGD
    bg = np.zeros((1, 65)); fg = np.zeros((1, 65))
    try: cv2.grabCut(cv2.GaussianBlur(crop, (5, 5), 0)[..., ::-1].copy(), gm, None, bg, fg, 6, cv2.GC_INIT_WITH_MASK)
    except cv2.error: return None
    g = (gm == cv2.GC_FGD) | (gm == cv2.GC_PR_FGD)
    g = ndimage.binary_opening(g, iterations=2 if kind == "green" else 1)
    if kind == "green": g |= mk & (d_in > 1.5)
    lab, n = ndimage.label(g)
    if not n: return None
    cc = np.bincount(lab[mk & (lab > 0)]).argmax() if (mk & (lab > 0)).any() else 0
    if not cc: return None
    g = ndimage.binary_fill_holes(lab == cc)
    if kind == "green": g = ndimage.binary_closing(g, iterations=int(1.5 / mpp))
    g = ndimage.gaussian_filter(g.astype(np.float32), 2.0 if kind == "green" else 1.2) > 0.5
    cs, _ = cv2.findContours(g.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cs: return None
    cnt = cv2.approxPolyDP(max(cs, key=cv2.contourArea), 0.9, True)[:, 0, :]
    a_old, a_new = mk.sum() * mpp * mpp, cv2.contourArea(cnt) * mpp * mpp
    if kind == "tee":   # tee boxes are built as rectangles: square them up when the shape says so
        rect = cv2.minAreaRect(cnt.astype(np.float32))
        if a_new / max(rect[1][0] * rect[1][1], 1e-6) >= 0.72:
            cnt = cv2.boxPoints(rect)
    if not (0.55 * a_old <= a_new <= 1.8 * a_old): return None       # implausible change: keep the model's
    return [list(M.px2ll(x + x0, y + y0)) for x, y in cnt]


def main(slug, kinds=("green", "bunker")):
    meta = json.load(open(f"courses/imagery/{slug}/meta.json"))["z19"]
    full = Image.open(f"courses/imagery/{slug}/z19.jpg").convert("RGB")
    M = A.Mosaic(full, 19, meta["tile_x0"], meta["tile_y0"])
    ep = f"courses/edits/{slug}.json"; e = json.load(open(ep))
    done = {k: 0 for k in kinds}   # tee boxes: GrabCut grows them into the rough here, so they stay as traced
    for s in e["shapes"]:
        if s["kind"] not in done or s.get("refined"): continue
        r = refine(full, M, s["coords"], s["kind"])
        if r: s["coords"] = r; s["refined"] = "z19"; done[s["kind"]] += 1
    json.dump(e, open(ep, "w"), separators=(",", ":"))
    print(slug, done)


if __name__ == "__main__":
    # optional second argument: which kinds to sharpen, e.g. "bunker" (hand-traced map greens are kept as they are)
    main(sys.argv[1], tuple(sys.argv[2].split(",")) if len(sys.argv) > 2 else ("green", "bunker"))
