"""Photo-real tee shot, 4K.

1. Render the hole from the tee (teeview.py): satellite imagery on terrain + 3D tree canopies. This fixes the
   layout: where the fairway, bunkers, tree lines and slopes really are.
2. A photographic diffusion model (Stable Diffusion 1.5 "Realistic Vision" + ControlNet depth) repaints the render
   as a photograph. The depth map from step 1 holds every shape in place; the model supplies real-looking grass,
   foliage, light and sky.
3. Upscale x4 with a real-world super-resolution model (Swin2SR) to 3840 px wide.
4. Draw the yardage-book marks at full resolution (positions come from the 3D scene, not from the AI).

Request file courses/photo-requests.txt:  slug | hole [| seeds e.g. 11,22,33]
Output: courses/teeview/<slug>-<hole>-photo.jpg (+ -photo-clean.jpg) and candidates/ previews for each seed.
"""
import json, math, os, sys, time
import numpy as np
from PIL import Image, ImageFilter, ImageEnhance
sys.path.insert(0, os.path.dirname(__file__))
import teeview as T

GEN_W, GEN_H = 1024, 432          # what the photo model paints (multiple of 8, ~2.4:1 like the book strip)
REN_W, REN_H = 1536, 648          # 3D render size (same aspect)
OUT_W = 3840                      # 4K
MODELS = ["SG161222/Realistic_Vision_V6.0_B1_noVAE", "SG161222/Realistic_Vision_V5.1_noVAE", "stable-diffusion-v1-5/stable-diffusion-v1-5"]
DEPTH_CN = "lllyasviel/control_v11f1p_sd15_depth"
VAE = "stabilityai/sd-vae-ft-mse"
SR = "caidas/swin2SR-realworld-sr-x4-64-bsrgan-psnr"


def log(*a): print(*a, flush=True)


def depth_map(depth):
    """ControlNet depth convention: near = bright, far/sky = dark"""
    d = depth.copy(); sky = ~np.isfinite(d)
    inv = np.where(sky, 0, 1.0 / np.maximum(d, 1.5))
    hi = np.percentile(inv[~sky], 99.5) if (~sky).any() else 1
    inv = np.clip(inv / hi, 0, 1) ** 0.55
    return Image.fromarray((inv * 255).astype(np.uint8)).convert("RGB")


def prompt_for(course, place, par):
    parts = [x.strip() for x in (place or "").split(",") if x.strip() and not any(ch.isdigit() for ch in x)]
    region = ", ".join(parts[-2:]) or "South Africa"
    p = (f"professional golf course photography, view from the tee box looking down the fairway of a par {par} hole "
         f"at {course}, {region}, parkland golf course, lush green kikuyu fairway with mowing stripes, light rough, "
         "tall mature trees lining both sides of the hole, eucalyptus, pine and oak, white sand bunker, "
         "bright morning sunlight, soft shadows, clear blue sky with a few white clouds, "
         "sharp focus, high detail, natural colours, shot on Canon EOS R5, 35mm lens, f/8, photorealistic, 8k")
    neg = ("cartoon, cgi, 3d render, video game, painting, illustration, drawing, blurry, soft, low quality, jpeg artifacts, "
           "people, golfer, person, animal, text, watermark, logo, signature, cars, buildings, houses, power lines, "
           "oversaturated, hdr, distorted, deformed trees, duplicate, snow, fog")
    return p, neg


def load_pipe():
    import torch
    from diffusers import StableDiffusionControlNetImg2ImgPipeline, ControlNetModel, AutoencoderKL, DPMSolverMultistepScheduler
    torch.set_num_threads(os.cpu_count() or 4)
    cn = ControlNetModel.from_pretrained(DEPTH_CN, torch_dtype=torch.float32)
    vae = AutoencoderKL.from_pretrained(VAE, torch_dtype=torch.float32)
    last = None
    for mid in MODELS:
        try:
            pipe = StableDiffusionControlNetImg2ImgPipeline.from_pretrained(mid, controlnet=cn, vae=vae, torch_dtype=torch.float32,
                                                                           safety_checker=None, requires_safety_checker=False)
            log("  photo model:", mid); break
        except Exception as e:
            last = e; log("  could not load", mid, repr(e)[:160])
    else:
        raise last
    pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config, use_karras_sigmas=True, algorithm_type="dpmsolver++")
    pipe.set_progress_bar_config(disable=True)
    return pipe


def upscale4(img, tile=160, pad=16):
    """Swin2SR x4, in tiles to keep memory down"""
    import torch
    from transformers import Swin2SRForImageSuperResolution
    model = Swin2SRForImageSuperResolution.from_pretrained(SR).eval()
    a = np.asarray(img.convert("RGB"), np.float32) / 255.0
    H, W = a.shape[:2]; out = np.zeros((H * 4, W * 4, 3), np.float32)
    for y in range(0, H, tile):
        for x in range(0, W, tile):
            y0, x0 = max(0, y - pad), max(0, x - pad); y1, x1 = min(H, y + tile + pad), min(W, x + tile + pad)
            t = torch.from_numpy(a[y0:y1, x0:x1].transpose(2, 0, 1).copy())[None]
            ph, pw = (8 - t.shape[2] % 8) % 8, (8 - t.shape[3] % 8) % 8
            if ph or pw: t = torch.nn.functional.pad(t, (0, pw, 0, ph), mode="reflect")
            with torch.no_grad(): r = model(pixel_values=t).reconstruction[0].clamp(0, 1).numpy().transpose(1, 2, 0)
            r = r[: (y1 - y0) * 4, : (x1 - x0) * 4]
            oy, ox = (y - y0) * 4, (x - x0) * 4
            hh, ww = min(tile, H - y) * 4, min(tile, W - x) * 4
            out[y * 4:y * 4 + hh, x * 4:x * 4 + ww] = r[oy:oy + hh, ox:ox + ww]
    return Image.fromarray((out * 255).round().astype(np.uint8))


def photo(slug, hole, seeds=(11, 22, 33), strength=0.62, steps=30, out_dir="courses/teeview"):
    import torch
    t0 = time.time()
    S = T.scene(slug, hole, log=log)
    h = S["hole"]
    base, proj = T.shot(S, eye=1.7, back=4, pitch=-1.0, maxd=900.0, W=REN_W, Hpx=REN_H)
    dimg = depth_map(proj.depth)
    os.makedirs(f"{out_dir}/candidates", exist_ok=True)
    base.save(f"{out_dir}/candidates/{slug}-{hole}-layout.jpg", quality=85)
    dimg.save(f"{out_dir}/candidates/{slug}-{hole}-depth.png")
    log(f"  scene + layout render {time.time() - t0:.0f}s")
    idx = {c["slug"]: c for c in json.load(open("courses/index.json"))["courses"]}
    course = S["edits"].get("name") or idx.get(slug, {}).get("name", slug)
    p, neg = prompt_for(course, idx.get(slug, {}).get("place", ""), h.get("par", 4))
    pipe = load_pipe()
    init = base.resize((GEN_W, GEN_H), Image.LANCZOS); ctrl = dimg.resize((GEN_W, GEN_H), Image.BILINEAR)
    outs = []
    for sd in seeds:
        t1 = time.time()
        g = torch.Generator().manual_seed(int(sd))
        im = pipe(p, image=init, control_image=ctrl, strength=strength, num_inference_steps=steps, guidance_scale=6.0,
                  controlnet_conditioning_scale=0.85, negative_prompt=neg, generator=g, width=GEN_W, height=GEN_H).images[0]
        im.save(f"{out_dir}/candidates/{slug}-{hole}-seed{sd}.jpg", quality=90); outs.append((sd, im))
        log(f"  seed {sd}: {time.time() - t1:.0f}s")
    del pipe
    # the first seed becomes the book photo (others stay as candidates to choose from)
    sd, best = outs[0]
    t1 = time.time()
    big = upscale4(best)
    big = big.resize((OUT_W, round(OUT_W * GEN_H / GEN_W)), Image.LANCZOS)
    big = ImageEnhance.Contrast(big).enhance(1.04).filter(ImageFilter.UnsharpMask(radius=1.6, percent=60, threshold=2))
    log(f"  4K upscale {time.time() - t1:.0f}s -> {big.size}")
    big.save(f"{out_dir}/{slug}-{hole}-photo-clean.jpg", quality=92)
    marked = T.overlays(big.copy(), proj, S["line_px"], S["mpp"], S["bunkers"], hole, h.get("par", 4), h.get("dist", 0),
                        S=OUT_W / REN_W, note="AI-rendered view")
    marked.save(f"{out_dir}/{slug}-{hole}-photo.jpg", quality=92)
    json.dump({"slug": slug, "hole": hole, "seed": sd, "seeds": [s for s, _ in outs], "strength": strength, "steps": steps,
               "prompt": p, "made": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
              open(f"{out_dir}/{slug}-{hole}-photo.json", "w"), indent=1)
    log(f"  done in {time.time() - t0:.0f}s")


def main():
    req = "courses/photo-requests.txt"
    if not os.path.exists(req): return
    for l in open(req):
        l = l.strip()
        if not l or l.startswith("#"): continue
        p = [x.strip() for x in l.split("|")]
        seeds = tuple(int(x) for x in p[2].split(",")) if len(p) > 2 and p[2] else (11, 22, 33)
        try: photo(p[0], int(p[1]), seeds=seeds)
        except Exception as ex:
            import traceback; traceback.print_exc(); log("FAILED", l, ex)


if __name__ == "__main__":
    main()
