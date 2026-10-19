// Tour yardage book pages in the style of the tour course guides used on the DP World Tour.
// Each hole is a vertical two-page spread (the book is bound at the top): the green end on the top page and the
// tee end on the bottom page, as one continuous map. Fairways in fine double lines inside a light rough corridor,
// soft tree canopies, numbers to the front of the green (black), from the back tee (blue, never past 350 yds),
// from the drive (red, par 5s), centre-line plates, edge and rough marks with leaders to the margin numbers,
// carry arcs with fairway widths, lettered run-outs and numbered carries, an inset, and the view from the tee
// with its markers across the foot of the spread.
import * as G from './geo.js';

export const PAGE_W = 1016, PAGE_H = 1778;          // 4" x 7" pocket pages; 1 unit = 0.1 mm
// the print-and-cut format (four pages to an A4 side, cut and bound at the top): pages 90 x 148.5 mm, drawn on the
// same 1016-unit width, with a band kept clear of numbers along the page edge that falls on the edge of the paper
export const PRINT = { pageMM: [90, 148.5], pageH: Math.round(1016 * 148.5 / 90), safe: 62 };
const W = PAGE_W;
const MAX_DRIVE = 320;                               // metres (350 yds): no from-tee numbers past this
const FONT = `font-family="'Roboto Condensed','Arial Narrow','Helvetica Neue',Arial,sans-serif"`;
const C = {
  ink: '#1b1b1b', soft: '#333', grey: '#777', blue: '#2b55a6', red: '#c4302b',
  rough: '#efe5de', semiEdge: '#c3b9b1', fwLine: '#4a4a4a', fwRib: '#efefef',
  sand: '#dcae70', sandEdge: '#a7773e', water: '#bcc6e6', waterEdge: '#8f9ccf',
  tree: '#76826a', spec: '#6e9b3c', specEdge: '#4d7426', badge: '#2457a8', elev: '#454545',
};
// tree crowns: lit side, body, shaded rim, feathered edge, and the colour of their soft shadow
const CANOPY = { shadow: '#323a2b', edge: '#7a866c', body: '#a3ad91', shade: '#8f9a7e', lit: '#bcc4ab' };
const CANOPY_PLAY = { shadow: '#2c4419', edge: '#4d7a2a', body: '#86b45e', shade: '#6f9d4a', lit: '#a7cf86' };
const RINGS = { m: [200, 225, 250, 275, 300, 320], yd: [225, 250, 275, 300, 325, 350] };
const RING_COL = ['#7a7a7a', '#3e8a57', '#8f3643', '#c8913b', '#34457d', '#7a4c9e'];

const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
const f1 = (n) => (Math.round(n * 10) / 10).toString();
const pts = (P) => P.map((p) => `${f1(p[0])},${f1(p[1])}`).join(' ');

function txt(x, y, s, size, col = C.ink, anchor = 'middle', bold = true, halo = '#fff') {
  const base = `x="${f1(x)}" y="${f1(y)}" text-anchor="${anchor}" ${FONT} font-weight="${bold ? 700 : 400}" font-size="${f1(size)}"`;
  const back = halo ? `<text ${base} fill="${halo}" stroke="${halo}" stroke-width="${f1(Math.max(3, size * 0.22))}" stroke-linejoin="round">${esc(s)}</text>` : '';
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

// label boxes, so nothing prints over anything else
class Occ {
  constructor() { this.b = []; }
  free(b) { return !this.b.some((o) => !(b[2] < o[0] || b[0] > o[2] || b[3] < o[1] || b[1] > o[3])); }
  take(b) { this.b.push(b); }
  static union(bs) { return [Math.min(...bs.map((b) => b[0])), Math.min(...bs.map((b) => b[1])), Math.max(...bs.map((b) => b[2])), Math.max(...bs.map((b) => b[3]))]; }
  static box(x, y, s, size, anchor) {
    const w = String(s).length * size * 0.5;
    const x0 = anchor === 'end' ? x - w : anchor === 'middle' ? x - w / 2 : x;
    return [x0 - 2, y - size * 0.78, x0 + w + 2, y + size * 0.12];
  }
}

// map view: O at (ox, oy), direction u up the page, s units per metre
function view(O, u, s, ox, oy) {
  const v = G.perpR(u);
  return {
    O, u, v, s,
    tf: (p) => { const d = G.sub(p, O); return [ox + G.dot(d, v) * s, oy - G.dot(d, u) * s]; },
    inv: (x, y) => G.add(O, G.add(G.mul(v, (x - ox) / s), G.mul(u, (oy - y) / s))),
  };
}

// ⊗ mark
const xmark = (x, y, r = 6.5) => `<circle cx="${f1(x)}" cy="${f1(y)}" r="${f1(r)}" fill="#fff" stroke="#555" stroke-width="1.3"/><path d="M${f1(x - r * 0.68)},${f1(y - r * 0.68)} L${f1(x + r * 0.68)},${f1(y + r * 0.68)} M${f1(x - r * 0.68)},${f1(y + r * 0.68)} L${f1(x + r * 0.68)},${f1(y - r * 0.68)}" stroke="#555" stroke-width="1.1"/>`;

// the plates on the centre line
const ICONS = ['yellowdot', 'redcross', 'yellowplus', 'reddot'];
function icon(kind, x, y, k = 1) {
  const r = 9.5 * k;
  if (kind === 'reddot') return `<circle cx="${f1(x)}" cy="${f1(y)}" r="${f1(r)}" fill="${C.red}"/>`;
  if (kind === 'yellowdot') return `<circle cx="${f1(x)}" cy="${f1(y)}" r="${f1(r)}" fill="#f1c21b" stroke="#9c7a08" stroke-width="1"/>`;
  if (kind === 'redsquare') return `<rect x="${f1(x - r)}" y="${f1(y - r)}" width="${f1(2 * r)}" height="${f1(2 * r)}" fill="${C.red}"/>`;
  const col = kind === 'redcross' ? C.red : '#e9b915';
  const a = r * 1.15, t = 2.9 * k;
  return `<path d="M${f1(x - a)},${f1(y - t)} H${f1(x - t)} V${f1(y - a)} H${f1(x + t)} V${f1(y - t)} H${f1(x + a)} V${f1(y + t)} H${f1(x + t)} V${f1(y + a)} H${f1(x - t)} V${f1(y + t)} H${f1(x - a)} Z" fill="${col}" stroke="${kind === 'redcross' ? '#8b1d18' : '#8f7108'}" stroke-width=".9"/>`;
}

function compassRose(x, y, deg, r = 18) {
  return `<g transform="translate(${f1(x)},${f1(y)}) rotate(${f1(deg)})">
    <path d="M0,${-r} L${r * 0.22},0 L0,${r * 0.22} L${-r * 0.22},0 Z" fill="${C.badge}"/>
    <path d="M0,${r} L${r * 0.22},0 L0,${-r * 0.22} L${-r * 0.22},0 Z" fill="#9fb2d9"/>
    <path d="M${-r},0 L0,${r * 0.16} L${r},0 L0,${-r * 0.16} Z" fill="#c6d1e8"/>
    <text x="0" y="${f1(-r - 4)}" text-anchor="middle" ${FONT} font-weight="700" font-size="${f1(r * 0.62)}" fill="${C.badge}" transform="rotate(${f1(-deg)} 0 ${f1(-r - 8)})">N</text></g>`;
}

const unitInfo = (units) => (units === 'yd' ? { k: 1.0936133, key: 'yd', label: 'Yds' } : { k: 1, key: 'm', label: 'Mts' });

// ---------------------------------------------------------------- the spread
export function bookPages(H, course, opts = {}) {
  const PH = opts.pageH || PAGE_H, TH = 2 * PH;       // page height (units): 4 x 7 inch by default
  const SAFE = opts.safe || 0;                       // band at the outer edges kept clear (printer margins)
  const [mmW, mmH] = opts.pageMM || [101.6, 177.8];
  const U = unitInfo(opts.units);
  const n = (m) => Math.round(m * U.k), toM = (v) => v / U.k;
  const rings = RINGS[U.key].filter((rv) => toM(rv) < H.toFront - 12);
  const par3 = H.par <= 3 || H.playCentre < 240;
  const par5 = H.par >= 5;
  const cap = MAX_DRIVE + 0.5;
  const driveRef = !par3 ? H.onLine(toM(U.key === 'yd' ? 275 : 250)) : null;   // where the drive lands (red numbers)
  // heights, the way the tour books print them: a small signed number above a distance is how far the FRONT of the
  // green sits above (+, uphill) or below (−, downhill) that spot; one under a from-tee number is how far that spot sits
  // above (+) or below (−) the back tee. Book units; 0 is level.
  const zAt = (p) => (course.terrain ? course.terrain.at(p) : null);
  const zFront = zAt(H.GF), zTee = zAt(H.TEE);
  const toGreen = (p) => { const a = zAt(p); return a == null || zFront == null ? null : Math.round((zFront - a) * U.k) || 0; };
  const fromTee = (p) => { const a = zAt(p); return a == null || zTee == null ? null : Math.round((a - zTee) * U.k) || 0; };
  const sgn = (v) => (v > 0 ? `+${v}` : v < 0 ? `−${-v}` : '0');
  const elev = (x, y, v, size, anchor = 'start', col = C.elev) => (v == null ? '' : txt(x, y, sgn(v), size, col, anchor, true));

  const photo = !!opts.teeView;
  const PHOTO_H = photo ? 300 : 0, FOOT_H = 104;
  const FT = TH - PHOTO_H - FOOT_H - SAFE;           // map runs from the top of the spread down to the footer
  // two layers: the base (rough, light-rough corridor, tree canopies), which the PDF carries as a 4K picture so the
  // soft shading stays smooth, and everything else, which stays vector (sharp at any zoom)
  const ob = [], o = [];
  ob.push(`<rect width="${W}" height="${TH}" fill="${C.rough}"/>`);

  // ---- scale and orientation: tee at the bottom, green at the top, as large as the spread allows
  // turn the hole on the page (up to 40° off the tee-to-green line) to draw it as large as the spread allows
  const shape = [...H.CL, ...H.green.geom, ...H.fairways.flatMap((f) => f.geom), ...H.tees.flatMap((t) => t.geom)];
  const TOPPAD = 150, BOTPAD = 210, CAP = par3 ? 14 : 10;
  const d0 = G.sub(H.GC, H.TEE), th0 = Math.atan2(d0[1], d0[0]);
  let best = null;
  for (let da = -40; da <= 40; da += 2) {
    const th = th0 + (da * Math.PI) / 180, uu = [Math.cos(th), Math.sin(th)], vv = G.perpR(uu);
    const a = shape.map((p) => G.dot(G.sub(p, H.TEE), uu)), l = shape.map((p) => G.dot(G.sub(p, H.TEE), vv));
    const sc = Math.min(CAP, (FT - TOPPAD - BOTPAD) / (Math.max(...a) - Math.min(...a, 0)), (W - 200) / (Math.max(...l) - Math.min(...l) + 10));
    const score = sc * (1 - 0.0025 * Math.abs(da));
    if (!best || score > best.score) best = { score, u: uu };
  }
  const u = best.u, v = G.perpR(u);
  const al = shape.map((p) => G.dot(G.sub(p, H.TEE), u)), la = shape.map((p) => G.dot(G.sub(p, H.TEE), v));
  const alMin = Math.min(...al, 0), alMax = Math.max(...al);
  const laMin = Math.min(...la), laMax = Math.max(...la);
  const s = Math.min(CAP, (FT - TOPPAD - BOTPAD) / (alMax - alMin), (W - 200) / (laMax - laMin + 10));
  const yMid = (TOPPAD + FT - BOTPAD) / 2, alMid = (alMin + alMax) / 2, laMid = (laMin + laMax) / 2;
  const V = view(H.TEE, u, s, W / 2 - laMid * s, yMid + alMid * s);
  const inMap = ([x, y], pad = 0) => x > pad && x < W - pad && y > pad && y < FT - pad;
  const near = (geom, pad = 80) => geom.some((p) => { const [x, y] = V.tf(p); return x > -pad && x < W + pad && y > -pad && y < FT + pad; });
  const Z = Math.max(0.8, Math.min(1, s / 5));      // print sizes shrink a touch on very long holes

  ob.push(`<clipPath id="mapb§"><rect x="0" y="0" width="${W}" height="${FT}"/></clipPath><g clip-path="url(#mapb§)">`);

  // ---- light rough corridor along the hole (white), outlined where it meets the deeper rough
  const corridor = [];
  for (const f of H.fairways) corridor.push({ d: smoothPath(f.geom.map(V.tf)), w: 2 * 11 * s });
  corridor.push({ d: smoothPath(H.green.geom.map(V.tf)), w: 2 * 10 * s });
  for (const t of H.tees) corridor.push({ d: smoothPath(t.geom.map(V.tf)), w: 2 * 7 * s });
  corridor.push({ d: smoothPath(H.CL.map(V.tf), false), w: 2 * (par3 ? 20 : 15) * s, open: true });
  for (const c of corridor) ob.push(`<path d="${c.d}" fill="${c.open ? 'none' : C.semiEdge}" stroke="${C.semiEdge}" stroke-width="${f1(c.w + 2.6)}" stroke-linejoin="round" stroke-linecap="round"/>`);
  for (const c of corridor) ob.push(`<path d="${c.d}" fill="${c.open ? 'none' : '#fff'}" stroke="#fff" stroke-width="${f1(c.w)}" stroke-linejoin="round" stroke-linecap="round"/>`);

  // ---- tree canopies, drawn as soft shaded crowns (light from the top left of the page) that merge into one mass
  // where they crowd together: each crown a smooth gradient with a feathered edge over a soft shadow, so it stays
  // smooth at any size (no rings or steps when printed or zoomed)
  const TS = opts.treeStyle || 'soft';
  const crowns = (list, P, id) => {
    if (!list.length) return '';
    const L = [...list].sort((a, b) => b.r - a.r);
    if (TS === 'haze') {
      return `<defs><radialGradient id="th${id}§"><stop offset="0" stop-color="${C.tree}" stop-opacity=".62"/><stop offset=".55" stop-color="${C.tree}" stop-opacity=".42"/><stop offset="1" stop-color="${C.tree}" stop-opacity="0"/></radialGradient></defs>`
        + `<g fill="url(#th${id}§)">` + L.map((t) => `<circle cx="${f1(t.x)}" cy="${f1(t.y)}" r="${f1(t.r * 1.15)}"/>`).join('') + '</g>';
    }
    return `<defs><radialGradient id="tb${id}§" cx=".5" cy=".5" r=".5" fx=".38" fy=".34"><stop offset="0" stop-color="${P.lit}"/><stop offset=".6" stop-color="${P.body}"/><stop offset=".88" stop-color="${P.shade}"/><stop offset="1" stop-color="${P.edge}" stop-opacity="0"/></radialGradient>`
      + `<radialGradient id="ts${id}§"><stop offset="0" stop-color="${P.shadow}" stop-opacity=".34"/><stop offset=".7" stop-color="${P.shadow}" stop-opacity=".16"/><stop offset="1" stop-color="${P.shadow}" stop-opacity="0"/></radialGradient></defs>`
      + `<g fill="url(#ts${id}§)">` + L.map((t) => `<circle cx="${f1(t.x + 0.16 * t.r)}" cy="${f1(t.y + 0.22 * t.r)}" r="${f1(t.r * 1.14)}"/>`).join('') + '</g>'
      + `<g fill="url(#tb${id}§)">` + L.map((t) => `<circle cx="${f1(t.x)}" cy="${f1(t.y)}" r="${f1(t.r)}"/>`).join('') + '</g>';
  };
  const trees = course.layers.tree.filter((t) => near([t.c], 40)).map((t) => { const [x, y] = V.tf(t.c); return { t, x, y, r: Math.max(t.r * s, 5) }; });
  // drawn per page (only the crowns on that page), so each page stays light
  const treeLayer = (pg) => crowns(trees.filter((t) => t.y + t.r * 1.3 > pg * PH && t.y - t.r * 1.3 < (pg + 1) * PH), CANOPY, 'a');
  ob.push('¤TREES¤');
  // trees in play: the biggest crowns right beside the landing area, marked with their distances
  const fwPolys = H.fairways.map((f) => f.geom);
  const inPlay = course.layers.tree.filter((t) => {
    const b = H.B(t.c); if (b < 150 || b > cap || t.r < 3.5) return false;
    const dEdge = Math.min(...fwPolys.map((p) => G.ptPolyDist(t.c, p)), Infinity);
    return dEdge > 0 && dEdge - t.r < 6;
  }).sort((a, b) => b.r - a.r).slice(0, 3);
  {
    const list = inPlay.map((t) => { const [x, y] = V.tf(t.c); return { x, y, r: Math.max(t.r * s, 8) }; });
    ob.push(crowns(list, CANOPY_PLAY, 'p'), '</g>');
    o.push(`<clipPath id="map§"><rect x="0" y="0" width="${W}" height="${FT}"/></clipPath><g clip-path="url(#map§)">`);
    for (const t of list) o.push(`<circle cx="${f1(t.x)}" cy="${f1(t.y)}" r="3.6" fill="${C.red}" stroke="#fff" stroke-width=".9"/>`);
  }

  // ---- water
  for (const w of course.layers.water.filter((q) => near(q.geom))) o.push(path(w.geom.map(V.tf), `fill="${C.water}" stroke="${C.waterEdge}" stroke-width="1.6"`));

  // ---- fairways and the green: white, edged with a fine double line
  const rib = Math.max(6.5, Math.min(11, 1.6 * s));
  const ribbon = (geom) => {
    const d = smoothPath(geom.map(V.tf));
    return `<path d="${d}" fill="#fff"/><path d="${d}" fill="none" stroke="${C.fwLine}" stroke-width="${f1(rib + 2.6)}" stroke-linejoin="round"/><path d="${d}" fill="none" stroke="${C.fwRib}" stroke-width="${f1(rib)}" stroke-linejoin="round"/>`;
  };
  for (const f of H.fairways) o.push(ribbon(f.geom));
  // the fill again on top keeps the inside clean, so the double line sits just outside the fairway edge
  for (const f of H.fairways) o.push(path(f.geom.map(V.tf), 'fill="#fff" stroke="none"'));
  o.push(ribbon(H.green.geom));
  o.push(path(H.green.geom.map(V.tf), 'fill="#fff" stroke="none"'));
  o.push(path(H.green.geom.map(V.tf), `fill="none" stroke="${C.fwLine}" stroke-width="1.6"`));
  for (const f of H.fairways) o.push(path(f.geom.map(V.tf), `fill="none" stroke="${C.fwLine}" stroke-width="1.3"`));
  // ---- bunkers and tee boxes of this hole
  for (const b of H.bunkers) o.push(path(b.geom.map(V.tf), `fill="${C.sand}" stroke="${C.sandEdge}" stroke-width="1.5"`));
  for (const t of H.tees) o.push(`<polygon points="${pts(t.geom.map(V.tf))}" fill="#fff" stroke="${C.ink}" stroke-width="2"/>`);
  // ---- the line of play, dotted
  o.push(`<polyline points="${pts(H.CL.map(V.tf))}" fill="none" stroke="#666" stroke-width="1.3" stroke-dasharray="1.2 6" stroke-linecap="round"/>`);
  o.push('</g>');

  // ================= numbers
  const occ = new Occ();
  occ.take([0, PH - 20, W, PH + 20]);                 // the fold between the pages
  if (SAFE) occ.take([0, -50, W, SAFE + 6]);          // nothing in the band along the top edge
  const INSET = [16, FT - 262, 206, FT - 16];
  occ.take(INSET);
  const blueOf = (p) => (H.B(p) <= cap ? `${n(H.B(p))}` : null);
  const redOf = (p) => (par5 && driveRef && H.B(p) > cap ? `${n(G.dist(driveRef, p))}` : null);

  // carry arcs from the back tee: label at the page edge, fairway width where they cross, ticks on the edges
  rings.forEach((rv, i) => {
    const r = toM(rv), col = RING_COL[RINGS[U.key].indexOf(rv)] || C.grey;
    const p0 = H.onLine(r); if (!p0) return;
    const d0 = G.norm(G.sub(p0, H.TEE)), arc = [];
    for (let a = -55; a <= 55; a += 1) {
      const t = (a * Math.PI) / 180;
      arc.push(V.tf(G.add(H.TEE, G.mul([d0[0] * Math.cos(t) - d0[1] * Math.sin(t), d0[0] * Math.sin(t) + d0[1] * Math.cos(t)], r))));
    }
    o.push(`<polyline points="${pts(arc)}" fill="none" stroke="${col}" stroke-width="1.5" opacity=".9" clip-path="url(#map§)"/>`);
    const left = arc.filter((p) => p[0] >= 0 && p[0] <= 60 && p[1] > 20 && p[1] < FT - 20).sort((a, b) => a[0] - b[0])[0];
    if (left) { const b = Occ.box(6, left[1] - 6, rv, 20, 'start'); if (occ.free(b)) { occ.take(b); o.push(txt(6, left[1] - 6, rv, 20, col, 'start', false)); } }
    const w = H.widthAt(p0);
    if (w) {
      for (const e of [w.left, w.right]) { const [x, y] = V.tf(e); o.push(`<circle cx="${f1(x)}" cy="${f1(y)}" r="3.4" fill="${col}"/>`); }
      const [x, y] = V.tf(w.right);
      const b = Occ.box(x + 12, y - 5, n(w.width), 19, 'start');
      if (occ.free(b)) { occ.take(b); o.push(txt(x + 12, y - 5, n(w.width), 19, col, 'start', false)); }
    }
  });

  // centre plates: drive plates where the arcs cross the line of play, then every 25 to the front
  const plates = [];
  rings.forEach((rv) => { const p = H.onLine(toM(rv)); if (p) plates.push({ p, drive: true, kind: driveRef && Math.abs(H.B(p) - toM(U.key === 'yd' ? 275 : 250)) < 1 ? 'redsquare' : null }); });
  for (let r = 25; ; r += 25) {
    const p = H.fromFront(toM(r)); if (!p) break;
    if (H.B(p) < 60) break;
    if (plates.some((q) => q.drive && Math.abs(H.B(q.p) - H.B(p)) < toM(14))) continue;
    // the tee shot is covered by the drive plates: approach plates only beyond the longest drive
    if (!par3 && H.B(p) < (rings.length ? toM(rings[rings.length - 1]) : 0) + 10) continue;
    plates.push({ p });
  }
  plates.sort((a, b) => H.B(a.p) - H.B(b.p));
  plates.forEach((pl, i) => {
    const [x, y] = V.tf(pl.p); if (!inMap([x, y], 30)) return;
    const kind = pl.kind || ICONS[i % ICONS.length];
    o.push(icon(kind, x, y, Z));
    const big = `${n(H.F(pl.p))}`, blue = blueOf(pl.p), red = redOf(pl.p), eg = toGreen(pl.p), et = blue ? fromTee(pl.p) : null;
    // number to the right of the plate, or the left if that is taken
    for (const side of [1, -1]) {
      const ax = x + side * 16 * Z, an = side > 0 ? 'start' : 'end';
      const below = (blue || red) ? 22 * Z : 0, below2 = et != null ? 18 * Z : 0;
      let by = y + 12 * Z;
      if (by - 32 * Z < PH + 20 && by + 12 * Z + below + below2 > PH - 20) by = by < PH ? PH - 20 - 12 * Z - below - below2 : PH + 20 + 32 * Z;   // keep off the fold
      // the room the whole stack takes: height above, the distance, from-tee (or drive) and height below
      const sub = blue || red, bs = [Occ.box(ax, by, big, 36 * Z, an)];
      if (eg != null) bs.push(Occ.box(ax + side * 2, by - 30 * Z, sgn(eg), 17 * Z, an));
      if (sub) bs.push(Occ.box(ax + side * 2, by + 21 * Z, sub, 19 * Z, an));
      if (et != null) bs.push(Occ.box(ax + side * 2, by + 38 * Z, sgn(et), 15 * Z, an));
      const bb = Occ.union(bs);
      if (!occ.free(bb) && side > 0) continue;
      occ.take(bb); occ.take([x - 12, y - 12, x + 12, y + 12]);
      o.push(elev(ax + side * 2, by - 30 * Z, eg, 17 * Z, an));
      o.push(txt(ax, by, big, 36 * Z, C.ink, an, true));
      if (blue) {
        o.push(txt(ax + side * 2, by + 21 * Z, blue, 19 * Z, C.blue, an, false));
        o.push(elev(ax + side * 2, by + 38 * Z, et, 15 * Z, an));
      } else if (red) o.push(txt(ax + side * 2, by + 21 * Z, red, 19 * Z, C.red, an, false));
      break;
    }
  });

  // fairway-edge marks (every 20, offset) and rough marks with leaders to the margin numbers (every 20)
  const edgeRow = (p) => {
    const w = H.widthAt(p); if (!w) return;
    for (const [e, side] of [[w.left, -1], [w.right, 1]]) {
      const [x, y] = V.tf(e); if (!inMap([x, y], 40)) continue;
      const lab = `${n(H.F(e))}`, ax = x + side * 11, an = side < 0 ? 'end' : 'start', eg = toGreen(e);
      const bs = [Occ.box(ax, y + 8, lab, 25 * Z, an), [x - 8 * Z, y - 8 * Z, x + 8 * Z, y + 8 * Z]], bl0 = blueOf(e);
      if (eg != null) bs.push(Occ.box(ax + side, y + 8 - 22 * Z, sgn(eg), 14 * Z, an));
      if (bl0) bs.push(Occ.box(ax, y + 8 + 19 * Z, bl0, 17 * Z, an));
      const bb = Occ.union(bs);
      if (!occ.free(bb)) continue;
      occ.take(bb);
      o.push(xmark(x, y, 6.5 * Z));
      o.push(elev(ax + side, y + 8 - 22 * Z, eg, 14 * Z, an));
      o.push(txt(ax, y + 8, lab, 25 * Z, C.ink, an, true));
      const bl = blueOf(e); if (bl) o.push(txt(ax, y + 8 + 19 * Z, bl, 17 * Z, C.blue, an, false));
    }
  };
  const roughRow = (p) => {
    const w = H.widthAt(p);
    const t = G.tangentAt(H.CL, G.projectOnLine(H.CL, p).s), nr = G.perpR(t);
    const edges = w ? [[w.left, -1], [w.right, 1]] : [[G.sub(p, G.mul(nr, 14)), -1], [G.add(p, G.mul(nr, 14)), 1]];
    for (const [e, side] of edges) {
      let q = G.add(e, G.mul(V.v, side * 20));
      let [qx, qy] = V.tf(q);
      const [ex] = V.tf(e);
      if (qy < 30 || qy > FT - 30) continue;
      const lim = side < 0 ? 150 : W - 150;
      if (side < 0 ? qx < lim : qx > lim) { qx = lim; q = V.inv(qx, qy); }
      if (side < 0 ? qx > ex - 18 : qx < ex + 18) continue;
      const mx = side < 0 ? 12 : W - 12, an = side < 0 ? 'start' : 'end';
      const lab = `${n(H.F(q))}`, bl = blueOf(q), rd = redOf(q), eg = toGreen(q), et = bl ? fromTee(q) : null;
      const b = Occ.box(mx, qy + 10, lab, 30 * Z, an), bs = [b];
      if (eg != null) bs.push(Occ.box(side < 0 ? mx + 2 : mx - 2, qy - 16 * Z, sgn(eg), 15 * Z, an));
      if (bl || rd) bs.push(Occ.box(mx, qy + 10 + 21 * Z, bl || rd, 18 * Z, an));
      if (et != null) bs.push(Occ.box(side < 0 ? mx + 2 : mx - 2, qy + 10 + 37 * Z, sgn(et), 14 * Z, an));
      const bb = Occ.union(bs);
      if (!occ.free(bb)) continue;
      occ.take(bb); occ.take([qx - 9, qy - 9, qx + 9, qy + 9]);
      const lx0 = side < 0 ? b[2] + 6 : qx + 9, lx1 = side < 0 ? qx - 9 : b[0] - 6;
      if (Math.abs(lx1 - lx0) > 8) o.push(`<line x1="${f1(lx0)}" y1="${f1(qy)}" x2="${f1(lx1)}" y2="${f1(qy)}" stroke="#555" stroke-width="1.2" stroke-dasharray="1.2 4" stroke-linecap="round"/>`);
      o.push(xmark(qx, qy, 6.5 * Z));
      o.push(elev(side < 0 ? mx + 2 : mx - 2, qy - 16 * Z, eg, 15 * Z, an));
      o.push(txt(mx, qy + 10, lab, 30 * Z, C.ink, an, true));
      if (bl) {
        o.push(txt(mx, qy + 10 + 21 * Z, bl, 18 * Z, C.blue, an, false));
        o.push(elev(side < 0 ? mx + 2 : mx - 2, qy + 10 + 37 * Z, et, 14 * Z, an));
      } else if (rd) o.push(txt(mx, qy + 10 + 21 * Z, rd, 18 * Z, C.red, an, false));
    }
  };
  const minB = par3 ? 40 : 90, stepR = s > 9 ? 10 : 20;
  for (let r = stepR; ; r += stepR) { const p = H.fromFront(toM(r)); if (!p || H.B(p) < minB) break; roughRow(p); }
  for (let r = stepR / 2; ; r += stepR) { const p = H.fromFront(toM(r)); if (!p || H.B(p) < minB) break; edgeRow(p); }

  // trees in play: distances beside them
  for (const t of inPlay) {
    const [x, y] = V.tf(t.c), r = Math.max(t.r * s, 8), side = H.side(t.c) < 0 ? -1 : 1;
    const ax = x + side * (r + 8), an = side < 0 ? 'end' : 'start', lab = `${n(H.F(t.c))}`, bl = blueOf(t.c);
    const b = Occ.box(ax, y + 6, lab, 24 * Z, an), bb = [b[0], b[1], b[2], b[3] + (bl ? 18 * Z : 0)];
    if (!occ.free(bb)) continue;
    occ.take(bb); o.push(txt(ax, y + 6, lab, 24 * Z, C.ink, an, true));
    if (bl) o.push(txt(ax, y + 6 + 18 * Z, bl, 16 * Z, C.blue, an, false));
  }

  // bunkers and water: front number (black) and from the tee (blue)
  const tag = (pt, main, sub, dx, dy, an) => {
    const [x, y] = V.tf(pt); if (!inMap([x, y], 15)) return;
    for (const [ddx, ddy, a] of [[dx, dy, an], [-dx, dy, an === 'end' ? 'start' : 'end'], [dx, dy + 26, an], [dx, dy - 26, an]]) {
      const b1 = Occ.box(x + ddx, y + ddy, main ?? sub, 24 * Z, a), b2 = sub && main != null ? Occ.box(x + ddx, y + ddy + 18 * Z, sub, 17 * Z, a) : b1;
      const b = [Math.min(b1[0], b2[0]), b1[1], Math.max(b1[2], b2[2]), b2[3]];
      if (!occ.free(b)) continue;
      occ.take(b);
      if (main != null) o.push(txt(x + ddx, y + ddy, main, 24 * Z, C.ink, a, true));
      if (sub) o.push(txt(x + ddx, y + ddy + (main != null ? 18 * Z : 0), sub, 17 * Z, C.blue, a, false));
      return;
    }
  };
  for (const b of H.bunkerPts) {
    tag(b.entry, `${n(H.F(b.entry))}`, b.greenside ? null : blueOf(b.entry), -10, 9, 'end');
    if (!b.greenside && H.B(b.carry) <= cap) tag(b.carry, null, blueOf(b.carry), 10, -5, 'start');
  }
  for (const w of H.waterPts) {
    tag(w.near, `${n(H.F(w.near))}`, blueOf(w.near), -10, 9, 'end');
    tag(w.far, `${n(H.F(w.far))}`, blueOf(w.far), 10, -4, 'start');
  }
  // the green: depth at the back edge (red), front to centre
  {
    const [bx, by] = V.tf(H.greenEdges.B), [fx, fy] = V.tf(H.greenEdges.F);
    const d = `${n(H.greenDepth)}`, b = Occ.box(bx, by - 8, d, 24, 'middle');
    if (occ.free(b)) { occ.take(b); o.push(txt(bx, by - 8, d, 24, C.red, 'middle', true)); }
    o.push(`<circle cx="${f1(fx)}" cy="${f1(fy)}" r="3.2" fill="${C.red}"/><circle cx="${f1(bx)}" cy="${f1(by)}" r="3.2" fill="${C.red}"/>`);
  }

  // run-outs (letters): where a straight drive through the landing area leaves the fairway
  const runouts = [];
  if (!par3) {
    for (const L0 of [toM(RINGS[U.key][1]), toM(RINGS[U.key][2]), toM(RINGS[U.key][3])]) {
      const p = H.onLine(L0), w = p && H.widthAt(p); if (!w) continue;
      for (const f of [0.12, 0.5, 0.88]) {
        const aim = G.add(w.left, G.mul(G.sub(w.right, w.left), f));
        const ro = H.runout(aim); if (!ro || !ro.containsAim) continue;
        const d = H.B(ro.exit);
        if (d < 170 || d > cap || d > H.toFront - 25) continue;
        if (runouts.some((q) => Math.abs(q.d - d) < 5 || G.dist(q.p, ro.exit) < 10)) continue;
        runouts.push({ p: ro.exit, d });
      }
    }
    runouts.sort((a, b) => a.d - b.d).splice(5);
  }
  // carries (numbers): to the fairway, over fairway bunkers and water
  const carries = [];
  if (!par3) {
    if (H.fairwayStart && H.B(H.fairwayStart) >= 110 && H.B(H.fairwayStart) <= cap) carries.push({ p: H.fairwayStart, d: H.B(H.fairwayStart) });
    for (const b of H.bunkerPts) if (!b.greenside && H.B(b.carry) >= 140 && H.B(b.carry) <= cap) carries.push({ p: b.carry, d: H.B(b.carry) });
    for (const w of H.waterPts) if (w.crosses && H.B(w.far) >= 80 && H.B(w.far) <= cap) carries.push({ p: w.far, d: H.B(w.far) });
    carries.sort((a, b) => a.d - b.d).splice(4);
  }
  const circled = (x, y, ch, col = C.blue, r = 11) => `<circle cx="${f1(x)}" cy="${f1(y)}" r="${r}" fill="#fff" stroke="${col}" stroke-width="1.6"/>` + txt(x, y + r * 0.45, ch, r * 1.25, col, 'middle', true, null);
  runouts.forEach((ro, i) => { ro.ch = 'ABCDE'[i]; const [x, y] = V.tf(ro.p); o.push(circled(x, y, ro.ch, C.ink, 10)); occ.take([x - 12, y - 12, x + 12, y + 12]); });
  carries.forEach((c, i) => { c.ch = `${i + 1}`; const [x, y] = V.tf(c.p); o.push(circled(x, y, c.ch, C.ink, 10)); occ.take([x - 12, y - 12, x + 12, y + 12]); });

  // tee boxes: distance from each to the centre of the green
  {
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
      const P = t.geom.map(V.tf), xr = Math.max(...P.map((p) => p[0])), yc = P.reduce((a, p) => a + p[1], 0) / P.length;
      if (yc > FT - 10 || yc < 10) continue;
      shown.push(d);
      for (const dy of [8, 40, -26]) {
        const b = Occ.box(xr + 12, yc + dy, n(d), 26, 'start'), bb = [b[0], b[1], b[2], b[3] + 14];
        if (!occ.free(bb)) continue;
        occ.take(bb);
        o.push(txt(xr + 12, yc + dy, n(d), 26, C.ink, 'start', true));
        o.push(txt(xr + 12, yc + dy + 15, isBack ? 'BACK TEE' : 'TEE', 11, C.grey, 'start', true));
        break;
      }
    }
    const [tx, ty] = V.tf(H.TEE);
    if (!labs.some((x) => x.isBack)) o.push(`<rect x="${f1(tx - 11)}" y="${f1(ty - 15)}" width="22" height="30" rx="3" fill="#fff" stroke="${C.ink}" stroke-width="1.8" stroke-dasharray="5 3"/>` + txt(tx + 20, ty + 8, n(H.playCentre), 26, C.ink, 'start', true) + txt(tx + 20, ty + 23, 'BACK TEE', 11, C.grey, 'start', true));
    o.push(`<rect x="${f1(tx - 5)}" y="${f1(ty - 4)}" width="10" height="8" rx="1.5" fill="#f1c21b" stroke="#555" stroke-width=".9"/>`);
  }

  // ---- inset: the whole hole small, with the compass
  {
    const [x0, y0, x1, y1] = INSET, w = x1 - x0, h = y1 - y0;
    o.push(`<rect x="${x0 + 5}" y="${y0 + 6}" width="${w}" height="${h}" rx="9" fill="#000" fill-opacity=".18"/>`);
    o.push(`<rect x="${x0}" y="${y0}" width="${w}" height="${h}" rx="9" fill="#fff" stroke="#3a3a3a" stroke-width="1.6"/>`);
    o.push(compassRose(x0 + w / 2, y0 + 42, -Math.atan2(u[0], u[1]) * 180 / Math.PI, 16));
    const P = [...H.CL, ...H.green.geom, ...H.tees.flatMap((t) => t.geom)];
    const a2 = P.map((p) => G.dot(G.sub(p, H.TEE), u)), l2 = P.map((p) => G.dot(G.sub(p, H.TEE), v));
    const k = Math.min((h - 96) / (Math.max(...a2) - Math.min(...a2) + 4), (w - 50) / (Math.max(...l2) - Math.min(...l2) + 6));
    const m2 = (Math.max(...l2) + Math.min(...l2)) / 2;
    const IV = view(H.TEE, u, k, x0 + w / 2 - m2 * k, y1 - 18 + Math.min(...a2, 0) * k);
    o.push(`<polyline points="${pts(H.CL.map(IV.tf))}" fill="none" stroke="#555" stroke-width="1.3" stroke-dasharray="1.5 4" stroke-linecap="round"/>`);
    for (const t of H.tees) o.push(`<polygon points="${pts(t.geom.map(IV.tf))}" fill="#fff" stroke="#333" stroke-width="1.3"/>`);
    o.push(path(H.green.geom.map(IV.tf), 'fill="#fff" stroke="#333" stroke-width="1.5"'));
    const [gx, gy] = IV.tf(H.GC); o.push(`<circle cx="${f1(gx)}" cy="${f1(gy)}" r="4.5" fill="#f1c21b" stroke="#8f7108" stroke-width=".8"/>`);
    // the front of the green against the back tee
    o.push(elev(Math.min(gx + 12, x1 - 40), gy + 8, toGreen(H.TEE), 22, 'start', C.ink));
  }

  // ---- footer: run-outs (letters) and carries (numbers)
  o.push(`<rect x="0" y="${FT}" width="${W}" height="${FOOT_H + (photo ? 0 : SAFE)}" fill="#fff"/>`);
  o.push(`<line x1="0" y1="${FT}" x2="${W}" y2="${FT}" stroke="#9b9b9b" stroke-width="1.4"/><line x1="0" y1="${FT + FOOT_H / 2}" x2="${W}" y2="${FT + FOOT_H / 2}" stroke="#d2d2d2" stroke-width="1.1"/>`);
  const row = (items, yy) => {
    const list = [...items].sort((a, b) => b.d - a.d);
    list.forEach((it, i) => {
      const x = 30 + i * 196;
      o.push(circled(x, yy - 9, it.ch, C.blue, 12));
      o.push(txt(x + 19, yy, n(it.d), 28, C.blue, 'start', false, null));
    });
  };
  row(runouts, FT + 36); row(carries, FT + FOOT_H / 2 + 36);
  if (!runouts.length && !carries.length) {
    o.push(txt(30, FT + 36, `F ${n(H.playFront)}   C ${n(H.playCentre)}   B ${n(H.playBack)}`, 28, C.blue, 'start', false, null));
    // the centre of the green against the back tee, and what the shot plays (a metre/yard up adds one)
    const zc = zAt(H.GC), tc = zc == null || zTee == null ? null : Math.round((zc - zTee) * U.k) || 0;
    const dep = `${n(H.greenDepth)} deep · ${n(H.greenWidth)} wide${tc == null ? '' : `  ·  ${sgn(tc)} to the centre  ·  plays ${n(H.playCentre) + tc}`}`;
    o.push(txt(30, FT + FOOT_H / 2 + 36, dep, 24, C.blue, 'start', false, null));
  }

  // ---- the view from the tee, with the plates on it
  const BOT = TH - SAFE, y0p = BOT - PHOTO_H;       // the photo ends above the safe band
  o.push('¤PHOTO¤');
  const ph = [];
  if (photo) {
    ph.push(`<clipPath id="ph§"><rect x="0" y="${y0p}" width="${W}" height="${PHOTO_H}"/></clipPath>`);
    ph.push(`<rect x="0" y="${y0p}" width="${W}" height="${PHOTO_H}" fill="#dfe7da"/>`);
    ph.push(`<image href="${opts.teeView}" x="0" y="${y0p}" width="${W}" height="${PHOTO_H}" preserveAspectRatio="xMidYMid slice" clip-path="url(#ph§)"/>`);
    const tm = opts.teeMarks;
    if (tm && tm.marks && tm.marks.length) {
      const k = Math.max(W / tm.w, PHOTO_H / tm.h), dx = (W - tm.w * k) / 2, dy = (PHOTO_H - tm.h * k) / 2;
      const at = (r) => {
        const M = tm.marks; let a = null, b = null;
        for (const m of M) { if (m.r <= r) a = m; if (m.r >= r && !b) b = m; }
        if (!a || !b) return null;
        const t = b.r === a.r ? 0 : (r - a.r) / (b.r - a.r);
        return { x: dx + (a.x + (b.x - a.x) * t) * k, y: y0p + dy + (a.y + (b.y - a.y) * t) * k, vis: a.vis && b.vis };
      };
      const tags = [];
      plates.filter((pl) => pl.drive).forEach((pl) => {
        const q = at(H.B(pl.p)); if (!q) return;
        if (q.x < 10 || q.x > W - 10 || q.y < y0p + 50 || q.y > BOT - 6) return;
        tags.push({ pl, q, x: q.x });
      });
      // spread the tags so they never overlap (54 units apart), keeping them over their spots where possible
      tags.sort((a, b) => (Math.round(a.q.x / 40) - Math.round(b.q.x / 40)) || (H.B(a.pl.p) - H.B(b.pl.p)));
      for (let it = 0; it < 60; it++) {
        let moved = false;
        for (let i = 1; i < tags.length; i++) {
          const gap = tags[i].x - tags[i - 1].x;
          if (gap < 54) { const d = (54 - gap) / 2; tags[i - 1].x -= d; tags[i].x += d; moved = true; }
        }
        for (const t of tags) t.x = Math.max(26, Math.min(W - 26, t.x));
        if (!moved) break;
      }
      for (const { pl, q, x } of tags) {
        const i = plates.indexOf(pl), kind = pl.kind || ICONS[i % ICONS.length];
        const top = y0p + 58;
        ph.push(`<line x1="${f1(x)}" y1="${f1(top)}" x2="${f1(q.x)}" y2="${f1(q.y)}" stroke="#fff" stroke-width="3.4"/><line x1="${f1(x)}" y1="${f1(top)}" x2="${f1(q.x)}" y2="${f1(q.y)}" stroke="#111" stroke-width="3.4" stroke-dasharray="7 7"/>`);
        ph.push(`<rect x="${f1(x - 24)}" y="${y0p + 4}" width="48" height="54" rx="4" fill="#fff" stroke="#bbb" stroke-width="1"/>` + icon(kind, x, y0p + 20, 0.9));
        ph.push(txt(x, y0p + 51, n(H.B(pl.p)), 20, C.blue, 'middle', true, null));
        ph.push(`<circle cx="${f1(q.x)}" cy="${f1(q.y)}" r="4" fill="#fff" stroke="#111" stroke-width="1.6"/>`);
      }
    }
    // hole number badge and the hole line
    if (SAFE) ph.push(`<rect x="0" y="${BOT}" width="${W}" height="${SAFE}" fill="#fff"/>`);
    ph.push(`<circle cx="70" cy="${BOT - 62}" r="46" fill="${C.badge}" stroke="#fff" stroke-width="6"/>` + txt(70, BOT - 43, H.ref, 54, '#fff', 'middle', true, null));
    ph.push(txt(130, BOT - 26, `PAR ${H.par}  ·  ${n(H.tagDist || H.playCentre)} ${U.label.toUpperCase()}${H.si ? '  ·  SI ' + H.si : ''}`, 24, '#fff', 'start', true, '#1b1b1b'));
  } else {
    ph.push(`<circle cx="${W - 62}" cy="${FT - 58}" r="44" fill="${C.badge}" stroke="#fff" stroke-width="6"/>` + txt(W - 62, FT - 40, H.ref, 50, '#fff', 'middle', true, null));
    ph.push(txt(W - 118, FT - 30, `PAR ${H.par}  ·  ${n(H.tagDist || H.playCentre)} ${U.label.toUpperCase()}${H.si ? '  ·  SI ' + H.si : ''}`, 22, C.ink, 'end', true));
  }
  // a hole whose back tee isn't where the card puts it says so, small, beside the inset
  if (H.note) {
    const lines = [''];
    for (const w_ of H.note.split(' ')) { if ((lines[lines.length - 1] + ' ' + w_).trim().length > 46) lines.push(w_); else lines[lines.length - 1] = (lines[lines.length - 1] + ' ' + w_).trim(); }
    const L3 = lines.slice(0, 3);
    L3.forEach((ln, i) => ph.push(txt(222, FT - (photo ? 22 : 64) - (L3.length - 1 - i) * 19, ln, 16, C.soft, 'start', false)));
  }

  const baseBody = ob.join(''), body = o.join(''), photoLayer = ph.join('');
  const wrap = (k, inner) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 ${k * PH} ${W} ${PH}" width="${mmW}mm" height="${mmH}mm">${inner}</svg>`;
  const ids = (str, k) => str.replace(/§/g, `${String(H.ref).replace(/\W/g, '')}p${k}`);
  // each page: the whole thing (screen, print), and its two layers (PDF)
  const page = (k) => {
    const b = ids(baseBody.replace('¤TREES¤', treeLayer(k)), k), v = ids(body.replace('¤PHOTO¤', k === 1 ? photoLayer : ''), k);
    return { svg: wrap(k, b + v), base: wrap(k, b), overlay: wrap(k, v) };
  };
  return [{ kind: 'top', ...page(0) }, { kind: 'bottom', ...page(1) }];
}
