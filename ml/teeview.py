"""Tee view: what a hole looks like standing on the tee, rendered from satellite imagery and terrain heights.

There is no photo of most tees, so we build the scene: satellite image draped over a terrain model, tree canopies
found in the image raised into 3D crowns, then a perspective render from the tee (voxel-space ray casting,
front to back, one ray per screen column). Yardage-book overlays on top: carry lines on the line of play,
bunker carries and the hole number.

Request file courses/teeview-requests.txt:  slug | hole [| eye height m]
Output: courses/teeview/<slug>-<hole>.jpg (eye level) and <slug>-<hole>-high.jpg (raised view).
"""
import io, json, math, os, sys, time
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from scipy import ndimage
sys.path.insert(0, os.path.dirname(__file__))
import automap_lib as A

TERRARIUM = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
_rng = np.random.default_rng(7)
GRAIN = ndimage.gaussian_filter(_rng.random((512, 512)), 0.7, mode="wrap")
GRAIN = (GRAIN - GRAIN.min()) / (GRAIN.max() - GRAIN.min())
RING = {200: (40, 110, 220), 225: (60, 160, 80), 250: (215, 50, 40), 275: (230, 175, 30), 300: (140, 80, 190)}


# ---------------------------------------------------------------- scene
def terrain(m, log=print, z=15):
    """terrain heights (metres) on the mosaic grid, from the open Terrarium tiles (SRTM / national DEMs)"""
    W, H = m.img.size
    lat0, lon0 = m.px2ll(0, 0); lat1, lon1 = m.px2ll(W, H)
    X0, Y0 = A.ll2world(lat0, lon0, z); X1, Y1 = A.ll2world(lat1, lon1, z)
    tx0, ty0, tx1, ty1 = int(X0 // 256), int(Y0 // 256), int(X1 // 256), int(Y1 // 256)
    dem = np.zeros(((ty1 - ty0 + 1) * 256, (tx1 - tx0 + 1) * 256), np.float32)
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            a = np.asarray(Image.open(io.BytesIO(A.http(TERRARIUM.format(z=z, x=tx, y=ty), timeout=30))).convert("RGB"), np.float32)
            dem[(ty - ty0) * 256:(ty - ty0 + 1) * 256, (tx - tx0) * 256:(tx - tx0 + 1) * 256] = a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768
    # sample onto the mosaic grid
    s = 2 ** (m.z - z)
    gx = (m.tx0 * 256 + np.arange(W) + .5) / s - tx0 * 256 - .5
    gy = (m.ty0 * 256 + np.arange(H) + .5) / s - ty0 * 256 - .5
    GY, GX = np.meshgrid(gy, gx, indexing="ij")
    out = ndimage.map_coordinates(ndimage.gaussian_filter(dem, 1.2), [GY, GX], order=1, mode="nearest")
    log(f"  terrain {out.min():.0f}-{out.max():.0f} m")
    return out.astype(np.float32)


def canopy(img, mpp, not_tree=None):
    """tree crowns: dark, green-leaning, textured pixels; returns crown height (m) per pixel"""
    a = np.asarray(img.convert("RGB"), np.float32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    lum = .3 * r + .59 * g + .11 * b
    tex = np.sqrt(np.maximum(ndimage.uniform_filter(lum ** 2, 7) - ndimage.uniform_filter(lum, 7) ** 2, 0))
    dark = ndimage.uniform_filter(lum, 5) < 92
    greenish = (g >= r * 0.92) & (g >= b * 0.95)
    t = dark & greenish & (tex > 9)
    t = ndimage.binary_opening(ndimage.binary_closing(t, iterations=2), iterations=2)
    if not_tree is not None: t &= ~not_tree
    lab, n = ndimage.label(t)
    sizes = ndimage.sum(t, lab, range(1, n + 1)) * mpp * mpp
    t &= np.isin(lab, np.nonzero(sizes >= 12)[0] + 1)
    d = ndimage.distance_transform_edt(t) * mpp
    # rounded crowns, made lumpy by the light and shade inside the canopy so trees read as individual trees
    detail = ndimage.gaussian_filter(lum, 1.2 / mpp) - ndimage.gaussian_filter(lum, 4 / mpp)
    lump = np.clip(1 + detail / 26.0, 0.72, 1.22)
    # steep sides (full height within ~3 m of the canopy edge), lumpy rounded tops ~11-15 m
    edge = np.sqrt(np.clip(d / 3.0, 0, 1))
    h = np.where(t, 12.5 * edge * lump, 0).astype(np.float32)
    return ndimage.gaussian_filter(h, 0.5 / mpp)


def hillshade(H, mpp, az=20, alt=42):
    gy, gx = np.gradient(H, mpp)
    az, alt = math.radians(az), math.radians(alt)
    sx, sy, sz = math.sin(az) * math.cos(alt), -math.cos(az) * math.cos(alt), math.sin(alt)   # sun from the north (southern hemisphere)
    n = np.sqrt(gx ** 2 + gy ** 2 + 1)
    return np.clip((-gx * sx - gy * sy + sz) / n, 0, 1)


# ---------------------------------------------------------------- render
def render(tex, H, mpp, cam, look, eye=1.8, W=1800, Hpx=640, hfov=56.0, maxd=700.0, pitch=-1.2, sun=None, ground=None):
    """cam, look: mosaic pixel coords. Returns RGB image and a projector for overlays."""
    th, tw = H.shape
    f = (W / 2) / math.tan(math.radians(hfov / 2))
    fwd = np.array([look[0] - cam[0], look[1] - cam[1]], np.float64); fwd /= np.linalg.norm(fwd)
    right = np.array([-fwd[1], fwd[0]])          # image x grows to the right when y points down
    ch = ndimage.map_coordinates(H, [[cam[1]], [cam[0]]], order=1)[0] + eye
    horizon = Hpx * 0.40 + f * math.tan(math.radians(pitch))   # looking down (pitch < 0) lifts the horizon
    # depth steps: fine near the tee, coarser far away
    zs = [2.5]
    while zs[-1] < maxd: zs.append(zs[-1] + 0.12 + zs[-1] * 0.0035)
    zs = np.array(zs)
    u = (np.arange(W) + .5 - W / 2) / f
    Z = zs[:, None]
    px = cam[0] + (fwd[0] * Z + right[0] * Z * u[None]) / mpp
    py = cam[1] + (fwd[1] * Z + right[1] * Z * u[None]) / mpp
    inside = (px >= 0) & (px < tw - 1) & (py >= 0) & (py < th - 1)
    pxc, pyc = np.clip(px, 0, tw - 1.001), np.clip(py, 0, th - 1.001)
    h = ndimage.map_coordinates(H, [pyc, pxc], order=1)
    ys = horizon + f * (ch - h) / Z                       # screen row of each sample's top
    if ground is not None:   # where the ground is under each sample, for shading tree walls
        g0 = np.where(inside, ndimage.map_coordinates(ground, [pyc, pxc], order=1), h)
        yg = horizon + f * (ch - g0) / Z
    else:
        yg = ys
    # colour of each sample
    col = np.stack([ndimage.map_coordinates(tex[..., k], [pyc, pxc], order=1) for k in range(3)], -1)
    # fine grass grain close to the camera (the photo is only ~0.3 m per pixel)
    gx = (px * mpp * 9).astype(np.int64) % GRAIN.shape[1]; gy = (py * mpp * 9).astype(np.int64) % GRAIN.shape[0]
    near = np.clip(1 - Z / 120.0, 0, 1)
    col *= (1 + (GRAIN[gy, gx] - 0.5) * 0.22 * near)[..., None]
    if sun is not None: col *= (0.62 + 0.55 * ndimage.map_coordinates(sun, [pyc, pxc], order=1))[..., None]
    fog = (1 - np.exp(-Z / 1500.0))[..., None]
    haze = np.array([196, 210, 222], np.float32)
    col = col * (1 - fog) + haze * fog
    col = np.where(inside[..., None], col, haze)
    # front-to-back: a row is drawn by the first (nearest) sample whose top reaches it
    run = np.minimum.accumulate(ys, axis=0)
    out = np.zeros((Hpx, W, 3), np.float32)
    wall_mask = np.zeros((Hpx, W), bool)
    rows = np.arange(Hpx) + .5
    sky_top, sky_hor = np.array([104, 148, 196], np.float32), np.array([214, 226, 236], np.float32)
    for c in range(W):
        m = -run[:, c]                                    # increasing
        k = np.searchsorted(m, -rows, side="left")        # first step with run <= row
        k = np.clip(k, 0, len(zs) - 1)
        hit = run[k, c] <= rows
        cc = col[k, c]
        span = yg[k, c] - ys[k, c]
        wall = (span > 1.5) & hit                         # this row is the side of a tree, not its top
        rel = np.clip((rows - ys[k, c]) / np.maximum(span, 1), 0, 1)
        shade = np.where(wall, 0.95 - 0.5 * rel, 1.0)
        cc = cc * shade[:, None]
        out[:, c] = np.where(hit[:, None], cc, 0)
        wall_mask[:, c] = wall
        t = np.clip(rows / max(horizon, 1), 0, 1)[:, None]
        out[~hit, c] = (sky_top * (1 - t) + sky_hor * t)[~hit]
    # leafy texture on tree sides
    leaf = ndimage.gaussian_filter(np.random.default_rng(3).random((Hpx, W)), 1.1)
    leaf = (leaf - leaf.mean()) / (leaf.std() + 1e-6)
    out = np.where(wall_mask[..., None], out * (1 + 0.16 * leaf)[..., None], out)
    img = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))

    def project(p, height=0.0):
        """mosaic px (+ height above ground) -> screen (x, y, depth, visible) or None"""
        d = np.array([p[0] - cam[0], p[1] - cam[1]]) * mpp
        z = d @ fwd; x = d @ right
        if z < 3: return None
        g = ndimage.map_coordinates(H, [[p[1]], [p[0]]], order=1)[0]
        sx, sy = W / 2 + f * x / z, horizon + f * (ch - g - height) / z
        c = int(np.clip(sx, 0, W - 1)); k = int(np.searchsorted(zs, z)) - 3
        visible = k <= 0 or sy <= run[k, c] + 3          # nothing nearer rises above it
        return sx, sy, z, bool(visible)
    return img, project


# ---------------------------------------------------------------- overlays
def font(size, bold=True):
    for p in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]:
        if os.path.exists(p): return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def dashed(d, x, y0, y1, fill, width=3, dash=10, gap=7):
    y = y0
    while y < y1:
        d.line([(x, y), (x, min(y + dash, y1))], fill=fill, width=width); y += dash + gap


def along(line, s):
    acc = 0
    for a, b in zip(line[:-1], line[1:]):
        L = math.dist(a, b)
        if acc + L >= s and L > 0:
            t = (s - acc) / L; return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        acc += L
    return line[-1]


def overlays(img, project, line_px, mpp, bunkers_px, hole, par, length_m, units="m"):
    d = ImageDraw.Draw(img, "RGBA"); W, Hh = img.size
    k = 1.0936133 if units == "yd" else 1.0
    L = sum(math.dist(a, b) for a, b in zip(line_px[:-1], line_px[1:])) * mpp
    # carry marks on the line of play: a dot on the ground, a dashed post, the number stacked above
    marks = []
    for dist, colr in RING.items():
        if dist > L - 20: continue
        p = project(along(line_px, dist / mpp))
        if p: marks.append((dist, colr, p))
    top = None
    for i, (dist, colr, (x, y, z, vis)) in enumerate(marks):
        ty = y - 70 if top is None else min(y - 70, top - 34)
        top = ty
        side = -1 if i % 2 else 1
        lx = x + side * 26
        dashed(d, x, ty + 14, y, (255, 255, 255, 230), width=2, dash=7, gap=5)
        d.line([(x, ty + 14), (lx, ty + 14)], fill=(255, 255, 255, 230), width=2)
        if vis: d.ellipse((x - 7, y - 7, x + 7, y + 7), fill=colr + (255,), outline=(255, 255, 255, 255), width=2)
        else: d.ellipse((x - 7, y - 7, x + 7, y + 7), outline=colr + (255,), width=3)   # over the crest: blind
        lab = str(round(dist * k)) + ("" if vis else " blind"); fz = font(22)
        tw = d.textlength(lab, font=fz); bx = lx if side > 0 else lx - tw - 14
        d.rounded_rectangle((bx, ty, bx + tw + 14, ty + 28), 6, fill=colr + (235,))
        d.text((bx + 7, ty + 2), lab, fill=(255, 255, 255), font=fz)
    # bunker carries
    for poly in bunkers_px:
        best = None
        for q in poly:
            # carry = distance from the tee along the line direction
            s = math.dist(line_px[0], q) * mpp
            if best is None or s > best[0]: best = (s, q)
        near = min(math.dist(along(line_px, t / mpp), best[1]) * mpp for t in range(0, int(L), 10))
        if not (150 <= best[0] <= 340 and near <= 45): continue
        c = (sum(q[0] for q in poly) / len(poly), sum(q[1] for q in poly) / len(poly))
        p = project(c)
        if not p: continue
        x, y, z, vis = p
        dashed(d, x, y - 60, y - 6, (255, 255, 255, 210), width=2, dash=6, gap=5)
        lab = f"Trap carry {round(best[0] * k)}"; fz = font(19)
        tw = d.textlength(lab, font=fz)
        d.text((x - tw / 2, y - 88), lab, fill=(255, 255, 255), font=fz, stroke_width=3, stroke_fill=(0, 0, 0, 160))
    # hole badge, like the book
    R = 38; cx, cy = W - 30 - R, Hh - 26 - R
    d.ellipse((cx - R - 3, cy - R - 3, cx + R + 3, cy + R + 3), fill=(255, 255, 255, 255))
    d.ellipse((cx - R, cy - R, cx + R, cy + R), fill=(200, 160, 60, 255))
    fz = font(40); t = str(hole); tw = d.textlength(t, font=fz)
    d.text((cx - tw / 2, cy - 25), t, fill=(255, 255, 255), font=fz)
    d.text((24, Hh - 40), f"Par {par} · {round(length_m * k)} {'yds' if units == 'yd' else 'm'} · rendered from satellite imagery and terrain data",
           fill=(255, 255, 255), font=font(17, False), stroke_width=2, stroke_fill=(0, 0, 0, 140))
    return img


# ---------------------------------------------------------------- driver
def view(slug, hole, eye=1.8, z=19, model_mask=True, log=print, out_dir="courses/teeview", local_mosaic=None, local_terrain=None):
    e = json.load(open(f"courses/edits/{slug}.json"))
    h = e["holes"][str(hole)]
    line = h["line"]
    lats = [p[0] for p in line]; lons = [p[1] for p in line]
    # area: the hole plus room to the sides and beyond the green
    pad = 0.0022
    s, n, w, ee = min(lats) - pad, max(lats) + pad, min(lons) - pad * 1.1, max(lons) + pad * 1.1
    m = local_mosaic or A.fetch_mosaic(s, w, n, ee, z=z, pad_m=80, log=log)
    mpp = m.mpp
    Hter = local_terrain(m) if local_terrain else terrain(m, log=log)
    # shapes the model drew (not trees) from the course edits
    W, Hh = m.img.size
    notree = Image.new("L", (W, Hh), 0); dn = ImageDraw.Draw(notree)
    bunkers = []
    for sh in e.get("shapes", []):
        poly = [m.ll2px(*p) for p in sh["coords"]]
        if sh["kind"] in ("fairway", "green", "bunker", "water", "tee"): dn.polygon(poly, fill=1)
        if sh["kind"] == "bunker": bunkers.append(poly)
    notree = np.asarray(notree).astype(bool)
    notree = ndimage.binary_dilation(notree, iterations=int(3 / mpp))
    trees = canopy(m.img, mpp, notree)
    line_px = [m.ll2px(*p) for p in line]
    # tees are open ground: keep the tee and the first stretch of the line of play clear of canopy
    yy, xx = np.mgrid[0:Hh, 0:W]
    clear = np.hypot(xx - line_px[0][0], yy - line_px[0][1]) * mpp < 28
    for t in range(0, 90, 5):
        q = along(line_px, t / mpp); clear |= np.hypot(xx - q[0], yy - q[1]) * mpp < 14
    trees[clear] = 0
    Htot = Hter + trees
    tex = np.asarray(m.img.convert("RGB"), np.float32)
    tex = np.asarray(Image.fromarray(tex.astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.6)), np.float32)
    sun = hillshade(ndimage.gaussian_filter(Htot, 0.6), mpp)
    tee = line_px[0]
    # look at the point 230 m down the line (where the drive lands), like a photographer on the tee would
    look = along(line_px, min(230, sum(math.dist(a, b) for a, b in zip(line_px[:-1], line_px[1:])) * mpp * 0.6) / mpp)
    # stand a couple of metres behind the tee marker
    v = np.array(look) - np.array(tee); v /= np.linalg.norm(v)
    cam = (tee[0] - v[0] * 4 / mpp, tee[1] - v[1] * 4 / mpp)
    os.makedirs(out_dir, exist_ok=True)
    outs = []
    for tag, e_h, back, pitch in (("", eye, 4, -1.0), ("-high", 14.0, 30, -6.0)):
        c = (tee[0] - v[0] * back / mpp, tee[1] - v[1] * back / mpp)
        t0 = time.time()
        img, proj = render(tex, Htot, mpp, c, look, eye=e_h, pitch=pitch, sun=sun, ground=Hter)
        img = overlays(img, proj, line_px, mpp, bunkers, hole, h.get("par", 4), h.get("dist", 0))
        path = f"{out_dir}/{slug}-{hole}{tag}.jpg"; img.save(path, quality=88); outs.append(path)
        log(f"  rendered {path} in {time.time() - t0:.0f}s")
    return outs


def main():
    req = "courses/teeview-requests.txt"
    if not os.path.exists(req): return
    for l in open(req):
        l = l.strip()
        if not l or l.startswith("#"): continue
        p = [x.strip() for x in l.split("|")]
        try: view(p[0], int(p[1]), eye=float(p[2]) if len(p) > 2 and p[2] else 1.8)
        except Exception as ex:
            import traceback; traceback.print_exc(); print("FAILED", l, ex)


if __name__ == "__main__":
    main()
