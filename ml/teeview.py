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
CLS_ROUGH, CLS_SHORT, CLS_TREE, CLS_SAND, CLS_WATER = 0, 1, 2, 3, 4
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


def domes(tree_list, m, mpp):
    """tree crowns measured from the imagery ([lat, lon, crown radius m]) raised as rounded crowns on a skirt,
    so each tree reads as a tree from the tee instead of a spike"""
    W, H = m.img.size
    h = np.zeros((H, W), np.float32)
    for lat, lon, r in tree_list:
        x, y = m.ll2px(lat, lon)
        R = r / mpp
        if x < -R or y < -R or x > W + R or y > H + R: continue
        top = float(np.clip(2.1 * r + 3.0, 6.0, 19.0))
        x0, x1 = int(max(0, x - R - 1)), int(min(W, x + R + 2)); y0, y1 = int(max(0, y - R - 1)), int(min(H, y + R + 2))
        if x1 <= x0 or y1 <= y0: continue
        yy, xx = np.mgrid[y0:y1, x0:x1]
        d = np.hypot(xx - x, yy - y) / R
        cap = np.where(d < 1, top * (0.42 + 0.58 * np.sqrt(np.clip(1 - d * d, 0, 1))), 0)
        h[y0:y1, x0:x1] = np.maximum(h[y0:y1, x0:x1], cap)
    return ndimage.gaussian_filter(h, 0.35 / mpp)


def hillshade(H, mpp, az=20, alt=42):
    gy, gx = np.gradient(H, mpp)
    az, alt = math.radians(az), math.radians(alt)
    sx, sy, sz = math.sin(az) * math.cos(alt), -math.cos(az) * math.cos(alt), math.sin(alt)   # sun from the north (southern hemisphere)
    n = np.sqrt(gx ** 2 + gy ** 2 + 1)
    return np.clip((-gx * sx - gy * sy + sz) / n, 0, 1)


# ---------------------------------------------------------------- render
def render(tex, H, mpp, cam, look, eye=1.8, W=1800, Hpx=640, hfov=56.0, maxd=700.0, pitch=-1.2, sun=None, ground=None, cls=None):
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
    clsS = ndimage.map_coordinates(cls, [pyc, pxc], order=0) if cls is not None else None
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
    depth = np.full((Hpx, W), np.inf, np.float32)
    clsimg = np.full((Hpx, W), 255, np.uint8)            # 255 = sky
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
        depth[:, c] = np.where(hit, zs[k], np.inf)
        if clsS is not None: clsimg[:, c] = np.where(hit, np.where(wall, CLS_TREE, clsS[k, c]), 255)
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
    project.depth = depth
    project.cls = clsimg if cls is not None else None
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


def overlays(img, project, line_px, mpp, bunkers_px, hole, par, length_m, units="m", S=1.0,
             note="rendered from satellite imagery and terrain data"):
    """yardage-book marks on the view. S = output size / render size (project() works in render pixels)."""
    d = ImageDraw.Draw(img, "RGBA"); W, Hh = img.size
    k = 1.0936133 if units == "yd" else 1.0
    q = lambda v: v * S
    def P(p):
        r = project(p)
        return None if r is None else (r[0] * S, r[1] * S, r[2], r[3])
    L = sum(math.dist(a, b) for a, b in zip(line_px[:-1], line_px[1:])) * mpp
    boxes = []                       # drawn label boxes, so later labels can step around them
    def free(b):
        m = q(6)
        return not any(b[0] < o[2] + m and o[0] < b[2] + m and b[1] < o[3] + m and o[1] < b[3] + m for o in boxes)
    # carry marks on the line of play: a dot on the ground, a dashed post, the number stacked above
    marks = []
    for dist, colr in RING.items():
        if dist > L - 20: continue
        p = P(along(line_px, dist / mpp))
        if p: marks.append((dist, colr, p))
    top = None
    for i, (dist, colr, (x, y, z, vis)) in enumerate(marks):
        ty = y - q(70) if top is None else min(y - q(70), top - q(34))
        top = ty
        side = -1 if i % 2 else 1
        lx = x + side * q(26)
        dashed(d, x, ty + q(14), y, (255, 255, 255, 230), width=max(1, round(q(2))), dash=q(7), gap=q(5))
        d.line([(x, ty + q(14)), (lx, ty + q(14))], fill=(255, 255, 255, 230), width=max(1, round(q(2))))
        r = q(7)
        if vis: d.ellipse((x - r, y - r, x + r, y + r), fill=colr + (255,), outline=(255, 255, 255, 255), width=max(1, round(q(2))))
        else: d.ellipse((x - r, y - r, x + r, y + r), outline=colr + (255,), width=max(1, round(q(3))))   # over the crest: blind
        lab = str(round(dist * k)) + ("" if vis else " blind"); fz = font(round(q(22)))
        tw = d.textlength(lab, font=fz); bx = lx if side > 0 else lx - tw - q(14)
        box = (bx, ty, bx + tw + q(14), ty + q(28))
        d.rounded_rectangle(box, round(q(6)), fill=colr + (235,)); boxes.append(box)
        d.text((bx + q(7), ty + q(2)), lab, fill=(255, 255, 255), font=fz)
    # bunker carries
    for poly in bunkers_px:
        best = None
        for pt in poly:
            sd = math.dist(line_px[0], pt) * mpp
            if best is None or sd > best[0]: best = (sd, pt)
        near = min(math.dist(along(line_px, t / mpp), best[1]) * mpp for t in range(0, int(L), 10))
        if not (150 <= best[0] <= 340 and near <= 45): continue
        c = (sum(pt[0] for pt in poly) / len(poly), sum(pt[1] for pt in poly) / len(poly))
        p = P(c)
        if not p: continue
        x, y, z, vis = p
        lab = f"Trap carry {round(best[0] * k)}"; fz = font(round(q(19)))
        tw = d.textlength(lab, font=fz)
        ly = y - q(88)
        for _ in range(12):
            if free((x - tw / 2 - q(4), ly - q(2), x + tw / 2 + q(4), ly + q(24))): break
            ly += q(26)
        boxes.append((x - tw / 2 - q(4), ly - q(2), x + tw / 2 + q(4), ly + q(24)))
        dashed(d, x, min(ly + q(26), y - q(6)), y - q(6), (255, 255, 255, 210), width=max(1, round(q(2))), dash=q(6), gap=q(5))
        d.text((x - tw / 2, ly), lab, fill=(255, 255, 255), font=fz, stroke_width=round(q(3)), stroke_fill=(0, 0, 0, 160))
    # hole badge, like the book
    R = q(38); cx, cy = W - q(30) - R, Hh - q(26) - R
    d.ellipse((cx - R - q(3), cy - R - q(3), cx + R + q(3), cy + R + q(3)), fill=(255, 255, 255, 255))
    d.ellipse((cx - R, cy - R, cx + R, cy + R), fill=(200, 160, 60, 255))
    fz = font(round(q(40))); t = str(hole); tw = d.textlength(t, font=fz)
    d.text((cx - tw / 2, cy - q(25)), t, fill=(255, 255, 255), font=fz)
    d.text((q(24), Hh - q(40)), f"Par {par} · {round(length_m * k)} {'yds' if units == 'yd' else 'm'}" + (f" · {note}" if note else ""),
           fill=(255, 255, 255), font=font(round(q(17)), False), stroke_width=round(q(2)), stroke_fill=(0, 0, 0, 140))
    return img


# ---------------------------------------------------------------- driver
def scene(slug, hole, z=19, log=print, local_mosaic=None, local_terrain=None):
    """everything needed to render a hole: textures, heights, the line of play, bunkers, camera direction"""
    e = json.load(open(f"courses/edits/{slug}.json"))
    h = e["holes"][str(hole)]
    line = h["line"]
    lats = [p[0] for p in line]; lons = [p[1] for p in line]
    pad = 0.0022   # the hole plus room to the sides and beyond the green
    s, n, w, ee = min(lats) - pad, max(lats) + pad, min(lons) - pad * 1.1, max(lons) + pad * 1.1
    m = local_mosaic or A.fetch_mosaic(s, w, n, ee, z=z, pad_m=80, log=log)
    mpp = m.mpp
    Hter = local_terrain(m) if local_terrain else terrain(m, log=log)
    W, Hh = m.img.size
    notree = Image.new("L", (W, Hh), 0); dn = ImageDraw.Draw(notree)
    clsim = Image.new("L", (W, Hh), CLS_ROUGH); dc = ImageDraw.Draw(clsim)
    bunkers = []
    order = {"fairway": CLS_SHORT, "tee": CLS_SHORT, "green": CLS_SHORT, "water": CLS_WATER, "bunker": CLS_SAND}
    for kind in ("fairway", "tee", "green", "water", "bunker"):
        for sh in e.get("shapes", []):
            if sh["kind"] != kind: continue
            poly = [m.ll2px(*p) for p in sh["coords"]]
            dn.polygon(poly, fill=1); dc.polygon(poly, fill=order[kind])
            if kind == "bunker": bunkers.append(poly)
    notree = ndimage.binary_dilation(np.asarray(notree).astype(bool), iterations=int(3 / mpp))
    trees = domes(e["trees"], m, mpp) if e.get("trees") else canopy(m.img, mpp, notree)
    line_px = [m.ll2px(*p) for p in line]
    # tees are open ground: keep the tee and the first stretch of the line of play clear of canopy
    yy, xx = np.mgrid[0:Hh, 0:W]
    clear = np.hypot(xx - line_px[0][0], yy - line_px[0][1]) * mpp < 40
    for t in range(0, 90, 5):
        qq = along(line_px, t / mpp); clear |= np.hypot(xx - qq[0], yy - qq[1]) * mpp < 14
    trees[clear] = 0
    Htot = Hter + trees
    cls = np.asarray(clsim).copy(); cls[trees > 1.5] = CLS_TREE
    tex = np.asarray(m.img.convert("RGB").filter(ImageFilter.GaussianBlur(0.6)), np.float32)
    sun = hillshade(ndimage.gaussian_filter(Htot, 0.6), mpp)
    tee = line_px[0]
    L = sum(math.dist(a, b) for a, b in zip(line_px[:-1], line_px[1:])) * mpp
    # look at the landing area (~230 m), like a photographer on the tee would
    look = along(line_px, min(230, L * 0.6) / mpp)
    v = np.array(look) - np.array(tee); v /= np.linalg.norm(v)
    return {"edits": e, "hole": h, "m": m, "mpp": mpp, "Hter": Hter, "Htot": Htot, "tex": tex, "sun": sun,
            "line_px": line_px, "bunkers": bunkers, "tee": tee, "look": look, "v": v, "cls": cls}


def shot(S, eye=1.8, back=4.0, pitch=-1.0, maxd=700.0, W=1800, Hpx=640, hfov=56.0):
    c = (S["tee"][0] - S["v"][0] * back / S["mpp"], S["tee"][1] - S["v"][1] * back / S["mpp"])
    return render(S["tex"], S["Htot"], S["mpp"], c, S["look"], eye=eye, pitch=pitch, sun=S["sun"], ground=S["Hter"], maxd=maxd, W=W, Hpx=Hpx, hfov=hfov, cls=S.get("cls"))


def view(slug, hole, eye=1.8, z=19, log=print, out_dir="courses/teeview", local_mosaic=None, local_terrain=None):
    S = scene(slug, hole, z=z, log=log, local_mosaic=local_mosaic, local_terrain=local_terrain)
    h = S["hole"]
    os.makedirs(out_dir, exist_ok=True)
    outs = []
    for tag, e_h, back, pitch, maxd in (("", eye, 4, -1.0, 700.0), ("-high", 14.0, 30, -6.0, 1800.0)):
        t0 = time.time()
        img, proj = shot(S, eye=e_h, back=back, pitch=pitch, maxd=maxd)
        img = overlays(img, proj, S["line_px"], S["mpp"], S["bunkers"], hole, h.get("par", 4), h.get("dist", 0))
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
