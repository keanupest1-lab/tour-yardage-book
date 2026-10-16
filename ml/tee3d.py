"""The view from the tee, rendered in 3D with Blender (Cycles), like the tee photos in tour yardage books.

Built entirely from measured data for the hole:
  ground  : terrain heights (courses/terrain/<slug>.json) draped with the satellite photo (z19, ~0.27 m per pixel),
            bunkers dug in, greens and tees slightly raised, water filled
  trees   : every crown measured from the imagery (courses/edits/<slug>.json "trees", plus crowns found in the
            photo outside the course), raised as trees with trunks at their real size and position
  light   : physical sky and sun; aerial haze with distance
Yardage-book marks (carry distances on the line of play, bunker carries, hole badge) are drawn on top; a mark
behind a crest or a tree is drawn hollow and labelled "blind" (checked by ray casting in the scene).

Run with Blender as a Python module (pip install bpy==4.2.0, Python 3.11):
  python ml/tee3d.py <slug> <hole>[,<hole>...] [--quick]
Output: courses/teeview/<slug>-<hole>.jpg (clean view from the back tee) and <slug>-<hole>.json (where the
line of play falls on the photo, every 5 m from the tee, for the book's markers).
Request file for the GitHub job: courses/tee3d-requests.txt  "slug | 1-18" or "slug | 1,4,7".
"""
import base64, json, math, os, sys, time
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import automap_lib as A
import teeview as T

Image.MAX_IMAGE_PIXELS = None
CLS = {"rough": 0, "fairway": 1, "green": 2, "tee": 3, "bunker": 4, "water": 5}
W_OUT, H_OUT = 1800, 640


def log(*a): print(*a, flush=True)


# ------------------------------------------------------------------ data for one hole
def mosaic_for(slug, line_ll, pad_m):
    """the saved z19 photo of the course, cropped (tile-aligned) around the hole; fetched if not saved"""
    mp = f"courses/imagery/{slug}/meta.json"
    if os.path.exists(mp):
        meta = json.load(open(mp))["z19"]
        full = Image.open(f"courses/imagery/{slug}/z19.jpg")
        M = A.Mosaic(full, 19, meta["tile_x0"], meta["tile_y0"])
        P = [M.ll2px(*p) for p in line_ll]; pad = pad_m / M.mpp
        x0 = max(0, int((min(p[0] for p in P) - pad) // 256)); y0 = max(0, int((min(p[1] for p in P) - pad) // 256))
        x1 = min(full.width // 256, int((max(p[0] for p in P) + pad) // 256) + 1)
        y1 = min(full.height // 256, int((max(p[1] for p in P) + pad) // 256) + 1)
        crop = full.crop((x0 * 256, y0 * 256, x1 * 256, y1 * 256)).convert("RGB")
        return A.Mosaic(crop, 19, meta["tile_x0"] + x0, meta["tile_y0"] + y0)
    lats = [p[0] for p in line_ll]; lons = [p[1] for p in line_ll]
    return A.fetch_mosaic(min(lats), min(lons), max(lats), max(lons), z=19, pad_m=pad_m, log=log)


def terrain_on(m, slug):
    """terrain heights at every pixel of the mosaic (metres)"""
    tp = f"courses/terrain/{slug}.json"
    if not os.path.exists(tp):
        return T.terrain(m, log=log)
    j = json.load(open(tp))
    q = np.frombuffer(base64.b64decode(j["data"]), "<u2").reshape(j["ny"], j["nx"]).astype(np.float32) * j["scale"] + j["base"]
    W, H = m.img.size
    lons = np.array([m.px2ll(x + .5, 0)[1] for x in range(W)])
    lats = np.array([m.px2ll(0, y + .5)[0] for y in range(H)])
    fx = (lons - j["w"]) / j["dlon"]; fy = (j["n"] - lats) / j["dlat"]
    FY, FX = np.meshgrid(fy, fx, indexing="ij")
    return ndimage.map_coordinates(q, [FY, FX], order=1, mode="nearest").astype(np.float32)


def prepare(slug, hole, quick=False):
    e = json.load(open(f"courses/edits/{slug}.json"))
    h = e["holes"][str(hole)]
    line_ll = h["line"]
    m = mosaic_for(slug, line_ll, 330)
    mpp = m.mpp; W, H = m.img.size
    log(f"  photo {W}x{H} px at {mpp:.2f} m/px")
    line_px = [m.ll2px(*p) for p in line_ll]
    ter = terrain_on(m, slug)
    # class map (photo resolution)
    cls_im = Image.new("L", (W, H), CLS["rough"]); dc = ImageDraw.Draw(cls_im)
    polys = {k: [] for k in CLS}
    for kind in ("fairway", "tee", "green", "water", "bunker"):
        for s in e.get("shapes", []):
            if s["kind"] != kind: continue
            poly = [m.ll2px(*p) for p in s["coords"]]
            if all(not (0 <= x < W and 0 <= y < H) for x, y in poly): continue
            dc.polygon(poly, fill=CLS[kind]); polys[kind].append(poly)
    cls = np.asarray(cls_im)
    # shape the ground: bunkers dug in, water lowered, greens and tees a little proud
    def soft(mask, blur_m): return ndimage.gaussian_filter(mask.astype(np.float32), blur_m / mpp)
    ground = ter.copy()
    ground += 0.28 * soft(cls == CLS["green"], 2.5) + 0.45 * soft(cls == CLS["tee"], 1.2)
    ground -= 0.5 * np.clip(soft(cls == CLS["bunker"], 0.7) * 1.6, 0, 1)
    water = []
    lab, n = ndimage.label(cls == CLS["water"])
    for i in range(1, n + 1):
        wm = lab == i
        if wm.sum() * mpp * mpp < 30: continue
        level = float(np.percentile(ter[wm], 30)) - 0.25
        ground = np.where(ndimage.binary_dilation(wm, iterations=2), np.minimum(ground, level - 0.9), ground)
        water.append(level)
    ground = ndimage.gaussian_filter(ground, 0.35 / mpp)
    # trees: measured crowns on the course + crowns found in the photo around it
    trees = []
    have = np.zeros((H, W), bool)
    yy, xx = np.ogrid[0:H, 0:W]
    for lat, lon, r in e.get("trees", []):
        x, y = m.ll2px(lat, lon)
        if -20 < x < W + 20 and -20 < y < H + 20: trees.append((x, y, r))
    small = m.img.resize((W // 2, H // 2), Image.BILINEAR)
    for x, y, r in trees:
        R = r / mpp
        x0, x1, y0, y1 = int(max(0, x - R)), int(min(W, x + R + 1)), int(max(0, y - R)), int(min(H, y + R + 1))
        if x1 > x0 and y1 > y0: have[y0:y1, x0:x1] |= (xx[:, x0:x1] - x) ** 2 + (yy[y0:y1] - y) ** 2 <= R * R
    try:
        from build_verified import crowns
        keep = ~ndimage.binary_dilation(have[::2, ::2], iterations=3)
        not_tree = ndimage.binary_dilation(cls[::2, ::2] > 0, iterations=3)
        extra = crowns(small, mpp * 2, keep, not_tree)
        trees += [(x * 2, y * 2, r) for x, y, r in extra]
        log(f"  trees: {len(trees) - len(extra)} measured on the course, {len(extra)} more around it")
    except Exception as ex:
        log("  extra tree detection skipped:", ex)
    # tree colour from the photo
    arr = np.asarray(small, np.float32) / 255
    cols = []
    for x, y, r in trees:
        xi, yi = int(np.clip(x / 2, 0, W // 2 - 1)), int(np.clip(y / 2, 0, H // 2 - 1))
        R = max(1, int(r / (mpp * 2) * 0.6))
        c = arr[max(0, yi - R):yi + R + 1, max(0, xi - R):xi + R + 1].reshape(-1, 3).mean(0)
        cols.append(c.tolist())
    # the photo has the real trees' shadows baked into the grass; the render casts its own, so lift them out
    img = np.asarray(m.img, np.float32)
    lum = img @ np.array([.3, .59, .11], np.float32)
    grass = (cls != CLS["water"]) & (cls != CLS["bunker"])
    wsum = ndimage.gaussian_filter((grass & (lum > 60)).astype(np.float32), 8 / mpp) + 1e-3
    ref = np.stack([ndimage.gaussian_filter(img[..., k] * (grass & (lum > 60)), 8 / mpp) / wsum for k in range(3)], -1)
    rl = ref @ np.array([.3, .59, .11], np.float32)
    dark = np.clip((0.82 * rl - lum) / np.maximum(0.45 * rl, 1), 0, 1) * grass
    lifted = img * (1 - dark[..., None]) + (ref * (0.9 + 0.2 * (img / np.maximum(lum[..., None], 1) - 1).clip(-.5, .5))) * dark[..., None]
    m = A.Mosaic(Image.fromarray(np.clip(lifted, 0, 255).astype(np.uint8)), m.z, m.tx0, m.ty0)
    return {"e": e, "h": h, "m": m, "mpp": mpp, "line_px": line_px, "ground": ground, "ter": ter, "cls": cls,
            "trees": trees, "tree_cols": cols, "water_polys": polys["water"], "water_levels": water, "bunkers": polys["bunker"]}


# ------------------------------------------------------------------ Blender scene
def aim_dist(line_px, mpp):
    """how far down the hole the camera looks: the green on a short hole, else the landing area,
    or the corner of a dogleg if that comes first"""
    L = sum(math.dist(a, b) for a, b in zip(line_px, line_px[1:])) * mpp
    if L < 260: return L
    d = min(230.0, 0.6 * L)
    if len(line_px) > 2:
        bend = math.dist(line_px[0], line_px[1]) * mpp
        if 120 < bend < d: d = bend
    return d


def build_scene(D, workdir, quick=False):
    import bpy, bmesh
    from mathutils import Vector
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    m, mpp = D["m"], D["mpp"]; W, H = m.img.size
    tx, ty = D["line_px"][0]
    to_local = lambda px, py: ((px - tx) * mpp, -(py - ty) * mpp)
    G = D["ground"]
    gat = lambda px, py: float(ndimage.map_coordinates(G, [[np.clip(py, 0, H - 1)], [np.clip(px, 0, W - 1)]], order=1)[0])

    _L = sum(math.dist(a, b) for a, b in zip(D["line_px"], D["line_px"][1:])) * mpp
    _lk = T.along(D["line_px"], aim_dist(D["line_px"], mpp) / mpp)
    heading0 = math.atan2(-(_lk[1] - ty), _lk[0] - tx)
    # ---- ground mesh (1 m near the line of play is plenty with the photo on it)
    step_m = 1.6 if quick else 1.0
    sp = step_m / mpp
    nx, ny = int((W - 1) / sp) + 1, int((H - 1) / sp) + 1
    pxs = np.arange(nx) * sp; pys = np.arange(ny) * sp
    Hg = ndimage.map_coordinates(G, np.meshgrid(pys, pxs, indexing="ij"), order=1)
    X, Y = np.meshgrid((pxs - tx) * mpp, -(pys - ty) * mpp)
    co = np.stack([X, Y, Hg], -1).reshape(-1, 3).astype(np.float32)
    idx = np.arange(nx * ny).reshape(ny, nx)
    quads = np.stack([idx[:-1, :-1], idx[1:, :-1], idx[1:, 1:], idx[:-1, 1:]], -1).reshape(-1, 4)
    me = bpy.data.meshes.new("ground")
    me.vertices.add(len(co)); me.vertices.foreach_set("co", co.ravel())
    me.loops.add(quads.size); me.loops.foreach_set("vertex_index", quads.ravel().astype(np.int32))
    me.polygons.add(len(quads)); me.polygons.foreach_set("loop_start", np.arange(0, quads.size, 4, dtype=np.int32))
    me.update(calc_edges=True)
    uv = me.uv_layers.new(name="UVMap")
    U = (np.repeat(pxs[None], ny, 0) / W).ravel(); V = (1 - np.repeat(pys[:, None], nx, 1) / H).ravel()
    lv = quads.ravel()
    uv.data.foreach_set("uv", np.stack([U[lv], V[lv]], -1).ravel().astype(np.float32))
    me.polygons.foreach_set("use_smooth", np.ones(len(quads), bool))
    ground = bpy.data.objects.new("ground", me); sc.collection.objects.link(ground)

    # ---- ground material: the photo, with grass and sand detail close up
    photo_path = os.path.join(workdir, "photo.jpg"); m.img.save(photo_path, quality=94)
    cls_path = os.path.join(workdir, "cls.png"); Image.fromarray((D["cls"] * 40).astype(np.uint8)).save(cls_path)
    mat = bpy.data.materials.new("ground"); mat.use_nodes = True
    nt = mat.node_tree; N = nt.nodes; L = nt.links
    bsdf = N["Principled BSDF"]
    tex0 = N.new("ShaderNodeTexImage"); tex0.image = bpy.data.images.load(photo_path); tex0.interpolation = "Cubic"
    # close to the camera the photo's 0.27 m pixels show as blotches: use a softened copy there, the grain adds the grass
    blur_path = os.path.join(workdir, "photo_soft.jpg")
    Image.fromarray(np.clip(ndimage.gaussian_filter(np.asarray(m.img, np.float32), (2.2 / mpp, 2.2 / mpp, 0)), 0, 255).astype(np.uint8)).save(blur_path, quality=92)
    texb = N.new("ShaderNodeTexImage"); texb.image = bpy.data.images.load(blur_path); texb.interpolation = "Cubic"
    camd = N.new("ShaderNodeCameraData")
    near = N.new("ShaderNodeMapRange"); near.inputs["From Min"].default_value = 25; near.inputs["From Max"].default_value = 90
    near.inputs["To Min"].default_value = 1.0; near.inputs["To Max"].default_value = 0.0
    L.new(camd.outputs["View Distance"], near.inputs["Value"])
    tex = N.new("ShaderNodeMix"); tex.data_type = "RGBA"
    L.new(near.outputs["Result"], tex.inputs["Factor"]); L.new(tex0.outputs["Color"], tex.inputs[6]); L.new(texb.outputs["Color"], tex.inputs[7])
    ctex = N.new("ShaderNodeTexImage"); ctex.image = bpy.data.images.load(cls_path); ctex.interpolation = "Closest"
    ctex.image.colorspace_settings.name = "Non-Color"
    hsv = N.new("ShaderNodeHueSaturation"); hsv.inputs["Saturation"].default_value = 1.12; hsv.inputs["Value"].default_value = 0.92
    L.new(tex.outputs[2], hsv.inputs["Color"])
    tc = N.new("ShaderNodeTexCoord")
    fine = N.new("ShaderNodeTexNoise"); fine.inputs["Scale"].default_value = 3.0; fine.inputs["Detail"].default_value = 8
    L.new(tc.outputs["Object"], fine.inputs["Vector"])
    blade = N.new("ShaderNodeTexNoise"); blade.inputs["Scale"].default_value = 60.0; blade.inputs["Detail"].default_value = 4
    L.new(tc.outputs["Object"], blade.inputs["Vector"])
    vary = N.new("ShaderNodeMapRange"); vary.inputs["To Min"].default_value = 0.82; vary.inputs["To Max"].default_value = 1.12
    L.new(fine.outputs["Fac"], vary.inputs["Value"])
    mul = N.new("ShaderNodeMix"); mul.data_type = "RGBA"; mul.blend_type = "MULTIPLY"; mul.inputs["Factor"].default_value = 1.0
    L.new(hsv.outputs["Color"], mul.inputs[6]); L.new(vary.outputs["Result"], mul.inputs[7])
    # close-up grass: fine colour grain the photo can't show
    grain = N.new("ShaderNodeTexNoise"); grain.inputs["Scale"].default_value = 28.0; grain.inputs["Detail"].default_value = 3
    L.new(tc.outputs["Object"], grain.inputs["Vector"])
    gr = N.new("ShaderNodeMapRange"); gr.inputs["To Min"].default_value = 0.8; gr.inputs["To Max"].default_value = 1.18
    L.new(grain.outputs["Fac"], gr.inputs["Value"])
    mul2 = N.new("ShaderNodeMix"); mul2.data_type = "RGBA"; mul2.blend_type = "MULTIPLY"; mul2.inputs["Factor"].default_value = 1.0
    L.new(mul.outputs[2], mul2.inputs[6]); L.new(gr.outputs["Result"], mul2.inputs[7])
    # mowing stripes on fairways and greens, running down the hole
    mp_ = N.new("ShaderNodeMapping"); mp_.inputs["Rotation"].default_value = (0, 0, -heading0)
    L.new(tc.outputs["Object"], mp_.inputs["Vector"])
    wave = N.new("ShaderNodeTexWave"); wave.wave_type = "BANDS"; wave.bands_direction = "Y"
    wave.inputs["Scale"].default_value = 2 * math.pi / (10 * 9.0); wave.inputs["Distortion"].default_value = 0
    L.new(mp_.outputs["Vector"], wave.inputs["Vector"])
    stp = N.new("ShaderNodeMath"); stp.operation = "GREATER_THAN"; stp.inputs[1].default_value = 0.5
    L.new(wave.outputs["Fac"], stp.inputs[0])
    short = N.new("ShaderNodeMath"); short.operation = "LESS_THAN"; short.inputs[1].default_value = 0.4   # fairway 40/255, green 80/255
    sh2 = N.new("ShaderNodeMath"); sh2.operation = "GREATER_THAN"; sh2.inputs[1].default_value = 0.1
    L.new(ctex.outputs["Color"], short.inputs[0]); L.new(ctex.outputs["Color"], sh2.inputs[0])
    sh3 = N.new("ShaderNodeMath"); sh3.operation = "MULTIPLY"; L.new(short.outputs[0], sh3.inputs[0]); L.new(sh2.outputs[0], sh3.inputs[1])
    sh4 = N.new("ShaderNodeMath"); sh4.operation = "MULTIPLY"; L.new(sh3.outputs[0], sh4.inputs[0]); L.new(stp.outputs[0], sh4.inputs[1])
    sh5 = N.new("ShaderNodeMapRange"); sh5.inputs["To Min"].default_value = 1.0; sh5.inputs["To Max"].default_value = 1.16
    L.new(sh4.outputs[0], sh5.inputs["Value"])
    mul3 = N.new("ShaderNodeMix"); mul3.data_type = "RGBA"; mul3.blend_type = "MULTIPLY"; mul3.inputs["Factor"].default_value = 1.0
    L.new(mul2.outputs[2], mul3.inputs[6]); L.new(sh5.outputs["Result"], mul3.inputs[7])
    L.new(mul3.outputs[2], bsdf.inputs["Base Color"])
    # sand (class 4 -> 160/255) smoother, grass rougher
    sandsel = N.new("ShaderNodeMath"); sandsel.operation = "COMPARE"; sandsel.inputs[1].default_value = 160 / 255; sandsel.inputs[2].default_value = 0.05
    L.new(ctex.outputs["Color"], sandsel.inputs[0])
    bstr = N.new("ShaderNodeMapRange"); bstr.inputs["To Min"].default_value = 0.35; bstr.inputs["To Max"].default_value = 0.08
    L.new(sandsel.outputs[0], bstr.inputs["Value"])
    bump = N.new("ShaderNodeBump"); bump.inputs["Distance"].default_value = 0.02
    L.new(bstr.outputs["Result"], bump.inputs["Strength"]); L.new(blade.outputs["Fac"], bump.inputs["Height"])
    L.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    bsdf.inputs["Roughness"].default_value = 0.92
    bsdf.inputs["Specular IOR Level"].default_value = 0.25
    ground.data.materials.append(mat)

    # ---- water
    wmat = bpy.data.materials.new("water"); wmat.use_nodes = True
    wb = wmat.node_tree.nodes["Principled BSDF"]
    wb.inputs["Base Color"].default_value = (0.015, 0.035, 0.03, 1); wb.inputs["Roughness"].default_value = 0.04
    wb.inputs["IOR"].default_value = 1.33; wb.inputs["Specular IOR Level"].default_value = 0.7
    wn = wmat.node_tree.nodes.new("ShaderNodeTexNoise"); wn.inputs["Scale"].default_value = 2.5
    wbmp = wmat.node_tree.nodes.new("ShaderNodeBump"); wbmp.inputs["Strength"].default_value = 0.06
    wmat.node_tree.links.new(wn.outputs["Fac"], wbmp.inputs["Height"]); wmat.node_tree.links.new(wbmp.outputs["Normal"], wb.inputs["Normal"])
    for poly, level in zip([p for p in D["water_polys"]], D["water_levels"] + [None] * 99):
        if level is None or len(poly) < 3: continue
        bm = bmesh.new()
        vs = [bm.verts.new((*to_local(*p), level)) for p in poly]
        try: bm.faces.new(vs)
        except ValueError: bm.free(); continue
        wme = bpy.data.meshes.new("water"); bm.to_mesh(wme); bm.free()
        o = bpy.data.objects.new("water", wme); o.data.materials.append(wmat); sc.collection.objects.link(o)

    # ---- trees: crowns of clustered, roughened spheres on trunks, one mesh, colour per tree from the photo
    rng = np.random.default_rng(11)
    protos = {}
    for sub in (2, 3):
        bm = bmesh.new(); bmesh.ops.create_icosphere(bm, subdivisions=sub, radius=1.0)
        protos[sub] = (np.array([v.co[:] for v in bm.verts], np.float32), np.array([[v.index for v in f.verts] for f in bm.faces], np.int32)); bm.free()
    def lumpy(sv):
        """smooth leafy lumps on a sphere: a few random waves over the surface"""
        f = np.ones((len(sv), 1), np.float32)
        for amp, freq in ((0.13, 2.5), (0.08, 5.0), (0.045, 9.0)):
            a = rng.standard_normal(3).astype(np.float32); a /= np.linalg.norm(a)
            b = rng.standard_normal(3).astype(np.float32); b /= np.linalg.norm(b)
            f[:, 0] += amp * np.sin(sv @ a * freq + rng.uniform(0, 6.3)) * np.cos(sv @ b * freq * 0.7 + rng.uniform(0, 6.3))
        return f
    tv, tfc, tcol = [], [], []; base = 0
    trunk_v, trunk_f = [], []; tb = 0
    for (x, y, r), col in zip(D["trees"], D["tree_cols"]):
        lx, ly = to_local(x, y)
        d = math.hypot(lx, ly)
        if d < 14: continue                                  # nothing standing on the tee or in the photographer's face
        gz = gat(x, y)
        r = float(np.clip(r, 1.5, 11))
        top = float(np.clip(1.7 * r + 3.2, 6.0, 17.0))
        cz = gz + top - 0.9 * r
        k = 7 if d < 380 else 4
        cen = np.column_stack([rng.uniform(-.45, .45, k) * r, rng.uniform(-.45, .45, k) * r, rng.uniform(-.3, .35, k) * r])
        cen[0] = 0
        rad = rng.uniform(0.5, 0.68, k) * r; rad[0] = 0.78 * r
        c = np.clip((np.array(col) ** 2.2) * 1.7 * rng.uniform(0.85, 1.12), 0, 1)   # photo sRGB -> linear, out of shade
        sv, sf = protos[3 if d < 160 else 2]; nsv = len(sv)
        for j in range(k):
            jitter = lumpy(sv)
            v = sv * jitter * rad[j] * np.array([1, 1, 0.95], np.float32) + np.array([lx + cen[j, 0], ly + cen[j, 1], cz + cen[j, 2]], np.float32)
            tv.append(v); tfc.append(sf + base); base += nsv
            tcol.append(np.repeat((c * rng.uniform(0.88, 1.12, 3)).clip(0, 1)[None], nsv, 0))
        # trunk: six-sided post up into the crown
        tr = max(0.16, 0.065 * r); h0, h1 = gz - 0.3, cz - 0.1 * r
        ang = np.arange(6) * math.pi / 3
        ring = np.column_stack([lx + tr * np.cos(ang), ly + tr * np.sin(ang)])
        trunk_v.append(np.vstack([np.column_stack([ring, np.full(6, h0)]), np.column_stack([ring * [1, 1] * 1 + 0, np.full(6, h1)])]))
        trunk_f += [[tb + i, tb + (i + 1) % 6, tb + 6 + (i + 1) % 6, tb + 6 + i] for i in range(6)]; tb += 12
    if tv:
        V = np.vstack(tv).astype(np.float32); F = np.vstack(tfc); Cc = np.vstack(tcol).astype(np.float32)
        tm = bpy.data.meshes.new("trees")
        tm.vertices.add(len(V)); tm.vertices.foreach_set("co", V.ravel())
        tm.loops.add(F.size); tm.loops.foreach_set("vertex_index", F.ravel())
        tm.polygons.add(len(F)); tm.polygons.foreach_set("loop_start", np.arange(0, F.size, 3, dtype=np.int32))
        tm.update(calc_edges=True)
        tm.polygons.foreach_set("use_smooth", np.ones(len(F), bool))
        ca = tm.color_attributes.new("leaf", "FLOAT_COLOR", "POINT")
        ca.data.foreach_set("color", np.column_stack([Cc, np.ones(len(Cc), np.float32)]).ravel())
        to = bpy.data.objects.new("trees", tm); sc.collection.objects.link(to)
        lm = bpy.data.materials.new("leaves"); lm.use_nodes = True
        n2 = lm.node_tree.nodes; l2 = lm.node_tree.links; b2 = n2["Principled BSDF"]
        at = n2.new("ShaderNodeVertexColor"); at.layer_name = "leaf"
        # the photo shows crowns from above in shade and sun: lift them a little, add leaf clumps
        gam = n2.new("ShaderNodeGamma"); gam.inputs["Gamma"].default_value = 1.0
        l2.new(at.outputs["Color"], gam.inputs["Color"])
        lc = n2.new("ShaderNodeTexVoronoi"); lc.inputs["Scale"].default_value = 3.5
        tcl = n2.new("ShaderNodeTexCoord"); l2.new(tcl.outputs["Object"], lc.inputs["Vector"])
        mr = n2.new("ShaderNodeMapRange"); mr.inputs["To Min"].default_value = 0.6; mr.inputs["To Max"].default_value = 1.25
        l2.new(lc.outputs["Distance"], mr.inputs["Value"])
        mx = n2.new("ShaderNodeMix"); mx.data_type = "RGBA"; mx.blend_type = "MULTIPLY"; mx.inputs["Factor"].default_value = 1
        l2.new(gam.outputs["Color"], mx.inputs[6]); l2.new(mr.outputs["Result"], mx.inputs[7])
        l2.new(mx.outputs[2], b2.inputs["Base Color"])
        bp = n2.new("ShaderNodeBump"); bp.inputs["Strength"].default_value = 0.9
        nz = n2.new("ShaderNodeTexNoise"); nz.inputs["Scale"].default_value = 9; nz.inputs["Detail"].default_value = 6
        l2.new(tcl.outputs["Object"], nz.inputs["Vector"]); l2.new(nz.outputs["Fac"], bp.inputs["Height"]); l2.new(bp.outputs["Normal"], b2.inputs["Normal"])
        b2.inputs["Roughness"].default_value = 0.75
        b2.inputs["Subsurface Weight"].default_value = 0.15
        b2.inputs["Subsurface Radius"].default_value = (0.2, 0.5, 0.1)
        to.data.materials.append(lm)
    if trunk_v:
        tvv = np.vstack(trunk_v).astype(np.float32); tff = np.array(trunk_f, np.int32)
        km = bpy.data.meshes.new("trunks")
        km.vertices.add(len(tvv)); km.vertices.foreach_set("co", tvv.ravel())
        km.loops.add(tff.size); km.loops.foreach_set("vertex_index", tff.ravel())
        km.polygons.add(len(tff)); km.polygons.foreach_set("loop_start", np.arange(0, tff.size, 4, dtype=np.int32))
        km.update(calc_edges=True)
        ko = bpy.data.objects.new("trunks", km); sc.collection.objects.link(ko)
        bm2 = bpy.data.materials.new("bark"); bm2.use_nodes = True
        bm2.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.09, 0.075, 0.06, 1)
        ko.data.materials.append(bm2)
    log(f"  scene: ground {nx}x{ny}, {len(D['trees'])} trees, {len(D['water_levels'])} ponds")

    # ---- sky and sun (afternoon light from behind the shoulder, like a photographer would pick)
    line = [to_local(*p) for p in D["line_px"]]
    look = to_local(*T.along(D["line_px"], aim_dist(D["line_px"], mpp) / mpp))
    heading = math.atan2(look[1], look[0])
    world = bpy.data.worlds.new("sky"); sc.world = world; world.use_nodes = True
    wn_ = world.node_tree.nodes; sky = wn_.new("ShaderNodeTexSky"); sky.sky_type = "NISHITA"
    sun_el = math.radians(52); sun_az = heading + math.radians(180 + 35)
    sky.sun_elevation = sun_el; sky.sun_rotation = sun_az - math.pi / 2; sky.sun_disc = False
    sky.altitude = 1700; sky.air_density = 1.0; sky.dust_density = 0.7
    world.node_tree.links.new(sky.outputs["Color"], wn_["Background"].inputs["Color"])
    wn_["Background"].inputs["Strength"].default_value = 0.45
    sun = bpy.data.lights.new("sun", "SUN"); sun.energy = 4.2; sun.angle = math.radians(0.6); sun.color = (1.0, 0.96, 0.9)
    so = bpy.data.objects.new("sun", sun); sc.collection.objects.link(so)
    sd = Vector((math.cos(sun_el) * math.cos(sun_az), math.cos(sun_el) * math.sin(sun_az), math.sin(sun_el)))
    so.rotation_euler = (-sd).to_track_quat("-Z", "Y").to_euler()

    # ---- render settings
    sc.render.engine = "CYCLES"; sc.cycles.device = "CPU"
    sc.cycles.samples = 24 if quick else 48; sc.cycles.use_denoising = True
    sc.cycles.max_bounces = 4; sc.cycles.diffuse_bounces = 2; sc.cycles.glossy_bounces = 2; sc.cycles.transmission_bounces = 2
    sc.render.resolution_x, sc.render.resolution_y = W_OUT, H_OUT
    sc.render.resolution_percentage = 50 if quick else 100
    sc.view_settings.view_transform = "AgX"; sc.view_settings.look = "AgX - Medium High Contrast"
    sc.view_settings.exposure = 0.0
    sc.render.image_settings.file_format = "PNG"
    sc.view_layers[0].use_pass_z = True
    # aerial haze by distance (sky keeps its own colour)
    sc.use_nodes = True
    cn = sc.node_tree.nodes; cl = sc.node_tree.links
    for nd in list(cn): cn.remove(nd)
    rl = cn.new("CompositorNodeRLayers"); comp = cn.new("CompositorNodeComposite")
    m1 = cn.new("CompositorNodeMath"); m1.operation = "MULTIPLY"; m1.inputs[1].default_value = -1 / 3500.0
    m2 = cn.new("CompositorNodeMath"); m2.operation = "EXPONENT"
    m3 = cn.new("CompositorNodeMath"); m3.operation = "SUBTRACT"; m3.inputs[0].default_value = 1.0
    lt = cn.new("CompositorNodeMath"); lt.operation = "LESS_THAN"; lt.inputs[1].default_value = 20000
    m4 = cn.new("CompositorNodeMath"); m4.operation = "MULTIPLY"
    m5 = cn.new("CompositorNodeMath"); m5.operation = "MULTIPLY"; m5.inputs[1].default_value = 0.85
    mix = cn.new("CompositorNodeMixRGB"); mix.inputs[2].default_value = (0.74, 0.8, 0.86, 1)
    cl.new(rl.outputs["Depth"], m1.inputs[0]); cl.new(m1.outputs[0], m2.inputs[0]); cl.new(m2.outputs[0], m3.inputs[1])
    cl.new(rl.outputs["Depth"], lt.inputs[0]); cl.new(m3.outputs[0], m4.inputs[0]); cl.new(lt.outputs[0], m4.inputs[1])
    cl.new(m4.outputs[0], m5.inputs[0]); cl.new(m5.outputs[0], mix.inputs[0])
    cl.new(rl.outputs["Image"], mix.inputs[1]); cl.new(mix.outputs[0], comp.inputs["Image"])

    cam = bpy.data.cameras.new("cam"); co_ = bpy.data.objects.new("cam", cam); sc.collection.objects.link(co_); sc.camera = co_
    cam.sensor_fit = "HORIZONTAL"; cam.sensor_width = 36
    return {"bpy": bpy, "sc": sc, "cam": co_, "to_local": to_local, "gat": gat, "look": look}


def shoot(S, D, out_png, eye=1.65, back=3.0, lens=34.0, shift_y=-0.05):
    bpy = S["bpy"]; from mathutils import Vector
    sc, co = S["sc"], S["cam"]
    look = S["look"]; d = Vector((look[0], look[1], 0)); d.normalize()
    tx, ty = D["line_px"][0]
    cx, cy = -d.x * back, -d.y * back
    gz = S["gat"](tx + cx / D["mpp"], ty - cy / D["mpp"])
    cz = gz + eye
    lz = S["gat"](tx + look[0] / D["mpp"], ty - look[1] / D["mpp"])
    co.location = (cx, cy, cz)
    v = Vector((look[0] - cx, look[1] - cy, (lz + 1.0) - cz))
    co.rotation_euler = v.to_track_quat("-Z", "Y").to_euler()
    co.data.lens = lens; co.data.shift_y = shift_y
    co.data.dof.use_dof = True; co.data.dof.focus_distance = 140.0; co.data.dof.aperture_fstop = 11
    sc.render.filepath = out_png
    t0 = time.time(); bpy.ops.render.render(write_still=True); log(f"  rendered {os.path.basename(out_png)} in {time.time() - t0:.0f}s")
    # where the yardage marks land on screen, and whether the camera can see them
    from bpy_extras.object_utils import world_to_camera_view
    dg = bpy.context.evaluated_depsgraph_get()
    W, H = sc.render.resolution_x * sc.render.resolution_percentage / 100, sc.render.resolution_y * sc.render.resolution_percentage / 100
    def project(p):
        lx, ly = S["to_local"](*p)
        z = S["gat"](*p) + 1.0
        P = Vector((lx, ly, z)); u = world_to_camera_view(sc, co, P)
        if u.z < 3: return None
        o = co.location.copy(); dv = P - o; dist = dv.length; dv.normalize()
        P3 = Vector((lx, ly, z + 2.0)); dv = P3 - o; dist = dv.length; dv.normalize()
        hit, loc, _n, _i, ob, _m = sc.ray_cast(dg, o, dv, distance=dist)
        # only trees make a mark blind: the ~30 m terrain model includes some canopy height, so crests in it
        # are not reliable enough to call a shot blind
        vis = (not hit) or (loc - o).length >= dist - 4.0 or (ob is not None and ob.name == "ground")
        if os.environ.get("TEE3D_DEBUG"): log(f"    mark at {dist:.0f} m: hit={hit} {ob.name if ob else ''} at {(loc - o).length:.0f}")
        return u.x * W, (1 - u.y) * H, u.z, vis
    return project, W


def marks(D, project, scale):
    """where the line of play is on the photo, every 5 m of distance from the back tee (100-420 m), so the book
    can put its markers (and their dashed drop lines) on the photo for any distance and units"""
    line, mpp = D["line_px"], D["mpp"]
    tee = line[0]
    L = sum(math.dist(a, b) for a, b in zip(line, line[1:])) * mpp
    out, want = [], list(range(100, 425, 5))
    s = 0.0
    while want and s <= L:
        p = T.along(line, s / mpp)
        r = math.dist(p, tee) * mpp
        if r >= want[0]:
            q = project(p)
            if q is not None:
                x, y, z, vis = q
                out.append({"r": want[0], "x": round(x * scale, 1), "y": round(y * scale, 1), "vis": bool(vis)})
            want.pop(0)
            continue
        s += 0.5
    return out


def render_hole(slug, hole, quick=False, out_dir="courses/teeview"):
    log(f"== {slug} hole {hole}")
    D = prepare(slug, hole, quick)
    workdir = os.path.join("/tmp", f"tee3d-{slug}-{hole}"); os.makedirs(workdir, exist_ok=True)
    S = build_scene(D, workdir, quick)
    os.makedirs(out_dir, exist_ok=True)
    png = os.path.join(workdir, "r.png")
    # a little above head height, as tour-book photos are taken (and clear of the ~30 m terrain model's bumps)
    project, Wr = shoot(S, D, png, eye=7.0, back=6.0, lens=36.0, shift_y=-0.08)
    img = Image.open(png).convert("RGB")
    scale = W_OUT / Wr
    if scale != 1: img = img.resize((W_OUT, H_OUT), Image.LANCZOS)
    # clean photo: the book draws its own markers on it
    path = f"{out_dir}/{slug}-{hole}.jpg"; img.save(path, quality=90)
    json.dump({"w": W_OUT, "h": H_OUT, "marks": marks(D, project, scale)}, open(f"{out_dir}/{slug}-{hole}.json", "w"))
    log(f"  saved {path} + marks")
    return [path]


def parse_holes(spec):
    out = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part: a, b = part.split("-"); out += list(range(int(a), int(b) + 1))
        elif part: out.append(int(part))
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    quick = "--quick" in sys.argv
    out_dir = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--out=")), "courses/teeview")
    jobs = []
    if args: jobs = [(args[0], parse_holes(args[1]))]
    elif os.path.exists("courses/tee3d-requests.txt"):
        for l in open("courses/tee3d-requests.txt"):
            l = l.strip()
            if l and not l.startswith("#"):
                s, hs = [x.strip() for x in l.split("|")]; jobs.append((s, parse_holes(hs)))
    shard = os.environ.get("SHARD")      # "k/n": this runner takes every n-th hole
    for slug, holes in jobs:
        if shard:
            k, n = map(int, shard.split("/")); holes = holes[k::n]
        for hno in holes:
            try: render_hole(slug, hno, quick, out_dir)
            except Exception as ex:
                import traceback; traceback.print_exc(); log("FAILED", slug, hno, ex)


if __name__ == "__main__":
    main()
