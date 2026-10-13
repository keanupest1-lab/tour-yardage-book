"""Fairway-first routing solver.

Works out which tee plays to which green, in order, from a predicted (or mapped) shape mask.
Unlike route_solver.py (straight lines scored by fairway coverage), candidate holes here are built
along the centre line of each detected fairway, so a hole follows its fairway instead of cutting
across two of them.

Units: metres in a local grid (x right, y down is fine). Mask cells are `cell` metres, origin (0, 0).
  greens : [(x, y)] green centres
  fairway: boolean array, True = fairway
  card   : [(par, length_m)] for the holes to route, in playing order
  start  : (x, y) where the first tee should be (clubhouse / previous green)
  end    : (x, y) where the last green should finish (clubhouse), optional
"""
import math
import numpy as np
from scipy import ndimage

# tunable weights (eval_local.py can override them with RS_PARAMS)
FALLBACK_BASE = 70.0    # a par 4/5 that follows no detected fairway starts at this cost
FALLBACK_SLOPE = 60.0   # ...plus this times the share of its line that is off fairway


def fairway_lines(fairway, cell, min_area=1500.0, step=12.0):
    """each fairway blob -> centre line (list of points from one end to the other) and its area"""
    lab, n = ndimage.label(fairway)
    out = []
    for i in range(1, n + 1):
        ys, xs = np.nonzero(lab == i)
        area = len(xs) * cell * cell
        if area < min_area: continue
        P = np.stack([(xs + 0.5) * cell, (ys + 0.5) * cell], 1)
        c = P.mean(0)
        u, s, vt = np.linalg.svd(P - c, full_matrices=False)
        ax = vt[0]
        t = (P - c) @ ax
        lo, hi = t.min(), t.max()
        if hi - lo < 60: continue
        line = []
        for a in np.arange(lo, hi + 1e-6, step):
            sel = (t >= a - step / 2) & (t < a + step / 2)
            if sel.sum() >= 2: line.append(tuple(P[sel].mean(0)))
        if len(line) < 3: continue
        # light smoothing
        sm = [line[0]] + [tuple((np.array(line[k - 1]) + np.array(line[k]) * 2 + np.array(line[k + 1])) / 4) for k in range(1, len(line) - 1)] + [line[-1]]
        width = area / max(hi - lo, 1)
        out.append({"id": len(out), "line": sm, "area": area, "length": hi - lo, "width": width})
    return out


def chain_fairways(fws, max_gap=75.0, max_turn=40.0):
    """a fairway split by a stream, dam or trees shows up as two pieces in line: offer each such pair as one fairway"""
    def heading(a, b): return math.atan2(b[1] - a[1], b[0] - a[0])
    out = []
    for f in fws: f.setdefault("ids", frozenset([f["id"]]))
    for i, f in enumerate(fws):
        for g in fws[i + 1:]:
            best = None
            for L1 in (f["line"], f["line"][::-1]):
                for L2 in (g["line"], g["line"][::-1]):
                    gap = math.dist(L1[-1], L2[0])
                    if gap > max_gap: continue
                    h1 = heading(L1[max(0, len(L1) - 4)], L1[-1]); h2 = heading(L2[0], L2[min(len(L2) - 1, 3)])
                    hj = heading(L1[-1], L2[0]) if gap > 5 else h1
                    turn = max(abs((h1 - h2 + math.pi) % (2 * math.pi) - math.pi), abs((h1 - hj + math.pi) % (2 * math.pi) - math.pi))
                    if math.degrees(turn) > max_turn: continue
                    if best is None or gap < best[0]: best = (gap, L1 + L2)
            if best:
                out.append({"id": f"{f['id']}+{g['id']}", "ids": frozenset([f["id"], g["id"]]), "line": best[1],
                            "area": f["area"] + g["area"], "length": f["length"] + g["length"] + best[0], "width": (f["width"] + g["width"]) / 2})
    return fws + out


def virtual_greens(fws, greens, near=90.0, beyond=22.0):
    """where a fairway ends with no green detected beyond it, guess one just past the end (the model missed it)"""
    out = []
    for f in fws:
        L = f["line"]
        for a, b in ((L[max(0, len(L) - 4)], L[-1]), (L[min(len(L) - 1, 3)], L[0])):
            if any(math.dist(b, g) < near for g in greens): continue
            d = math.dist(a, b) or 1.0
            v = (b[0] + (b[0] - a[0]) / d * beyond, b[1] + (b[1] - a[1]) / d * beyond)
            if all(math.dist(v, o) > 40 for o in out): out.append(v)
    return out


def _length(pts):
    return sum(math.dist(a, b) for a, b in zip(pts[:-1], pts[1:]))


def _frac_on(mask, cell, pts, a=0.0, b=1.0, step=5.0):
    L = _length(pts)
    if L <= 0: return 0.0
    on = tot = 0
    s = a * L
    while s <= b * L:
        p = _point_at(pts, s)
        i, j = int(p[1] / cell), int(p[0] / cell)
        if 0 <= i < mask.shape[0] and 0 <= j < mask.shape[1]: on += bool(mask[i, j])
        tot += 1; s += step
    return on / tot if tot else 0.0


def _point_at(pts, s):
    acc = 0.0
    for p, q in zip(pts[:-1], pts[1:]):
        d = math.dist(p, q)
        if acc + d >= s and d > 0:
            t = (s - acc) / d
            return (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)
        acc += d
    return pts[-1]


def _simplify(pts, tol=6.0):
    """Douglas-Peucker"""
    if len(pts) < 3: return pts
    a, b = np.array(pts[0]), np.array(pts[-1])
    ab = b - a; L = np.linalg.norm(ab) or 1.0
    d = [abs(ab[0] * (p[1] - a[1]) - ab[1] * (p[0] - a[0])) / L for p in pts[1:-1]]
    k = int(np.argmax(d)) + 1
    if d[k - 1] > tol:
        return _simplify(pts[:k + 1], tol)[:-1] + _simplify(pts[k:], tol)
    return [pts[0], pts[-1]]


def tee_points(tee_mask, cell, min_area=40.0, max_area=3000.0):
    """centres of detected tee boxes"""
    lab, n = ndimage.label(tee_mask)
    pts = []
    for i in range(1, n + 1):
        a = (lab == i).sum() * cell * cell
        if a < min_area or a > max_area: continue
        cy, cx = ndimage.center_of_mass(lab == i)
        pts.append(((cx + 0.5) * cell, (cy + 0.5) * cell))
    return pts


def _snap_tee(pts, length, tees, radius=45.0, bonus=14.0):
    """move the tee end onto a detected tee box if one is close; returns (points, cost change)"""
    if not tees: return pts, 0.0
    t = pts[0]
    best = min(tees, key=lambda q: math.dist(q, t))
    if math.dist(best, t) > radius: return pts, 0.0
    new = [best] + pts[1:]
    return new, abs(_length(new) - length) * 0.3 - bonus


def candidates_for(green, par, length, fairways, fw_mask, cell, near_green=95.0, tees=None):
    """possible holes ending at `green`; each is (points tee->green, fairway id or None, base cost)"""
    out = []
    if par >= 4:
        for f in fairways:
            for line in (f["line"], f["line"][::-1]):
                gap = math.dist(line[-1], green)     # fairway end -> green
                if gap > near_green: continue
                path = [green] + line[::-1]          # green -> fairway, walking back towards the tee
                L = _length(path)
                cost = max(0.0, gap - 45) * 0.4
                if L > length + 25:
                    # fairway longer than the hole (shared / overlapping fairway): cut it
                    acc = 0.0; cut = [path[0]]
                    for p, q in zip(path[:-1], path[1:]):
                        d = math.dist(p, q)
                        if acc + d >= length:
                            t = (length - acc) / d; cut.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)); break
                        cut.append(q); acc += d
                    path = cut; cost += (L - length) * 0.15
                else:
                    ext = length - L                 # tee box -> start of fairway
                    a, b = path[-2], path[-1]
                    d = math.dist(a, b) or 1.0
                    # use the direction of the last ~40 m of fairway for the extension
                    k = max(0, len(path) - 4); a = path[k]; d = math.dist(a, b) or 1.0
                    tee = (b[0] + (b[0] - a[0]) / d * ext, b[1] + (b[1] - a[1]) / d * ext)
                    path = path + [tee]
                    lo, hi = (25, 230) if par == 4 else (25, 280)
                    if ext < lo: cost += (lo - ext) * 0.5
                    if ext > hi: cost += (ext - hi) * 0.4
                pts = _simplify(path[::-1])
                pts, dc = _snap_tee(pts, length, tees)
                out.append((pts, f.get("ids", frozenset([f["id"]])), cost + dc))
    # straight-line fallback (always for par 3, heavily penalised for par 4/5)
    for k in range(36):
        a = 2 * math.pi * k / 36
        tee = (green[0] + length * math.cos(a), green[1] + length * math.sin(a))
        pts = [tee, green]
        cover = _frac_on(fw_mask, cell, pts, 0.1, 0.9)
        if par == 3:
            cost = cover * 40     # a par 3 should not play down someone else's fairway
        else:
            cost = FALLBACK_BASE + (1 - _frac_on(fw_mask, cell, pts, 0.3, 0.92)) * FALLBACK_SLOPE
        pts, dc = _snap_tee(pts, length, tees, radius=25.0)
        out.append((pts, None, cost + dc))
    if par == 3 and tees:
        for t in tees:
            d = math.dist(t, green)
            if abs(d - length) > 30: continue
            pts = [t, green]
            out.append((pts, None, _frac_on(fw_mask, cell, pts, 0.1, 0.9) * 40 + abs(d - length) * 0.3 - 14))
    return out


def solve(greens, fairway, cell, card, start=None, end=None, tee_mask=None, beam=800, walk_free=50.0, walk_max=320.0,
          walk_weight=0.25, end_weight=0.4, unused_fairway_cost=25.0, unused_min_area=4000.0, turn=None):
    """turn = {hole_index: (x, y)}: after that hole the green should be near this point (e.g. {8: clubhouse})"""
    fws = fairway_lines(fairway, cell)
    tees = tee_points(tee_mask, cell) if tee_mask is not None else []
    cand = {}
    for gi, g in enumerate(greens):
        for hi, (par, L) in enumerate(card):
            cand[(gi, hi)] = candidates_for(g, par, L, fws, fairway, cell, tees=tees)
    states = [(0.0, frozenset(), frozenset(), [])]   # cost, used greens, used fairways, holes
    for hi, (par, L) in enumerate(card):
        nxt = []
        for cost, ug, uf, holes in states:
            prev = greens[holes[-1][0]] if holes else start
            for gi, g in enumerate(greens):
                if gi in ug: continue
                if prev is not None and math.dist(prev, g) > L + walk_max + 40: continue
                for pts, fid, c0 in cand[(gi, hi)]:
                    if fid is not None and fid in uf: continue
                    walk = math.dist(prev, pts[0]) if prev is not None else 0.0
                    if walk > walk_max: continue
                    c = c0 + max(0.0, walk - walk_free) * walk_weight
                    if turn and hi in turn: c += end_weight * max(0.0, math.dist(g, turn[hi]) - 80)
                    nxt.append((cost + c, ug | {gi}, uf | ({fid} if fid is not None else frozenset()),
                                holes + [(gi, pts, fid, round(walk))]))
        if not nxt:
            return None
        nxt.sort(key=lambda s: s[0])
        seen = {}; states = []
        for s in nxt:
            k = (s[3][-1][0], s[3][-1][2])
            if seen.get(k, 0) >= 6: continue
            seen[k] = seen.get(k, 0) + 1; states.append(s)
            if len(states) >= beam: break

    big = {f["id"] for f in fws if f["area"] >= unused_min_area}
    def final(s):
        c = s[0] + unused_fairway_cost * len(big - s[2])
        if end is not None: c += end_weight * max(0.0, math.dist(greens[s[3][-1][0]], end) - 80)
        return c
    states.sort(key=final)
    best = states[0]
    return {"cost": round(final(best), 1), "holes": [{"green": gi, "points": pts, "fairway": fid, "walk": w}
                                                    for gi, pts, fid, w in best[3]], "fairways": fws, "tees": tees,
            "unused_fairways": sorted(big - best[2]),
            "runner_up_gap": round(final(states[1]) - final(best), 1) if len(states) > 1 else None}


def refine(result, greens, fairway, cell, card, start=None, end=None, tee_mask=None, iters=60000, restarts=6,
           walk_free=50.0, walk_max=320.0, walk_weight=0.25, end_weight=0.4, unused_fairway_cost=25.0,
           unused_min_area=4000.0, turn=None, seed=1, fixed=None):
    """Simulated annealing over the whole routing, starting from solve()'s answer.
    The beam search fixes early holes first and can't undo them; this can."""
    import random
    rnd = random.Random(seed)
    fws = result["fairways"]; tees = result["tees"]
    big = {f["id"] for f in fws if f["area"] >= unused_min_area}
    C = {}
    for gi, g in enumerate(greens):
        for hi, (par, L) in enumerate(card):
            C[(gi, hi)] = candidates_for(g, par, L, fws, fairway, cell, tees=tees)
    N = len(card)

    def total(sol):
        c = 0.0; ug = {}; uf = {}
        prev = start
        for hi, (gi, ci) in enumerate(sol):
            pts, fid, c0 = C[(gi, hi)][ci]
            c += c0
            if prev is not None:
                w = math.dist(prev, pts[0])
                c += max(0.0, w - walk_free) * walk_weight + max(0.0, w - walk_max) * 2.0
            if turn and hi in turn: c += end_weight * max(0.0, math.dist(greens[gi], turn[hi]) - 80)
            ug[gi] = ug.get(gi, 0) + 1
            if fid is not None: uf[fid] = uf.get(fid, 0) + 1
            prev = greens[gi]
        if end is not None: c += end_weight * max(0.0, math.dist(prev, end) - 80)
        c += 400 * sum(v - 1 for v in ug.values()) + 400 * sum(v - 1 for v in uf.values())
        c += unused_fairway_cost * len(big - set(uf))
        return c

    def best_ci(gi, hi):
        return min(range(len(C[(gi, hi)])), key=lambda k: C[(gi, hi)][k][2])

    # initial solution from the beam result
    init = []
    for hi, h in enumerate(result["holes"]):
        gi = h["green"]
        ci = next((k for k, (p, f, c) in enumerate(C[(gi, hi)]) if f == h["fairway"] and math.dist(p[0], h["points"][0]) < 1), best_ci(gi, hi))
        if fixed and hi in fixed: gi = fixed[hi]; ci = best_ci(gi, hi)
        init.append((gi, ci))
    best = list(init); best_c = total(best)
    for r in range(restarts):
        if r % 2 == 1:   # odd restarts begin from a random routing so the search is not anchored to the beam answer
            sol = []
            for hi in range(N):
                gi = fixed[hi] if fixed and hi in fixed else rnd.randrange(len(greens))
                sol.append((gi, best_ci(gi, hi)))
        else:
            sol = list(best)
        cur = total(sol)
        T0 = 40.0
        for it in range(iters):
            T = T0 * (1 - it / iters) + 0.5
            new = list(sol)
            mv = rnd.random()
            if mv < 0.3:                      # smart re-pick: a green in reach of the previous one, best-fitting candidate
                hi = rnd.randrange(N)
                prevp = greens[new[hi - 1][0]] if hi > 0 else start
                gi = fixed[hi] if fixed and hi in fixed else rnd.randrange(len(greens))
                cs = C[(gi, hi)]
                if prevp is None: ci = best_ci(gi, hi)
                else: ci = min(range(len(cs)), key=lambda k: cs[k][2] + max(0.0, math.dist(prevp, cs[k][0][0]) - walk_free) * walk_weight)
                new[hi] = (gi, ci)
            elif mv < 0.55:                      # re-pick one hole (any green, any candidate)
                hi = rnd.randrange(N); gi = rnd.randrange(len(greens))
                if fixed and hi in fixed: gi = fixed[hi]
                new[hi] = (gi, rnd.randrange(len(C[(gi, hi)])))
            elif mv < 0.75:                   # another candidate for the same green
                hi = rnd.randrange(N); gi = new[hi][0]
                new[hi] = (gi, rnd.randrange(len(C[(gi, hi)])))
            else:                             # swap the greens of two holes with the same par
                a, b = rnd.randrange(N), rnd.randrange(N)
                if a == b or card[a][0] != card[b][0] or (fixed and (a in fixed or b in fixed)): continue
                ga, gb = new[a][0], new[b][0]
                new[a] = (gb, best_ci(gb, a)); new[b] = (ga, best_ci(ga, b))
            nc = total(new)
            if nc < cur or rnd.random() < math.exp((cur - nc) / T):
                sol, cur = new, nc
                if cur < best_c: best, best_c = list(sol), cur
    holes = []
    prev = start
    for hi, (gi, ci) in enumerate(best):
        pts, fid, c0 = C[(gi, hi)][ci]
        holes.append({"green": gi, "points": pts, "fairway": fid, "walk": round(math.dist(prev, pts[0])) if prev else 0})
        prev = greens[gi]
    used = {h["fairway"] for h in holes if h["fairway"] is not None}
    return {**result, "cost": round(best_c, 1), "holes": holes, "unused_fairways": sorted(big - used), "start_cost": round(total(init), 1)}


def _cross(p1, p2, q1, q2):
    """do segments p1-p2 and q1-q2 properly intersect?"""
    def o(a, b, c): return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    d1, d2, d3, d4 = o(q1, q2, p1), o(q1, q2, p2), o(p1, p2, q1), o(p1, p2, q2)
    return (d1 * d2 < 0) and (d3 * d4 < 0)


def _crossings(pts, others):
    n = 0
    for a, b in zip(pts[:-1], pts[1:]):
        for o in others:
            for c, d in zip(o[:-1], o[1:]):
                if _cross(a, b, c, d): n += 1
    return n


def solve_dp(greens, fairway, cell, card, start=None, end=None, tee_mask=None, beam=6000, per_green=3,
             walk_free=50.0, walk_max=320.0, walk_weight=0.25, end_weight=0.4, unused_fairway_cost=25.0,
             unused_min_area=4000.0, turn=None, fixed=None, cross_cost=45.0, green_cost=None):
    """Beam search with dominance pruning: of two partial routings that end on the same green and have
    used the same greens and fairways, only the cheaper can lead to the best answer, so the other is dropped.
    This lets a much wider beam fit in the same time."""
    fws = chain_fairways(fairway_lines(fairway, cell))
    tees = tee_points(tee_mask, cell) if tee_mask is not None else []
    big = frozenset(f["id"] for f in fws if f["area"] >= unused_min_area and len(f["ids"]) == 1)
    cand = {}
    for gi, g in enumerate(greens):
        for hi, (par, L) in enumerate(card):
            cand[(gi, hi)] = candidates_for(g, par, L, fws, fairway, cell, tees=tees)
    memo = {}
    def bbox(pts):
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)
    bb = {}
    def xmemo(gi, hi, ci, g2, h2, c2):
        k = (gi, hi, ci, g2, h2, c2)
        v = memo.get(k)
        if v is None:
            a = cand[(gi, hi)][ci][0]; b = cand[(g2, h2)][c2][0]
            ba = bb.get((gi, hi, ci)) or bb.setdefault((gi, hi, ci), bbox(a))
            bo = bb.get((g2, h2, c2)) or bb.setdefault((g2, h2, c2), bbox(b))
            v = 0 if (ba[2] < bo[0] or bo[2] < ba[0] or ba[3] < bo[1] or bo[3] < ba[1]) else _crossings(a, [b])
            memo[k] = v
        return v
    states = {(None, frozenset(), frozenset()): (0.0, ())}
    for hi, (par, L) in enumerate(card):
        nxt = {}
        for (last, ug, uf), (cost, holes) in states.items():
            prev = greens[last] if last is not None else start
            played = [(g2, h2, c2) for h2, (g2, c2) in enumerate(holes)] if cross_cost else []
            for gi, g in enumerate(greens):
                if gi in ug: continue
                if fixed and hi in fixed and gi != fixed[hi]: continue
                if prev is not None and math.dist(prev, g) > L + walk_max + 40: continue
                opts = []
                for ci, (pts, fid, c0) in enumerate(cand[(gi, hi)]):
                    if fid is not None and not fid.isdisjoint(uf): continue
                    walk = math.dist(prev, pts[0]) if prev is not None else 0.0
                    if walk > walk_max: continue
                    opts.append((c0 + max(0.0, walk - walk_free) * walk_weight, ci, fid))
                opts.sort()
                if played:   # holes don't cross each other: charge for every crossing (checked on the best few only)
                    opts = sorted((c + cross_cost * sum(xmemo(gi, hi, ci, *o) for o in played), ci, fid) for c, ci, fid in opts[:12])
                extra = end_weight * max(0.0, math.dist(g, turn[hi]) - 80) if turn and hi in turn else 0.0
                if green_cost: extra += green_cost[gi]
                seen_f = set()
                for c, ci, fid in opts:
                    if fid in seen_f: continue          # best option per fairway only
                    seen_f.add(fid)
                    key = (gi, ug | {gi}, uf | (fid if fid is not None else frozenset()))
                    nc = cost + c + extra
                    if key not in nxt or nc < nxt[key][0]:
                        nxt[key] = (nc, holes + ((gi, ci),))
                    if len(seen_f) >= per_green: break
        if not nxt: return None
        items = sorted(nxt.items(), key=lambda kv: kv[1][0])[:beam]
        states = dict(items)

    def final(k, v):
        c = v[0] + unused_fairway_cost * len(big - k[2])
        if end is not None: c += end_weight * max(0.0, math.dist(greens[k[0]], end) - 80)
        return c
    ranked = sorted(states.items(), key=lambda kv: final(*kv))
    (k, v) = ranked[0]
    holes = []; prev = start
    for hi, (gi, ci) in enumerate(v[1]):
        pts, fid, c0 = cand[(gi, hi)][ci]
        holes.append({"green": gi, "points": pts, "fairway": None if fid is None else "+".join(map(str, sorted(fid))), "walk": round(math.dist(prev, pts[0])) if prev is not None else 0})
        prev = greens[gi]
    return {"cost": round(final(k, v), 1), "holes": holes, "fairways": fws, "tees": tees,
            "unused_fairways": sorted(big - k[2]),
            "runner_up_gap": round(final(*ranked[1]) - final(k, v), 1) if len(ranked) > 1 else None}
