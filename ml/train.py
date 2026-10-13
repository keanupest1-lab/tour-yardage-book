"""Trains the golf-feature segmentation model and runs it on Irene for a reality check.
Model: U-Net with an ImageNet ResNet-34 encoder (segmentation_models_pytorch).
Classes: 0 background, 1 fairway, 2 green, 3 tee, 4 bunker, 5 water.
Runs on CPU (GitHub Actions) or GPU if present. Outputs go to out/.
"""
import json, math, os, random, time
import numpy as np
import torch, torch.nn as nn
from PIL import Image
import segmentation_models_pytorch as smp

DATA = os.environ.get("DATA", "ds/ml/data")
OUT = "out"; os.makedirs(OUT, exist_ok=True)
EPOCHS = int(os.environ.get("EPOCHS", "14"))
CROP = 256
BATCH = int(os.environ.get("BATCH", "12"))
TIME_BUDGET = float(os.environ.get("TIME_BUDGET_MIN", "270")) * 60
NAMES = ["background", "fairway", "green", "tee", "bunker", "water"]
dev = "cuda" if torch.cuda.is_available() else "cpu"
torch.set_num_threads(os.cpu_count() or 4)
random.seed(1); np.random.seed(1); torch.manual_seed(1)
MEAN = np.array([0.485, 0.456, 0.406], np.float32); STD = np.array([0.229, 0.224, 0.225], np.float32)

index = json.load(open(f"{DATA}/index.json"))
courses = sorted({c["course"] for c in index["chips"]})
random.shuffle(courses)
val_courses = set(courses[: max(2, len(courses) // 10)])
train_ids = [c["id"] for c in index["chips"] if c["course"] not in val_courses]
val_ids = [c["id"] for c in index["chips"] if c["course"] in val_courses]
print(f"{len(train_ids)} train chips, {len(val_ids)} val chips, {len(courses)} courses", flush=True)

def load(cid):
    img = np.asarray(Image.open(f"{DATA}/images/{cid}.jpg").convert("RGB"), np.float32) / 255.0
    m = np.asarray(Image.open(f"{DATA}/masks/{cid}.png"), np.int64)
    return img, m

def norm(img): return ((img - MEAN) / STD).transpose(2, 0, 1)

class DS(torch.utils.data.Dataset):
    def __init__(self, ids, train): self.ids, self.train = ids, train
    def __len__(self): return len(self.ids) * (2 if self.train else 1)
    def __getitem__(self, i):
        img, m = load(self.ids[i % len(self.ids)])
        if self.train:
            y, x = np.random.randint(0, 512 - CROP + 1, 2)
            img, m = img[y:y + CROP, x:x + CROP], m[y:y + CROP, x:x + CROP]
            k = np.random.randint(4); img, m = np.rot90(img, k), np.rot90(m, k)
            if np.random.rand() < 0.5: img, m = img[:, ::-1], m[:, ::-1]
            # colour jitter: brown dry grass vs lush green, haze, exposure
            img = img * np.random.uniform(0.8, 1.2, 3).astype(np.float32) + np.random.uniform(-0.06, 0.06)
            if np.random.rand() < 0.4:
                g = img.mean(2, keepdims=True); img = img * 0.6 + g * 0.4 + np.array([0.05, 0.02, -0.03], np.float32)
            img = np.clip(img, 0, 1)
        return torch.from_numpy(np.ascontiguousarray(norm(img))), torch.from_numpy(np.ascontiguousarray(m))

# class weights from pixel frequencies (rare classes matter most)
cnt = np.zeros(6)
for cid in train_ids[: min(400, len(train_ids))]:
    cnt += np.bincount(np.asarray(Image.open(f"{DATA}/masks/{cid}.png")).ravel(), minlength=6)[:6]
freq = cnt / cnt.sum(); w = 1 / np.sqrt(freq + 1e-4); w = w / w.mean()
print("pixel share", dict(zip(NAMES, np.round(freq, 3))), "weights", np.round(w, 2), flush=True)

model = smp.Unet("resnet34", encoder_weights="imagenet", classes=6).to(dev)
if os.path.exists("init.pt"):   # continue from an earlier run
    model.load_state_dict({k: v.float() for k, v in torch.load("init.pt", map_location=dev).items()})
    print("continuing from init.pt", flush=True)
ce = nn.CrossEntropyLoss(weight=torch.tensor(w, dtype=torch.float32, device=dev))
dice = smp.losses.DiceLoss("multiclass")
opt = torch.optim.AdamW(model.parameters(), lr=4e-4, weight_decay=1e-4)
tl = torch.utils.data.DataLoader(DS(train_ids, True), batch_size=BATCH, shuffle=True, num_workers=2, drop_last=True)
MAX_LR = float(os.environ.get("MAX_LR", "6e-4"))
sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=MAX_LR, total_steps=EPOCHS * len(tl))

def evaluate():
    model.eval(); inter = np.zeros(6); union = np.zeros(6)
    with torch.no_grad():
        for cid in val_ids:
            img, m = load(cid)
            p = model(torch.from_numpy(norm(img))[None].to(dev)).argmax(1)[0].cpu().numpy()
            for k in range(6):
                inter[k] += np.sum((p == k) & (m == k)); union[k] += np.sum((p == k) | (m == k))
    model.train()
    return {NAMES[k]: round(float(inter[k] / union[k]), 3) if union[k] else None for k in range(6)}

t0 = time.time(); history = []
for ep in range(EPOCHS):
    tot = 0; n = 0
    for x, y in tl:
        x, y = x.to(dev), y.to(dev)
        out = model(x); loss = ce(out, y) + dice(out, y)
        opt.zero_grad(); loss.backward(); opt.step(); sched.step()
        tot += loss.item(); n += 1
        if time.time() - t0 > TIME_BUDGET: break
    iou = evaluate()
    history.append({"epoch": ep + 1, "loss": round(tot / max(n, 1), 4), "iou": iou, "minutes": round((time.time() - t0) / 60, 1)})
    print(history[-1], flush=True)
    torch.save({k: v.half() for k, v in model.state_dict().items()}, f"{OUT}/model.pt")  # fp16 keeps it under GitHub's 100 MB file limit
    json.dump(history, open(f"{OUT}/metrics.json", "w"), indent=1)
    if time.time() - t0 > TIME_BUDGET: print("time budget reached"); break

# ---- reality check on Irene (never seen in training) ----
PAL = np.array([[0, 0, 0], [60, 170, 80], [140, 255, 140], [255, 255, 255], [240, 210, 130], [70, 140, 255]], np.uint8)
def predict_big(path):
    im = np.asarray(Image.open(path).convert("RGB"), np.float32) / 255.0
    H, W = im.shape[:2]; S = 512; step = 384
    prob = np.zeros((6, H, W), np.float32); cnt = np.zeros((H, W), np.float32)
    model.eval()
    with torch.no_grad():
        for y in list(range(0, max(1, H - S), step)) + [max(0, H - S)]:
            for x in list(range(0, max(1, W - S), step)) + [max(0, W - S)]:
                tile = im[y:y + S, x:x + S]
                p = torch.softmax(model(torch.from_numpy(norm(tile))[None].to(dev)), 1)[0].cpu().numpy()
                prob[:, y:y + S, x:x + S] += p; cnt[y:y + S, x:x + S] += 1
    lab = (prob / np.maximum(cnt, 1)).argmax(0).astype(np.uint8)
    return im, lab
irene = "courses/imagery/irene-country-club/z18.jpg"
if os.path.exists(irene):
    im, lab = predict_big(irene)
    Image.fromarray(lab).save(f"{OUT}/irene_z18_mask.png")
    over = (im * 255).astype(np.uint8).copy(); sel = lab > 0
    over[sel] = (over[sel] * 0.45 + PAL[lab[sel]] * 0.55).astype(np.uint8)
    Image.fromarray(over).save(f"{OUT}/irene_z18_overlay.jpg", quality=85)
    print("irene class share", {NAMES[k]: round(float((lab == k).mean()), 4) for k in range(6)})
print("done")
