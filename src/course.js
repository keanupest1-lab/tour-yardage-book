// Turns parsed OSM features into holes with every tour-book measurement.
// Distances are metres internally; the renderer converts to yards when asked.
import * as G from './geo.js';

const isPoly = (f) => f.line && f.closed;

function classify(f) {
  const t = f.tags;
  if (t.golf === 'hole' && f.line) return 'hole';
  if (t.golf === 'green' && isPoly(f)) return 'green';
  if (t.golf === 'tee' && isPoly(f)) return 'tee';
  if (t.golf === 'fairway' && isPoly(f)) return 'fairway';
  if ((t.golf === 'bunker' || t.natural === 'sand') && isPoly(f)) return 'bunker';
  if ((t.golf === 'water_hazard' || t.golf === 'lateral_water_hazard' || t.natural === 'water' || t.water) && isPoly(f)) return 'water';
  if ((t.natural === 'wood' || t.landuse === 'forest') && isPoly(f)) return 'wood';
  if (t.natural === 'tree' && f.point) return 'tree';
  if (t.natural === 'tree_row' && f.line) return 'treerow';
  if (t.golf === 'out_of_bounds' && f.line) return 'ob';
  if (t.leisure === 'golf_course' && isPoly(f)) return 'boundary';
  return null;
}

export function buildCourse(features) {
  const pts = features.flatMap((f) => (f.point ? [f.point] : f.line || []));
  if (!pts.length) throw new Error('No course features found here on OpenStreetMap.');
  const lon0 = pts.reduce((s, p) => s + p[0], 0) / pts.length;
  const lat0 = pts.reduce((s, p) => s + p[1], 0) / pts.length;
  const proj = G.makeProjection(lat0, lon0);

  const L = { hole: [], green: [], tee: [], fairway: [], bunker: [], water: [], wood: [], tree: [], ob: [], boundary: [] };
  for (const f of features) {
    const k = classify(f);
    if (!k) continue;
    if (k === 'tree') { L.tree.push({ c: proj.fwd(f.point), r: 5 }); continue; }
    if (k === 'treerow') {
      const line = f.line.map(proj.fwd); const len = G.lineLength(line);
      for (let s = 0; s <= len; s += 8) L.tree.push({ c: G.pointAlong(line, s), r: 4.5 });
      continue;
    }
    const geom = f.line.map(proj.fwd);
    const poly = f.closed ? geom.slice(0, -1) : geom;
    L[k].push({ id: f.id, tags: f.tags, geom: poly });
  }

  const holes = [], skipped = [];
  for (const h of L.hole) {
    try { holes.push(buildHole(h, L)); } catch (e) { skipped.push({ ref: h.tags.ref || '?', reason: e.message }); }
  }
  holes.sort((a, b) => (parseInt(a.ref) || 99) - (parseInt(b.ref) || 99));
  // Several courses in one area (e.g. St Andrews links): group holes by the named course boundary their tee sits in
  const named = L.boundary.filter((b) => b.tags.name);
  const groups = [];
  for (const b of named) {
    const idx = holes.map((h, i) => (G.pointInPoly(h.TEE, b.geom) ? i : -1)).filter((i) => i >= 0);
    if (idx.length) groups.push({ name: b.tags.name, holes: idx, area: G.polyArea(b.geom) });
  }
  // a tee inside two boundaries (outer estate + inner course) belongs to the smaller one
  for (const g of groups) g.holes = g.holes.filter((i) => !groups.some((o) => o !== g && o.area < g.area && o.holes.includes(i)));
  const grouped = new Set(groups.flatMap((g) => g.holes));
  const rest = holes.map((_, i) => i).filter((i) => !grouped.has(i));
  if (rest.length) groups.push({ name: groups.length ? 'Other holes' : 'All holes', holes: rest });
  const usable = groups.filter((g) => g.holes.length);
  const census = { holeLines: L.hole.length, greens: L.green.length, fairways: L.fairway.length, tees: L.tee.length, bunkers: L.bunker.length, water: L.water.length, trees: L.tree.length };
  return { proj, layers: L, holes, skipped, census, groups: usable.length > 1 ? usable : [], lat0, lon0 };
}

function nearest(list, p, maxD) {
  let best = null, bd = Infinity;
  for (const it of list) {
    const d = G.ptPolyDist(p, it.geom);
    if (d < bd) { bd = d; best = it; }
  }
  return bd <= maxD ? best : null;
}

function buildHole(h, L) {
  let line = h.geom.slice();
  // OSM convention is tee -> green; flip if the start sits on a green and the end does not
  const startGreen = nearest(L.green, line[0], 3), endGreen = nearest(L.green, line[line.length - 1], 3);
  if (startGreen && !endGreen) line.reverse();

  if (line.length < 2 || G.lineLength(line) < 60) throw new Error('hole line too short');
  let green = nearest(L.green, line[line.length - 1], 45);
  let greenEstimated = false;
  if (!green) {
    // No green mapped near the end of the hole line: assume a typical 28 x 24 m green centred 12 m past the line end
    const end = line[line.length - 1], dirE = G.norm(G.sub(end, line[line.length - 2]));
    const c = G.add(end, G.mul(dirE, 12)), side = G.perpR(dirE);
    const geom = [];
    for (let k = 0; k < 24; k++) {
      const a = (k / 24) * Math.PI * 2;
      geom.push(G.add(c, G.add(G.mul(dirE, 14 * Math.cos(a)), G.mul(side, 12 * Math.sin(a)))));
    }
    green = { id: 'est-' + (h.tags.ref || ''), tags: {}, geom, estimated: true };
    line = line.concat([c]);
    greenEstimated = true;
  }
  const GC = G.centroid(green.geom);
  const CL = line.slice(0, -1).concat([GC]);
  // drop points that sit inside the green (keeps the approach direction clean)
  while (CL.length > 2 && G.pointInPoly(CL[CL.length - 2], green.geom)) CL.splice(CL.length - 2, 1);
  const TEE = CL[0];

  // front of green along the line of play, back of green along the approach axis
  const last = CL[CL.length - 2];
  const U = G.norm(G.sub(GC, last));
  const V = G.perpR(U);
  const farBack = G.add(GC, G.mul(U, 80)), farFront = G.sub(GC, G.mul(U, 80));
  const through = G.segmentInside(farFront, farBack, green.geom);
  let GF, GB;
  if (through.length) {
    const seg = through.reduce((a, b) => (G.dist(a[0], GC) + G.dist(a[1], GC) < G.dist(b[0], GC) + G.dist(b[1], GC) ? a : b));
    [GF, GB] = seg;
  } else {
    GF = green.geom.reduce((a, b) => (G.dist(a, last) < G.dist(b, last) ? a : b));
    GB = green.geom.reduce((a, b) => (G.dist(a, last) > G.dist(b, last) ? a : b));
  }

  const corridor = (geom, w) => geom.some((p) => G.ptLineDist(p, CL) <= w) || G.ptPolyDist(CL[Math.floor(CL.length / 2)], geom) === 0;
  const tees = L.tee.filter((t) => G.ptPolyDist(TEE, t.geom) < 60);
  const fairways = L.fairway.filter((f) => G.lineInside(CL, f.geom).reduce((t, r) => t + (r.s1 - r.s0), 0) >= 25);
  const bunkers = L.bunker.filter((b) => corridor(b.geom, 45) || G.polyDist(b.geom, green.geom) < 25);
  const water = L.water.filter((w) => corridor(w.geom, 70));
  const trees = L.tree.filter((t) => G.ptLineDist(t.c, CL) < 75);
  const woods = L.wood.filter((w) => corridor(w.geom, 75));

  const lengthM = G.lineLength(CL);
  const par = parseInt(h.tags.par) || (lengthM < 230 ? 3 : lengthM < 430 ? 4 : 5);
  const tagDist = parseFloat(h.tags.dist);

  const hole = {
    ref: h.tags.ref || '?', par, si: h.tags.handicap || h.tags.stroke_index || '', name: h.tags.name || '',
    lengthM, tagDist: isFinite(tagDist) ? tagDist : null,
    CL, TEE, GC, GF, GB, U, V, green, tees, fairways, bunkers, water, trees, woods,
    warnings: [],
  };
  if (greenEstimated) hole.warnings.push('Green not mapped on OpenStreetMap: shown as an estimated oval at the end of the hole line. Green numbers are approximate.');
  if (!fairways.length && par > 3) hole.warnings.push('No fairway mapped on OpenStreetMap for this hole.');
  measure(hole);
  return hole;
}

// ---------- measurements ----------
export function measure(H) {
  const { CL, TEE, GF, GC, U, V } = H;
  const B = (p) => G.dist(TEE, p);          // from back tee
  const F = (p) => G.dist(p, GF);           // to front of green
  H.B = B; H.F = F;
  H.toFront = B(GF); H.toCentre = B(GC); H.toBack = B(H.GB);
  H.onLine = (r) => G.radialHit(TEE, r, CL, false);       // point on line of play r from tee
  H.fromFront = (r) => G.radialHit(GF, r, CL, true);      // point on line of play r short of the front

  const fwPolys = H.fairways.map((f) => f.geom);
  // perpendicular fairway width through a point on the line of play
  H.widthAt = (p) => {
    if (!p) return null;
    const s = G.projectOnLine(CL, p).s;
    const t = G.tangentAt(CL, s), n = G.perpR(t);
    const a = G.sub(p, G.mul(n, 90)), b = G.add(p, G.mul(n, 90));
    let best = null;
    for (const poly of fwPolys) {
      for (const seg of G.segmentInside(a, b, poly)) {
        const along = G.dist(seg[0], p) + G.dist(p, seg[1]) - G.dist(seg[0], seg[1]);
        const d = Math.min(G.dist(seg[0], p), G.dist(seg[1], p));
        if (along > 1 && d > 12) continue;          // that fairway piece is beside, not under, the line of play
        const score = along <= 1 ? 0 : d;
        if (!best || score < best.score) best = { score, seg };
      }
    }
    if (!best) return null;
    const [p0, p1] = best.seg;
    const side = (q) => G.dot(G.sub(q, p), n);
    const left = side(p0) < side(p1) ? p0 : p1, right = left === p0 ? p1 : p0;
    return { width: G.dist(p0, p1), left, right };
  };

  // straight-line run-out from the tee through an aim point
  H.runout = (aim) => {
    if (!aim) return null;
    const dir = G.norm(G.sub(aim, TEE));
    const end = G.add(TEE, G.mul(dir, 450));
    let best = null;
    for (const poly of fwPolys) {
      for (const seg of G.segmentInside(TEE, end, poly)) {
        const containsAim = G.dist(seg[0], TEE) <= B(aim) + 1 && G.dist(seg[1], TEE) >= B(aim) - 1;
        if (containsAim || !best) best = { entry: seg[0], exit: seg[1], containsAim };
        if (containsAim) break;
      }
    }
    return best;
  };

  // carry to the start of the fairway along the line of play
  const insideRuns = fwPolys.flatMap((poly) => G.lineInside(CL, poly)).sort((a, b) => a.s0 - b.s0);
  H.fairwayStart = insideRuns.length ? G.pointAlong(CL, insideRuns[0].s0) : null;
  H.fairwayEnd = insideRuns.length ? G.pointAlong(CL, insideRuns[insideRuns.length - 1].s1) : null;
  H.throatOffset = H.fairwayEnd ? F(H.fairwayEnd) : null;
  H.throat = H.throatOffset != null ? H.widthAt(H.fromFront(H.throatOffset + 3)) : null;

  // bunkers: entry, carry, left and right flank (never a centre number)
  const sideOf = (q) => G.dot(G.sub(q, GC), V);
  H.bunkerPts = H.bunkers.map((b) => {
    const g = b.geom;
    const entry = g.reduce((a, c) => (B(c) < B(a) ? c : a));
    const carry = g.reduce((a, c) => (B(c) > B(a) ? c : a));
    const left = g.reduce((a, c) => (sideOf(c) < sideOf(a) ? c : a));
    const right = g.reduce((a, c) => (sideOf(c) > sideOf(a) ? c : a));
    return { geom: g, entry, carry, left, right, greenside: G.polyDist(g, H.green.geom) < 25 };
  });

  // water: near edge and far bank (from the tee); if crossing the line, the crossing points
  H.waterPts = H.water.map((w) => {
    const g = w.geom;
    const runs = G.lineInside(CL, g);
    if (runs.length) {
      const r0 = runs[0], r1 = runs[runs.length - 1];
      return { geom: g, crosses: true, near: G.pointAlong(CL, r0.s0), far: G.pointAlong(CL, r1.s1) };
    }
    return { geom: g, crosses: false, near: g.reduce((a, c) => (B(c) < B(a) ? c : a)), far: g.reduce((a, c) => (B(c) > B(a) ? c : a)) };
  });

  // green geometry relative to its centre: along = +back, side = +right
  H.along = (p) => G.dot(G.sub(p, GC), U);
  H.side = (p) => G.dot(G.sub(p, GC), V);
  const edge = (dir) => {
    const far = G.add(GC, G.mul(dir, 90));
    const segs = G.segmentInside(GC, far, H.green.geom);
    return segs.length ? segs[segs.length - 1][1] : GC;
  };
  H.greenEdges = { F: edge(G.mul(U, -1)), B: edge(U), L: edge(G.mul(V, -1)), R: edge(V) };
  H.greenDepth = G.dist(H.greenEdges.F, H.greenEdges.B);
  H.greenWidth = G.dist(H.greenEdges.L, H.greenEdges.R);
}
