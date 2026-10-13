"""Adds the greens and tee boxes a course's map is missing, using the AI shape model on satellite imagery,
so every hole can start on its tee box and finish on its real green.

Request file courses/enrich-requests.txt: one library slug per line.
Writes the new shapes into courses/edits/<slug>.json (source "model"; anything a person drew is kept),
plus a check image courses/qa/<slug>.jpg (hole lines red, tee boxes white, greens green).
Conservative on purpose: greens only near the end of a hole line, tee boxes only near the start of one.
"""
import datetime, json, math, os, sys
import numpy as np
from PIL import Image, ImageDraw
sys.path.insert(0, os.path.dirname(__file__))
import automap_lib as A
from automap import polygons

MODEL = os.environ.get("MODEL", "models/model.pt")


def log(*a): print(*a, flush=True)


def course_shapes(slug):
    base = json.load(open(f"courses/{slug}.json"))
    ep = f"courses/edits/{slug}.json"
    ed = json.load(open(ep)) if os.path.exists(ep) else {"version": 1, "slug": slug, "holes": {}, "shapes": []}
    have = {"green": [], "tee": []}; lines = {}
    for e in base.get("elements", []):
        t = e.get("tags", {}); g = e.get("geometry")
        if not g: continue
        pts = [(p["lat"], p["lon"]) for p in g if p]
        if t.get("golf") in have and len(pts) > 2: have[t["golf"]].append(pts)
        if t.get("golf") == "hole" and len(pts) >= 2: lines.setdefault(str(t.get("ref")), []).append(pts)
    for s in ed.get("shapes", []):
        if s["kind"] in have: have[s["kind"]].append([tuple(p) for p in s["coords"]])
    for ref, h in ed.get("holes", {}).items():
        if h.get("line") and len(h["line"]) >= 2: lines[ref] = [[tuple(p) for p in h["line"]]]
    return base, ed, have, [l for v in lines.values() for l in v]


def inside(pt, ring):
    x, y = pt; r = False
    for i in range(len(ring)):
        x1, y1 = ring[i]; x2, y2 = ring[i - 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1: r = not r
    return r


def enrich(slug, model):
    base, ed, have, lines = course_shapes(slug)
    if not lines: log("  no hole lines: nothing to fit"); return
    lats = [p[0] for l in lines for p in l]; lons = [p[1] for l in lines for p in l]
    m = A.fetch_mosaic(min(lats), min(lons), max(lats), max(lons), z=18, pad_m=120, log=log)
    mpp = m.mpp
    lab, _ = A.predict(model, m.img)
    px = lambda p: m.ll2px(*p)
    L = [[px(p) for p in l] for l in lines]
    starts = [l[0] for l in L]; ends = [l[-1] for l in L]
    old = {k: [[px(p) for p in r] for r in v] for k, v in have.items()}
    def overlaps(poly, kind):
        c = (sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))
        for r in old[kind]:
            rc = (sum(p[0] for p in r) / len(r), sum(p[1] for p in r) / len(r))
            if inside(c, r) or inside(rc, poly) or math.dist(c, rc) * mpp < 8: return True
        return False
    import cv2
    rgb = np.asarray(m.img.convert("RGB"), np.float32)
    def looks_like_tee(poly):
        """tee boxes are mown grass pads: fairly rectangular, grass-coloured, not bright sand"""
        a = np.array(poly, np.float32)
        (cx, cy), (w, h), _ = cv2.minAreaRect(a)
        area = cv2.contourArea(a)
        rect = area / max(w * h, 1e-6)
        mk = np.zeros(rgb.shape[:2], np.uint8); cv2.fillPoly(mk, [a.astype(np.int32)], 1)
        px_ = rgb[mk.astype(bool)]
        if len(px_) < 10: return False
        r, g, b = px_.mean(0); lum = 0.3 * r + 0.59 * g + 0.11 * b
        long_, short = max(w, h), max(min(w, h), 1e-6)
        area_m = area * mpp * mpp
        return (rect >= 0.75 and long_ / short >= 1.25 and 30 <= area_m <= 900
                and lum < 165 and g >= r * 0.97 and g > b)
    added = {"green": 0, "tee": 0}
    new = []
    for kind, cls, lo, hi, near, pts_ref in (("green", 2, 150, 2600, 70, ends), ("tee", 3, 25, 1500, 180, starts)):
        for poly in polygons(lab, cls, mpp, lo):
            area = abs(sum(poly[i - 1][0] * poly[i][1] - poly[i][0] * poly[i - 1][1] for i in range(len(poly)))) / 2 * mpp * mpp
            if area > hi: continue
            c = (sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))
            if min(math.dist(c, q) for q in pts_ref) * mpp > near: continue
            if kind == "tee" and min(math.dist(c, q) for q in ends) * mpp < 40: continue   # next to a green: not a tee
            if kind == "tee" and not looks_like_tee(poly): continue
            if overlaps(poly, kind): continue
            new.append({"id": f"ai{kind}{len(new)}", "kind": kind, "coords": [list(m.px2ll(x, y)) for x, y in poly], "source": "model"})
            old[kind].append(poly); added[kind] += 1
    # replace earlier model-added greens/tees from this job, keep everything else
    ed["shapes"] = [s for s in ed.get("shapes", []) if not (s.get("source") == "model" and s.get("fit"))] + [dict(s, fit=True) for s in new]
    ed["updated"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    ed.setdefault("slug", slug)
    os.makedirs("courses/edits", exist_ok=True)
    json.dump(ed, open(f"courses/edits/{slug}.json", "w"), indent=1)
    log(f"  added {added['green']} greens, {added['tee']} tee boxes")
    # check image
    os.makedirs("courses/qa", exist_ok=True)
    img = m.img.convert("RGB"); d = ImageDraw.Draw(img, "RGBA")
    for r in old["green"]: d.polygon(r, outline=(120, 255, 120, 255), fill=(120, 255, 120, 70))
    for r in old["tee"]: d.polygon(r, outline=(255, 255, 255, 255), fill=(255, 255, 255, 90))
    for l in L:
        d.line(l, fill=(255, 50, 50, 230), width=4)
    s = 1800 / max(img.size)
    if s < 1: img = img.resize((int(img.width * s), int(img.height * s)))
    img.save(f"courses/qa/{slug}.jpg", quality=80)


def main():
    req = "courses/enrich-requests.txt"
    if not os.path.exists(req): return
    model = A.load_model(MODEL)
    for line in open(req):
        slug = line.strip()
        if not slug or slug.startswith("#"): continue
        log("==", slug)
        try: enrich(slug, model)
        except Exception as e:
            import traceback; traceback.print_exc(); log("  FAILED", repr(e))


if __name__ == "__main__":
    main()
