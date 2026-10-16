"""Raw elevation for a course from the Copernicus DEM GLO-30 (ESA / Airbus TanDEM-X, 1 arc-second, about
30 m; free with attribution), read straight from its public cloud-optimised GeoTIFFs on AWS. Runs in GitHub
Actions (the bucket isn't reachable from the workspace) when courses/dem-requests.txt changes.
Line format: slug.  Output: courses/dem/<slug>-cop30.json  {transform, width, height, data (base64 float32)}
This is a surface model (it sees tree tops): scripts/ground_model.py turns it into ground heights."""
import base64, json, math, os
import numpy as np
import rasterio
from rasterio.windows import from_bounds
from rasterio.merge import merge

URL = "https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM/Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM.tif"


def tile(lat, lon):
    la, lo = math.floor(lat), math.floor(lon)
    return URL.format(ns="N" if la >= 0 else "S", lat=abs(la), ew="E" if lo >= 0 else "W", lon=abs(lo))


idx = {c["slug"]: c for c in json.load(open("courses/index.json"))["courses"]}
os.makedirs("courses/dem", exist_ok=True)
for line in open("courses/dem-requests.txt"):
    slug = line.strip()
    if not slug or slug.startswith("#"): continue
    s, n, w, e = idx[slug]["bbox"]; pad = 0.006
    s, n, w, e = s - pad, n + pad, w - pad, e + pad
    urls = sorted({tile(la, lo) for la in (s, n) for lo in (w, e)})
    print("==", slug, urls, flush=True)
    srcs = [rasterio.open(u) for u in urls]
    if len(srcs) == 1:
        src = srcs[0]
        win = from_bounds(w, s, e, n, src.transform).round_offsets().round_lengths()
        arr = src.read(1, window=win).astype("<f4"); tr = src.window_transform(win)
    else:
        arr, tr = merge(srcs, bounds=(w, s, e, n)); arr = arr[0].astype("<f4")
    t = list(tr)[:6]
    json.dump({"source": "Copernicus DEM GLO-30 (DGED), (c) DLR e.V. 2010-2014 and (c) Airbus Defence and Space GmbH 2014-2018, provided under COPERNICUS by the European Union and ESA",
               "transform": t, "width": int(arr.shape[1]), "height": int(arr.shape[0]),
               "data": base64.b64encode(arr.tobytes()).decode()}, open(f"courses/dem/{slug}-cop30.json", "w"))
    print(f"  {arr.shape[1]}x{arr.shape[0]} px, {np.nanmin(arr):.1f}-{np.nanmax(arr):.1f} m", flush=True)
