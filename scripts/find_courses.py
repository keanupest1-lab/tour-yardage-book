"""Lists golf courses (name, bounds, mapped holes/greens) inside areas, from OpenStreetMap.
Input courses/find-requests.txt: label | south,west,north,east   Output courses/found/<label>.json"""
import json, os, re, sys, time
sys.path.insert(0, "ml")
import automap_lib as A

os.makedirs("courses/found", exist_ok=True)
for line in open("courses/find-requests.txt"):
    line = line.strip()
    if not line or line.startswith("#"): continue
    label, bb = [x.strip() for x in line.split("|")]
    s, w, n, e = map(float, bb.split(","))
    b = f"{s},{w},{n},{e}"
    j = A.overpass(f'[out:json][timeout:120];(way["leisure"="golf_course"]({b});relation["leisure"="golf_course"]({b}););out tags bb;'
                   f'(way["golf"="hole"]({b}););out tags bb;(way["golf"="green"]({b}););out bb;')
    out = []
    for el in j["elements"]:
        t = el.get("tags", {}); bb2 = el.get("bounds")
        if t.get("leisure") == "golf_course":
            out.append({"type": el["type"], "id": el["id"], "name": t.get("name"), "tags": t, "bounds": bb2})
    holes = [el for el in j["elements"] if el.get("tags", {}).get("golf") == "hole"]
    greens = [el for el in j["elements"] if el.get("tags", {}).get("golf") == "green"]
    for c in out:
        b2 = c["bounds"]
        inb = lambda el: el.get("bounds") and b2["minlat"] <= (el["bounds"]["minlat"] + el["bounds"]["maxlat"]) / 2 <= b2["maxlat"] and b2["minlon"] <= (el["bounds"]["minlon"] + el["bounds"]["maxlon"]) / 2 <= b2["maxlon"]
        c["holes"] = sorted([h.get("tags", {}).get("ref", "?") for h in holes if inb(h)])
        c["greens"] = sum(1 for g in greens if inb(g))
    json.dump(out, open(f"courses/found/{re.sub('[^a-z0-9]+', '-', label.lower())}.json", "w"), indent=1)
    print(label, [(c["name"], len(c["holes"]), c["greens"]) for c in out])
    time.sleep(5)
