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
    for e in base["elements"]:
        t = e.get("tags", {}); g = [(p["lat"], p["lon"]) for p in (e.get("geometry") or []) if p]
        k = t.get("golf")
        if k == "hole" and t.get("ref"): holes[int(t["ref"])] = {"line": g, "par": int(t.get("par") or 0)}
        elif k in ("green", "tee", "fairway", "bunker") and len(g) > 3: shapes.append({"kind": k, "ll": g[:-1] if g[0] == g[-1] else g, "id": f"osm{e['id']}"})
        elif (k in WATER or t.get("natural") == "water") and len(g) > 3: shapes.append({"kind": "water", "ll": g[:-1] if g[0] == g[-1] else g, "id": f"osm{e['id']}"})
        elif t.get("leisure") == "golf_course" and len(g) > 3: boundary = g
    def centroid(P):
        x, y = P[:, 0], P[:, 1]; x1, y1 = np.roll(x, -1), np.roll(y, -1)
        cr = x * y1 - x1 * y; A2 = cr.sum()
        if abs(A2) < 1e-9: return P.mean(0)
        return np.array([((x + x1) * cr).sum() / (3 * A2), ((y + y1) * cr).sum() / (3 * A2)])
    for s in shapes:
        s["xy"] = np.array([fwd(*p) for p in s["ll"]])
        s["c"] = centroid(s["xy"])
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
        card = hs["yellow_yd"] * YD
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
        out_holes[str(r)] = {"par": h["par"], "si": hs.get("si"), "dist": dist, "line": [inv(p) for p in P], "source": "verified",
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
                      "source": "verified" if s.get("added") or s.get("extended") else "osm"} for s in shapes],
          "trees": trees, "source": "verified", "card": spec.get("card"), "designer": spec.get("designer"), "verified": spec.get("verified")}
    json.dump(ed, open(f"courses/edits/{slug}.json", "w"), separators=(",", ":"))
    print(f"{slug}: {len(out_holes)} holes, {len(ed['shapes'])} shapes, {len(trees)} trees")


if __name__ == "__main__":
    main(sys.argv[1])
