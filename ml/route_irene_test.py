# Test harness used on Irene: routes all 18 holes from the model mask (needs the scratch geo helpers and models branch output).
import sys, json, math, numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage
sys.path.insert(0, '/home/claude/tour-yardage-books/ml'); sys.path.insert(0, '.')
import importlib, route_solver2; importlib.reload(route_solver2)
from geo import MPP18
S = '../models/'
m = np.array(Image.open(S + 'irene_z18_mask.png'))
lab, n = ndimage.label(m == 2)
G = []
for i in range(1, n + 1):
    if (lab == i).sum() * MPP18 ** 2 < 150: continue
    cy, cx = ndimage.center_of_mass(lab == i); G.append((cx, cy))
K = 4
fw = (m == 1)
fw = ndimage.binary_opening(fw, iterations=2)
small = fw.reshape(fw.shape[0] // K, K, fw.shape[1] // K, K).mean((1, 3)) > 0.4
cell = K * MPP18
tm = (m == 3)
tee_small = tm.reshape(tm.shape[0] // K, K, tm.shape[1] // K, K).mean((1, 3)) > 0.3
greens = [(x * MPP18, y * MPP18) for x, y in G]
which = sys.argv[1]
club = (2120 * MPP18, 1480 * MPP18)
if which == 'back':
    card = list(zip([5,4,4,3,4,4,3,5,4], [522,401,392,171,437,313,133,507,318]))
    truth = [(2077,330),(1460,380),(822,757),(1057,685),(405,1270),(930,1215),(745,1348),(1490,1352),(2180,1320)]
elif which == 'full':
    card = list(zip([4,5,4,3,4,4,3,4,5,5,4,4,3,4,4,3,5,4], [370,563,471,208,415,338,138,303,517,522,401,392,171,437,313,133,507,318]))
    truth = [None]*9 + [(2077,330),(1460,380),(822,757),(1057,685),(405,1270),(930,1215),(745,1348),(1490,1352),(2180,1320)]
else:
    card = list(zip([4,5,4,3,4,4,3,4,5], [370,563,471,208,415,338,138,303,517]))
    truth = None
res = route_solver2.solve(greens, small, cell, card, tee_mask=tee_small, start=club, end=club, turn=({8: club} if which == 'full' else None), beam=int(sys.argv[2]) if len(sys.argv) > 2 else 800)
kw = dict(start=club, end=club, tee_mask=tee_small, turn=({8: club} if which == 'full' else None))
fixed = None
if len(sys.argv) > 3 and sys.argv[3] == 'fixback':
    fixed = {k: min(range(len(G)), key=lambda i: math.dist(G[i], t)) for k, t in enumerate(truth) if t}
res = route_solver2.solve_dp(greens, small, cell, card, fixed=fixed, beam=int(sys.argv[2]), **kw)

print('unused big fairways', res['unused_fairways'])
print('cost', res['cost'], 'runner-up gap', res['runner_up_gap'], 'fairways found', len(res['fairways']))
ok = 0
for k, h in enumerate(res['holes']):
    g = G[h['green']]; tee = (h['points'][0][0] / MPP18, h['points'][0][1] / MPP18)
    hit = truth and truth[k] and math.dist(g, truth[k]) < 60; ok += bool(hit)
    print(f"hole {k+1}: green ({int(g[0])},{int(g[1])}) tee ({int(tee[0])},{int(tee[1])}) fairway {h['fairway']} walk {h['walk']}m", 'OK' if hit else ('WRONG' if truth and truth[k] else ''))
if truth: print('correct greens', ok, '/ 9')
im = Image.open(S + 'irene_z18_overlay.jpg'); d = ImageDraw.Draw(im)
for f in res['fairways']:
    d.line([(x / MPP18, y / MPP18) for x, y in f['line']], fill=(255, 255, 0), width=3)
for t in res['tees']:
    x, y = t[0] / MPP18, t[1] / MPP18; d.rectangle((x - 9, y - 9, x + 9, y + 9), outline=(0, 200, 255), width=4)
for k, h in enumerate(res['holes']):
    pts = [(x / MPP18, y / MPP18) for x, y in h['points']]
    d.line(pts, fill=(255, 40, 40), width=9); x, y = pts[0]
    d.ellipse((x - 26, y - 26, x + 26, y + 26), fill=(255, 255, 255)); d.text((x - 12, y - 14), str(k + (10 if which == 'back' else 1)), fill=(0, 0, 0), font_size=28)
im.resize((im.width // 2, im.height // 2)).save(f'route2_{which}.jpg', quality=82)
json.dump([{'hole': k + 1, 'pts_px': [(x / MPP18, y / MPP18) for x, y in h['points']]} for k, h in enumerate(res['holes'])], open(f'route2_{which}.json', 'w'))
