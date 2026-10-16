"""Extra map features a yardage book needs that the course query leaves out: rivers, streams and drains
(waterway lines, with their widths when mapped), and buildings (so roofs are never taken for ground heights). Runs in GitHub Actions (Overpass isn't reachable from the
workspace) when courses/osm-extra-requests.txt changes. Line format:  slug
Output: courses/osm-extra/<slug>.json (Overpass JSON, geometry included)."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ml"))
import automap_lib as A

idx = {c["slug"]: c for c in json.load(open("courses/index.json"))["courses"]}
os.makedirs("courses/osm-extra", exist_ok=True)
for line in open("courses/osm-extra-requests.txt"):
    slug = line.strip()
    if not slug or slug.startswith("#"): continue
    c = idx.get(slug)
    if not c: print("unknown", slug); continue
    s, n, w, e = c["bbox"]; pad = 0.004
    q = f"""[out:json][timeout:60];
(
  way["waterway"~"^(river|stream|canal|drain|ditch|brook)$"]({s - pad},{w - pad},{n + pad},{e + pad});
  way["natural"="water"]({s - pad},{w - pad},{n + pad},{e + pad});
  relation["natural"="water"]({s - pad},{w - pad},{n + pad},{e + pad});
  way["water"="river"]({s - pad},{w - pad},{n + pad},{e + pad});
  way["building"]({s - pad},{w - pad},{n + pad},{e + pad});
);
out geom;"""
    try:
        j = A.overpass(q)
        json.dump(j, open(f"courses/osm-extra/{slug}.json", "w"))
        print(slug, len(j.get("elements", [])), "elements")
    except Exception as ex:
        print("FAILED", slug, ex)
