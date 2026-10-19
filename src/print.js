// Print-and-cut book: the pages laid four to an A4 side, the way the DECADE-style books are printed at home.
// Each A4 side holds two copies of two pages (left and right columns are the same, so one print run makes two books,
// one for the player and one for the caddie). Pages are 90 x 148.5 mm: the sheet is cut down the middle, across the
// middle and along the two outer lines. Printed both sides (flip on long edge), cut, and stacked, it makes a book
// bound along its top edge: open it and each hole is a spread, the green end on the upper page, the tee end below.
//
// How the pages fall (N sheets, 2N leaves; the book is the bottom halves in sheet order, then the top halves turned
// over as one stack): sheet s, front:  bottom = leaf s front, top = leaf 2N+1-s back (both upright)
//                                back:   bottom = leaf s back,  top = leaf 2N+1-s front (both turned 180 degrees)
import { PAGE_W, PRINT } from './clere.js';

const W = PAGE_W, PH = PRINT.pageH;
export const SHEET = { w: 210, h: 297, colX: [15, 105], rowY: [0, 148.5], pageW: PRINT.pageMM[0], pageH: PRINT.pageMM[1] };
const MM = W / PRINT.pageMM[0];                       // units per millimetre on a print page
const FONT = `font-family="'Roboto Condensed','Arial Narrow','Helvetica Neue',Arial,sans-serif"`;
const GOLD = '#a8863f', GOLD_INK = '#967534', BLUE = '#2b55a6', INK = '#1e1e1e';

const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
const f1 = (n) => (Math.round(n * 10) / 10).toString();
const wrap = (inner) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${PH}" width="${PRINT.pageMM[0]}mm" height="${PRINT.pageMM[1]}mm">${inner}</svg>`;
const text = (x, y, s, size, fill = INK, anchor = 'middle', bold = false) =>
  `<text x="${f1(x)}" y="${f1(y)}" text-anchor="${anchor}" ${FONT} font-weight="${bold ? 700 : 400}" font-size="${f1(size)}" fill="${fill}">${esc(s)}</text>`;

// widths: measure(text, size, bold) from the page (canvas, with the book face loaded); a fair estimate otherwise
const estimate = (s, size, bold) => String(s).length * size * (bold ? 0.53 : 0.47);
// letter-spaced line, one glyph at a time (so it looks the same in the PDF as on screen)
function spaced(m, s, cx, y, size, fill, bold, gap) {
  const ws = [...s].map((ch) => m(ch, size, bold));
  const total = ws.reduce((a, b) => a + b, 0) + gap * (ws.length - 1);
  let x = cx - total / 2, out = '';
  [...s].forEach((ch, i) => { if (ch !== ' ') out += text(x, y, ch, size, fill, 'start', bold); x += ws[i] + gap; });
  return out;
}
// the largest size (up to max) at which the line fits the width
const fitSize = (m, s, max, width, bold) => Math.min(max, (max * width) / Math.max(1, m(s, max, bold)));

export function coverFace({ name, place, par, total, unitsLabel, year, measure }) {
  const m = measure || estimate;
  const o = [`<rect width="${W}" height="${PH}" fill="#fff"/>`];
  const x0 = 4.5 * MM, y0 = 4.5 * MM, x1 = W - 4.5 * MM, y1 = PH - 8 * MM;          // the bottom edge is the paper's edge
  o.push(`<rect x="${f1(x0)}" y="${f1(y0)}" width="${f1(x1 - x0)}" height="${f1(y1 - y0)}" rx="${f1(3 * MM)}" fill="none" stroke="#282828" stroke-width="${f1(0.5 * MM)}"/>`);
  o.push(spaced(m, 'TOUR YARDAGE BOOKS', W / 2, 15 * MM, 4.4 * MM, '#3a3a3a', true, 0.42 * MM));
  o.push(`<line x1="${f1(W / 2 - 12 * MM)}" y1="${f1(18 * MM)}" x2="${f1(W / 2 + 12 * MM)}" y2="${f1(18 * MM)}" stroke="${GOLD}" stroke-width="${f1(0.4 * MM)}"/>`);
  o.push(text(W / 2, 22 * MM, 'Elite yardage book', 2.6 * MM, '#787878'));
  // a flag on its green
  const gy = 39 * MM;
  o.push(`<ellipse cx="${f1(W / 2)}" cy="${f1(gy)}" rx="${f1(14 * MM)}" ry="${f1(3.4 * MM)}" fill="#ecf2e8"/>`);
  o.push(`<line x1="${f1(W / 2 - 0.9 * MM)}" y1="${f1(gy)}" x2="${f1(W / 2 - 0.9 * MM)}" y2="${f1(gy - 13.5 * MM)}" stroke="#282828" stroke-width="${f1(0.45 * MM)}"/>`);
  o.push(`<path d="M${f1(W / 2 - 0.9 * MM)},${f1(gy - 13.5 * MM)} L${f1(W / 2 + 7.6 * MM)},${f1(gy - 11.1 * MM)} L${f1(W / 2 - 0.9 * MM)},${f1(gy - 8.7 * MM)} Z" fill="${GOLD}"/>`);
  o.push(`<circle cx="${f1(W / 2 + 2.6 * MM)}" cy="${f1(gy - 0.5 * MM)}" r="${f1(0.75 * MM)}" fill="#fff" stroke="#3c3c3c" stroke-width="${f1(0.22 * MM)}"/>`);
  // the course name, on one line or two balanced lines
  const nm = String(name || '').toUpperCase(), maxW = x1 - x0 - 10 * MM, big = 7 * MM;
  let lines = [nm];
  if (m(nm, big, true) > maxW) {
    const words = nm.split(/\s+/); let best = null;
    for (let i = 1; i < words.length; i++) {
      const t = [words.slice(0, i).join(' '), words.slice(i).join(' ')];
      const w = Math.max(...t.map((x) => m(x, big, true)));
      if (!best || w < best.w) best = { t, w };
    }
    if (best) lines = best.t;
  }
  const size = Math.min(...lines.map((l) => fitSize(m, l, big, maxW, true)));
  let y = 53 * MM;
  lines.forEach((l, i) => o.push(text(W / 2, y + i * size * 1.08, l, size, GOLD_INK, 'middle', true)));
  y += (lines.length - 1) * size * 1.08 + 7 * MM;
  if (place) { o.push(text(W / 2, y, place, fitSize(m, place, 3.4 * MM, maxW, true), INK, 'middle', true)); y += 5 * MM; }
  const line2 = `Par ${par}  ·  ${Number(total).toLocaleString('en-ZA')} ${unitsLabel}  ·  Back tees`;
  o.push(text(W / 2, y, line2, fitSize(m, line2, 3.4 * MM, maxW, true), INK, 'middle', true)); y += 8.5 * MM;
  o.push(spaced(m, unitsLabel.toUpperCase(), W / 2, y, 5 * MM, BLUE, true, 0.85 * MM));
  // the gold band
  const by = Math.max(y + 5 * MM, 86 * MM);
  o.push(`<rect x="${f1(x0 + 0.25 * MM)}" y="${f1(by)}" width="${f1(x1 - x0 - 0.5 * MM)}" height="${f1(10 * MM)}" fill="${GOLD}"/>`);
  o.push(spaced(m, `${year} COURSE GUIDE  ·  YARDAGE BOOK`, W / 2, by + 6.6 * MM, 3.05 * MM, '#fff', true, 0.27 * MM));
  // how to read a plate: a sample, each number with what it means
  const u = unitsLabel === 'yards' ? 'yds' : 'm';
  const rows = [
    ['−2', 2.4, '#454545', true, `green front 2 ${u} below this spot: downhill (+ uphill)`],
    ['151', 4.0, '#1b1b1b', true, 'to the front of the green'],
    ['250', 2.8, BLUE, false, `from the back tee (to ${u === 'yds' ? '350 yds' : '320 m'})`],
    ['−3', 2.3, '#454545', true, `this spot is 3 ${u} below the back tee`],
  ];
  const xr = 25 * MM, xt = 28.5 * MM, noteW = x1 - xt - 3 * MM;
  let ky = by + 13.5 * MM;
  const ky0 = ky - 3 * MM;
  rows.forEach(([s, sz, col, bold, note], i) => {
    if (i) ky += sz * MM * 1.12;
    o.push(text(xr, ky, s, sz * MM, col, 'end', bold));
    o.push(text(xt, ky - (sz > 3.5 ? 0.8 * MM : 0.15 * MM), note, fitSize(m, note, 2.1 * MM, noteW, false), '#5f5f5f', 'start'));
  });
  o.push(`<line x1="${f1(xr + 1.7 * MM)}" y1="${f1(ky0)}" x2="${f1(xr + 1.7 * MM)}" y2="${f1(ky + 0.8 * MM)}" stroke="#cdcdcd" stroke-width="${f1(0.2 * MM)}"/>`);
  const red = 'Red: from the drive (par 5s).  Circled letters: run-outs.  Circled numbers: carries.';
  o.push(text(W / 2, ky + 5 * MM, red, fitSize(m, red, 2.1 * MM, x1 - x0 - 6 * MM, false), '#5f5f5f'));
  const made = [`Made ${new Date().toISOString().slice(0, 10)} with Tour Yardage Books from satellite imagery, laser and satellite`,
    'height data and the club scorecard. Check distances on site before competition use.'];
  const msz = Math.min(...made.map((l) => fitSize(m, l, 1.75 * MM, x1 - x0 - 6 * MM, false)));
  made.forEach((l, i) => o.push(text(W / 2, y1 - 5.2 * MM + i * 2.4 * MM, l, msz, '#8c8c8c')));
  return wrap(o.join(''));
}

// club distances to fill in (the page above the notes at the back of the book)
export function clubsFace({ unitsLabel, measure }) {
  const m = measure || estimate;
  const o = [`<rect width="${W}" height="${PH}" fill="#fff"/>`];
  o.push(spaced(m, 'CLUB DISTANCES', W / 2, 13.5 * MM, 4.2 * MM, '#2a2a2a', true, 0.4 * MM));
  o.push(text(W / 2, 18 * MM, `${unitsLabel[0].toUpperCase()}${unitsLabel.slice(1)}`, 2.5 * MM, '#787878'));
  const clubs = ['Driver', '3 wood', '5 wood', 'Hybrid', '3 iron', '4 iron', '5 iron', '6 iron', '7 iron', '8 iron', '9 iron', 'PW', 'GW', 'SW', 'LW'];
  const x0 = 6 * MM, x1 = W - 6 * MM, c1 = x0 + 30 * MM, c2 = c1 + (x1 - c1) / 2;
  const top = 22 * MM, head = 7 * MM, rowH = (PH - 6 * MM - top - head) / clubs.length;
  o.push(`<rect x="${f1(x0)}" y="${f1(top)}" width="${f1(x1 - x0)}" height="${f1(head + rowH * clubs.length)}" fill="none" stroke="#555" stroke-width="${f1(0.3 * MM)}"/>`);
  o.push(`<rect x="${f1(x0)}" y="${f1(top)}" width="${f1(x1 - x0)}" height="${f1(head)}" fill="#f1ede3"/>`);
  o.push(text(x0 + 2.5 * MM, top + head - 2.2 * MM, 'Club', 2.6 * MM, '#333', 'start', true));
  o.push(text((c1 + c2) / 2, top + head - 2.2 * MM, 'Carry', 2.6 * MM, '#333', 'middle', true));
  o.push(text((c2 + x1) / 2, top + head - 2.2 * MM, 'Total', 2.6 * MM, '#333', 'middle', true));
  clubs.forEach((c, i) => {
    const y = top + head + rowH * (i + 1);
    if (i < clubs.length - 1) o.push(`<line x1="${f1(x0)}" y1="${f1(y)}" x2="${f1(x1)}" y2="${f1(y)}" stroke="#c9c9c9" stroke-width="${f1(0.2 * MM)}"/>`);
    o.push(text(x0 + 2.5 * MM, y - rowH / 2 + 1 * MM, c, 2.7 * MM, '#2a2a2a', 'start'));
  });
  for (const x of [c1, c2]) o.push(`<line x1="${f1(x)}" y1="${f1(top)}" x2="${f1(x)}" y2="${f1(top + head + rowH * clubs.length)}" stroke="#9a9a9a" stroke-width="${f1(0.25 * MM)}"/>`);
  return wrap(o.join(''));
}

// ruled notes (the page under the club distances; its bottom edge is the paper's edge)
export function notesFace({ title = 'NOTES', measure } = {}) {
  const m = measure || estimate;
  const o = [`<rect width="${W}" height="${PH}" fill="#fff"/>`];
  o.push(spaced(m, title, W / 2, 13.5 * MM, 4.2 * MM, '#2a2a2a', true, 0.6 * MM));
  for (let y = 24 * MM; y < PH - 9 * MM; y += 7.2 * MM) o.push(`<line x1="${f1(6 * MM)}" y1="${f1(y)}" x2="${f1(W - 6 * MM)}" y2="${f1(y)}" stroke="#cfcfcf" stroke-width="${f1(0.2 * MM)}"/>`);
  return wrap(o.join(''));
}

export function backFace({ name, measure }) {
  const m = measure || estimate;
  const o = [`<rect width="${W}" height="${PH}" fill="#fff"/>`];
  o.push(spaced(m, 'TOUR YARDAGE BOOKS', W / 2, PH * 0.52, 3.4 * MM, '#8a8a8a', true, 0.35 * MM));
  o.push(`<line x1="${f1(W / 2 - 9 * MM)}" y1="${f1(PH * 0.52 + 2.6 * MM)}" x2="${f1(W / 2 + 9 * MM)}" y2="${f1(PH * 0.52 + 2.6 * MM)}" stroke="${GOLD}" stroke-width="${f1(0.35 * MM)}"/>`);
  if (name) o.push(text(W / 2, PH * 0.52 + 7 * MM, name, fitSize(m, name, 2.6 * MM, W - 16 * MM, false), '#9a9a9a'));
  return wrap(o.join(''));
}

// a page turned 180 degrees (backs of the leaves)
export function turned(svg) {
  const vb = svg.match(/viewBox="([^"]+)"/);
  if (!vb) return svg;
  const [x, y, w, h] = vb[1].split(/\s+/).map(Number);
  const open = svg.indexOf('>') + 1, close = svg.lastIndexOf('</svg>');
  return `${svg.slice(0, open)}<g transform="rotate(180 ${f1(x + w / 2)} ${f1(y + h / 2)})">${svg.slice(open, close)}</g></svg>`;
}

// faces in reading order -> sheets: [{ side: 'front'|'back', sheet, slots: [{ face, row: 0 top | 1 bottom, turned }] }]
export function impose(faces) {
  const L = faces.length / 2, N = L / 2;
  if (faces.length % 4) throw new Error('the book needs a multiple of four pages');
  const front = (l) => faces[2 * l - 2], back = (l) => faces[2 * l - 1];
  const sides = [];
  for (let s = 1; s <= N; s++) {
    sides.push({ sheet: s, side: 'front', slots: [{ face: back(2 * N + 1 - s), row: 0, turned: false }, { face: front(s), row: 1, turned: false }] });
    sides.push({ sheet: s, side: 'back', slots: [{ face: front(2 * N + 1 - s), row: 0, turned: true }, { face: back(s), row: 1, turned: true }] });
  }
  return { sheets: N, sides };
}
