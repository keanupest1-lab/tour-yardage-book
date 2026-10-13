"""Terrain heights for a course, for contours and uphill/downhill numbers in the book.

Request file courses/terrain-requests.txt: one library slug per line.
Output courses/terrain/<slug>.json:
  {s, w, n, e, nx, ny, step_m, dlat, dlon, base, scale, data}  data = base64 little-endian uint16, row i at
  lat n - i*dlat, column j at lon w + j*dlon,
  height_m = base + value * scale. Source: the open Terrarium tiles (SRTM and national elevation models).
"""
import base64, io, json, math, os, sys
import numpy as np
from PIL import Image
from scipy import ndimage
sys.path.insert(0, os.path.dirname(__file__))
import automap_lib as A

TERRARIUM = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"


def heights(s, w, n, e, step=5.0, z=15, log=print):
    X0, Y0 = A.ll2world(n, w, z); X1, Y1 = A.ll2world(s, e, z)
    tx0, ty0, tx1, ty1 = int(X0 // 256), int(Y0 // 256), int(X1 // 256), int(Y1 // 256)
    dem = np.zeros(((ty1 - ty0 + 1) * 256, (tx1 - tx0 + 1) * 256), np.float32)
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            a = np.asarray(Image.open(io.BytesIO(A.http(TERRARIUM.format(z=z, x=tx, y=ty), timeout=30))).convert("RGB"), np.float32)
            dem[(ty - ty0) * 256:(ty - ty0 + 1) * 256, (tx - tx0) * 256:(tx - tx0 + 1) * 256] = a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768
    # the source is ~30 m data: smooth away its stair-steps before sampling a fine grid
    dem = ndimage.gaussian_filter(dem, 1.0)
    lat_m = 111320.0; lon_m = lat_m * math.cos(math.radians((s + n) / 2))
    ny = int((n - s) * lat_m / step) + 1; nx = int((e - w) * lon_m / step) + 1
    lats = n - np.arange(ny) * step / lat_m; lons = w + np.arange(nx) * step / lon_m
    px = np.array([A.ll2world(lats[0], lo, z)[0] for lo in lons]) - tx0 * 256 - .5
    py = np.array([A.ll2world(la, lons[0], z)[1] for la in lats]) - ty0 * 256 - .5
    PY, PX = np.meshgrid(py, px, indexing="ij")
    out = ndimage.map_coordinates(dem, [PY, PX], order=3, mode="nearest")
    log(f"  {nx}x{ny} grid, {out.min():.1f}-{out.max():.1f} m")
    return out, nx, ny, step / lat_m, step / lon_m


def main():
    req = "courses/terrain-requests.txt"
    if not os.path.exists(req): return
    idx = {c["slug"]: c for c in json.load(open("courses/index.json"))["courses"]}
    os.makedirs("courses/terrain", exist_ok=True)
    for line in open(req):
        slug = line.strip()
        if not slug or slug.startswith("#"): continue
        c = idx.get(slug)
        if not c: print("unknown", slug); continue
        s, n, w, e = c["bbox"]
        pad = 0.003
        s, n, w, e = s - pad, n + pad, w - pad, e + pad
        print("==", slug, flush=True)
        try:
            H, nx, ny, dlat, dlon = heights(s, w, n, e)
            base = float(np.floor(H.min())); scale = 0.02
            q = np.clip(np.round((H - base) / scale), 0, 65535).astype("<u2")
            json.dump({"s": s, "w": w, "n": n, "e": e, "nx": nx, "ny": ny, "step_m": 5.0, "dlat": dlat, "dlon": dlon, "base": base, "scale": scale,
                       "source": "Terrarium elevation tiles (SRTM / national DEMs), smoothed",
                       "data": base64.b64encode(q.tobytes()).decode()}, open(f"courses/terrain/{slug}.json", "w"))
        except Exception as ex:
            import traceback; traceback.print_exc(); print("FAILED", slug, ex)


if __name__ == "__main__":
    main()
