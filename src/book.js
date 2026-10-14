// Tour-book pages in the classic tour style (green on top, the hole below; fairways outlined twice,
// ⊗ marks on the fairway edges and in the rough with dotted leaders to the margin numbers, soft tree canopies,
// faint contours, carry arcs, run-out and carry footer, small hole inset).
// Each hole gives 1 or 2 pocket pages (4" x 7" = 101.6 x 177.8 mm; 1 unit = 0.1 mm):
//   approach page : green at up to 1:480 with its grid, then the last ~200 m to the green at a large scale
//   tee page      : the tee shot from the back tee (par 4 and 5), at the largest scale that fits
// A par 3 is one page: the green, and the whole hole below it.
import * as G from './geo.js';
import { contourPaths } from './terrain.js';

export const PAGE_W = 1016, PAGE_H = 1778;
const W = PAGE_W, PH = PAGE_H;
const FONT = "font-family=\"'Helvetica Neue','Inter',Arial,sans-serif\"";
const C = {
  ink: '#1d1d1d', soft: '#555', grey: '#8a8f94', grid: '#dfe3e5', blue: '#2a74c9', gold: '#b8922c', red: '#c9372c',
  fwEdge: '#5f6265', cut: '#62b05a', sandF: '#e9d4ad', sandL: '#c09f6c', water: '#dcecf8', waterL: '#5e9fd6',
  contour: '#d4d4d4', contourMaj: '#c2c2c2', tee: '#1d1d1d',
};
const RING_COLS = { 200: '#2a74c9', 225: '#55a84a', 250: '#c9372c', 275: '#e0a91f', 300: '#8f5cbf', 325: '#8f5cbf' };
const ringCol = (i) => ['#55a84a', '#c9372c', '#e0a91f', '#8f5cbf', '#2a74c9'][i % 5];

const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
const f1 = (n) => (Math.round(n * 10) / 10).toString();
const pts = (P) => P.map((p) => `${f1(p[0])},${f1(p[1])}`).join(' ');

function txt(x, y, s, size, col = C.ink, anchor = 'middle', w = 400, halo = true) {
  const base = `x="${f1(x)}" y="${f1(y)}" text-anchor="${anchor}" ${FONT} font-weight="${w}" font-size="${size}"`;
  const back = halo ? `<text ${base} fill="#fff" stroke="#fff" stroke-width="${Math.max(4, size * 0.26)}" stroke-linejoin="round">${esc(s)}</text>` : '';
  return back + `<text ${base} fill="${col}">${esc(s)}</text>`;
}
function smoothPath(P, closed = true) {
  const n = P.length;
  if (n < 3) return `M${pts(P).replace(/ /g, ' L')}`;
  let d = `M${f1(P[0][0])},${f1(P[0][1])} `;
  const segs = closed ? n : n - 1;
  for (let i = 0; i < segs; i++) {
    const p0 = P[(i - 1 + n) % n], p1 = P[i], p2 = P[(i + 1) % n], p3 = P[(i + 2) % n];
    const q0 = closed || i > 0 ? p0 : p1, q3 = closed || i + 2 < n ? p3 : p2;
    const c1 = [p1[0] + (p2[0] - q0[0]) / 6, p1[1] + (p2[1] - q0[1]) / 6];
    const c2 = [p2[0] - (q3[0] - p1[0]) / 6, p2[1] - (q3[1] - p1[1]) / 6];
    d += `C${f1(c1[0])},${f1(c1[1])} ${f1(c2[0])},${f1(c2[1])} ${f1(p2[0])},${f1(p2[1])} `;
  }
  return closed ? d + 'Z' : d;
}
const path = (P, attrs, closed = true) => `<path d="${smoothPath(P, closed)}" ${attrs}/>`;

class Occ {
  constructor() { this.b = []; }
  free(b) { return !this.b.some((o) => !(b[2] < o[0] || b[0] > o[2] || b[3] < o[1] || b[1] > o[3])); }
  take(b) { this.b.push(b); }
  static box(x, y, s, size, anchor) {
    const w = String(s).length * size * 0.56;
    const x0 = anchor === 'end' ? x - w : anchor === 'middle' ? x - w / 2 : x;
    return [x0 - 2, y - size * 0.82, x0 + w + 2, y + size * 0.18];
  }
}

const unitInfo = (units) => (units === 'yd' ? { k: 1.0936133, label: 'Yds', grid: 4.572 } : { k: 1, label: 'Mts', grid: 5 });

// a page view: O at (ox, oy), direction u pointing up the page, s units per metre
function view(O, u, s, ox, oy) {
  const v = G.perpR(u);
  return {
    O, u, v, s,
    tf: (p) => { const d = G.sub(p, O); return [ox + G.dot(d, v) * s, oy - G.dot(d, u) * s]; },
    inv: (x, y) => G.add(O, G.add(G.mul(v, (x - ox) / s), G.mul(u, (oy - y) / s))),
  };
}

function compass(x, y, deg, r = 26) {
  return `<g transform="translate(${f1(x)},${f1(y)}) rotate(${f1(deg)})">
    <path d="M0,${-r * 1.5} L${r * 0.28},0 L0,${r * 0.35} L${-r * 0.28},0 Z" fill="#7a8088"/>
    <path d="M0,${r * 1.5} L${r * 0.28},0 L0,${-r * 0.35} L${-r * 0.28},0 Z" fill="#c7cbcf"/>
    <path d="M${-r * 1.1},0 H${r * 1.1} M0,${-r * 0.9} V${r * 0.9}" stroke="#9aa0a5" stroke-width="1.6"/>
    <text x="0" y="${-r * 1.62}" text-anchor="middle" ${FONT} font-size="${r * 0.6}" fill="#555" transform="rotate(${f1(-deg)} 0 ${-r * 1.8})">N</text></g>`;
}

// ⊗ mark
const xmark = (x, y, r = 7, col = '#666') => `<circle cx="${f1(x)}" cy="${f1(y)}" r="${r}" fill="#fff" stroke="${col}" stroke-width="1.4"/><path d="M${f1(x - r * 0.7)},${f1(y - r * 0.7)} L${f1(x + r * 0.7)},${f1(y + r * 0.7)} M${f1(x - r * 0.7)},${f1(y + r * 0.7)} L${f1(x + r * 0.7)},${f1(y - r * 0.7)}" stroke="${col}" stroke-width="1.2"/>`;
// centre markers (the plates on the line of play): yellow plus, red dot, yellow dot, red cross in turn
function marker(x, y, i) {
  const k = i % 4, col = k === 0 || k === 2 ? '#e8b81e' : C.red;
  if (k === 1 || k === 2) return `<circle cx="${f1(x)}" cy="${f1(y)}" r="10" fill="${col}" stroke="${k === 2 ? '#b68a0c' : '#8e2018'}" stroke-width="1"/>`;
  return `<path d="M${f1(x - 11)},${f1(y)} H${f1(x + 11)} M${f1(x)},${f1(y - 11)} V${f1(y + 11)}" stroke="${col}" stroke-width="6" stroke-linecap="round"/>`;
}

const defs = () => `<defs><radialGradient id="tree" cx="50%" cy="50%" r="50%">
  <stop offset="0%" stop-color="#9bbb78"/><stop offset="40%" stop-color="#b8cfa0"/><stop offset="100%" stop-color="#fafcf7"/></radialGradient>
  <radialGradient id="sand" cx="45%" cy="40%" r="60%"><stop offset="0%" stop-color="#f1dfbd"/><stop offset="100%" stop-color="#ddc192"/></radialGradient>
  <filter id="sh" x="-20%" y="-20%" width="140%" height="140%"><feDropShadow dx="2" dy="3" stdDeviation="4" flood-opacity=".25"/></filter></defs>`;

// ---------------------------------------------------------------- the map layer
function drawMap(o, H, course, V, clip, id, opts = {}) {
  const [x0, y0, x1, y1] = clip;
  const inClip = (geom, pad = 60) => geom.some((p) => { const [x, y] = V.tf(p); return x > x0 - pad && x < x1 + pad && y > y0 - pad && y < y1 + pad; });
  o.push(`<clipPath id="${id}"><rect x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}"/></clipPath><g clip-path="url(#${id})">`);
  // contours, 1 m (heavier every 5 m)
  if (course.terrain && opts.contours !== false) {
    const st = 12, Z = [];
    for (let y = y0; y <= y1 + st; y += st) { const row = []; for (let x = x0; x <= x1 + st; x += st) row.push(course.terrain.at(V.inv(x, y))); Z.push(row); }
    for (const { level, d } of contourPaths(Z, x0, y0, st, 1)) {
      const maj = Math.round(level) % 5 === 0;
      o.push(`<path d="${d}" fill="none" stroke="${maj ? C.contourMaj : C.contour}" stroke-width="${maj ? 1.5 : 1}" stroke-linecap="round"/>`);
    }
  }
  // tree canopies: soft, darker at the trunk
  const trees = H.trees.filter((t) => inClip([t.c], 40)).map((t) => { const [x, y] = V.tf(t.c); return { x, y, r: Math.max(t.r * V.s, 7) }; }).sort((a, b) => b.r - a.r);
  if (opts.pdf) {   // PDF: the gradient as rings of flat colour (radial gradients don't survive the conversion well)
    for (const [k, col] of [[1.08, '#e7efdd'], [0.82, '#d2e0c2'], [0.58, '#bcd1a5'], [0.34, '#a6c28a']]) for (const t of trees) o.push(`<circle cx="${f1(t.x)}" cy="${f1(t.y)}" r="${f1(t.r * k)}" fill="${col}"/>`);
  } else for (const t of trees) o.push(`<circle cx="${f1(t.x)}" cy="${f1(t.y)}" r="${f1(t.r * 1.08)}" fill="url(#tree)"/>`);
  for (const t of trees) if (t.r > 14) o.push(`<circle cx="${f1(t.x)}" cy="${f1(t.y)}" r="${f1(Math.min(2.6, t.r * 0.1))}" fill="#5d7d45"/>`);
  for (const w of H.water.filter((q) => inClip(q.geom))) {
    o.push(path(w.geom.map(V.tf), `fill="${C.water}" stroke="${C.waterL}" stroke-width="2"`));
    o.push(path(G.offsetPoly(w.geom, 1.2).map(V.tf), `fill="none" stroke="${C.red}" stroke-width="1.4" stroke-dasharray="9 6"`));
  }
  // fairways: the first cut (green) outside, the fairway edge (grey) inside
  const cutW = Math.max(8, 2 * 2.4 * V.s);   // the first cut: a green line ~2.4 m outside the fairway edge
  for (const f of H.fairways) o.push(path(f.geom.map(V.tf), `fill="#fff" stroke="${C.cut}" stroke-width="${f1(cutW + 2)}" stroke-linejoin="round"`));
  for (const f of H.fairways) o.push(path(f.geom.map(V.tf), `fill="#fff" stroke="#fff" stroke-width="${f1(cutW - 2)}" stroke-linejoin="round"`));
  for (const f of H.fairways) o.push(path(f.geom.map(V.tf), `fill="#fff" stroke="${C.fwEdge}" stroke-width="2.2"`));
  for (const t of H.tees) o.push(path(t.geom.map(V.tf), `fill="#fff" stroke="${C.tee}" stroke-width="2.2"`));
  const colW = Math.max(8, 2 * 2.0 * V.s);
  o.push(path(H.green.geom.map(V.tf), `fill="#fff" stroke="${C.cut}" stroke-width="${f1(colW + 2)}" stroke-linejoin="round"`));
  o.push(path(H.green.geom.map(V.tf), `fill="#fff" stroke="#fff" stroke-width="${f1(colW - 2)}" stroke-linejoin="round"`));
  o.push(path(H.green.geom.map(V.tf), `fill="#fff" stroke="${C.ink}" stroke-width="2.6"`));
  for (const b of H.bunkers) o.push(path(b.geom.map(V.tf), `fill="url(#sand)" stroke="${C.sandL}" stroke-width="1.6"`));
  o.push('</g>');
}

// ---------------------------------------------------------------- yardage annotations
function annot(H, course, V, U, occ, o, clip) {
  const n = (m) => Math.round(m * U.k), toM = (v) => v / U.k;
  const [cx0, cy0, cx1, cy1] = clip;
  const inside = ([x, y], pad = 0) => x > cx0 + pad && x < cx1 - pad && y > cy0 + pad && y < cy1 - pad;
  const elevAt = (p) => { if (!course.terrain) return null; const a = course.terrain.at(p), g = course.terrain.at(H.GC); return a == null || g == null ? null : Math.round((a - g) * U.k); };
  const showBlue = (p) => H.B(p) <= toM(380);
  return {
    n, toM, inside, elevAt,
    // fairway-edge ⊗ with its number, and a rough ⊗ further out with a dotted leader to the margin number
    row(p) {
      if (!p) return;
      const w = H.widthAt(p);
      const t = G.tangentAt(H.CL, G.projectOnLine(H.CL, p).s), nrm = G.perpR(t);
      const edges = w ? [[w.left, -1], [w.right, 1]] : [[G.add(p, G.mul(nrm, -14)), -1], [G.add(p, G.mul(nrm, 14)), 1]];
      for (const [e, side] of edges) {
        const [ex, ey] = V.tf(e);
        if (!inside([ex, ey], 20)) continue;
        if (w) {
          const lab = `${n(H.F(e))}`, ax = ex - side * 12, an = side < 0 ? 'start' : 'end';
          const b = Occ.box(ax, ey + 8, lab, 22, an);
          if (occ.free(b)) { occ.take(b); o.push(xmark(ex, ey, 6.5)); o.push(txt(ax, ey + 8, lab, 22, C.soft, an, 400)); }
        }
        // rough point ~20 m outside the edge (towards the page side)
        let q = G.add(e, G.mul(V.v, side * 20));
        let [qx, qy] = V.tf(q);
        if (!inside([qx, qy], 70)) { qx = side < 0 ? Math.max(cx0 + 140, Math.min(qx, ex - 30)) : Math.min(cx1 - 140, Math.max(qx, ex + 30)); q = V.inv(qx, qy); }
        if (side < 0 ? qx > ex - 20 : qx < ex + 20) continue;
        const mx = side < 0 ? cx0 + 20 : cx1 - 20, an = side < 0 ? 'start' : 'end';
        const lab = `${n(H.F(q))}`, el = elevAt(q), blue = showBlue(q) ? `${n(H.B(q))}` : null;
        const b = Occ.box(mx, qy + 10, lab, 30, an);
        const bb = [b[0], b[1] - 16, b[2], b[3] + (blue ? 22 : 0)];
        if (!occ.free(bb)) continue;
        occ.take(bb);
        const lx0 = side < 0 ? b[2] + 6 : qx + 9, lx1 = side < 0 ? qx - 9 : b[0] - 6;
        o.push(`<line x1="${f1(lx0)}" y1="${f1(qy)}" x2="${f1(lx1)}" y2="${f1(qy)}" stroke="#777" stroke-width="1.3" stroke-dasharray="2 4"/>`);
        o.push(xmark(qx, qy, 6.5));
        o.push(txt(mx, qy + 10, lab, 30, C.ink, an, 400));
        if (el != null) o.push(txt(side < 0 ? mx + 2 : mx - 2, qy - 14, `${el}`, 15, C.grey, an, 400, false));
        if (blue) o.push(txt(mx, qy + 30, blue, 18, C.blue, an, 400));
      }
    },
    // centre plate: marker, number to the front, from the tee in blue, height against the green in grey
    centre(p, i) {
      if (!p) return;
      const [x, y] = V.tf(p);
      if (!inside([x, y], 30)) return;
      const lab = `${n(H.F(p))}`, blue = showBlue(p) ? `${n(H.B(p))}` : null, el = elevAt(p);
      o.push(marker(x, y, i));
      o.push(txt(x + 16, y + 12, lab, 34, C.ink, 'start', 400));
      if (blue) o.push(txt(x + 18, y + 34, blue, 18, C.blue, 'start', 400));
      if (el != null) o.push(txt(x + 6, y - 14, `${el}`, 14, C.grey, 'start', 400, false));
      occ.take([x - 14, y - 30, x + 16 + lab.length * 20, y + 38]);
    },
    // carry arcs from the back tee: label at the page edge, fairway width at the fairway edge, ticks on the edges
    rings(list) {
      list.forEach((rv, i) => {
        const r = toM(rv); if (r > H.toFront - 15) return;
        const col = RING_COLS[rv] || ringCol(i), d0 = G.norm(G.sub(H.GC, H.TEE));
        const arc = [];
        for (let a = -80; a <= 80; a += 1) {
          const t = (a * Math.PI) / 180;
          arc.push(V.tf(G.add(H.TEE, G.mul([d0[0] * Math.cos(t) - d0[1] * Math.sin(t), d0[0] * Math.sin(t) + d0[1] * Math.cos(t)], r))));
        }
        const vis = arc.filter((p) => inside(p, 0));
        if (vis.length < 2) return;
        o.push(`<polyline points="${pts(arc)}" fill="none" stroke="${col}" stroke-width="1.6" opacity=".8" clip-path="url(#cl${clip.join('')})"/>`);
        const left = vis.reduce((a, b) => (b[0] < a[0] ? b : a));
        if (left[0] < cx0 + 40) { const b = Occ.box(cx0 + 6, left[1] + 6, `-${rv}`, 18, 'start'); if (occ.free(b)) { occ.take(b); o.push(txt(cx0 + 6, left[1] + 6, `-${rv}`, 18, col, 'start', 400)); } }
        const p = H.onLine(r); const w = p && H.widthAt(p);
        if (w) {
          for (const e of [w.left, w.right]) { const [x, y] = V.tf(e); if (inside([x, y])) o.push(`<circle cx="${f1(x)}" cy="${f1(y)}" r="3.5" fill="${col}"/>`); }
          const [x, y] = V.tf(w.right);
          const b = Occ.box(x + 10, y - 6, `${n(w.width)}`, 18, 'start');
          if (inside([x, y]) && occ.free(b)) { occ.take(b); o.push(txt(x + 10, y - 6, `${n(w.width)}`, 18, col, 'start', 400)); }
        }
      });
    },
    hazards() {
      const tag = (p, main, sub, dx, dy, an) => {
        const [x, y] = V.tf(p); if (!inside([x, y], 20)) return;
        for (const [ddx, ddy, a] of [[dx, dy, an], [-dx, dy, an === 'end' ? 'start' : 'end'], [dx, dy + 28, an]]) {
          const b1 = Occ.box(x + ddx, y + ddy, main ?? sub, 24, a), b2 = sub && main != null ? Occ.box(x + ddx, y + ddy + 20, sub, 17, a) : b1;
          const b = [Math.min(b1[0], b2[0]), b1[1], Math.max(b1[2], b2[2]), b2[3]];
          if (!occ.free(b)) continue;
          occ.take(b);
          if (main != null) o.push(txt(x + ddx, y + ddy, main, 24, C.ink, a, 400));
          if (sub) o.push(txt(x + ddx, y + ddy + (main != null ? 20 : 0), sub, main != null ? 17 : 19, C.blue, a, 400));
          return;
        }
      };
      for (const b of H.bunkerPts) {
        tag(b.entry, `${n(H.F(b.entry))}`, b.greenside ? null : `${n(H.B(b.entry))}`, -12, 10, 'end');
        if (!b.greenside) tag(b.carry, null, `${n(H.B(b.carry))}`, 12, -6, 'start');
      }
      for (const w of H.waterPts) {
        tag(w.near, `${n(H.F(w.near))}`, `${n(H.B(w.near))}`, -12, 10, 'end');
        tag(w.far, `${n(H.F(w.far))}`, `${n(H.B(w.far))}`, 12, -4, 'start');
      }
    },
  };
}

// the green, magnified (top of the approach page)
function greenSection(o, H, course, U, top, bottom) {
  const n = (m) => Math.round(m * U.k);
  const depth = H.greenDepth, width = H.greenWidth;
  const kMax = 1000 / 480 * 10;
  const notes = top + 120;
  const k = Math.min(kMax, (bottom - notes - 60) / (depth + 14), 820 / (width + 24));
  const front = H.along(H.greenEdges.F);
  const cx = W / 2, frontY = bottom - 46;
  const V = view(H.GC, H.U, k, cx, frontY - (-front) * k);
  const g = U.grid * k;
  // grid over the whole section
  o.push(`<rect x="0" y="${top}" width="${W}" height="${bottom - top}" fill="#fff"/>`);
  for (let x = cx % g; x < W; x += g) o.push(`<line x1="${f1(x)}" y1="${top}" x2="${f1(x)}" y2="${bottom}" stroke="${C.grid}" stroke-width="1.3"/>`);
  for (let y = frontY; y > top; y -= g) o.push(`<line x1="0" y1="${f1(y)}" x2="${W}" y2="${f1(y)}" stroke="${C.grid}" stroke-width="1.3"/>`);
  for (let y = frontY + g; y < bottom; y += g) o.push(`<line x1="0" y1="${f1(y)}" x2="${W}" y2="${f1(y)}" stroke="${C.grid}" stroke-width="1.3"/>`);
  const clip = [0, notes, W, bottom];
  const id = `gs${String(H.ref).replace(/\W/g, '')}`;
  o.push(`<rect x="0" y="${notes}" width="${W}" height="${bottom - notes}" fill="#fbfcfa"/>`);
  drawMap(o, H, course, V, clip, id, { contours: false, pdf: U.pdf });
  // the grid again, faint, over the fills
  o.push(`<g opacity=".55">`);
  for (let x = cx % g; x < W; x += g) o.push(`<line x1="${f1(x)}" y1="${notes}" x2="${f1(x)}" y2="${bottom}" stroke="${C.grid}" stroke-width="1.1"/>`);
  for (let y = frontY; y > notes; y -= g) o.push(`<line x1="0" y1="${f1(y)}" x2="${W}" y2="${f1(y)}" stroke="${C.grid}" stroke-width="1.1"/>`);
  o.push('</g>');
  // centre line front -> back, depth at the back
  const [fx, fy] = V.tf(H.greenEdges.F), [bx, by] = V.tf(H.greenEdges.B);
  o.push(`<line x1="${f1(fx)}" y1="${f1(fy)}" x2="${f1(bx)}" y2="${f1(by)}" stroke="${C.red}" stroke-width="1.4" opacity=".7"/>`);
  o.push(`<circle cx="${f1(bx)}" cy="${f1(by)}" r="6" fill="#e8b81e" stroke="#a07a0a"/>`);
  o.push(txt(bx, by - 14, n(depth), 32, C.red, 'middle', 400));
  // depths round the edge
  const ring = [...H.green.geom, H.green.geom[0]], total = G.lineLength(ring), occ = new Occ();
  occ.take([bx - 36, by - 46, bx + 36, by]);
  for (let i = 0; i < 18; i++) {
    const p = G.pointAlong(ring, (total * i) / 18), a = H.along(p) - front;
    if (a < 2 || a > depth - 2) continue;
    const [x, y] = V.tf(p), sd = H.side(p) > 0 ? 1 : -1;
    const lx = x - sd * 22, ly = y + 6, s = n(a), b = Occ.box(lx, ly, s, 17, 'middle');
    if (!occ.free(b)) continue; occ.take(b); o.push(txt(lx, ly, s, 17, '#444', 'middle', 400, false));
  }
  // depth axis (blue) from the front
  for (let j = 0; j * U.grid <= depth + 12; j++) {
    const y = frontY - j * U.grid * k;
    if (y < notes + 14) break;
    o.push(txt(40, y + 8, j * 5, 24, '#2f4fa0', 'middle', 400));
  }
  o.push(txt(W - 40, notes + 54, `Par ${H.par}`, 36, C.gold, 'end', 400));
  o.push(txt(W - 40, notes + 92, `${n(H.tagDist || H.playCentre)} ${U.label}`, 36, C.gold, 'end', 400));
  o.push(compass(W - 92, notes + 172, -Math.atan2(H.U[0], H.U[1]) * 180 / Math.PI, 20));
  o.push(`<line x1="0" y1="${bottom}" x2="${W}" y2="${bottom}" stroke="${C.ink}" stroke-width="4"/>`);
}

// hole inset: the whole hole, tee at the bottom, with the height change
function inset(o, H, course, U, x, y, w, h) {
  const u = G.norm(G.sub(H.GC, H.TEE));
  const pts_ = [...H.CL, ...H.green.geom];
  const v = G.perpR(u);
  const al = pts_.map((p) => G.dot(G.sub(p, H.TEE), u)), la = pts_.map((p) => G.dot(G.sub(p, H.TEE), v));
  const k = Math.min((h - 70) / (Math.max(...al) - Math.min(...al) + 10), (w - 40) / (Math.max(...la) - Math.min(...la) + 20));
  const mid = (Math.max(...la) + Math.min(...la)) / 2;
  const V = view(H.TEE, u, k, x + w / 2 - mid * k, y + h - 26 - (-Math.min(...al, 0)) * k);
  o.push(`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="4" fill="#fff" stroke="#7d8287" stroke-width="1.6" filter="url(#sh)"/>`);
  o.push(compass(x + w / 2, y + 34, -Math.atan2(u[0], u[1]) * 180 / Math.PI, 12));
  o.push(`<polyline points="${pts(H.CL.map(V.tf))}" fill="none" stroke="${C.red}" stroke-width="1.6" stroke-dasharray="4 4"/>`);
  o.push(path(H.green.geom.map(V.tf), `fill="#fff" stroke="${C.ink}" stroke-width="1.6"`));
  const [tx, ty] = V.tf(H.TEE);
  o.push(`<rect x="${f1(tx - 6)}" y="${f1(ty - 5)}" width="12" height="10" rx="2" fill="#e8b81e" stroke="#555" stroke-width="1"/>`);
  if (course.terrain) {
    const a = course.terrain.at(H.TEE), g = course.terrain.at(H.GC);
    if (a != null && g != null) { const [gx, gy] = V.tf(H.GC); o.push(txt(gx + 22, gy + 6, `${Math.round((g - a) * U.k) > 0 ? '+' : ''}${Math.round((g - a) * U.k)}`, 16, C.ink, 'start', 400, false)); }
  }
}

function footer(o, rows, y0) {
  o.push(`<rect x="0" y="${y0}" width="${W}" height="${PH - y0}" fill="#fff"/><line x1="0" y1="${y0}" x2="${W}" y2="${y0}" stroke="#b9bdc1" stroke-width="1.5"/>`);
  rows.forEach((r, i) => {
    const yy = y0 + 34 + i * 34;
    o.push(`<line x1="0" y1="${yy + 11}" x2="${W}" y2="${yy + 11}" stroke="#e3e5e7" stroke-width="1"/>`);
    o.push(txt(20, yy, r[0], 15, C.blue, 'start', 700, false));
    if (r[1] != null) o.push(txt(260, yy, r[1], 24, C.blue, 'start', 400, false));
    if (r[2] != null) o.push(txt(620, yy, r[2], 24, C.blue, 'start', 400, false));
  });
}

const svgDoc = (parts) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${PH}" width="101.6mm" height="177.8mm">${parts.join('')}</svg>`;

// ---------------------------------------------------------------- pages
export function bookPages(H, course, opts = {}) {
  const U = { ...unitInfo(opts.units), pdf: !!opts.pdf };
  const n = (m) => Math.round(m * U.k);
  const ringsList = U.k > 1 ? [250, 275, 300, 325] : [200, 225, 250, 275, 300];
  const par3 = H.par <= 3 || H.playCentre < 240;
  const step = 20;   // margin rows every 20 yards / metres
  // run-outs from the tee through the A and B carry points
  const ros = [];
  for (const [letter, rv] of [['A', ringsList[1]], ['B', ringsList[3]]]) {
    const r = rv / U.k, aim = H.onLine(r); if (!aim || par3) continue;
    const ro = H.runout(aim); if (!ro || !ro.containsAim) continue;
    ros.push({ letter, ro, carry: n(H.B(ro.entry)), out: n(H.B(ro.exit)) });
  }
  const dz = course.terrain ? (() => { const a = course.terrain.at(H.TEE), g = course.terrain.at(H.GC); return a == null || g == null ? null : g - a; })() : null;
  const pages = [];

  // ===== approach page: green on top, the approach below
  {
    const o = [`<rect width="${W}" height="${PH}" fill="#fff"/>`, defs()];
    const GS = 580, FOOT = PH - 104;
    greenSection(o, H, course, U, 0, GS);
    // scale: the approach ~200 m (par 3: the whole hole) down the page, at most ~1:1750
    const mapTop = GS + 4, mapBot = FOOT;
    const span = par3 ? H.playFront + 22 : 200;
    const s = Math.min(5.9, (mapBot - mapTop - 30) / span);
    // centre the line of play across the page
    const u = G.norm(G.sub(H.GC, G.pointAlong(H.CL, Math.max(0, H.playCentre - (par3 ? H.playCentre : 150)))));
    const near = H.CL.filter((p) => G.dist(p, H.GF) < span);
    const lat = near.map((p) => G.dot(G.sub(p, H.GF), G.perpR(u)));
    const mid = lat.length ? (Math.max(...lat, 0) + Math.min(...lat, 0)) / 2 : 0;
    const V = view(H.GF, u, s, W / 2 - mid * s, mapTop + 34);
    const clip = [0, mapTop, W, mapBot];
    const id = `ap${String(H.ref).replace(/\W/g, '')}`;
    drawMap(o, H, course, V, clip, id, { pdf: U.pdf });
    o.push(`<clipPath id="cl${clip.join('')}"><rect x="0" y="${mapTop}" width="${W}" height="${mapBot - mapTop}"/></clipPath>`);
    const occ = new Occ();
    occ.take([0, mapBot - 250, 190, mapBot]);           // the inset
    const A = annot(H, course, V, U, occ, o, clip);
    A.rings(ringsList);
    for (let i = 1, r = 25; ; r += 25, i++) { const p = H.fromFront(r / U.k); if (!p || H.B(p) < 30) break; const [, y] = V.tf(p); if (y > mapBot) break; A.centre(p, i); }
    for (let r = step; ; r += step) { const p = H.fromFront(r / U.k); if (!p) break; const [, y] = V.tf(p); if (y > mapBot + 20) break; A.row(p); }
    A.hazards();
    if (par3) teeMarks(o, H, V, U, occ, clip);
    o.push(`<line x1="0" y1="${mapTop}" x2="${W}" y2="${mapTop}" stroke="${C.ink}" stroke-width="4"/>`);
    inset(o, H, course, U, 18, mapBot - 238, 162, 222);
    const rows = [];
    if (ros.length) rows.push(['RUN OUT', ros[0] ? `${ros[0].out} (${ros[0].letter})` : null, ros[1] ? `${ros[1].out} (${ros[1].letter})` : null],
      ['CARRY', ros[0] ? `${ros[0].carry}` : null, ros[1] ? `${ros[1].carry}` : null]);
    else rows.push(['TO GREEN', `F ${n(H.playFront)}  C ${n(H.playCentre)}  B ${n(H.playBack)}`, null],
      ['GREEN', dz == null ? null : Math.abs(dz) < 1 ? 'level with tee' : `${Math.abs(Math.round(dz * U.k))} ${U.label.toLowerCase()} ${dz > 0 ? 'above' : 'below'} tee`, dz == null || Math.abs(dz) < 1 ? null : `plays ${n((H.tagDist || H.playCentre) + dz)}`]);
    footer(o, rows, FOOT);
    pages.push({ kind: 'approach', svg: svgDoc(o) });
  }

  // ===== tee page (par 4 and 5): the tee shot from the back tee
  if (!par3) {
    const o = [`<rect width="${W}" height="${PH}" fill="#fff"/>`, defs()];
    const FOOT = PH - 140, top = 0;
    const u = G.norm(G.sub(H.CL[1], H.CL[0]));
    // cover the line of play from the tee to where the approach page starts (with some overlap)
    const sEnd = Math.max(120, H.playFront - 180);
    const covered = [];
    for (let d = 0; d <= sEnd; d += 10) covered.push(G.pointAlong(H.CL, d));
    const fw = H.fairways.flatMap((f) => f.geom).filter((p) => G.projectOnLine(H.CL, p).s <= sEnd + 20);
    const al = covered.map((p) => G.dot(G.sub(p, H.TEE), u));
    const la = [...covered, ...fw].map((p) => G.dot(G.sub(p, H.TEE), G.perpR(u)));
    const tee0 = FOOT - 70;
    const s = Math.min(5.9, (tee0 - top - 60) / (Math.max(...al) + 25), (W - 260) / (Math.max(...la) - Math.min(...la) + 20));
    const mid = (Math.max(...la) + Math.min(...la)) / 2;
    const V = view(H.TEE, u, s, W / 2 - mid * s, tee0);
    const clip = [0, top, W, FOOT];
    const id = `tp${String(H.ref).replace(/\W/g, '')}`;
    drawMap(o, H, course, V, clip, id, { pdf: U.pdf });
    o.push(`<clipPath id="cl${clip.join('')}"><rect x="0" y="${top}" width="${W}" height="${FOOT - top}"/></clipPath>`);
    const occ = new Occ();
    occ.take([600, 0, W, 165]);                           // header disc
    occ.take([0, 0, 130, 140]);                           // compass
    const A = annot(H, course, V, U, occ, o, clip);
    A.rings(ringsList);
    // where the carry arcs cross the line of play: the plates, big number to the front
    ringsList.forEach((rv, i) => { const p = H.onLine(rv / U.k); if (p) A.centre(p, i); });
    for (let r = 140; ; r += step) { const p = H.onLine(r / U.k); if (!p) break; const [, y] = V.tf(p); if (y < top + 20) break; A.row(p); }
    A.hazards();
    for (const { ro, letter } of ros) {
      const [x, y] = V.tf(ro.exit);
      if (!A.inside([x, y])) continue;
      o.push(`<circle cx="${f1(x)}" cy="${f1(y)}" r="13" fill="#fff" stroke="${C.ink}" stroke-width="1.6"/>` + txt(x, y + 6, letter, 16, C.ink, 'middle', 700, false));
      const lab = `${n(H.B(ro.exit))}`, lft = H.side(ro.exit) < 0;
      const b = Occ.box(x + (lft ? -20 : 20), y + 6, lab, 20, lft ? 'end' : 'start');
      if (occ.free(b)) { occ.take(b); o.push(txt(x + (lft ? -20 : 20), y + 6, lab, 20, C.blue, lft ? 'end' : 'start', 400)); }
    }
    teeMarks(o, H, V, U, occ, clip);
    o.push(compass(70, 80, -Math.atan2(u[0], u[1]) * 180 / Math.PI, 20));
    // header: hole number
    o.push(`<circle cx="${W - 84}" cy="74" r="54" fill="${C.gold}"/>` + txt(W - 84, 94, H.ref, 56, '#fff', 'middle', 700, false));
    o.push(txt(W - 150, 150, `Par ${H.par}${H.si ? ' · SI ' + H.si : ''}`, 22, C.ink, 'end', 400));
    const rows = [['TO GREEN', `F ${n(H.playFront)}  C ${n(H.playCentre)}  B ${n(H.playBack)}`, null]];
    if (dz != null) rows.push(['GREEN', Math.abs(dz) < 1 ? 'level with tee' : `${Math.abs(Math.round(dz * U.k))} ${U.label.toLowerCase()} ${dz > 0 ? 'above' : 'below'} tee`, Math.abs(dz) < 1 ? null : `plays ${n((H.tagDist || H.playCentre) + dz)}`]);
    rows.push(['HOLE', `${n(H.tagDist || H.playCentre)} ${U.label}`, H.playCentre - H.toCentre > 10 ? `dogleg · straight ${n(H.toCentre)}` : null]);
    footer(o, rows, FOOT);
    pages.push({ kind: 'tee', svg: svgDoc(o) });
  }
  return pages;
}

// tee boxes of the hole with their distance to the centre of the green; the back tee marked
function teeMarks(o, H, V, U, occ, clip) {
  const n = (m) => Math.round(m * U.k);
  const back = H.tees.reduce((a, t) => (!a || G.ptPolyDist(H.TEE, t.geom) < G.ptPolyDist(H.TEE, a.geom) ? t : a), null);
  const labs = [];
  for (const t of H.tees) {
    const isBack = t === back && G.ptPolyDist(H.TEE, t.geom) < 10;
    labs.push({ t, isBack, d: isBack ? H.playCentre : H.playCentre - Math.max(0, G.projectOnLine(H.CL, G.centroid(t.geom)).s) });
  }
  labs.sort((a, b) => (b.isBack - a.isBack) || (b.d - a.d));
  const shown = [];
  for (const { t, isBack, d } of labs) {
    if (shown.some((x) => Math.abs(x - d) < 6)) continue;
    const P = t.geom.map(V.tf); const xr = Math.max(...P.map((p) => p[0])), yc = P.reduce((a, p) => a + p[1], 0) / P.length;
    if (yc > clip[3] || yc < clip[1]) continue;
    shown.push(d);
    let ly = yc + 8; const lab = `${n(d)}`;
    if (!occ.free(Occ.box(xr + 14, ly, lab, 26, 'start'))) ly += 34;
    occ.take(Occ.box(xr + 14, ly + 14, lab, 26, 'start'));
    o.push(txt(xr + 14, ly, lab, 26, C.ink, 'start', 400));
    o.push(txt(xr + 14, ly + 17, isBack ? 'BACK TEE' : 'TEE', 12, C.gold, 'start', 700, false));
  }
  const [tx, ty] = V.tf(H.TEE);
  if (!labs.some((x) => x.isBack)) {
    o.push(`<rect x="${f1(tx - 12)}" y="${f1(ty - 16)}" width="24" height="32" rx="4" fill="#fff" stroke="${C.ink}" stroke-width="2" stroke-dasharray="5 4"/>`);
    o.push(txt(tx + 22, ty + 6, `${n(H.playCentre)}`, 26, C.ink, 'start', 400));
    o.push(txt(tx + 22, ty + 23, 'BACK TEE', 12, C.gold, 'start', 700, false));
  }
  o.push(`<rect x="${f1(tx - 6)}" y="${f1(ty - 5)}" width="12" height="10" rx="2" fill="#e8b81e" stroke="#555" stroke-width="1"/>`);
}
