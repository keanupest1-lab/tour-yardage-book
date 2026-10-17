"""Independent height measurements over a course, to check (and anchor) the uphill / downhill numbers.

  * ICESat-2 (NASA laser altimeter, 2018-): photons the ATL08 product classes as ground, through SlideRule
    (public NASA-funded processing service, slideruleearth.io). Saved two ways: 20 m ground-surface fits along each
    beam (ATL06-SR on ground photons) and the ground photons themselves (thinned).
  * GEDI (laser on the ISS, 2019-): L2A footprints (25 m) with the lowest-mode (ground) elevation, through SlideRule.
  * Two more surface models, independent of the Copernicus one: ALOS AW3D30 (optical stereo, JAXA) and NASADEM
    (SRTM radar, reprocessed), read from Microsoft Planetary Computer's open copies.

Laser heights are ellipsoidal (WGS84); the models are on geoids. Only differences inside a course matter for slopes,
so the offsets are estimated when they are combined (scripts/ground_model.py).
Runs in GitHub Actions when courses/heights-requests.txt changes (one slug per line).
Output: courses/heights/<slug>-icesat2.json, <slug>-icesat2-photons.json, <slug>-gedi.json, courses/dem/<slug>-<model>.json
"""
import base64, json, math, os, sys, time, traceback
import numpy as np

OUT = "courses/heights"
os.makedirs(OUT, exist_ok=True); os.makedirs("courses/dem", exist_ok=True)
idx = {c["slug"]: c for c in json.load(open("courses/index.json"))["courses"]}
PAD = 0.004       # about 400 m around the course


def log(*a):
    print(*a, flush=True)


def region(slug):
    s, n, w, e = idx[slug]["bbox"]
    s, n, w, e = s - PAD, n + PAD, w - PAD, e + PAD
    return s, n, w, e, [{"lon": w, "lat": s}, {"lon": e, "lat": s}, {"lon": e, "lat": n}, {"lon": w, "lat": n}, {"lon": w, "lat": s}]


def col(df, *names):
    for nm in names:
        if nm in df.columns: return df[nm].to_numpy()
    return None


def num(a, nd=None):
    a = np.asarray(a, dtype=float)
    return [None if not np.isfinite(v) else (round(float(v), nd) if nd is not None else float(v)) for v in a]


def lonlat(df):
    if "longitude" in df.columns: return df["longitude"].to_numpy(float), df["latitude"].to_numpy(float)
    return df.geometry.x.to_numpy(float), df.geometry.y.to_numpy(float)


def times(df):
    for nm in ("time", "time_ns"):
        if nm in df.columns:
            v = df[nm]
            try: return [str(t)[:19] for t in v]
            except Exception: pass
    try: return [str(t)[:19] for t in df.index]
    except Exception: return [None] * len(df)


def icesat2(slug, poly):
    from sliderule import icesat2 as is2
    base = {"poly": poly, "srt": is2.SRT_LAND, "cnf": is2.CNF_BACKGROUND, "atl08_class": ["atl08_ground"],
            "t0": "2018-10-01T00:00:00Z", "t1": time.strftime("%Y-%m-%dT00:00:00Z")}
    # 20 m ground fits, every 10 m
    t = time.time()
    try:
        p = dict(base, len=20.0, res=10.0, ats=5.0, cnt=5, maxi=6, H_min_win=3.0, sigma_r_max=5.0)
        df = is2.atl06p(p)
        log(f"  ICESat-2 ground fits: {len(df)} segments in {time.time() - t:.0f}s; columns {list(df.columns)}")
        if len(df):
            lo, la = lonlat(df)
            rec = {"source": "ICESat-2 ATL03 ground photons (ATL08 class) fitted in 20 m segments by SlideRule ATL06-SR; heights above the WGS84 ellipsoid",
                   "lat": num(la, 7), "lon": num(lo, 7), "h": num(col(df, "h_mean"), 3), "dh_dx": num(col(df, "dh_fit_dx"), 4),
                   "n": num(col(df, "n_fit_photons"), 0), "rms": num(col(df, "rms_misfit"), 3), "w": num(col(df, "w_surface_window_final"), 2),
                   "rgt": num(col(df, "rgt"), 0), "cycle": num(col(df, "cycle"), 0), "spot": num(col(df, "spot"), 0), "gt": num(col(df, "gt"), 0),
                   "time": times(df)}
            json.dump(rec, open(f"{OUT}/{slug}-icesat2.json", "w"))
    except Exception as e:
        log("  ICESat-2 ground fits failed:", repr(e)); traceback.print_exc()
    # the ground photons themselves
    t = time.time()
    try:
        p = dict(base, len=20.0, res=20.0, pass_invalid=True)
        df = is2.atl03sp(p)
        log(f"  ICESat-2 ground photons: {len(df)} in {time.time() - t:.0f}s; columns {list(df.columns)}")
        if len(df):
            lo, la = lonlat(df)
            h = col(df, "height", "h_ph")
            keep = np.arange(len(df))
            if len(df) > 150000: keep = np.sort(np.random.default_rng(1).choice(len(df), 150000, replace=False))
            sub = lambda a, nd: num(np.asarray(a)[keep], nd) if a is not None else None
            tm = times(df)
            rec = {"source": "ICESat-2 ATL03 photons classed ground by ATL08, via SlideRule; heights above the WGS84 ellipsoid",
                   "lat": sub(la, 7), "lon": sub(lo, 7), "h": sub(h, 3), "cnf": sub(col(df, "atl03_cnf"), 0),
                   "rgt": sub(col(df, "rgt"), 0), "cycle": sub(col(df, "cycle"), 0), "spot": sub(col(df, "spot"), 0), "gt": sub(col(df, "gt", "track"), 0),
                   "time": [tm[i] for i in keep]}
            json.dump(rec, open(f"{OUT}/{slug}-icesat2-photons.json", "w"))
    except Exception as e:
        log("  ICESat-2 photons failed:", repr(e)); traceback.print_exc()


def gedi(slug, poly):
    from sliderule import gedi as gd
    t = time.time()
    try:
        df = gd.gedi02ap({"poly": poly, "t0": "2019-03-01T00:00:00Z", "t1": time.strftime("%Y-%m-%dT00:00:00Z")})
        log(f"  GEDI L2A: {len(df)} footprints in {time.time() - t:.0f}s; columns {list(df.columns)}")
        if len(df):
            lo, la = lonlat(df)
            rec = {"source": "GEDI L2A footprints via SlideRule: elevation_lm is the lowest mode (ground), heights above the WGS84 ellipsoid",
                   "lat": num(la, 7), "lon": num(lo, 7), "elev_lm": num(col(df, "elevation_lm"), 3), "elev_hr": num(col(df, "elevation_hr"), 3),
                   "sensitivity": num(col(df, "sensitivity"), 3), "solar_elevation": num(col(df, "solar_elevation"), 1),
                   "flags": num(col(df, "flags"), 0), "beam": num(col(df, "beam"), 0), "orbit": num(col(df, "orbit"), 0), "time": times(df)}
            json.dump(rec, open(f"{OUT}/{slug}-gedi.json", "w"))
    except Exception as e:
        log("  GEDI failed:", repr(e)); traceback.print_exc()


def models(slug, s, n, w, e):
    import planetary_computer, pystac_client, rasterio
    from rasterio.windows import from_bounds
    from rasterio.merge import merge
    cat = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1", modifier=planetary_computer.sign_inplace)
    for coll, asset, name, src_note in (("alos-dem", "data", "aw3d30", "ALOS World 3D 30 m (AW3D30), (c) JAXA; via Microsoft Planetary Computer"),
                                         ("nasadem", "elevation", "nasadem", "NASADEM (NASA JPL, SRTM reprocessed); via Microsoft Planetary Computer")):
        try:
            items = list(cat.search(collections=[coll], bbox=[w, s, e, n]).items())
            hrefs = [it.assets[asset].href for it in items if asset in it.assets]
            log(f"  {name}: {len(hrefs)} tiles")
            if not hrefs: continue
            srcs = [rasterio.open(h) for h in hrefs]
            if len(srcs) == 1:
                src = srcs[0]
                win = from_bounds(w, s, e, n, src.transform).round_offsets().round_lengths()
                arr = src.read(1, window=win).astype("<f4"); tr = src.window_transform(win); nod = src.nodata
            else:
                arr, tr = merge(srcs, bounds=(w, s, e, n)); arr = arr[0].astype("<f4"); nod = srcs[0].nodata
            if nod is not None: arr[arr == nod] = np.nan
            json.dump({"source": src_note, "transform": list(tr)[:6], "width": int(arr.shape[1]), "height": int(arr.shape[0]),
                       "data": base64.b64encode(arr.tobytes()).decode()}, open(f"courses/dem/{slug}-{name}.json", "w"))
            log(f"    {arr.shape[1]}x{arr.shape[0]} px, {np.nanmin(arr):.1f}-{np.nanmax(arr):.1f} m")
        except Exception as ex:
            log(f"  {name} failed:", repr(ex)); traceback.print_exc()


def main():
    want = [l.strip() for l in open("courses/heights-requests.txt") if l.strip() and not l.startswith("#")]
    try:
        from sliderule import sliderule as sr
        sr.init("slideruleearth.io", verbose=False)
        log("SlideRule", sr.get_version() if hasattr(sr, "get_version") else "")
    except Exception as e:
        log("SlideRule init failed:", repr(e))
    for slug in want:
        s, n, w, e, poly = region(slug)
        log(f"== {slug}  ({s:.4f},{w:.4f})-({n:.4f},{e:.4f})")
        models(slug, s, n, w, e)
        icesat2(slug, poly)
        gedi(slug, poly)


if __name__ == "__main__":
    main()
