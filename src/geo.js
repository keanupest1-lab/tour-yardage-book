// Planar geometry helpers. All coordinates are [x, y] in metres (x = east, y = north)
// after projecting lon/lat with a local equirectangular projection (error < 0.1% over a course).

export const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
export const sub = (a, b) => [a[0] - b[0], a[1] - b[1]];
export const add = (a, b) => [a[0] + b[0], a[1] + b[1]];
export const mul = (a, k) => [a[0] * k, a[1] * k];
export const dot = (a, b) => a[0] * b[0] + a[1] * b[1];
export const norm = (a) => { const n = Math.hypot(a[0], a[1]) || 1; return [a[0] / n, a[1] / n]; };
export const perpR = (u) => [u[1], -u[0]]; // right-hand normal of a direction (x east, y north)

export function makeProjection(lat0, lon0) {
  // metres per degree on the WGS84 ellipsoid at the course's latitude (error well under 1 m over a course)
  const a = 6378137, e2 = 0.00669437999014, rad = Math.PI / 180, sn = Math.sin(lat0 * rad), w = 1 - e2 * sn * sn;
  const kx = (a / Math.sqrt(w)) * Math.cos(lat0 * rad) * rad;
  const ky = ((a * (1 - e2)) / Math.pow(w, 1.5)) * rad;
  return {
    fwd: ([lon, lat]) => [(lon - lon0) * kx, (lat - lat0) * ky],
    inv: ([x, y]) => [x / kx + lon0, y / ky + lat0],
  };
}

export function lineLength(L) {
  let s = 0;
  for (let i = 1; i < L.length; i++) s += dist(L[i - 1], L[i]);
  return s;
}

export function pointAlong(L, s) {
  let acc = 0;
  for (let i = 1; i < L.length; i++) {
    const d = dist(L[i - 1], L[i]);
    if (acc + d >= s) {
      const t = d ? (s - acc) / d : 0;
      return [L[i - 1][0] + (L[i][0] - L[i - 1][0]) * t, L[i - 1][1] + (L[i][1] - L[i - 1][1]) * t];
    }
    acc += d;
  }
  return L[L.length - 1];
}

function closestOnSeg(p, a, b) {
  const ab = sub(b, a);
  const L2 = dot(ab, ab);
  const t = L2 ? Math.max(0, Math.min(1, dot(sub(p, a), ab) / L2)) : 0;
  return { t, pt: [a[0] + ab[0] * t, a[1] + ab[1] * t] };
}

// distance along polyline of the closest point to p
export function projectOnLine(L, p) {
  let best = { d: Infinity, s: 0, pt: L[0], seg: 0 };
  let acc = 0;
  for (let i = 1; i < L.length; i++) {
    const { t, pt } = closestOnSeg(p, L[i - 1], L[i]);
    const d = dist(p, pt);
    const segLen = dist(L[i - 1], L[i]);
    if (d < best.d) best = { d, s: acc + t * segLen, pt, seg: i - 1 };
    acc += segLen;
  }
  return best;
}

export function tangentAt(L, s) {
  const a = pointAlong(L, Math.max(0, s - 3));
  const b = pointAlong(L, Math.min(lineLength(L), s + 3));
  return norm(sub(b, a));
}

export function pointInPoly(p, poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > p[1]) !== (yj > p[1]) && p[0] < ((xj - xi) * (p[1] - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

export function ptPolyDist(p, poly) {
  if (pointInPoly(p, poly)) return 0;
  let m = Infinity;
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i], b = poly[(i + 1) % poly.length];
    m = Math.min(m, dist(p, closestOnSeg(p, a, b).pt));
  }
  return m;
}

export function ptLineDist(p, L) { return projectOnLine(L, p).d; }

export function polyDist(P, Q) {
  let m = Infinity;
  for (const p of P) m = Math.min(m, ptPolyDist(p, Q));
  for (const q of Q) m = Math.min(m, ptPolyDist(q, P));
  return m;
}

export function centroid(poly) {
  let a = 0, cx = 0, cy = 0;
  for (let i = 0; i < poly.length; i++) {
    const [x0, y0] = poly[i], [x1, y1] = poly[(i + 1) % poly.length];
    const f = x0 * y1 - x1 * y0;
    a += f; cx += (x0 + x1) * f; cy += (y0 + y1) * f;
  }
  if (Math.abs(a) < 1e-9) {
    const n = poly.length;
    return [poly.reduce((s, p) => s + p[0], 0) / n, poly.reduce((s, p) => s + p[1], 0) / n];
  }
  return [cx / (3 * a), cy / (3 * a)];
}

export function polyArea(poly) {
  let a = 0;
  for (let i = 0; i < poly.length; i++) {
    const [x0, y0] = poly[i], [x1, y1] = poly[(i + 1) % poly.length];
    a += x0 * y1 - x1 * y0;
  }
  return Math.abs(a / 2);
}

// Parameters t in [0,1] where segment a->b crosses polygon edges
function segPolyTs(a, b, poly) {
  const ts = [];
  const r = sub(b, a);
  for (let i = 0; i < poly.length; i++) {
    const c = poly[i], d = poly[(i + 1) % poly.length];
    const s = sub(d, c);
    const den = r[0] * s[1] - r[1] * s[0];
    if (Math.abs(den) < 1e-12) continue;
    const ca = sub(c, a);
    const t = (ca[0] * s[1] - ca[1] * s[0]) / den;
    const u = (ca[0] * r[1] - ca[1] * r[0]) / den;
    if (t >= 0 && t <= 1 && u >= 0 && u <= 1) ts.push(t);
  }
  return ts;
}

// Pieces of segment a->b that lie inside poly, as [[p0,p1], ...] ordered from a
export function segmentInside(a, b, poly) {
  const ts = [0, ...segPolyTs(a, b, poly), 1].sort((x, y) => x - y);
  const out = [];
  const at = (t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
  for (let i = 1; i < ts.length; i++) {
    if (ts[i] - ts[i - 1] < 1e-9) continue;
    const mid = at((ts[i] + ts[i - 1]) / 2);
    if (pointInPoly(mid, poly)) {
      const last = out[out.length - 1];
      if (last && dist(last[1], at(ts[i - 1])) < 1e-6) last[1] = at(ts[i]);
      else out.push([at(ts[i - 1]), at(ts[i])]);
    }
  }
  return out;
}

// Same for a polyline: inside pieces as {s0, s1} distances along L
export function lineInside(L, poly) {
  const res = [];
  let acc = 0;
  for (let i = 1; i < L.length; i++) {
    const a = L[i - 1], b = L[i], len = dist(a, b);
    for (const [p0, p1] of segmentInside(a, b, poly)) {
      const s0 = acc + dist(a, p0), s1 = acc + dist(a, p1);
      const last = res[res.length - 1];
      if (last && Math.abs(last.s1 - s0) < 1e-6) last.s1 = s1; else res.push({ s0, s1 });
    }
    acc += len;
  }
  return res;
}

// First point along L (from the start, or from the end) at straight-line distance r from c
export function radialHit(c, r, L, fromEnd = false) {
  const segs = [];
  for (let i = 1; i < L.length; i++) segs.push([L[i - 1], L[i]]);
  if (fromEnd) segs.reverse();
  for (const [p, q] of fromEnd ? segs.map(([a, b]) => [b, a]) : segs) {
    const d = sub(q, p), f = sub(p, c);
    const A = dot(d, d), B = 2 * dot(f, d), C = dot(f, f) - r * r;
    const disc = B * B - 4 * A * C;
    if (A === 0 || disc < 0) continue;
    const sq = Math.sqrt(disc);
    const ts = [(-B - sq) / (2 * A), (-B + sq) / (2 * A)].filter((t) => t >= 0 && t <= 1).sort((x, y) => x - y);
    if (ts.length) return [p[0] + d[0] * ts[0], p[1] + d[1] * ts[0]];
  }
  return null;
}

export function circlePoly(c, r, n = 24) {
  const out = [];
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2;
    out.push([c[0] + r * Math.cos(a), c[1] + r * Math.sin(a)]);
  }
  return out;
}

export function bbox(points) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const [x, y] of points) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }
  return { x0, y0, x1, y1 };
}

// Grow a polygon outward by d metres (vertex offset along averaged normals; fine for collars/margins)
export function offsetPoly(poly, d) {
  const n = poly.length;
  const ccw = signedArea(poly) > 0;
  return poly.map((p, i) => {
    const a = poly[(i - 1 + n) % n], b = poly[(i + 1) % n];
    const t = norm(sub(b, a));
    let nrm = ccw ? [t[1], -t[0]] : [-t[1], t[0]];
    return [p[0] + nrm[0] * d, p[1] + nrm[1] * d];
  });
}

export function signedArea(poly) {
  let a = 0;
  for (let i = 0; i < poly.length; i++) {
    const [x0, y0] = poly[i], [x1, y1] = poly[(i + 1) % poly.length];
    a += x0 * y1 - x1 * y0;
  }
  return a / 2;
}
