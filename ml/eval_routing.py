"""Phase 2 test: route courses the model has never seen, and score the result against how
OpenStreetMap mappers routed them.

For each fully mapped 18-hole course in ml/eval_regions.txt (regions left out of training):
  1. satellite mosaic (z18) -> AI shape mask
  2. routing from the mask alone, using the scorecard (par + hole lengths from the OSM hole lines)
  3. a second routing with the real green positions, to separate "model missed a green" from "solver chose wrong"
Scores: share of holes whose green is right (within 35 m), and median tee error.
Output (out_eval/): results.json, summary.md, <course>.jpg overlays (cyan = truth, red = ours).
"""
import json, math, os, sys, time, traceback
import numpy as np
from PIL import Image, ImageDraw
sys.path.insert(0, os.path.dirname(__file__))
import automap_lib as A

OUT = "out_eval"; os.makedirs(OUT, exist_ok=True)
MODEL = os.environ.get("MODEL", "models/model.pt")
PER_REGION = int(os.environ.get("PER_REGION", "3"))
MAX_COURSES = int(os.environ.get("MAX_COURSES", "14"))
T0 = time.time(); BUDGET = float(os.environ.get("BUDGET_MIN", "150")) * 60
logf = open(f"{OUT}/eval.log", "a")
def log(*a):
    s = " ".join(str(x) for x in a); print(s, flush=True); logf.write(s + "\n"); logf.flush()


def hav(a, b):  # (lat, lon) metres
    R = 6371000; p1, p2 = math.radians(a[0]), math.radians(b[0]); dp = p2 - p1; dl = math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def inside(pt, ring):
    x, y = pt; r = False
    for i in range(len(ring)):
        x1, y1 = ring[i]; x2, y2 = ring[i - 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1: r = not r
    return r


def rings(el):
    if el["type"] == "way" and el.get("geometry"):
        g = [(p["lat"], p["lon"]) for p in el["geometry"] if p]
        return [g] if len(g) > 3 else []
    if el["type"] == "relation":
        return [[(p["lat"], p["lon"]) for p in m["geometry"] if p] for m in el.get("members", [])
                if m.get("role") == "outer" and m.get("geometry")]
    return []


def courses_in(region_bbox):
    s, w, n, e = region_bbox
    b = f"{s},{w},{n},{e}"
    j = A.overpass(f'[out:json][timeout:180];(way["leisure"="golf_course"]({b});relation["leisure"="golf_course"]({b});'
                   f'way["golf"="hole"]({b});way["golf"="green"]({b});nwr["golf"="clubhouse"]({b});'
                   f'nwr["building"="clubhouse"]({b});nwr["amenity"="clubhouse"]({b}););out geom;', log=log)
    els = j["elements"]
    holes = [x for x in els if x.get("tags", {}).get("golf") == "hole" and x.get("geometry")]
    greens = []
    for x in els:
        if x.get("tags", {}).get("golf") == "green":
            for r in rings(x): greens.append((sum(p[0] for p in r) / len(r), sum(p[1] for p in r) / len(r)))
    clubs = []
    for x in els:
        t = x.get("tags", {})
        if t.get("golf") == "clubhouse" or t.get("building") == "clubhouse" or t.get("amenity") == "clubhouse":
            if x["type"] == "node": clubs.append((x["lat"], x["lon"]))
            else:
                for r in rings(x): clubs.append((sum(p[0] for p in r) / len(r), sum(p[1] for p in r) / len(r)))
    out = []
    for c in els:
        t = c.get("tags", {})
        if t.get("leisure") != "golf_course": continue
        for ring in rings(c):
            mid = lambda h: h["geometry"][len(h["geometry"]) // 2]
            mine = [h for h in holes if inside((mid(h)["lat"], mid(h)["lon"]), ring)]
            refs = {}
            for h in mine:
                try: r = int(str(h["tags"].get("ref", "")).strip())
                except ValueError: continue
                refs.setdefault(r, []).append(h)
            if sorted(refs) != list(range(1, 19)) or any(len(v) > 1 for v in refs.values()): continue
            g_in = [g for g in greens if inside(g, ring)]
            if len(g_in) < 15: continue
            card, truth = [], []
            for r in range(1, 19):
                h = refs[r][0]; pts = [(p["lat"], p["lon"]) for p in h["geometry"]]
                # OSM hole lines run tee -> green; flip if the start is the end nearer a green
                if g_in and min(hav(pts[0], g) for g in g_in) + 15 < min(hav(pts[-1], g) for g in g_in): pts = pts[::-1]
                L = sum(hav(a, b) for a, b in zip(pts[:-1], pts[1:]))
                try: par = int(h["tags"].get("par"))
                except (TypeError, ValueError): par = 3 if L < 230 else (4 if L < 430 else 5)
                card.append((par, round(L))); truth.append(pts)
            club = [x for x in clubs if inside(x, ring) or min(hav(x, p) for p in ring[::max(1, len(ring) // 60)]) < 250]
            out.append({"name": t.get("name", f'{c["type"]}{c["id"]}'), "ring": ring, "card": card, "truth": truth,
                        "greens_osm": g_in, "club": club[0] if club else None})
    return out


def draw(m, lab, res, truth, path, title):
    img = A.overlay(m.img, lab, 0.45); d = ImageDraw.Draw(img)
    for k, pts in enumerate(truth):
        P = [m.ll2px(*p) for p in pts]; d.line(P, fill=(0, 230, 255), width=7)
    if res:
        for h in res["holes"]:
            d.line(h["points"], fill=(255, 40, 40), width=6); x, y = h["points"][0]
            d.ellipse((x - 20, y - 20, x + 20, y + 20), fill=(255, 255, 255)); d.text((x - 10, y - 12), str(h["hole"]), fill=(0, 0, 0), font_size=22)
    d.rectangle((0, 0, img.width, 40), fill=(0, 0, 0)); d.text((10, 8), title, fill=(255, 255, 255), font_size=22)
    s = 1600 / max(img.size)
    if s < 1: img = img.resize((int(img.width * s), int(img.height * s)))
    img.save(path, quality=80)


def score(res, truth, m, mpp):
    if not res: return {"greens_right": 0, "tee_err_median": None}
    ok = 0; tee_err = []
    for h, t in zip(res["holes"], truth):
        tg = m.ll2px(*t[-1]); tt = m.ll2px(*t[0])
        if math.dist(h["green_px"], tg) * mpp < 35: ok += 1
        tee_err.append(math.dist(h["points"][0], tt) * mpp)
    return {"greens_right": ok, "tee_err_median": round(float(np.median(tee_err)), 1)}


def main():
    model = A.load_model(MODEL)
    regions = [l.split("|") for l in open(os.path.join(os.path.dirname(__file__), "eval_regions.txt")) if l.strip() and not l.startswith("#")]
    results = json.load(open(f"{OUT}/results.json")) if os.path.exists(f"{OUT}/results.json") else []
    done = {r["name"] for r in results}
    for name, bbox in regions:
        if len(results) >= MAX_COURSES or time.time() - T0 > BUDGET: break
        name = name.strip(); log("region", name)
        try: cs = courses_in(tuple(map(float, bbox.split(","))))
        except Exception as e: log("  skip region:", e); continue
        log(f"  {len(cs)} fully routed 18-hole courses")
        for c in cs[:PER_REGION]:
            if c["name"] in done or len(results) >= MAX_COURSES or time.time() - T0 > BUDGET: continue
            try:
                t1 = time.time()
                lats = [p[0] for p in c["ring"]]; lons = [p[1] for p in c["ring"]]
                m = A.fetch_mosaic(min(lats), min(lons), max(lats), max(lons), log=log)
                mpp = m.mpp
                lab, _ = A.predict(model, m.img)
                club = m.ll2px(*c["club"]) if c["club"] else m.ll2px(*c["truth"][0][0])
                start_note = "clubhouse" if c["club"] else "hole-1 tee (no clubhouse mapped)"
                res = A.route_from_mask(lab, mpp, c["card"], start_px=club, end_px=club, turn_px={8: club}, log=log)
                s1 = score(res, c["truth"], m, mpp)
                # same again with the real green positions (isolates solver mistakes from missed greens)
                gpx = [m.ll2px(*g) for g in c["greens_osm"]]
                res2 = A.route_from_mask(lab, mpp, c["card"], start_px=club, end_px=club, turn_px={8: club}, greens_px=gpx, log=log)
                s2 = score(res2, c["truth"], m, mpp)
                # how many real greens the model found at all
                found = A.green_centres(lab, mpp)
                hit = sum(1 for g in gpx if found and min(math.dist(g, (x, y)) for x, y, _ in found) * mpp < 30)
                slug = "".join(ch if ch.isalnum() else "-" for ch in c["name"].lower())[:50]
                draw(m, lab, res, c["truth"], f"{OUT}/{slug}.jpg", f'{c["name"]}: {s1["greens_right"]}/18 greens right (ours red, OSM cyan)')
                r = {"name": c["name"], "region": name, "start": start_note, "card": c["card"],
                     "model_greens_found": f"{hit}/{len(gpx)}", "routing_model": s1, "routing_true_greens": s2,
                     "cost": res and res["cost"], "runner_up_gap": res and res["runner_up_gap"],
                     "seconds": round(time.time() - t1), "image": f"{slug}.jpg"}
                results.append(r); done.add(c["name"])
                log("  ", json.dumps(r))
            except Exception as e:
                log("  course failed", c["name"], repr(e)); traceback.print_exc()
            json.dump(results, open(f"{OUT}/results.json", "w"), indent=1)
    # summary
    lines = ["| Course | Region | Greens found | Routing (AI greens) | Routing (true greens) | Tee error |", "|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f'| {r["name"]} | {r["region"]} | {r["model_greens_found"]} | {r["routing_model"]["greens_right"]}/18 | '
                     f'{r["routing_true_greens"]["greens_right"]}/18 | {r["routing_model"]["tee_err_median"]} m |')
    if results:
        a = sum(r["routing_model"]["greens_right"] for r in results) / (18 * len(results))
        b = sum(r["routing_true_greens"]["greens_right"] for r in results) / (18 * len(results))
        lines += ["", f"Overall: {a:.0%} of holes routed right from the AI alone; {b:.0%} with true green positions. {len(results)} courses."]
    open(f"{OUT}/summary.md", "w").write("\n".join(lines) + "\n")
    log("\n".join(lines))


if __name__ == "__main__":
    main()
