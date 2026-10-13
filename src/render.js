// Tour-style yardage book pages as SVG strings.
// Each hole = two pocket pages (4" x 7" = 101.6 x 177.8 mm; 1 unit = 0.1 mm):
//   green page (upper)  -> notes grid + magnified green at 1:480 with 5-unit grid and depth axis
//   hole page  (lower)  -> tee-to-green map, carry rings, centre markers, margin numbers, hazards, run-outs
import * as G from './geo.js';
import { contourPaths } from './terrain.js';

export const PAGE_W = 1016, PAGE_H = 1778;
const FONT = "font-family=\"'Inter','Helvetica Neue',Arial,sans-serif\"";
const C = {
  ink: '#151515', grey: '#7a7f85', grid: '#d6dadd', blue: '#2a7fd4', gold: '#b8922c', red: '#d23b2f',
  fwLine: '#3f8f52', fwFill: '#f3f8f0', green: '#ffffff', sandF: '#efe3c8', sandL: '#b9995f',
  water: '#ddedf9', waterL: '#4a90d0', canopy: '#f2f5ef', canopyL: '#6d7a64', rough: '#ffffff', contour: '#b39a74',
};
const RING_COLS = ['#2a7fd4', '#5fae4c', '#d23b2f', '#e0ad22', '#8f5cbf'];

const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
const f1 = (n) => (Math.round(n * 10) / 10).toString();

function txt(x, y, s, size, col = C.ink, anchor = 'middle', w = 600, halo = true) {
  const base = `x="${f1(x)}" y="${f1(y)}" text-anchor="${anchor}" ${FONT} font-weight="${w}" font-size="${size}"`;
  const back = halo ? `<text ${base} fill="#fff" stroke="#fff" stroke-width="${Math.max(5, size * 0.28)}" stroke-linejoin="round">${esc(s)}</text>` : '';
  return back + `<text ${base} fill="${col}">${esc(s)}</text>`;
}
const pts = (P) => P.map((p) => `${f1(p[0])},${f1(p[1])}`).join(' ');

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
const poly = (P, attrs) => `<polygon points="${pts(P)}" ${attrs}/>`;

// simple label collision avoidance
class Occupancy {
  constructor() { this.boxes = []; }
  free(b) { return !this.boxes.some((o) => !(b[2] < o[0] || b[0] > o[2] || b[3] < o[1] || b[1] > o[3])); }
  take(b) { this.boxes.push(b); }
  // bounding box of a label anchored at (x,y) baseline
  static box(x, y, s, size, anchor) {
    const w = String(s).length * size * 0.6;
    const x0 = anchor === 'end' ? x - w : anchor === 'middle' ? x - w / 2 : x;
    return [x0 - 2, y - size * 0.85, x0 + w + 2, y + size * 0.15];
  }
}

function compass(x, y, deg, scale = 1) {
  return `<g transform="translate(${f1(x)},${f1(y)}) scale(${scale})">
    <circle r="30" fill="#fff" stroke="#9aa0a5" stroke-width="2"/>
    <g transform="rotate(${f1(deg)})"><path d="M0,-40 L9,-6 L0,-12 L-9,-6 Z" fill="${C.red}"/><path d="M0,40 L9,6 L0,12 L-9,6 Z" fill="#9aa0a5"/>
    <text x="0" y="-46" text-anchor="middle" ${FONT} font-weight="700" font-size="18" fill="#555">N</text></g></g>`;
}

const inView = (P, tf, W, H, pad = 80) => P.some((p) => { const [x, y] = tf(p); return x > -pad && x < W + pad && y > -pad && y < H + pad; });

const unitInfo = (units) => (units === 'yd' ? { k: 1.0936133, label: 'Yds', grid: 4.572 } : { k: 1, label: 'Mts', grid: 5 });

// ======================= GREEN PAGE =======================
export function greenPage(H, course, opts = {}) {
  const U = unitInfo(opts.units);
  const n = (m) => Math.round(m * U.k);
  const W = PAGE_W, Hh = PAGE_H;
  const depth = H.greenDepth, width = H.greenWidth;
  // Rules of Golf limit for green-reading material: no larger than 1:480 (3/8" = 5 yd). 1 m = 20.83 units.
  const kMax = 1000 / 480 * 10;
  const k = Math.min(kMax, 880 / (depth + 16), 900 / (width + 16));
  const cx = W / 2, frontY = Hh - 260;
  const front = H.along(H.greenEdges.F);
  const tf = (p) => [cx + H.side(p) * k, frontY - (H.along(p) - front) * k];
  const o = [`<rect width="${W}" height="${Hh}" fill="#fff"/>`];

  // grid across the whole page (top = notes area), aligned to the centre axis and the front edge
  const g = U.grid * k;
  for (let x = cx % g; x < W; x += g) o.push(`<line x1="${f1(x)}" y1="0" x2="${f1(x)}" y2="${Hh}" stroke="${C.grid}" stroke-width="1.4"/>`);
  for (let y = frontY % g; y < Hh; y += g) o.push(`<line x1="0" y1="${f1(y)}" x2="${W}" y2="${f1(y)}" stroke="${C.grid}" stroke-width="1.4"/>`);

  // surroundings (clipped to the map zone)
  const mapTop = frontY - (depth + 22) * k;
  o.push(`<clipPath id="gp${H.ref}"><rect x="0" y="${f1(mapTop)}" width="${W}" height="${f1(Hh - mapTop - 90)}"/></clipPath><g clip-path="url(#gp${H.ref})">`);
  o.push(`<rect x="0" y="${f1(mapTop)}" width="${W}" height="${Hh}" fill="#f8fbf6" opacity="0.7"/>`);
  const vis = (L) => L.filter((f) => inView(f.geom, tf, W, Hh));
  for (const w of vis(course.layers.water)) o.push(poly(w.geom.map(tf), `fill="${C.water}" stroke="${C.waterL}" stroke-width="2.5"`));
  for (const f of vis(course.layers.fairway)) o.push(path(f.geom.map(tf), `fill="${C.fwFill}" stroke="${C.fwLine}" stroke-width="3"`));
  for (const t of course.layers.tree.filter((t) => inView([t.c], tf, W, Hh))) {
    const [x, y] = tf(t.c); o.push(`<circle cx="${f1(x)}" cy="${f1(y)}" r="${f1(t.r * k)}" fill="${C.canopy}" stroke="${C.canopyL}" stroke-width="1.8" stroke-dasharray="4 4"/>`);
  }
  const collar = G.offsetPoly(H.green.geom, 1.8);
  o.push(path(collar.map(tf), `fill="#fff" stroke="${C.fwLine}" stroke-width="2.5"`));
  for (const gr of vis(course.layers.green)) o.push(path(gr.geom.map(tf), `fill="#fff" stroke="${C.ink}" stroke-width="${gr === H.green ? 4 : 2}"`));
  for (const b of vis(course.layers.bunker)) o.push(path(b.geom.map(tf), `fill="${C.sandF}" stroke="${C.sandL}" stroke-width="2.5"`));
  if (H.green.estimated) o.push(path(H.green.geom.map(tf), `fill="#fff" stroke="${C.ink}" stroke-width="3" stroke-dasharray="10 7"`));
  // faint grid again over the fills
  for (let x = cx % g; x < W; x += g) o.push(`<line x1="${f1(x)}" y1="0" x2="${f1(x)}" y2="${Hh}" stroke="${C.grid}" stroke-width="1.1" opacity="0.8"/>`);
  for (let y = frontY % g; y < Hh; y += g) o.push(`<line x1="0" y1="${f1(y)}" x2="${W}" y2="${f1(y)}" stroke="${C.grid}" stroke-width="1.1" opacity="0.8"/>`);
  o.push('</g>');

  // red centre line front -> back, depth at the back, (0) centre
  const [fx, fy] = tf(H.greenEdges.F), [bx, by] = tf(H.greenEdges.B), [gx, gy] = tf(H.GC);
  o.push(`<line x1="${f1(fx)}" y1="${f1(fy)}" x2="${f1(bx)}" y2="${f1(by)}" stroke="${C.red}" stroke-width="2.5"/>`);
  o.push(`<circle cx="${f1(fx)}" cy="${f1(fy)}" r="6" fill="${C.red}"/><circle cx="${f1(bx)}" cy="${f1(by)}" r="6" fill="#e0ad22" stroke="#8a6a10"/>`);
  o.push(txt(bx, by - 16, n(depth), 34, C.red, 'middle', 700));
  o.push(`<circle cx="${f1(gx)}" cy="${f1(gy)}" r="13" fill="#fff" stroke="${C.ink}" stroke-width="3"/>` + txt(gx, gy + 7, '0', 18, C.ink, 'middle', 700, false));
  // perimeter depths from the front
  const ring = H.green.geom, N = 16, occ = new Occupancy();
  occ.take([bx - 40, by - 50, bx + 40, by]);
  const total = G.lineLength([...ring, ring[0]]);
  for (let i = 0; i < N; i++) {
    const p = G.pointAlong([...ring, ring[0]], (total * i) / N);
    const a = H.along(p) - front;
    if (a < 2 || a > depth - 2) continue;
    const [x, y] = tf(p), sd = H.side(p) > 0 ? 1 : -1;
    const lx = x - sd * 24, ly = y + 8, s = n(a);
    const box = Occupancy.box(lx, ly, s, 22, 'middle');
    if (!occ.free(box)) continue;
    occ.take(box); o.push(txt(lx, ly, s, 22, '#444', 'middle', 600));
  }
  // width across the centre
  const [lx, ly] = tf(H.greenEdges.L), [rx, ry] = tf(H.greenEdges.R);
  o.push(txt(lx - 14, ly + 8, `${n(H.greenEdges ? Math.abs(H.side(H.greenEdges.L)) : 0)}`, 24, C.ink, 'end', 700));
  o.push(txt(rx + 14, ry + 8, `${n(Math.abs(H.side(H.greenEdges.R)))}`, 24, C.ink, 'start', 700));
  // depth axis
  for (let j = 0; j * U.grid <= depth + 10; j++) {
    const y = frontY - j * U.grid * k;
    if (y < mapTop + 10) break;
    o.push(txt(46, y + 9, j * 5, 26, '#333', 'middle', 600));
  }
  // the view from the tee across the top of the page (the notes area), when there is one
  if (opts.teeView) {
    const ih = Math.round(W * 640 / 1800);
    if (mapTop > ih + 30) {
      o.push(`<clipPath id="tv${H.ref}"><rect x="0" y="0" width="${W}" height="${ih}"/></clipPath>`);
      o.push(`<image href="${opts.teeView}" x="0" y="0" width="${W}" height="${ih}" preserveAspectRatio="xMidYMid slice" clip-path="url(#tv${H.ref})"/>`);
      o.push(`<line x1="0" y1="${ih}" x2="${W}" y2="${ih}" stroke="${C.ink}" stroke-width="3"/>`);
    }
  }
  // header & meta
  const len = n(H.tagDist || H.toCentre);
  o.push(txt(W - 48, mapTop + 50, `Par ${H.par}`, 40, C.gold, 'end', 600));
  o.push(txt(W - 48, mapTop + 94, `${len} ${U.label}`, 40, C.gold, 'end', 600));
  o.push(compass(W - 90, mapTop + 170, -Math.atan2(H.U[0], H.U[1]) * 180 / Math.PI, 0.9));
  o.push(txt(W / 2, Hh - 52, `Depth ${n(depth)} · Width ${n(width)} · Front to centre ${n(-front)}`, 26, C.ink, 'middle', 600, false));
  o.push(txt(W / 2, Hh - 20, `Scale 1:${Math.round(10000 / k)} · 1 square = 5 ${U.label.toLowerCase()} · © OpenStreetMap contributors`, 18, C.grey, 'middle', 500, false));
  return svgDoc(o, W, Hh);
}

// ======================= HOLE PAGE =======================
export function holePage(H, course, opts = {}) {
  const U = unitInfo(opts.units);
  const n = (m) => Math.round(m * U.k);
  const toM = (v) => v / U.k;
  const W = PAGE_W, Hh = PAGE_H;
  const BAR = 190, TOP = 30, BOT = Hh - BAR;
  const dir = G.sub(H.GC, H.TEE);
  const phi = Math.atan2(dir[0], dir[1]);
  const rot = (p) => { const q = G.sub(p, H.TEE); return [q[0] * Math.cos(phi) - q[1] * Math.sin(phi), q[0] * Math.sin(phi) + q[1] * Math.cos(phi)]; };
  const focus = [...H.CL, ...H.green.geom, ...H.fairways.flatMap((f) => f.geom).filter((p) => G.ptLineDist(p, H.CL) < 60), ...H.tees.flatMap((t) => t.geom)].map(rot);
  const bb = G.bbox(focus);
  const s = Math.min((BOT - TOP - 60) / (bb.y1 - bb.y0), 560 / Math.max(bb.x1 - bb.x0, 1));
  const midX = (bb.x0 + bb.x1) / 2;
  const tf = (p) => { const r = rot(p); return [W / 2 + (r[0] - midX) * s, TOP + 30 + (bb.y1 - r[1]) * s]; };
  const o = [`<rect width="${W}" height="${Hh}" fill="#fff"/>`];
  const occ = new Occupancy();

  o.push(`<clipPath id="hp${H.ref}"><rect x="0" y="${TOP}" width="${W}" height="${BOT - TOP}"/></clipPath><g clip-path="url(#hp${H.ref})">`);
  const vis = (L) => L.filter((f) => inView(f.geom, tf, W, Hh));
  // canopy: dashed outlines merge visually (strokes first, fills on top hide inner edges)
  for (const w of vis(course.layers.wood)) o.push(poly(w.geom.map(tf), `fill="${C.canopy}" stroke="${C.canopyL}" stroke-width="1.8" stroke-dasharray="4 4"`));
  const trees = course.layers.tree.filter((t) => inView([t.c], tf, W, Hh)).map((t) => { const [x, y] = tf(t.c); return { x, y, r: Math.max(t.r * s, 6) }; });
  for (const t of trees) o.push(`<circle cx="${f1(t.x)}" cy="${f1(t.y)}" r="${f1(t.r)}" fill="none" stroke="${C.canopyL}" stroke-width="1.8" stroke-dasharray="3 4"/>`);
  for (const t of trees) o.push(`<circle cx="${f1(t.x)}" cy="${f1(t.y)}" r="${f1(t.r - 1.2)}" fill="${C.canopy}"/>`);
  for (const w of vis(course.layers.water)) {
    o.push(poly(w.geom.map(tf), `fill="${C.water}" stroke="${C.waterL}" stroke-width="2.5"`));
    o.push(poly(G.offsetPoly(w.geom, 1).map(tf), `fill="none" stroke="${C.red}" stroke-width="1.6" stroke-dasharray="10 6"`));
  }
  for (const ob of vis(course.layers.ob)) o.push(`<polyline points="${pts(ob.geom.map(tf))}" fill="none" stroke="${C.red}" stroke-width="2.5" stroke-dasharray="8 8"/>`);
  for (const f of vis(course.layers.fairway)) o.push(path(f.geom.map(tf), `fill="${C.fwFill}" stroke="${C.fwLine}" stroke-width="${H.fairways.includes(f) ? 3.2 : 1.6}"`));
  for (const t of vis(course.layers.tee)) o.push(path(t.geom.map(tf), `fill="${C.fwFill}" stroke="${C.ink}" stroke-width="${H.tees.includes(t) ? 2.4 : 1.2}"`));
  for (const gr of vis(course.layers.green)) {
    o.push(path(G.offsetPoly(gr.geom, 1.8).map(tf), `fill="#fff" stroke="${C.fwLine}" stroke-width="2"`));
    o.push(path(gr.geom.map(tf), `fill="#fff" stroke="${C.ink}" stroke-width="${gr === H.green ? 2.8 : 1.4}"`));
  }
  for (const b of vis(course.layers.bunker)) o.push(path(b.geom.map(tf), `fill="${C.sandF}" stroke="${C.sandL}" stroke-width="2"`));
  if (H.green.estimated) o.push(path(H.green.geom.map(tf), `fill="#fff" stroke="${C.ink}" stroke-width="2.4" stroke-dasharray="7 5"`));

  // terrain contours (1 m, every 5th heavier) from the course's elevation model
  if (course.terrain) {
    const inv = (x, y) => {
      const r0 = (x - W / 2) / s + midX, r1 = bb.y1 - (y - TOP - 30) / s;
      return G.add(H.TEE, [r0 * Math.cos(phi) + r1 * Math.sin(phi), -r0 * Math.sin(phi) + r1 * Math.cos(phi)]);
    };
    const st = 12, Z = [];
    for (let y = TOP; y <= BOT + st; y += st) { const row = []; for (let x = 0; x <= W + st; x += st) row.push(course.terrain.at(inv(x, y))); Z.push(row); }
    for (const { level, d } of contourPaths(Z, 0, TOP, st, 1)) {
      const major = Math.round(level) % 5 === 0;
      o.push(`<path d="${d}" fill="none" stroke="${C.contour}" stroke-width="${major ? 1.9 : 1.1}" opacity="${major ? 0.85 : 0.6}" stroke-linecap="round"/>`);
    }
  }

  // carry rings from the back tee (par 4/5)
  const rings = H.par >= 4 ? (U.k > 1 ? [225, 250, 275, 300, 325] : [200, 225, 250, 275, 300]) : [];
  const ringData = [];
  rings.forEach((rv, i) => {
    const r = toM(rv);
    if (r > H.toFront - 25) return;
    const col = RING_COLS[i % RING_COLS.length];
    const arc = [];
    for (let a = -75; a <= 75; a += 1) {
      const t = (a * Math.PI) / 180, d = G.norm(dir);
      const v = [d[0] * Math.cos(t) - d[1] * Math.sin(t), d[0] * Math.sin(t) + d[1] * Math.cos(t)];
      arc.push(tf(G.add(H.TEE, G.mul(v, r))));
    }
    o.push(`<polyline points="${pts(arc)}" fill="none" stroke="${col}" stroke-width="2.2" opacity="0.85"/>`);
    ringData.push({ rv, r, col, arc });
  });
  o.push(`<polyline points="${pts(H.CL.map(tf))}" fill="none" stroke="#c5c5c5" stroke-width="1.2" stroke-dasharray="1 5"/>`);
  o.push('</g>');

  // centre markers where rings cross the line of play
  ringData.forEach(({ rv, r, col }, i) => {
    const p = H.onLine(r); if (!p) return;
    const [x, y] = tf(p);
    o.push(i % 2 === 0 ? `<circle cx="${f1(x)}" cy="${f1(y)}" r="10" fill="${col}"/>`
      : `<path d="M${f1(x - 11)},${f1(y)} H${f1(x + 11)} M${f1(x)},${f1(y - 11)} V${f1(y + 11)}" stroke="${col}" stroke-width="6"/>`);
    o.push(txt(x + 16, y + 4, n(H.F(p)), 36, C.ink, 'start', 600)); o.push(txt(x + 16, y + 30, rv, 22, C.blue, 'start', 600));
    occ.take([x - 14, y - 34, x + 110, y + 38]);
  });
  // plain centre points (to the front)
  for (const rv of [50, 75, 100, 125, 150, 175]) {
    const p = H.fromFront(toM(rv)); if (!p || G.dist(p, H.TEE) < 40) continue;
    const [x, y] = tf(p), box = [x - 8, y - 22, x + 90, y + 40];
    if (!occ.free(box)) continue;
    occ.take(box);
    o.push(`<circle cx="${f1(x)}" cy="${f1(y)}" r="5" fill="${C.ink}"/>`);
    o.push(txt(x + 14, y + 10, rv, 30, C.ink, 'start', 600)); o.push(txt(x + 14, y + 34, n(H.B(p)), 20, C.blue, 'start', 600));
  }
  // ring labels and landing-zone widths
  for (const { rv, r, col, arc } of ringData) {
    const lab = arc.reduce((a, b) => (Math.abs(b[0] - 170) < Math.abs(a[0] - 170) ? b : a));
    if (lab[1] > TOP + 10 && lab[1] < BOT - 10) { const bxx = Occupancy.box(lab[0], lab[1] - 8, rv, 24, 'middle'); if (occ.free(bxx)) { occ.take(bxx); o.push(txt(lab[0], lab[1] - 8, rv, 24, col, 'middle', 700)); } }
    const w = H.widthAt(H.onLine(r));
    if (w) { const [x, y] = tf(w.left); const s_ = `[${n(w.width)}]`, b = Occupancy.box(x - 12, y + 26, s_, 20, 'end'); if (occ.free(b)) { occ.take(b); o.push(txt(x - 12, y + 26, s_, 20, col, 'end', 700)); } }
  }
  // margin numbers: fairway edges every 25 units with dotted leaders
  const step = toM(25);
  for (let r = step; r < H.toFront - 20; r += step) {
    const p = H.fromFront(r); const w = H.widthAt(p); if (!w) continue;
    for (const [q, side] of [[w.left, 'L'], [w.right, 'R']]) {
      const [x, y] = tf(q);
      if (y < TOP + 30 || y > BOT - 40) continue;
      const L = side === 'L';
      const bx = L ? 70 : W - 70;
      const box = L ? [8, y - 26, 86, y + 34] : [W - 86, y - 26, W - 8, y + 34];
      if (!occ.free(box)) continue; occ.take(box);
      o.push(`<line x1="${L ? 86 : f1(x)}" y1="${f1(y)}" x2="${L ? f1(x) : W - 86}" y2="${f1(y)}" stroke="#8a8a8a" stroke-width="1.4" stroke-dasharray="2 5"/>`);
      o.push(`<circle cx="${bx}" cy="${f1(y)}" r="8" fill="#fff" stroke="#555" stroke-width="1.8"/>`);
      o.push(txt(L ? 56 : W - 56, y + 9, n(H.F(q)), 28, C.ink, L ? 'end' : 'start', 600));
      o.push(txt(L ? 56 : W - 56, y + 32, n(H.B(q)), 18, C.blue, L ? 'end' : 'start', 600));
      o.push(`<line x1="${f1(x - 6)}" y1="${f1(y)}" x2="${f1(x + 6)}" y2="${f1(y)}" stroke="${C.red}" stroke-width="3"/>`);
      if (r <= toM(160)) { const s_ = `[${n(w.width)}]`, [lx, ly] = tf(w.left), b = Occupancy.box(lx - 12, ly + 24, s_, 19, 'end'); if (occ.free(b)) { occ.take(b); o.push(txt(lx - 12, ly + 24, s_, 19, '#666', 'end', 700)); } }
    }
  }
  // hazards: bunkers (entry / carry / flanks), water (near / far)
  const tag = (p, main, sub, dx, dy, anchor, size = 22) => {
    const [x, y] = tf(p);
    const cand = [[dx, dy, anchor], [-dx, dy, anchor === 'end' ? 'start' : 'end'], [dx, dy + 30, anchor], [dx, dy - 30, anchor]];
    for (const [ddx, ddy, an] of cand) {
      const b1 = Occupancy.box(x + ddx, y + ddy, main ?? '', size, an), b2 = Occupancy.box(x + ddx, y + ddy + size * 0.85, sub, size * 0.75, an);
      const b = [Math.min(b1[0], b2[0]), b1[1], Math.max(b1[2], b2[2]), b2[3]];
      if (!occ.free(b)) continue;
      occ.take(b);
      o.push(`<circle cx="${f1(x)}" cy="${f1(y)}" r="3.5" fill="${C.ink}"/>`);
      if (main != null) o.push(txt(x + ddx, y + ddy, main, size, C.ink, an, 600));
      o.push(txt(x + ddx, y + ddy + (main != null ? size * 0.85 : 0), sub, size * 0.75, C.blue, an, 700));
      return;
    }
  };
  for (const b of H.bunkerPts) {
    tag(b.entry, b.greenside ? null : n(H.F(b.entry)), n(H.B(b.entry)), 10, 22, 'start');
    tag(b.carry, null, n(H.B(b.carry)), -10, -12, 'end');
    if (!b.greenside) { tag(b.left, null, n(H.B(b.left)), -12, 6, 'end'); tag(b.right, null, n(H.B(b.right)), 12, 6, 'start'); }
  }
  for (const w of H.waterPts) {
    tag(w.near, n(H.F(w.near)), n(H.B(w.near)), -12, 12, 'end');
    tag(w.far, n(H.F(w.far)), n(H.B(w.far)), 12, -8, 'start');
  }
  // straight-line run-outs (A, B)
  const ros = [];
  if (H.par >= 4) {
    for (const [letter, rv] of [['A', rings[1]], ['B', rings[3]]]) {
      const ro = H.runout(H.onLine(toM(rv))); if (!ro || !ro.containsAim) continue;
      ros.push({ letter, carry: n(H.B(ro.entry)), out: n(H.B(ro.exit)) });
      const [x, y] = tf(ro.exit);
      o.push(`<path d="M${f1(x - 18)},${f1(y)} H${f1(x + 18)} M${f1(x)},${f1(y - 18)} V${f1(y + 18)}" stroke="${C.ink}" stroke-width="2.5" stroke-dasharray="4 3"/>`);
      const lft = H.side(ro.exit) < H.side(H.onLine(toM(rv)));
      const lab = `${n(H.B(ro.exit))} ${letter}`, lx = x + (lft ? -22 : 22), b = Occupancy.box(lx, y - 6, lab, 20, lft ? 'end' : 'start');
      occ.take(b); o.push(txt(lx, y - 6, lab, 20, C.blue, lft ? 'end' : 'start', 700));
    }
  }
  // throat width + fairway-end offset to the front
  if (H.throat) {
    const [ax, ay] = tf(H.throat.left), [bx2, by2] = tf(H.throat.right);
    o.push(`<line x1="${f1(ax)}" y1="${f1(ay)}" x2="${f1(bx2)}" y2="${f1(by2)}" stroke="${C.ink}" stroke-width="2"/>`);
    const lab = `throat [${n(H.throat.width)}] · +${n(H.throatOffset)} to front`, b = Occupancy.box(Math.max(ax, bx2) + 10, Math.max(ay, by2) + 20, lab, 17, 'start');
    if (occ.free(b)) { occ.take(b); o.push(txt(Math.max(ax, bx2) + 10, Math.max(ay, by2) + 20, lab, 17, C.ink, 'start', 700)); }
  }
  // back tee marker
  const [tx, ty] = tf(H.TEE);
  o.push(`<circle cx="${f1(tx)}" cy="${f1(ty)}" r="7" fill="#e0ad22" stroke="#8a6a10" stroke-width="1.5"/>`);
  o.push(compass(W - 70, TOP + 70, -phi * 180 / Math.PI, 0.8));

  // bottom bar: run-outs / carry / to green, hole disc
  const y0 = BOT;
  o.push(`<rect x="0" y="${y0}" width="${W}" height="${BAR}" fill="#fff"/><line x1="0" y1="${y0 + 2}" x2="${W}" y2="${y0 + 2}" stroke="${C.ink}" stroke-width="4"/>`);
  const rows = [];
  for (const r of ros) rows.push([`RUN OUT ${r.letter}`, `${r.out}`, r.letter === 'A' ? `carry to fairway ${r.carry}` : '']);
  // to the green along the line of play (doglegs), straight line otherwise
  const dog = H.playCentre - H.toCentre > 10;
  rows.push(['TO GREEN', dog ? `F ${n(H.playFront)}  C ${n(H.playCentre)}  B ${n(H.playBack)}` : `F ${n(H.toFront)}  C ${n(H.toCentre)}  B ${n(H.toBack)}`,
    dog ? `dogleg · straight ${n(H.toCentre)}` : '']);
  // elevation: green against back tee, and what that does to the hole
  const el = course.terrain ? [course.terrain.at(H.TEE), course.terrain.at(H.GC)] : null;
  if (el && el[0] != null && el[1] != null) {
    const dz = el[1] - el[0], base = H.tagDist || (dog ? H.playCentre : H.toCentre);
    const word = Math.abs(dz) < 1 ? 'level with tee' : `${Math.abs(dz).toFixed(0)} m ${dz > 0 ? 'above' : 'below'} tee`;
    rows.push(['GREEN', Math.abs(dz) >= 1 ? `${word} · plays ${n(base + dz)}` : word, '']);
  }
  const X1 = 196, X2 = 352, X3 = 640;
  rows.forEach((row, i) => {
    const yy = y0 + 40 + i * 34;
    o.push(txt(X1, yy, row[0], 17, C.blue, 'start', 700, false));
    o.push(txt(X2, yy, row[1], 26, C.ink, 'start', 600, false));
    if (row[2]) o.push(txt(X3, yy, row[2], 17, C.grey, 'start', 600, false));
  });
  o.push(inset(H, course, 14, y0 + 14, 166, BAR - 40));
  o.push(`<circle cx="${W - 92}" cy="${y0 + 92}" r="62" fill="${C.gold}"/>` + txt(W - 92, y0 + 112, H.ref, 56, '#fff', 'middle', 700, false));
  o.push(txt(W - 172, y0 + 172, `Par ${H.par}${H.si ? ' · SI ' + H.si : ''} · ${n(H.tagDist || H.toCentre)} ${U.label}`, 20, C.ink, 'end', 600, false));
  o.push(txt(22, Hh - 10, 'Black = to front of green · Blue = from back tee · © OpenStreetMap contributors', 15, C.grey, 'start', 500, false));
  return svgDoc(o, W, Hh);
}

// small north-up plan of the hole within the course (bottom-left of the hole page)
function inset(H, course, x, y, w, h) {
  const own = [...H.CL, ...H.green.geom, ...H.fairways.flatMap((f) => f.geom)];
  const bb = G.bbox(own);
  const pad = 30, cx = (bb.x0 + bb.x1) / 2, cy = (bb.y0 + bb.y1) / 2;
  const k = Math.min((w - 12) / (bb.x1 - bb.x0 + 2 * pad), (h - 12) / (bb.y1 - bb.y0 + 2 * pad));
  const tf = (p) => [x + w / 2 + (p[0] - cx) * k, y + h / 2 - (p[1] - cy) * k];
  const near = (geom) => geom.some((p) => Math.abs(p[0] - cx) * k < w && Math.abs(p[1] - cy) * k < h);
  const id = `in${String(H.ref).replace(/\W/g, '')}`;
  const o = [`<clipPath id="${id}"><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="6"/></clipPath>`,
    `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="6" fill="#f6f8f4" stroke="#9aa0a5" stroke-width="1.5"/>`, `<g clip-path="url(#${id})">`];
  for (const wv of course.layers.water.filter((q) => near(q.geom))) o.push(poly(wv.geom.map(tf), `fill="${C.water}" stroke="${C.waterL}" stroke-width="1"`));
  for (const f of course.layers.fairway.filter((q) => near(q.geom))) o.push(poly(f.geom.map(tf), `fill="${H.fairways.includes(f) ? '#cfe6c6' : '#e7efe3'}" stroke="none"`));
  for (const g of course.layers.green.filter((q) => near(q.geom))) o.push(poly(g.geom.map(tf), `fill="${g === H.green ? '#fff' : '#f2f2f2'}" stroke="${g === H.green ? C.ink : '#aaa'}" stroke-width="${g === H.green ? 1.4 : 0.8}"`));
  for (const b of course.layers.bunker.filter((q) => near(q.geom))) o.push(poly(b.geom.map(tf), `fill="${C.sandF}" stroke="none"`));
  for (const hh of course.layers.hole) if (hh !== H.src && near(hh.geom || [])) o.push(`<polyline points="${pts((hh.geom || []).map(tf))}" fill="none" stroke="#b8bcc0" stroke-width="1"/>`);
  o.push(`<polyline points="${pts(H.CL.map(tf))}" fill="none" stroke="${C.red}" stroke-width="2.2" stroke-linejoin="round"/>`);
  const [tx, ty] = tf(H.TEE); o.push(`<circle cx="${f1(tx)}" cy="${f1(ty)}" r="4" fill="#e0ad22" stroke="#8a6a10" stroke-width="1"/>`);
  o.push('</g>');
  o.push(`<path d="M${x + w - 14},${y + 22} l5,-12 l5,12 l-5,-3 z" fill="${C.ink}"/>` + txt(x + w - 9, y + 34, 'N', 11, C.ink, 'middle', 700, false));
  return o.join('');
}

function svgDoc(parts, w, h) {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}" width="101.6mm" height="177.8mm">${parts.join('')}</svg>`;
}
