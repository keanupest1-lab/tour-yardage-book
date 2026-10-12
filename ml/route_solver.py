"""Routing solver: works out which tee plays to which green, in order.

Inputs (all in a local metre grid):
  greens   : list of (x, y) green centres
  fairway  : boolean raster (True = fairway or green), cell size `cell` metres, origin (x0, y0) = grid (0, 0)
  card     : list of (par, length_m) for holes 1..N
  start    : (x, y) of the first tee area (clubhouse); optional
Each hole is tee -> (optional bend) -> green. A candidate hole is scored on:
  - length error vs the scorecard
  - how much of the line of play (from 30% to 92% of the way) runs over fairway (par 4/5 only)
  - walking distance from the previous green to this tee
Beam search over the sequence picks the routing with the lowest total cost.
"""
import math
import numpy as np

def _frac_on(mask, cell, x0, y0, pts, a=0.30, b=0.92, step=4.0):
    """share of the path between fractions a..b of its length that lies on True cells"""
    segs = list(zip(pts[:-1], pts[1:]))
    L = sum(math.dist(p, q) for p, q in segs)
    if L <= 0: return 0.0
    on = tot = 0; s = a * L
    while s <= b * L:
        acc = 0
        for p, q in segs:
            d = math.dist(p, q)
            if acc + d >= s:
                t = (s - acc) / d if d else 0
                x = p[0] + (q[0] - p[0]) * t; y = p[1] + (q[1] - p[1]) * t
                i = int((y - y0) / cell); j = int((x - x0) / cell)
                if 0 <= i < mask.shape[0] and 0 <= j < mask.shape[1]:
                    on += bool(mask[i, j])
                tot += 1
                break
            acc += d
        s += step
    return on / tot if tot else 0.0

def candidates(green, length, par, mask, cell, x0, y0, n_dirs=48):
    """possible holes ending at `green`: straight lines and single doglegs of the right length"""
    out = []
    for k in range(n_dirs):
        a = 2 * math.pi * k / n_dirs
        tee = (green[0] + length * math.cos(a), green[1] + length * math.sin(a))
        out.append([tee, green])
        if par >= 4:
            # doglegs: bend at 55-70% of the way, 18-35 degrees
            for frac in (0.6,):
                for bend in (-30, 30):
                    b = math.radians(bend)
                    l1 = length * frac; l2 = length - l1
                    ang2 = a + b   # direction from green back to bend
                    bp = (green[0] + l2 * math.cos(ang2), green[1] + l2 * math.sin(ang2))
                    tee2 = (bp[0] + l1 * math.cos(a - b * 0.2), bp[1] + l1 * math.sin(a - b * 0.2))
                    out.append([tee2, bp, green])
    scored = []
    for pts in out:
        cover = _frac_on(mask, cell, x0, y0, pts) if par >= 4 else 0.0
        scored.append((pts, cover))
    return scored

def solve(greens, mask, cell, x0, y0, card, start=None, beam=400, walk_free=60, walk_max=300, allow_shared=True, shared_penalty=35.0, end=None, end_weight=0.5):
    cand = {}
    for gi, g in enumerate(greens):
        for hi, (par, L) in enumerate(card):
            cs = candidates(g, L, par, mask, cell, x0, y0)
            cs.sort(key=lambda c: -c[1])
            cand[(gi, hi)] = cs[:24]
    # state: (cost, used greens, holes list)
    states = [(0.0, frozenset(), [])]
    for hi, (par, L) in enumerate(card):
        nxt = []
        for cost, used, holes in states:
            prev = greens[holes[-1][0]] if holes else start
            for gi, g in enumerate(greens):
                reuse = gi in used
                if reuse and not allow_shared: continue
                if prev is not None and math.dist(prev, g) > L + walk_max + 40: continue
                for pts, cover in cand[(gi, hi)]:
                    tee = pts[0]
                    walk = math.dist(prev, tee) if prev is not None else 0.0
                    if walk > walk_max: continue
                    c = max(0.0, walk - walk_free) * 0.15 + (shared_penalty if reuse else 0.0)
                    if par >= 4: c += (1.0 - cover) * 60
                    else: c += 0 if math.dist(tee, g) > 0 else 0
                    nxt.append((cost + c, used | {gi}, holes + [(gi, pts, round(cover, 2), round(walk))]))
        nxt.sort(key=lambda s: s[0])
        # keep diversity: best few per last-green
        seen = {}; states = []
        for s in nxt:
            k = s[2][-1][0]
            if seen.get(k, 0) >= max(4, beam // 6): continue
            seen[k] = seen.get(k, 0) + 1; states.append(s)
            if len(states) >= beam: break
        if not states:
            print('  no states after hole', hi + 1); return None
    if end is not None:   # the last green should come back near the clubhouse
        states = sorted(states, key=lambda s: s[0] + end_weight * max(0.0, math.dist(greens[s[2][-1][0]], end) - 80))
    return states[0]
