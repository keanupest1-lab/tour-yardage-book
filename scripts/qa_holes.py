"""Checks every hole in the library: does it start on a tee box and end on a green?

For each course (map data + edits merged the way the app does it) it reports, per hole:
  tee  = distance from the start of the hole line to the nearest tee box (0 = on it)
  green= distance from the end of the hole line to the nearest green (0 = on it), and whether that green
         is also the end of another hole (two holes to one green = one of them is wrong, unless a double green)
Usage: python3 scripts/qa_holes.py [slug ...]  -> prints a table, writes courses/qa.json
"""
import json, math, os, sys

M_LAT = 111320.0


def to_xy(lat0, lon0):
    k = math.cos(math.radians(lat0))
    return lambda p: ((p[1] - lon0) * M_LAT * k, (p[0] - lat0) * M_LAT)


def inside(pt, ring):
    x, y = pt; r = False
    for i in range(len(ring)):
        x1, y1 = ring[i]; x2, y2 = ring[i - 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1: r = not r
    return r


def seg_d(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy or 1)))
    return math.dist(p, (a[0] + t * dx, a[1] + t * dy))


def poly_d(p, ring):
    if inside(p, ring): return 0.0
    return min(seg_d(p, ring[i - 1], ring[i]) for i in range(len(ring)))


def course_data(slug):
    base = json.load(open(f"courses/{slug}.json"))
    ep = f"courses/edits/{slug}.json"
    ed = json.load(open(ep)) if os.path.exists(ep) else {"holes": {}, "shapes": []}
    tees, greens, holes = [], [], {}
    for e in base.get("elements", []):
        t = e.get("tags", {}); g = e.get("geometry")
        if not g: continue
        pts = [(p["lat"], p["lon"]) for p in g if p]
        if t.get("golf") == "tee" and len(pts) > 2: tees.append(pts)
        if t.get("golf") == "green" and len(pts) > 2: greens.append(pts)
        if t.get("golf") == "hole" and t.get("ref"): holes.setdefault(str(t["ref"]), []).append({"line": pts, "par": t.get("par"), "src": "map"})
    for s in ed.get("shapes", []):
        pts = [tuple(p) for p in s["coords"]]
        if s["kind"] == "tee": tees.append(pts)
        if s["kind"] == "green": greens.append(pts)
    for ref, h in ed.get("holes", {}).items():
        if h.get("line") and len(h["line"]) >= 2:
            holes[ref] = [{"line": [tuple(p) for p in h["line"]], "par": h.get("par"), "src": h.get("source", "editor")}]
    return tees, greens, holes


def check(slug):
    tees, greens, holes = course_data(slug)
    allpts = [p for v in holes.values() for h in v for p in h["line"]] or [p for g in greens for p in g]
    if not allpts: return []
    xy = to_xy(sum(p[0] for p in allpts) / len(allpts), sum(p[1] for p in allpts) / len(allpts))
    T = [[xy(p) for p in r] for r in tees]; G = [[xy(p) for p in r] for r in greens]
    rows = []
    for ref in sorted(holes, key=lambda r: (len(r), r)):
        for h in holes[ref]:
            L = [xy(p) for p in h["line"]]
            # green: the line should finish on a green (normally its middle)
            gd = [(poly_d(L[-1], g), i) for i, g in enumerate(G)]
            gdist, gi = min(gd) if gd else (None, None)
            # tee: the line should start on a tee box; a tee box a little in front is still "that tee"
            td = min((poly_d(L[0], t) for t in T), default=None)
            near_t = sorted(((poly_d(L[0], t), k) for k, t in enumerate(T)))[:1]
            length = sum(math.dist(a, b) for a, b in zip(L[:-1], L[1:]))
            rows.append({"ref": ref, "src": h["src"], "length": round(length), "tee_gap": None if td is None else round(td),
                         "green_gap": None if gdist is None else round(gdist), "green_id": gi})
    ends = {}
    for r in rows:
        if r["green_gap"] is not None and r["green_gap"] < 25: ends.setdefault(r["green_id"], []).append(r["ref"])
    for r in rows:
        r["shares_green_with"] = [x for x in ends.get(r["green_id"], []) if x != r["ref"]] if r["green_gap"] is not None and r["green_gap"] < 25 else []
        probs = []
        if r["tee_gap"] is None: probs.append("no tee boxes mapped")
        elif r["tee_gap"] > 8: probs.append(f"starts {r['tee_gap']} m from a tee box")
        if r["green_gap"] is None: probs.append("no greens mapped")
        elif r["green_gap"] > 8: probs.append(f"ends {r['green_gap']} m from a green")
        if r["shares_green_with"]: probs.append(f"same green as hole {', '.join(r['shares_green_with'])}")
        r["problems"] = probs
    return rows


def main():
    idx = json.load(open("courses/index.json"))
    slugs = sys.argv[1:] or [c["slug"] for c in idx["courses"]]
    out = {}
    for slug in slugs:
        rows = check(slug); out[slug] = rows
        bad = [r for r in rows if r["problems"]]
        print(f"== {slug}: {len(rows)} hole lines, {len(bad)} with problems")
        for r in bad: print(f"   {r['ref']:>3} ({r['src']}, {r['length']} m): " + "; ".join(r["problems"]))
    json.dump(out, open("courses/qa.json", "w"), indent=1)


if __name__ == "__main__":
    main()
