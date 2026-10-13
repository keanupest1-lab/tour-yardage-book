// Terrain heights for a saved course (courses/terrain/<slug>.json, built by the terrain job from open elevation data).
// Gives heights on the course's planar coordinates and contour lines for the book pages.

export async function loadTerrain(slug, proj) {
  let j;
  try { const r = await fetch(`courses/terrain/${slug}.json`); if (!r.ok) return null; j = await r.json(); } catch { return null; }
  const bin = atob(j.data), n = bin.length / 2;
  const q = new Uint16Array(n);
  for (let i = 0; i < n; i++) q[i] = bin.charCodeAt(2 * i) | (bin.charCodeAt(2 * i + 1) << 8);
  const { nx, ny, base, scale, dlat, dlon } = j;
  const north = j.n, west = j.w;
  // height at a course point (metres), bilinear; null outside the grid
  const at = (p) => {
    const [lon, lat] = proj.inv(p);
    const fy = (north - lat) / dlat, fx = (lon - west) / dlon;
    if (!(fx >= 0 && fy >= 0 && fx < nx - 1 && fy < ny - 1)) return null;
    const x0 = Math.floor(fx), y0 = Math.floor(fy), tx = fx - x0, ty = fy - y0, k = y0 * nx + x0;
    const v = q[k] * (1 - tx) * (1 - ty) + q[k + 1] * tx * (1 - ty) + q[k + nx] * (1 - tx) * ty + q[k + nx + 1] * tx * ty;
    return base + v * scale;
  };
  return { at, source: j.source };
}

// Marching squares over a sampled grid: Z[r][c] heights at screen points X0 + c*step, Y0 + r*step.
// Returns [{level, d}] with an SVG path per level.
export function contourPaths(Z, X0, Y0, step, interval) {
  const rows = Z.length, cols = Z[0].length;
  let lo = Infinity, hi = -Infinity;
  for (const r of Z) for (const v of r) if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  if (!isFinite(lo)) return [];
  const out = [];
  const f = (n) => Math.round(n * 10) / 10;
  for (let L = Math.ceil(lo / interval) * interval; L <= hi; L += interval) {
    let d = '';
    for (let r = 0; r < rows - 1; r++) {
      for (let c = 0; c < cols - 1; c++) {
        const a = Z[r][c], b = Z[r][c + 1], e = Z[r + 1][c + 1], g = Z[r + 1][c];
        if (a == null || b == null || e == null || g == null) continue;
        const idx = (a > L ? 8 : 0) | (b > L ? 4 : 0) | (e > L ? 2 : 0) | (g > L ? 1 : 0);
        if (idx === 0 || idx === 15) continue;
        const x = X0 + c * step, y = Y0 + r * step;
        const top = [x + step * (L - a) / (b - a), y], right = [x + step, y + step * (L - b) / (e - b)];
        const bot = [x + step * (L - g) / (e - g), y + step], left = [x, y + step * (L - a) / (g - a)];
        const segs = {
          1: [[left, bot]], 2: [[bot, right]], 3: [[left, right]], 4: [[top, right]], 5: [[left, top], [bot, right]],
          6: [[top, bot]], 7: [[left, top]], 8: [[left, top]], 9: [[top, bot]], 10: [[top, right], [left, bot]],
          11: [[top, right]], 12: [[left, right]], 13: [[bot, right]], 14: [[left, bot]],
        }[idx];
        for (const [p, q] of segs) d += `M${f(p[0])},${f(p[1])}L${f(q[0])},${f(q[1])}`;
      }
    }
    if (d) out.push({ level: L, d });
  }
  return out;
}
