"""Builds a golf-course segmentation dataset from OpenStreetMap labels + satellite imagery.

For each region in ml/regions.txt:
  1. Overpass: golf course boundaries and golf features (greens, fairways, tees, bunkers, water).
  2. Keep only courses that look fully mapped (enough greens, fairways and bunkers).
  3. For every 512 x 512 chip (2 x 2 tiles at zoom 18) inside such a course: download imagery and
     rasterise a label mask.
Classes: 0 background, 1 fairway, 2 green, 3 tee, 4 bunker, 5 water.
Output: ml/data/images/*.jpg, ml/data/masks/*.png, ml/data/index.json
"""
import io, json, math, os, sys, time, urllib.parse, urllib.request
from PIL import Image, ImageDraw

UA = {"User-Agent": "TourYardageBooks/0.5 (+https://github.com/keanupest1-lab/tour-yardage-book)"}
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter"]
TILE = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
Z = 18
CLASS = {"fairway": 1, "green": 2, "tee": 3, "bunker": 4, "water": 5}
DRAW_ORDER = ["fairway", "tee", "green", "water", "bunker"]
MAX_CHIPS = int(os.environ.get("MAX_CHIPS", "4000"))
MIN = {"green": 9, "fairway": 7, "bunker": 6}

def http(url, data=None, timeout=120):
    req = urllib.request.Request(url, data=data, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def overpass(q):
    for attempt in range(6):
        ep = OVERPASS[attempt % len(OVERPASS)]
        try:
            raw = http(ep, ("data=" + urllib.parse.quote(q)).encode(), timeout=200)
            if raw.startswith(b"{"):
                j = json.loads(raw)
                if "remark" not in j or "error" not in j.get("remark", "").lower():
                    return j
        except Exception as e:
            print("  overpass", ep, e)
        time.sleep(15)
    raise RuntimeError("overpass failed")

def kind(tags):
    g = tags.get("golf")
    if g in ("fairway", "green", "tee", "bunker"): return g
    if g in ("water_hazard", "lateral_water_hazard") or tags.get("natural") == "water": return "water"
    if tags.get("natural") == "sand" and g is None: return None
    return None

def lonlat_to_px(lon, lat, z=Z):
    n = 2 ** z * 256
    x = (lon + 180) / 360 * n
    r = math.radians(lat)
    y = (1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * n
    return x, y

def point_in(p, poly):
    x, y = p; inside = False
    for i in range(len(poly)):
        x1, y1 = poly[i]; x2, y2 = poly[i - 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1: inside = not inside
    return inside

def rings(el):
    if el["type"] == "way" and "geometry" in el:
        g = [(p["lon"], p["lat"]) for p in el["geometry"]]
        return [g] if len(g) > 3 and g[0] == g[-1] else []
    if el["type"] == "relation":
        return [[(p["lon"], p["lat"]) for p in m["geometry"]] for m in el.get("members", [])
                if m.get("role") == "outer" and m.get("geometry") and len(m["geometry"]) > 3]
    return []

def main():
    os.makedirs("ml/data/images", exist_ok=True); os.makedirs("ml/data/masks", exist_ok=True)
    idx_path = "ml/data/index.json"
    index = json.load(open(idx_path)) if os.path.exists(idx_path) else {"chips": [], "courses": []}
    done_courses = {c["id"] for c in index["courses"]}
    regions = [l.strip() for l in open("ml/regions.txt") if l.strip() and not l.startswith("#")]
    for line in regions:
        if len(index["chips"]) >= MAX_CHIPS: break
        name, bbox = [s.strip() for s in line.split("|")]
        s, w, n, e = map(float, bbox.split(","))
        b = f"{s},{w},{n},{e}"
        print("region", name)
        try:
            j = overpass(f'[out:json][timeout:180];(way["leisure"="golf_course"]({b});relation["leisure"="golf_course"]({b});'
                         f'way["golf"~"^(green|fairway|tee|bunker|water_hazard|lateral_water_hazard)$"]({b});'
                         f'way["natural"="water"]({b}););out geom;')
        except Exception as ex:
            print("  skip region:", ex); continue
        courses, feats = [], []
        for el in j["elements"]:
            t = el.get("tags", {})
            if t.get("leisure") == "golf_course":
                for r in rings(el): courses.append({"id": f'{el["type"][0]}{el["id"]}', "name": t.get("name", "?"), "ring": r})
            else:
                k = kind(t)
                if k:
                    for r in rings(el): feats.append((k, r))
        print(f"  {len(courses)} courses, {len(feats)} features")
        for c in courses:
            if c["id"] in done_courses or len(index["chips"]) >= MAX_CHIPS: continue
            ring = c["ring"]
            mine = [(k, r) for k, r in feats if point_in((sum(p[0] for p in r) / len(r), sum(p[1] for p in r) / len(r)), ring)]
            cnt = {k: sum(1 for kk, _ in mine if kk == k) for k in CLASS}
            if any(cnt.get(k, 0) < v for k, v in MIN.items()):
                continue
            # water anywhere near the course counts (lakes often extend past the boundary)
            near = mine + [(k, r) for k, r in feats if k == "water" and (k, r) not in mine]
            pxring = [lonlat_to_px(*p) for p in ring]
            x0 = int(min(p[0] for p in pxring) // 512) * 512; x1 = int(max(p[0] for p in pxring) // 512 + 1) * 512
            y0 = int(min(p[1] for p in pxring) // 512) * 512; y1 = int(max(p[1] for p in pxring) // 512 + 1) * 512
            if (x1 - x0) * (y1 - y0) > 512 * 512 * 120: print("  too big", c["name"]); continue
            made = 0
            for cy in range(y0, y1, 512):
                for cx in range(x0, x1, 512):
                    centre = (cx + 256, cy + 256)
                    if not point_in(centre, pxring): continue
                    mask = Image.new("L", (512, 512), 0); d = ImageDraw.Draw(mask)
                    for k in DRAW_ORDER:
                        for kk, r in near:
                            if kk != k: continue
                            pts = [(px - cx, py - cy) for px, py in (lonlat_to_px(*p) for p in r)]
                            if max(p[0] for p in pts) < 0 or min(p[0] for p in pts) > 512 or max(p[1] for p in pts) < 0 or min(p[1] for p in pts) > 512: continue
                            d.polygon(pts, fill=CLASS[k])
                    hist = mask.histogram()
                    if sum(hist[1:6]) < 512 * 512 * 0.04: continue   # mostly empty chip: skip
                    img = Image.new("RGB", (512, 512))
                    ok = True
                    for ty in range(2):
                        for tx in range(2):
                            X, Y = cx // 256 + tx, cy // 256 + ty
                            try:
                                img.paste(Image.open(io.BytesIO(http(TILE.format(z=Z, x=X, y=Y), timeout=30))).convert("RGB"), (tx * 256, ty * 256))
                            except Exception:
                                ok = False
                    if not ok: continue
                    cid = f"{c['id']}_{cx // 512}_{cy // 512}"
                    img.save(f"ml/data/images/{cid}.jpg", quality=88); mask.save(f"ml/data/masks/{cid}.png")
                    index["chips"].append({"id": cid, "course": c["id"], "region": name})
                    made += 1
            index["courses"].append({"id": c["id"], "name": c["name"], "region": name, "counts": cnt, "chips": made})
            print(f"  + {c['name']}: {made} chips {cnt}")
            json.dump(index, open(idx_path, "w"))
    json.dump(index, open(idx_path, "w"))
    print("total chips", len(index["chips"]), "courses", len(index["courses"]))

if __name__ == "__main__":
    main()
