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
