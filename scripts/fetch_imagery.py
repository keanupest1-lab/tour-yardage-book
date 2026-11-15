"""Downloads satellite tiles for requested areas and stitches a georeferenced mosaic.
Runs in GitHub Actions when courses/imagery-requests.txt changes.
Line format:  slug | south,north,west,east | zoom
Output: courses/imagery/<slug>/z<zoom>.jpg and meta.json (exact mosaic bounds, for pixel <-> lat/lon)."""
import io, json, math, os, time, urllib.request
from PIL import Image

SRC = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
UA = {"User-Agent": "TourYardageBooks/0.4 (+https://github.com/keanupest1-lab/tour-yardage-book)"}

def tile_xy(lat, lon, z):
    n = 2 ** z
    x = (lon + 180) / 360 * n
    r = math.radians(lat)
    y = (1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * n
    return x, y

def tile_to_lonlat(x, y, z):
    n = 2 ** z
    lon = x / n * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lon, lat

def get(url, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return r.read()
        except Exception as e:
            time.sleep(2 + 3 * i)
    return None

lines = [l.strip() for l in open("courses/imagery-requests.txt") if l.strip() and not l.startswith("#")]
for line in lines:
    slug, bbox, zoom = [p.strip() for p in line.split("|")]
    s, n, w, e = map(float, bbox.split(","))
    z = int(zoom)
    out_dir = f"courses/imagery/{slug}"
    if os.path.exists(f"{out_dir}/z{z}.jpg"):
        print("skip", slug, z); continue
    os.makedirs(out_dir, exist_ok=True)
    x0, y0 = map(int, map(math.floor, tile_xy(n, w, z)))
    x1, y1 = map(int, map(math.floor, tile_xy(s, e, z)))
    W, H = (x1 - x0 + 1) * 256, (y1 - y0 + 1) * 256
    mosaic = Image.new("RGB", (W, H), (40, 40, 40))
    missing = 0
    for ty in range(y0, y1 + 1):
        for tx in range(x0, x1 + 1):
            data = get(SRC.format(z=z, x=tx, y=ty))
            if not data: missing += 1; continue
            try: mosaic.paste(Image.open(io.BytesIO(data)).convert("RGB"), ((tx - x0) * 256, (ty - y0) * 256))
            except Exception: missing += 1
    mosaic.save(f"{out_dir}/z{z}.jpg", quality=86)
    west, north = tile_to_lonlat(x0, y0, z)
    east, south = tile_to_lonlat(x1 + 1, y1 + 1, z)
    meta_path = f"{out_dir}/meta.json"
    meta = json.load(open(meta_path)) if os.path.exists(meta_path) else {}
    meta[f"z{z}"] = {"zoom": z, "tile_x0": x0, "tile_y0": y0, "width": W, "height": H,
                    "bounds": {"north": north, "south": south, "west": west, "east": east},
                    "missing_tiles": missing, "source": "Esri World Imagery", "requested_bbox": [s, n, w, e]}
    json.dump(meta, open(meta_path, "w"), indent=1)
    print("saved", slug, z, W, H, "missing", missing)

# Capture dates: ask Esri's imagery metadata service which source and date each requested area shows at
# the zooms we use (points on a 3 x 3 grid over the area). Saved to courses/imagery/<slug>/capture.json.
import urllib.parse
META_HOSTS = ["https://metadata.maptiles.arcgis.com/arcgis/rest/services/World_Imagery_Metadata/MapServer",
              "https://services.arcgisonline.com/arcgis/rest/services/World_Imagery_Metadata/MapServer"]

def get_json(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
            data = r.read()
    except Exception as ex:
        print("   ", url[:120], "->", repr(ex)[:200]); return None
    try: return json.loads(data)
    except Exception:
        print("   ", url[:120], "-> not JSON:", data[:200]); return None

# Esri's Wayback archive lists every World Imagery release with its own metadata service (release dates, sources)
wb = get_json("https://s3-us-west-2.amazonaws.com/config.maptiles.arcgis.com/waybackconfig.json")
if wb:
    rel = sorted(wb.items(), key=lambda kv: kv[1].get("itemTitle", ""))[-3:]
    for k, v in rel: print("wayback release", k, v.get("itemTitle"), v.get("metadataLayerUrl"))
    META_HOSTS = [v["metadataLayerUrl"].rstrip("/") for k, v in rel[::-1] if v.get("metadataLayerUrl")] + META_HOSTS

done = set()
for line in lines:
    slug, bbox, zoom = [p.strip() for p in line.split("|")]
    if slug in done or os.path.exists(f"courses/imagery/{slug}/capture.json"): continue
    done.add(slug)
    s, n, w, e = map(float, bbox.split(","))
    out = {"points": [], "service": None, "layers": []}
    for host in META_HOSTS:
        svc = get_json(host + "?f=json")
        if not svc or "layers" not in svc: print("metadata service unavailable:", host); continue
        out["service"] = host
        out["layers"] = [{k: L.get(k) for k in ("id", "name", "minScale", "maxScale")} for L in svc["layers"]]
        for fy in (0.2, 0.5, 0.8):
            for fx in (0.2, 0.5, 0.8):
                lat, lon = s + (n - s) * fy, w + (e - w) * fx
                pt = {"lat": round(lat, 6), "lon": round(lon, 6), "hits": []}
                for L in svc["layers"]:
                    q = urllib.parse.urlencode({"geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint",
                                                "inSR": 4326, "spatialRel": "esriSpatialRelIntersects",
                                                "outFields": "*", "returnGeometry": "false", "f": "json"})
                    r = get_json(f"{host}/{L['id']}/query?{q}")
                    for f in (r or {}).get("features", []):
                        a = f.get("attributes", {})
                        pt["hits"].append({"layer": L["id"], "name": L.get("name"),
                                           **{k: a[k] for k in a if k.upper().startswith(("SRC", "NICE", "DATE", "RES", "MIN", "MAX"))}})
                out["points"].append(pt)
        break
    if not out["service"]: continue          # nothing learned: leave no file, so a later run asks again
    json.dump(out, open(f"courses/imagery/{slug}/capture.json", "w"), indent=1)
    centre = out["points"][4]["hits"] if len(out["points"]) > 4 else []
    print("capture", slug, [(h.get("name"), h.get("SRC_DATE"), h.get("SRC_DESC"), h.get("SRC_RES")) for h in centre][:12])
