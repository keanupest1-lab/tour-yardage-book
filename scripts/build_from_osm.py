"""Builds a hand-verified course from its OpenStreetMap snapshot plus a check against the scorecard and imagery.

courses/verified/<slug>.json (with "base": "osm") holds, per hole: the card (par from the map, stroke index,
yellow/white yards) and where the back tee is:
  "tee": "card"                      -> on the line of play, exactly the card length from the green (the card tee
                                        was found on the imagery at that spot)
  "tee": {"card": true, "lat": 5}    -> the card length, 5 m to the right of the line
  "tee": {"along": -5, "lat": 0}     -> fixed spot, metres along the line from the mapped start (- = further back),
                                        used where the card tee isn't on the imagery (the hole is then printed at the
                                        length it measures)
and "retag" fixes for mis-tagged map shapes. This writes courses/edits/<slug>.json with every hole, all the shapes
(map shapes, tee pads added where a back tee had none), tree crowns from the imagery and "replaceGolf": true, so
the app uses this set alone. Greens and bunkers are then sharpened with scripts/refine_z19.py.
With "base": "mask" (a course the map has no shapes for) the shapes come from the shape model run on the course's
saved imagery ("mask", aligned with "imagery"/"level"), each hole's line of play is given in pixels of that imagery
("px", back tee first, green last), lengths may be given in metres ("yellow_m"), and the spec can add shapes the
model missed or drop ones it got wrong ("add": [{"kind", "px"}], "drop": [{"kind", "px", "r"}], pixels of the same
imagery).
Usage: python3 scripts/build_from_osm.py <slug>
"""
import datetime, json, math, os, sys
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ml"))
sys.path.insert(0, os.path.dirname(__file__))
import automap_lib as A
from build_verified import crowns

YD = 0.9144
Image.MAX_IMAGE_PIXELS = None


def main(slug):
    spec = json.load(open(f"courses/verified/{slug}.json"))
    base = json.load(open(f"courses/{slug}.json"))
    lat0 = sum(e["bounds"]["minlat"] + e["bounds"]["maxlat"] for e in base["elements"] if e.get("bounds")) / (2 * sum(1 for e in base["elements"] if e.get("bounds")))
    lon0 = sum(e["bounds"]["minlon"] + e["bounds"]["maxlon"] for e in base["elements"] if e.get("bounds")) / (2 * sum(1 for e in base["elements"] if e.get("bounds")))
    # metres per degree on the WGS84 ellipsoid (the app measures the same way)
    sn = math.sin(math.radians(lat0)); w_ = 1 - 0.00669437999014 * sn * sn
    kx = 6378137 / math.sqrt(w_) * math.cos(math.radians(lat0)) * math.pi / 180
    ky = 6378137 * (1 - 0.00669437999014) / w_ ** 1.5 * math.pi / 180
    fwd = lambda la, lo: np.array([(lo - lon0) * kx, (la - lat0) * ky])
    inv = lambda p: [round(float(p[1] / ky + lat0), 7), round(float(p[0] / kx + lon0), 7)]

    shapes, holes, trees_osm, boundary = [], {}, [], None
    WATER = {"water_hazard", "lateral_water_hazard"}
    from_mask = spec.get("base") == "mask"
    for e in base["elements"]:
        t = e.get("tags", {}); g = [(p["lat"], p["lon"]) for p in (e.get("geometry") or []) if p]
        k = t.get("golf")
        if k == "hole" and t.get("ref") and not from_mask: holes[int(t["ref"])] = {"line": g, "par": int(t.get("par") or 0)}
        elif k in ("green", "tee", "fairway", "bunker") and len(g) > 3 and not from_mask: shapes.append({"kind": k, "ll": g[:-1] if g[0] == g[-1] else g, "id": f"osm{e['id']}"})
        elif (k in WATER or t.get("natural") == "water") and len(g) > 3: shapes.append({"kind": "water", "ll": g[:-1] if g[0] == g[-1] else g, "id": f"osm{e['id']}"})
        elif t.get("leisure") == "golf_course" and len(g) > 3: boundary = g
    if from_mask:
        # shapes traced by the model on the saved imagery, holes from the checked lines of play (pixels of it)
        from automap import polygons
        lvl = spec.get("level", "z18")
        mm = json.load(open(spec["imagery"]))[lvl]
        im_m = Image.open(os.path.join(os.path.dirname(spec["imagery"]), f"{lvl}.jpg"))
        MM = A.Mosaic(im_m, mm["zoom"], mm["tile_x0"], mm["tile_y0"]); mpp_m = MM.mpp
        lab = np.array(Image.open(spec["mask"]))
        osm_water = [sh for sh in shapes if sh["kind"] == "water"]
        shapes = []
        n_m = 0
        skip = set(spec.get("skip_classes", []))
        for kind, cls, min_m2 in (("fairway", 1, 250), ("green", 2, 120), ("tee", 3, 20), ("bunker", 4, 8), ("water", 5, 40)):
            if kind in skip: continue
            sm = ndimage.gaussian_filter((lab == cls).astype(np.float32), 1.6 / mpp_m if kind in ("fairway", "water") else 0.9 / mpp_m)
            cl = np.where(sm > 0.5, cls, 0).astype(np.uint8)
            for pp in polygons(cl, cls, mpp_m, min_m2, eps=0.7):
                shapes.append({"kind": kind, "ll": [tuple(MM.px2ll(x, y)) for x, y in pp], "id": f"m{kind}{n_m}"}); n_m += 1
        for hs in spec["holes"]:
            holes[int(hs["hole"])] = {"line": [tuple(MM.px2ll(x, y)) for x, y in hs["px"]], "par": int(hs["par"])}
        for k_, a_ in enumerate(spec.get("add", [])):
            shapes.append({"kind": a_["kind"], "ll": [tuple(MM.px2ll(x, y)) for x, y in a_["px"]], "id": f"add{a_['kind']}{k_}", "added": True})
        print(f"  {n_m} shapes from the model, {len(spec.get('add', []))} added by hand")
        # tee boxes are built as rectangles: the model's tee blobs become the rectangle of the same area and heading
        import cv2
        for sh in shapes:
            if sh["kind"] != "tee" or sh.get("added"): continue
            px_ = np.array([MM.ll2px(*p) for p in sh["ll"]], np.float32)
            (cx_, cy_), (w_, h_), ang = cv2.minAreaRect(px_)
            area_ = cv2.contourArea(px_)
            if w_ * h_ <= 0 or area_ <= 0: continue
            k_ = math.sqrt(area_ / (w_ * h_))
            box = cv2.boxPoints(((cx_, cy_), (w_ * k_, h_ * k_), ang))
            sh["ll"] = [tuple(MM.px2ll(float(x), float(y))) for x, y in box]
    def centroid(P):
        x, y = P[:, 0], P[:, 1]; x1, y1 = np.roll(x, -1), np.roll(y, -1)
        cr = x * y1 - x1 * y; A2 = cr.sum()
        if abs(A2) < 1e-9: return P.mean(0)
        return np.array([((x + x1) * cr).sum() / (3 * A2), ((y + y1) * cr).sum() / (3 * A2)])
    for s in shapes:
        s["xy"] = np.array([fwd(*p) for p in s["ll"]])
        s["c"] = centroid(s["xy"])
    if from_mask:
        # map water the model didn't trace (kept only where it doesn't overlap the model's), then the hand drops
        from shapely.geometry import Polygon as _P
        mw = [_P(sh["xy"]).buffer(0) for sh in shapes if sh["kind"] == "water"]
        for ow in osm_water:
            ow["xy"] = np.array([fwd(*p) for p in ow["ll"]]); ow["c"] = centroid(ow["xy"])
            pg = _P(ow["xy"]).buffer(0)
            if not any(pg.intersects(m_) for m_ in mw): shapes.append(ow)
        for d_ in spec.get("drop", []):
            q = fwd(*MM.px2ll(*d_["px"])); r_ = d_.get("r", 6)
            before = len(shapes)
            shapes = [sh for sh in shapes if not (sh["kind"] == d_["kind"] and not sh.get("added") and
                      (np.linalg.norm(sh["c"] - q) < r_ or _P(sh["xy"]).buffer(0).distance(__import__("shapely.geometry", fromlist=["Point"]).Point(q)) < 0.5))]
            if len(shapes) == before: print(f"  drop {d_['kind']} at {d_['px']}: nothing there")
        # sand the model sees inside a pond is a fountain or a reflection, not a bunker
        ponds = [_P(sh["xy"]).buffer(0) for sh in shapes if sh["kind"] == "water"]
        from shapely.geometry import Point as _Pt
        n0 = len(shapes)
        shapes = [sh for sh in shapes if not (sh["kind"] == "bunker" and any(pd.contains(_Pt(*sh["c"])) for pd in ponds))]
        if len(shapes) < n0: print(f"  {n0 - len(shapes)} bunkers inside ponds dropped")
        # the model also finds sand arenas, lawns and ponds in the gardens around the course: keep what is near a hole
        from shapely.geometry import LineString as _L
        from shapely.ops import unary_union as _U
        lines_ = _U([_L([fwd(*p) for p in h_["line"]]) for h_ in holes.values()])
        reach = {"bunker": 70, "fairway": 45, "water": 120, "tee": 60, "green": 60}
        n0 = len(shapes)
        shapes = [sh for sh in shapes if sh.get("added") or _P(sh["xy"]).buffer(0).distance(lines_) < reach.get(sh["kind"], 80)]
        print(f"  {n0 - len(shapes)} shapes far from every hole dropped")
    # rivers and streams (fetched separately): drawn as water at their mapped or usual width, near the holes only
    xp = f"courses/osm-extra/{slug}.json"
    if os.path.exists(xp):
        from shapely.geometry import LineString, Polygon
        from shapely.ops import unary_union
        WIDTH = {"river": 10.0, "stream": 5.0, "brook": 4.0, "canal": 6.0, "drain": 3.0, "ditch": 2.5}
        hole_lines = [LineString([fwd(*p) for p in h["line"]]) for h in holes.values()]
        reach = unary_union([hl.buffer(160) for hl in hole_lines])
        have = unary_union([Polygon(sh["xy"]).buffer(0) for sh in shapes if sh["kind"] == "water"]) if any(sh["kind"] == "water" for sh in shapes) else None
        polys = []
        for e in json.load(open(xp)).get("elements", []):
            t = e.get("tags", {}); g = [(p["lat"], p["lon"]) for p in (e.get("geometry") or []) if p]
            if len(g) < 2: continue
            if t.get("waterway") in WIDTH:
                try: wd = float(str(t.get("width", "")).replace(",", ".").split()[0])
                except (ValueError, IndexError): wd = WIDTH[t["waterway"]]
                polys.append(LineString([fwd(*p) for p in g]).buffer(wd / 2, cap_style=2, join_style=1))
            elif (t.get("natural") == "water" or t.get("water") == "river") and len(g) > 3 and g[0] == g[-1]:
                polys.append(Polygon([fwd(*p) for p in g]).buffer(0))
        if polys:
            water = unary_union(polys).intersection(reach)
            if have is not None: water = water.difference(have.buffer(0.5))
            geoms = list(getattr(water, "geoms", [water]))
            n_new = 0
            for k, gm in enumerate(geoms):
                if gm.geom_type != "Polygon" or gm.area < 25: continue
                xy = np.array(gm.simplify(0.4).exterior.coords)[:-1]
                shapes.append({"kind": "water", "ll": [tuple(inv(p)) for p in xy], "xy": xy, "c": centroid(xy), "id": f"river{k}", "river": True})
                n_new += 1
            print(f"  rivers and streams: {n_new} water areas added")

    def ring_area(P): x, y = P[:, 0], P[:, 1]; return abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1))) / 2
    def inside(p, P):
        x, y = p; r = False
        for i in range(len(P)):
            x1, y1 = P[i]; x2, y2 = P[i - 1]
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1: r = not r
        return r
    def poly_dist(p, P):
        if inside(p, P): return 0.0
        best = 1e9
        for i in range(len(P)):
            a, b = P[i - 1], P[i]; ab = b - a
            t = max(0, min(1, np.dot(p - a, ab) / max(np.dot(ab, ab), 1e-9)))
            best = min(best, np.linalg.norm(p - (a + t * ab)))
        return best

    # mis-tagged shapes
    for fx in spec.get("retag", []):
        end = fwd(*holes[fx["near_hole_end"]]["line"][-1])
        cand = [s for s in shapes if s["kind"] == fx["from"]]
        s = min(cand, key=lambda s: poly_dist(end, s["xy"]))
        if poly_dist(end, s["xy"]) < 15: s["kind"] = fx["to"]; print(f"  retagged {s['id']} {fx['from']} -> {fx['to']} (hole {fx['near_hole_end']})")

    if from_mask:
        # the model marks odd patches of short grass as tee: keep tee-sized shapes beside the first 170 m of a hole
        from shapely.geometry import LineString as _LS, Polygon as _PG
        starts = []
        for hs in spec["holes"]:
            P0 = [fwd(*p) for p in holes[int(hs["hole"])]["line"]]
            u0 = (P0[1] - P0[0]) / np.linalg.norm(P0[1] - P0[0])
            starts.append(_LS([P0[0] - u0 * 40, P0[0] + u0 * min(170, np.linalg.norm(P0[1] - P0[0]))]))
        def tee_ok(sh):
            if sh.get("added"): return True
            a_ = ring_area(sh["xy"])
            return 8 <= a_ <= 1200 and min(_PG(sh["xy"]).buffer(0).distance(st_) for st_ in starts) < 22
        n0 = len(shapes)
        shapes = [sh for sh in shapes if sh["kind"] != "tee" or tee_ok(sh)]
        print(f"  {n0 - len(shapes)} stray tee-like patches dropped, {sum(1 for sh in shapes if sh['kind'] == 'tee')} tee boxes kept")
    greens = [s for s in shapes if s["kind"] == "green"]
    out_holes, added_pads, extended, report = {}, [], [], []
    for hs in spec["holes"]:
        r = hs["hole"]; h = holes[r]
        P = [fwd(*p) for p in h["line"]]
        # the line finishes in the middle of its green
        g = min(greens, key=lambda s: poly_dist(P[-1], s["xy"]))
        if poly_dist(P[-1], g["xy"]) < 25: P[-1] = g["c"]
        t0 = P[0]; u = (P[1] - t0) / np.linalg.norm(P[1] - t0); v = np.array([u[1], -u[0]])   # v: right of the line
        bend = P[1] if len(P) >= 3 else P[-1]
        rest = sum(np.linalg.norm(P[i + 1] - P[i]) for i in range(1, len(P) - 1)) if len(P) >= 3 else 0.0
        card = hs["yellow_m"] if hs.get("yellow_m") else hs["yellow_yd"] * YD
        tee = hs["tee"]
        if tee == "card" or (isinstance(tee, dict) and tee.get("card")):
            lat = tee.get("lat", 0) if isinstance(tee, dict) else 0
            # |bend - (t0 + a u + lat v)| = card - rest  ->  solve for a (the root behind the bend)
            w = bend - t0 - lat * v; R = card - rest
            bq = np.dot(w, u); cq = np.dot(w, w) - R * R
            a = bq - math.sqrt(max(bq * bq - cq, 0))
            T = t0 + a * u + lat * v; mode = "card"
        else:
            a, lat = tee["along"], tee.get("lat", 0)
            T = t0 + a * u + lat * v; mode = "fixed"
        P[0] = T
        L = sum(np.linalg.norm(P[i + 1] - P[i]) for i in range(len(P) - 1))
        dist = round(card) if mode == "card" else round(L)
        out_holes[str(r)] = {"par": h["par"], "si": hs.get("si"), "dist": dist, "card": round(card), "line": [inv(p) for p in P], "source": "verified",
                             **({"note": hs["note"]} if hs.get("note") and mode == "fixed" else {})}
        report.append((r, mode, round(a, 1), round(L), round(card)))
        # tee pad: the back tee must stand on one
        tees = [s for s in shapes if s["kind"] == "tee"]
        d0 = min((poly_dist(T, s["xy"]) for s in tees), default=1e9)
        if d0 > 1.0:
            near = min(tees, key=lambda s: poly_dist(T, s["xy"])) if tees else None
            if near is not None and d0 < 9 and abs(np.dot(near["c"] - T, v)) < 8:
                # extend that pad back to cover the tee (rectangle aligned with the line)
                al = (near["xy"] - T) @ u; la_ = (near["xy"] - T) @ v
                a0, a1 = min(al.min(), -3.0), al.max(); l0, l1 = min(la_.min(), -3.0), max(la_.max(), 3.0)
                near["xy"] = np.array([T + u * aa + v * ll for aa, ll in ((a0, l0), (a1, l0), (a1, l1), (a0, l1))])
                near["ll"] = [tuple(inv(p)) for p in near["xy"]]; near["extended"] = True; extended.append(r)
            else:
                pad = np.array([T + u * aa + v * ll for aa, ll in ((-3, -5), (13, -5), (13, 5), (-3, 5))])
                shapes.append({"kind": "tee", "ll": [tuple(inv(p)) for p in pad], "xy": pad, "c": centroid(pad), "id": f"pad{r}", "added": True})
                added_pads.append(r)
    for row in report: print("  hole %2d  %-5s tee moved %6.1f m  length %4d m  card %4d m" % row)
    print("  tee pads added for holes", added_pads, "extended for holes", extended)

    if from_mask:
        from shapely.geometry import Polygon as _PG, Point as _Pt
        from shapely.ops import unary_union as _UU
        # tee boxes traced on the high-res photo (spec "tee_boxes": rectangles in imagery px) replace the model's
        # rough blobs they sit on
        tb = spec.get("tee_boxes") or []
        if tb:
            n_rep = 0
            for box in tb:
                xy = np.array([fwd(*MM.px2ll(*q)) for q in box["px"]]); c_ = centroid(xy)
                hit = [sh for sh in shapes if sh["kind"] == "tee" and (_PG(sh["xy"]).buffer(0).contains(_Pt(*c_)) or np.linalg.norm(sh["c"] - c_) < 6)]
                for sh in hit:
                    sh["xy"] = xy; sh["ll"] = [tuple(inv(q)) for q in xy]; sh["c"] = c_; sh["traced"] = True; n_rep += 1
            print(f"  {n_rep} tee boxes replaced by ones traced on the photo")
        # fairways stop at the green: the model's fairway class runs on round the collar, which would draw as a second
        # ring around the green. Keep the approach (in front of the green, as wide as the green's front) and trim the
        # fairway away beside and behind the green, then round off the corners the cut leaves
        from shapely.geometry import LineString as _LSg
        cut = []
        for r_, oh in out_holes.items():
            P = [fwd(*q) for q in oh["line"]]
            g = min(greens, key=lambda s_: poly_dist(P[-1], s_["xy"]))
            ug = (P[-1] - P[-2]) / np.linalg.norm(P[-1] - P[-2]); nrm = np.array([ug[1], -ug[0]])
            G_ = _PG(g["xy"]).buffer(0); gc_ = P[-1]
            al = (g["xy"] - gc_) @ ug
            front = al.min()
            # the green's width a few metres in from its front edge
            chord = G_.intersection(_LSg([gc_ + ug * (front + 4) - nrm * 80, gc_ + ug * (front + 4) + nrm * 80]))
            if chord.is_empty: continue
            pts_ = np.array(chord.coords) if chord.geom_type == "LineString" else np.vstack([np.array(c_.coords) for c_ in chord.geoms])
            lat_ = (pts_ - gc_) @ nrm; l0, l1 = lat_.min() + 1.0, lat_.max() - 1.0
            if l1 - l0 < 4: l0, l1 = (l0 + l1) / 2 - 2, (l0 + l1) / 2 + 2
            appr = _PG([gc_ + ug * (front - 80) + nrm * l0, gc_ + ug * (front + 6) + nrm * l0, gc_ + ug * (front + 6) + nrm * l1, gc_ + ug * (front - 80) + nrm * l1])
            cut.append(G_.buffer(16).difference(appr).union(G_.buffer(0.6)))
        CUT = _UU(cut)
        new_shapes, n_cut = [], 0
        for sh in shapes:
            if sh["kind"] != "fairway":
                new_shapes.append(sh); continue
            F = _PG(sh["xy"]).buffer(0)
            if not F.intersects(CUT):
                new_shapes.append(sh); continue
            rest = F.difference(CUT).buffer(-2.0).buffer(2.0).buffer(3.0).buffer(-3.0).difference(CUT); n_cut += 1
            parts = [q for q in getattr(rest, "geoms", [rest]) if q.area >= 60]
            for k_, q in enumerate(parts):
                xy = np.array(q.simplify(0.25).exterior.coords)[:-1]
                new_shapes.append({**sh, "xy": xy, "ll": [tuple(inv(p_)) for p_ in xy], "c": centroid(xy), "id": sh["id"] + ("" if k_ == 0 else f"-{k_}")})
        shapes = new_shapes
        print(f"  {n_cut} fairways trimmed where they ran round a green")

    # tree crowns from the photo (z18), inside the course and clear of the playing shapes
    meta = json.load(open(spec["imagery"]))["z18"]
    img = Image.open(os.path.join(os.path.dirname(spec["imagery"]), "z18.jpg"))
    m = A.Mosaic(img, 18, meta["tile_x0"], meta["tile_y0"]); mpp = m.mpp
    Wd, Hd = img.size
    keep_im = Image.new("L", (Wd, Hd), 0)
    if boundary: ImageDraw.Draw(keep_im).polygon([m.ll2px(*p) for p in boundary], fill=1)
    else: keep_im.paste(1, (0, 0, Wd, Hd))
    keep = ndimage.binary_dilation(np.asarray(keep_im).astype(bool), iterations=int(25 / mpp))
    nt = Image.new("L", (Wd, Hd), 0); dn = ImageDraw.Draw(nt)
    for s in shapes: dn.polygon([m.ll2px(*p) for p in s["ll"]], fill=1)
    not_tree = ndimage.binary_dilation(np.asarray(nt).astype(bool), iterations=int(2 / mpp))
    trees = [[round(la, 7), round(lo, 7), round(float(rr), 1)] for la, lo, rr in
             ((*m.px2ll(x, y), r_) for x, y, r_ in crowns(img, mpp, keep, not_tree))]
    print(f"  {len(trees)} tree crowns")

    ed = {"version": 1, "slug": slug, "name": spec["name"], "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
          "replaceGolf": True, "holes": out_holes,
          "shapes": [{"id": s["id"], "kind": s["kind"], "coords": [list(p) for p in s["ll"]],
                      "source": "verified" if s.get("added") or s.get("extended") else ("model" if from_mask and not s["id"].startswith("osm") else "osm")} for s in shapes],
          "trees": trees, "source": "verified", "card": spec.get("card"), "designer": spec.get("designer"), "verified": spec.get("verified")}
    json.dump(ed, open(f"courses/edits/{slug}.json", "w"), separators=(",", ":"))
    print(f"{slug}: {len(out_holes)} holes, {len(ed['shapes'])} shapes, {len(trees)} trees")


if __name__ == "__main__":
    main(sys.argv[1])
