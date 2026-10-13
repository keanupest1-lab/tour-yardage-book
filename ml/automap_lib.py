"""Shared pieces of the automatic course mapper (used by eval_routing.py and automap.py).

course area -> satellite mosaic -> AI shape mask -> greens / fairways / tees -> routing -> hole lines (lat/lon)
"""
import io, json, math, os, time, urllib.parse, urllib.request
import numpy as np
from PIL import Image
from scipy import ndimage

UA = {"User-Agent": "TourYardageBooks/0.6 (+https://github.com/keanupest1-lab/tour-yardage-book)"}
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
TILE = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
NAMES = ["background", "fairway", "green", "tee", "bunker", "water"]
PAL = np.array([[0, 0, 0], [60, 170, 80], [140, 255, 140], [255, 255, 255], [240, 210, 130], [70, 140, 255]], np.uint8)


def http(url, data=None, timeout=60):
    req = urllib.request.Request(url, data=data, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def overpass(q, tries=8, log=print):
    for a in range(tries):
        ep = OVERPASS[a % len(OVERPASS)]
        try:
            raw = http(ep, ("data=" + urllib.parse.quote(q)).encode(), timeout=200)
            if raw.startswith(b"{"):
                j = json.loads(raw)
                if "error" not in j.get("remark", "").lower(): return j
            log(f"  overpass {ep.split('/')[2]}: bad answer")
        except Exception as e:
            log(f"  overpass {ep.split('/')[2]}: {e}")
        time.sleep(12)
    raise RuntimeError("overpass failed")


# ---------- Web Mercator ----------
def ll2world(lat, lon, z):
    n = 2 ** z * 256
    r = math.radians(lat)
    return (lon + 180) / 360 * n, (1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * n


def world2ll(x, y, z):
    n = 2 ** z * 256
    lon = x / n * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lat, lon


class Mosaic:
    """a stitched block of z-level tiles; pixel (0,0) is the top-left of tile (tx0, ty0)"""
    def __init__(self, img, z, tx0, ty0):
        self.img, self.z, self.tx0, self.ty0 = img, z, tx0, ty0

    @property
    def mpp(self):  # metres per pixel at the mosaic centre
        lat, _ = self.px2ll(self.img.width / 2, self.img.height / 2)
        return 156543.03392 * math.cos(math.radians(lat)) / 2 ** self.z

    def px2ll(self, x, y):
        return world2ll(self.tx0 * 256 + x, self.ty0 * 256 + y, self.z)

    def ll2px(self, lat, lon):
        X, Y = ll2world(lat, lon, self.z)
        return X - self.tx0 * 256, Y - self.ty0 * 256


def fetch_mosaic(s, w, n, e, z=18, pad_m=60, log=print):
    dlat = pad_m / 111000; dlon = pad_m / (111000 * math.cos(math.radians((s + n) / 2)))
    x0, y0 = ll2world(n + dlat, w - dlon, z); x1, y1 = ll2world(s - dlat, e + dlon, z)
    tx0, ty0, tx1, ty1 = int(x0 // 256), int(y0 // 256), int(x1 // 256), int(y1 // 256)
    if (tx1 - tx0 + 1) * (ty1 - ty0 + 1) > 900: raise RuntimeError("area too big for one mosaic")
    img = Image.new("RGB", ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256), (40, 40, 40))
    missing = 0
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            for a in range(3):
                try:
                    img.paste(Image.open(io.BytesIO(http(TILE.format(z=z, x=tx, y=ty), timeout=30))).convert("RGB"),
                              ((tx - tx0) * 256, (ty - ty0) * 256)); break
                except Exception:
                    time.sleep(1 + 2 * a)
            else:
                missing += 1
    log(f"  mosaic {img.width}x{img.height}, {missing} tiles missing")
    return Mosaic(img, z, tx0, ty0)


# ---------- model ----------
_MEAN = np.array([0.485, 0.456, 0.406], np.float32); _STD = np.array([0.229, 0.224, 0.225], np.float32)


def load_model(path):
    import torch, segmentation_models_pytorch as smp
    m = smp.Unet("resnet34", encoder_weights=None, classes=6)
    sd = torch.load(path, map_location="cpu")
    m.load_state_dict({k: v.float() for k, v in sd.items()})
    m.eval()
    return m


def predict(model, img, tta=True):
    """sliding-window prediction over a PIL image -> label array (H, W) and per-class probabilities"""
    import torch
    im = np.asarray(img.convert("RGB"), np.float32) / 255.0
    H, W = im.shape[:2]; S = 512; step = 384
    prob = np.zeros((6, H, W), np.float32); cnt = np.zeros((H, W), np.float32)
    ys = list(range(0, max(1, H - S), step)) + [max(0, H - S)]
    xs = list(range(0, max(1, W - S), step)) + [max(0, W - S)]
    with torch.no_grad():
        for y in ys:
            for x in xs:
                t = im[y:y + S, x:x + S]
                ph, pw = S - t.shape[0], S - t.shape[1]
                if ph or pw: t = np.pad(t, ((0, ph), (0, pw), (0, 0)), mode="reflect")
                inp = torch.from_numpy(((t - _MEAN) / _STD).transpose(2, 0, 1).copy())[None]
                p = torch.softmax(model(inp), 1)[0]
                if tta:  # flip averaging: cleaner edges for little cost
                    p = p + torch.softmax(model(torch.flip(inp, [3])), 1)[0].flip([2])
                    p = p / 2
                p = p.numpy()[:, :S - ph, :S - pw]
                prob[:, y:y + S - ph, x:x + S - pw] += p; cnt[y:y + S - ph, x:x + S - pw] += 1
    prob /= np.maximum(cnt, 1)
    return prob.argmax(0).astype(np.uint8), prob


def overlay(img, lab, alpha=0.55):
    im = np.asarray(img.convert("RGB")).copy(); sel = lab > 0
    im[sel] = (im[sel] * (1 - alpha) + PAL[lab[sel]] * alpha).astype(np.uint8)
    return Image.fromarray(im)


# ---------- from mask to routing inputs ----------
def green_centres(lab, mpp, min_m2=150, max_m2=2200):
    L, n = ndimage.label(lab == 2)
    out = []
    for i in range(1, n + 1):
        a = (L == i).sum() * mpp * mpp
        if a < min_m2 or a > max_m2: continue
        cy, cx = ndimage.center_of_mass(L == i)
        out.append((cx, cy, a))
    return out


def downsample(mask, K, thr):
    H, W = mask.shape[0] // K * K, mask.shape[1] // K * K
    return mask[:H, :W].reshape(H // K, K, W // K, K).mean((1, 3)) > thr


def route_from_mask(lab, mpp, card, start_px=None, end_px=None, turn_px=None, beam=6000, K=4, greens_px=None, log=print,
                    guess_greens=True, guess_cost=40.0):
    """returns routing in pixel coordinates: [{hole, par, length, points:[(x,y)...], green_px}] plus diagnostics"""
    import route_solver2 as RS
    if greens_px is None: greens_px = [(x, y) for x, y, _ in green_centres(lab, mpp)]
    fw = ndimage.binary_opening(lab == 1, iterations=2)
    small = downsample(fw, K, 0.4)
    tees = downsample(lab == 3, K, 0.3)
    cell = K * mpp
    greens = [(x * mpp, y * mpp) for x, y in greens_px]
    to_m = lambda p: (p[0] * mpp, p[1] * mpp) if p is not None else None
    turn = {k: to_m(v) for k, v in turn_px.items()} if turn_px else None
    n_real = len(greens); gcost = [0.0] * n_real
    if guess_greens:   # fairway ends with no green found: offer an estimated green there, at a price
        guesses = RS.virtual_greens(RS.fairway_lines(small, cell), greens)
        greens = greens + guesses; gcost += [guess_cost] * len(guesses)
        greens_px = list(greens_px) + [(x / mpp, y / mpp) for x, y in guesses]
    if n_real < len(card): log(f"  only {n_real} greens found for {len(card)} holes")
    args = dict(start=to_m(start_px), end=to_m(end_px), tee_mask=tees, turn=turn, beam=beam, green_cost=gcost)
    res = RS.solve_dp(greens, small, cell, card, **args)
    if res is None or res["cost"] > 45 * len(card):
        # courses split by roads or housing have long walks between holes: allow them (at a price) and keep the better answer
        wide = RS.solve_dp(greens, small, cell, card, walk_max=700.0, **args)
        if wide is not None and (res is None or wide["cost"] < res["cost"]):
            log(f"  long walks allowed: cost {res and res['cost']} -> {wide['cost']}"); res = wide
    if res is None: return None
    holes = []
    for k, h in enumerate(res["holes"]):
        holes.append({"hole": k + 1, "par": card[k][0], "length": card[k][1], "green_px": greens_px[h["green"]], "green_guessed": h["green"] >= n_real,
                      "points": [(x / mpp, y / mpp) for x, y in h["points"]], "fairway": h["fairway"], "walk": h["walk"]})
    return {"holes": holes, "cost": res["cost"], "runner_up_gap": res["runner_up_gap"],
            "unused_fairways": res["unused_fairways"], "n_greens": n_real, "n_guessed": len(greens) - n_real,
            "n_fairways": len(res["fairways"])}
