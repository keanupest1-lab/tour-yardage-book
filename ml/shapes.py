"""Runs the shape model over a course's saved satellite mosaic (courses/imagery/<slug>/z18.jpg), so hand-checked
courses can be traced from the same pixels the holes are checked on.

Request file courses/shapes-requests.txt: one slug per line, optionally followed by model branches to run
(default: models). Writes, per branch:
  courses/verified/<slug>-mask18[-<branch>].png   class per pixel (1 fairway, 2 green, 3 tee, 4 bunker, 5 water)
  courses/qa/<slug>-shapes18[-<branch>].jpg       the classes over the imagery, for checking by eye
"""
import json, os, subprocess, sys, time
import numpy as np
from PIL import Image
sys.path.insert(0, os.path.dirname(__file__))
import automap_lib as A

Image.MAX_IMAGE_PIXELS = None
os.makedirs("courses/verified", exist_ok=True); os.makedirs("courses/qa", exist_ok=True)
for line in open("courses/shapes-requests.txt"):
    line = line.strip()
    if not line or line.startswith("#"): continue
    slug, *branches = line.split()
    branches = branches or ["models"]
    img = Image.open(f"courses/imagery/{slug}/z18.jpg").convert("RGB")
    for br in branches:
        path = f"models/{br}.pt"
        if not os.path.exists(path):
            subprocess.run(f"git fetch --depth 1 origin {br} && git show FETCH_HEAD:model.pt > {path}", shell=True, check=True)
        model = A.load_model(path)
        t = time.time(); lab, prob = A.predict(model, img)
        suffix = "" if br == "models" else f"-{br}"
        Image.fromarray(lab).save(f"courses/verified/{slug}-mask18{suffix}.png", optimize=True)
        ov = A.overlay(img, lab, 0.45)
        sc = 3000 / max(ov.size)
        if sc < 1: ov = ov.resize((int(ov.width * sc), int(ov.height * sc)), Image.LANCZOS)
        ov.save(f"courses/qa/{slug}-shapes18{suffix}.jpg", quality=82)
        counts = {k: int((lab == i).sum()) for i, k in enumerate(["none", "fairway", "green", "tee", "bunker", "water"])}
        print(f"{slug} {br}: {img.width}x{img.height} in {time.time() - t:.0f}s, pixels per class {counts}", flush=True)
