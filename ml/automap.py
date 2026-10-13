"""Automatic course mapper: course name + scorecard -> a full yardage book in the course library.

Request file courses/automap-requests.txt, one course per line:
  Course name | pars (18 numbers) | lengths in metres (18 numbers) [| clubhouse lat,lon] [| south,west,north,east]
e.g.
  Irene Country Club | 4 5 4 3 4 4 3 4 5 5 4 4 3 4 4 3 5 4 | 370 563 471 208 415 338 138 303 517 522 401 392 171 437 313 133 507 318

Steps: find the course (OpenStreetMap search), satellite mosaic, AI shape mask, routing against the scorecard,
then write courses/<slug>.json (map snapshot) + courses/edits/<slug>.json (holes and shapes) + the library index.
Holes and shapes the AI drew are marked so the app can say they need checking.
"""
import datetime, json, math, os, re, sys, time, urllib.parse
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
import automap_lib as A

MODEL = os.environ.get("MODEL", "models/model.pt")
REQ = "courses/automap-requests.txt"
LOG = []
def log(*a):
    s = " ".join(str(x) for x in a); print(s, flush=True); LOG.append(s)


def slugify(s):
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:60]


def nominatim(q):
    raw = A.http("https://nominatim.openstreetmap.org/search?format=jsonv2&limit=10&q=" + urllib.parse.quote(q), timeout=30)
    rows = json.loads(raw)
    golf = [r for r in rows if r.get("type") == "golf_course"] or [r for r in rows if "golf" in r.get("display_name", "").lower()] or rows
    if not golf: raise RuntimeError("course not found on OpenStreetMap search")
    r = golf[0]; s, n, w, e = map(float, r["boundingbox"])
    return {"name": r.get("name") or r["display_name"].split(",")[0], "place": r["display_name"], "bbox": [s, n, w, e]}


def osm_snapshot(s, w, n, e):
    """the map data the app normally loads, plus any clubhouse"""
    b = f"{s},{w},{n},{e}"
    q = (f'[out:json][timeout:120];(way["golf"]({b});relation["golf"]({b});way["leisure"="golf_course"]({b});'
         f'relation["leisure"="golf_course"]({b});way["natural"~"^(water|wood|tree_row)$"]({b});node["natural"="tree"]({b});'
         f'nwr["building"="clubhouse"]({b});nwr["amenity"="clubhouse"]({b}););out geom;')
    return A.overpass(q, log=log)


def clubhouse_from(snapshot):
    for el in snapshot.get("elements", []):
        t = el.get("tags", {})
        if t.get("golf") == "clubhouse" or t.get("building") == "clubhouse" or t.get("amenity") == "clubhouse":
            if el["type"] == "node": return el["lat"], el["lon"]
            g = el.get("geometry") or []
            if g: return sum(p["lat"] for p in g) / len(g), sum(p["lon"] for p in g) / len(g)
    return None


def polygons(lab, cls, mpp, min_m2, eps=1.2):
    import cv2
    mk = (lab == cls).astype(np.uint8)
    mk = cv2.morphologyEx(mk, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    cs, _ = cv2.findContours(mk, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    out = []
    for c in cs:
        if cv2.contourArea(c) * mpp * mpp < min_m2: continue
        c = cv2.approxPolyDP(c, eps, True)[:, 0, :]
        if len(c) >= 3: out.append([(float(x), float(y)) for x, y in c])
    return out


def seg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy or 1)))
    return math.dist(p, (a[0] + t * dx, a[1] + t * dy))


def map_course(line, model):
    parts = [p.strip() for p in line.split("|")]
    name = parts[0]
    # scorecard optional: "Name | auto" (or empty pars) maps from the ground alone
    pars = [int(x) for x in parts[1].split()] if len(parts) > 1 and parts[1].strip() not in ("", "auto") else []
    lens = [float(x) for x in parts[2].split()] if len(parts) > 2 and parts[2].strip() else []
    if pars and (len(pars) != len(lens) or len(pars) not in (9, 18)): raise ValueError("need 9 or 18 pars and lengths")
    club = tuple(map(float, parts[3].split(","))) if len(parts) > 3 and parts[3] else None
    if len(parts) > 4 and parts[4]:
        s, w, n, e = map(float, parts[4].split(","))
        old = {}
        if os.path.exists("courses/index.json"):
            old = next((c for c in json.load(open("courses/index.json"))["courses"] if c["slug"] == slugify(name)), {})
        meta = {"name": name, "place": old.get("place", name), "bbox": [s, n, w, e]}
    else:
        meta = nominatim(name if "golf" in name.lower() else name + " golf")
        s, n, w, e = meta["bbox"]
    # golf course boxes from search are sometimes just the clubhouse: make sure the box covers a course
    cy, cx = (s + n) / 2, (w + e) / 2
    half_lat = max((n - s) / 2, 0.0075); half_lon = max((e - w) / 2, 0.0075 / math.cos(math.radians(cy)))
    s, n, w, e = cy - half_lat, cy + half_lat, cx - half_lon, cx + half_lon
    slug = slugify(name)
    log(f"== {name} ({slug}) box {s:.5f},{w:.5f},{n:.5f},{e:.5f}")
    snap = osm_snapshot(s, w, n, e)
    club = club or clubhouse_from(snap)
    m = A.fetch_mosaic(s, w, n, e, log=log)
    mpp = m.mpp
    t = time.time(); lab, _ = A.predict(model, m.img); log(f"  shapes found in {time.time() - t:.0f}s")
    card = list(zip(pars, lens)) if pars else [None] * 18
    cpx = m.ll2px(*club) if club else None
    turn = {8: cpx} if cpx and len(card) == 18 else None
    res = A.route_from_mask(lab, mpp, card, start_px=cpx, end_px=cpx, turn_px=turn, log=log)
    if not res: raise RuntimeError("routing found no answer (too few greens or fairways detected)")
    log(f"  routed: cost {res['cost']}, next-best routing {res['runner_up_gap']} worse, {res['n_greens']} greens, {res['n_fairways']} fairways")

    note = ("Mapped automatically: shapes found by the AI model in satellite imagery and holes matched to the scorecard. "
            "Check against the club course map.")
    holes = {}
    for h in res["holes"]:
        hn = note if not h.get("estimated") else (note.replace("matched to the scorecard", "worked out from the ground")
                                                     + " No scorecard was used: par and length are estimates.")
        holes[str(h["hole"])] = {"par": h["par"], "dist": round(h["length"]), "line": [list(m.px2ll(x, y)) for x, y in h["points"]],
                                 "note": hn, "source": "model"}
    lines = [h["points"] for h in res["holes"]]
    def near(poly, tol_m):
        c = (sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))
        return min(seg_dist(c, a, b) for L in lines for a, b in zip(L[:-1], L[1:])) * mpp < tol_m
    shapes = []; n_id = 0
    for kind, cls, mn, tol in (("fairway", 1, 1500, 30), ("green", 2, 150, 20), ("bunker", 4, 25, 50), ("water", 5, 300, 90), ("tee", 3, 40, 25)):
        for p in polygons(lab, cls, mpp, mn):
            if not near(p, tol): continue
            shapes.append({"id": f"ai{kind}{n_id}", "kind": kind, "coords": [list(m.px2ll(x, y)) for x, y in p], "source": "model"}); n_id += 1
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    edits = {"version": 1, "slug": slug, "name": name, "updated": now, "holes": holes, "shapes": shapes,
             "source": {"method": "automap", "model": os.environ.get("MODEL_BRANCH", "models"), "cost": res["cost"],
                        "runner_up_gap": res["runner_up_gap"], "clubhouse": club}}
    os.makedirs("courses/edits", exist_ok=True)
    edits_path = f"courses/edits/{slug}.json"
    if os.path.exists(edits_path):   # keep anything a person traced: only fill holes they haven't
        old = json.load(open(edits_path))
        for k, v in old.get("holes", {}).items():
            if v.get("source") != "model" and v.get("line"): edits["holes"][k] = v
        edits["shapes"] = [x for x in old.get("shapes", []) if x.get("source") != "model"] + shapes
    json.dump(edits, open(edits_path, "w"), indent=1)
    json.dump({"name": name, "place": meta["place"], "bbox": [s, n, w, e], "fetched": now, "elements": snap.get("elements", [])},
              open(f"courses/{slug}.json", "w"))
    idx_path = "courses/index.json"
    idx = json.load(open(idx_path)) if os.path.exists(idx_path) else {"courses": []}
    idx["courses"] = [c for c in idx["courses"] if c["slug"] != slug] + [{
        "slug": slug, "query": name, "name": name, "place": meta["place"], "bbox": [s, n, w, e], "fetched": now,
        "census": {"holesReady": len(holes), "automap": True}}]
    json.dump(idx, open(idx_path, "w"), indent=1)
    # preview image for checking
    os.makedirs("courses/automap", exist_ok=True)
    from PIL import ImageDraw
    img = A.overlay(m.img, lab, 0.45); d = ImageDraw.Draw(img)
    for h in res["holes"]:
        d.line(h["points"], fill=(255, 40, 40), width=6); x, y = h["points"][0]
        d.ellipse((x - 20, y - 20, x + 20, y + 20), fill=(255, 255, 255)); d.text((x - 10, y - 12), str(h["hole"]), fill=(0, 0, 0), font_size=22)
    sc = 1800 / max(img.size)
    if sc < 1: img = img.resize((int(img.width * sc), int(img.height * sc)))
    img.save(f"courses/automap/{slug}.jpg", quality=78)
    # the raw shape map + where it sits, so routing can be re-run and tuned without the slow steps
    from PIL import Image
    Image.fromarray(lab).save(f"courses/automap/{slug}_mask.png", optimize=True)
    json.dump({"z": m.z, "tx0": m.tx0, "ty0": m.ty0, "width": m.img.width, "height": m.img.height, "card": card,
               "clubhouse": club}, open(f"courses/automap/{slug}_mask.json", "w"))
    log(f"  saved {len(holes)} holes, {len(shapes)} shapes -> {edits_path}")
    return slug


def main():
    if not os.path.exists(REQ): return
    done_path = "courses/automap/done.txt"
    done = set(open(done_path).read().split("\n")) if os.path.exists(done_path) else set()
    todo = [l.strip() for l in open(REQ) if l.strip() and not l.startswith("#")]
    todo = [l for l in todo if l not in done or os.environ.get("FORCE") == "1"]
    if not todo: log("nothing to map"); return
    model = A.load_model(MODEL)
    os.makedirs("courses/automap", exist_ok=True)
    for line in todo:
        try:
            map_course(line, model)
            with open(done_path, "a") as f: f.write(line + "\n")
        except Exception as e:
            log(f"  FAILED {line.split('|')[0].strip()}: {e!r}")
    with open("courses/automap/last_run.log", "w") as f: f.write("\n".join(LOG) + "\n")


if __name__ == "__main__":
    main()
