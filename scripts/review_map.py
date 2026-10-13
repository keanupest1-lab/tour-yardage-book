"""Course map for checking: every hole as the book draws it (after tee/green snapping), numbered at the tee.
Needs courses/qa/<slug>.jpg from the enrich job. Usage: python3 scripts/review_map.py <slug> <out.jpg>"""
import json, math, subprocess, sys
sys.path.insert(0, 'ml')
import automap_lib as A
from enrich import course_shapes
from PIL import Image, ImageDraw, ImageFont

slug, out = sys.argv[1], sys.argv[2]
base, ed, have, lines = course_shapes(slug)
lats = [p[0] for l in lines for p in l]; lons = [p[1] for l in lines for p in l]
s, w, n, e = min(lats), min(lons), max(lats), max(lons); pad = 120
dlat = pad / 111000; dlon = pad / (111000 * math.cos(math.radians((s + n) / 2)))
x0, y0 = A.ll2world(n + dlat, w - dlon, 18); x1, y1 = A.ll2world(s - dlat, e + dlon, 18)
tx0, ty0 = int(x0 // 256), int(y0 // 256); W = (int(x1 // 256) - tx0 + 1) * 256
img = Image.open(f'courses/qa/{slug}.jpg').convert('RGB'); k = img.width / W
def P(p):
    X, Y = A.ll2world(p[0], p[1], 18); return ((X - tx0 * 256) * k, (Y - ty0 * 256) * k)
data = json.loads(subprocess.run(['node', 'scripts/holes_ll.mjs', slug], capture_output=True, text=True).stdout)
d = ImageDraw.Draw(img, 'RGBA')
try: font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 20)
except Exception: font = None
for h in data['holes']:
    L = [P(p) for p in h['line']]
    d.line(L, fill=(255, 225, 0, 255), width=4)
    gx, gy = L[-1]; d.ellipse((gx - 6, gy - 6, gx + 6, gy + 6), fill=(255, 225, 0))
for h in data['holes']:
    x, y = P(h['line'][0]); col = (20, 120, 60) if h['teeFound'] else (200, 40, 40)
    d.ellipse((x - 15, y - 15, x + 15, y + 15), fill=col, outline=(255, 255, 255), width=2)
    t = h['ref']; tw = d.textlength(t, font=font)
    d.text((x - tw / 2, y - 12), t, fill=(255, 255, 255), font=font)
img.save(out, quality=85)
print(out, img.size)
