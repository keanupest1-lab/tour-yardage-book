// Course editor: trace holes on satellite imagery. Edits autosave on this device and save to the course library.
import { loadCourseData } from './osm.js';
import { emptyEdits, mergeEdits, slugify } from './edits.js';

const L = window.L;
const $ = (s) => document.querySelector(s);
const params = new URLSearchParams(location.search);
const ls = { get: (k) => { try { return localStorage.getItem(k); } catch { return null; } }, set: (k, v) => { try { localStorage.setItem(k, v); return true; } catch { return false; } } };

const S = {
  slug: params.get('slug') || slugify(params.get('name') || 'course'),
  name: params.get('name') || 'Course',
  place: params.get('place') || '',
  bbox: (params.get('bbox') || '').split(',').map(Number),
  saved: params.get('saved') === '1',
  base: null, edits: null, hole: 1, maxHole: 18, tool: null, pending: [],
};
const COLORS = { line: '#ff9f1c', green: '#7CFC9A', fairway: '#3fbf6a', bunker: '#f1d38a', water: '#4aa3ff', tee: '#ffffff' };

function hint(msg, kind = '') { const el = $('#hint'); el.textContent = msg; el.className = 'hint ' + kind; }
function persist() { S.edits.updated = new Date().toISOString(); ls.set(`tyb-edits:${S.slug}`, JSON.stringify(S.edits)); }

// ---------- map ----------
const map = L.map('map', { zoomControl: false, attributionControl: true, maxZoom: 21 });
window.__edmap = map; // test hook
L.control.zoom({ position: 'topright' }).addTo(map);
const base = L.layerGroup().addTo(map), edited = L.layerGroup().addTo(map), draft = L.layerGroup().addTo(map);

async function addImagery() {
  let token = null;
  try { const r = await fetch('api/config'); if (r.ok) token = (await r.json()).mapboxToken; } catch {}
  if (token) {
    L.tileLayer(`https://api.mapbox.com/styles/v1/mapbox/satellite-v9/tiles/512/{z}/{x}/{y}@2x?access_token=${token}`,
      { tileSize: 512, zoomOffset: -1, maxZoom: 21, maxNativeZoom: 20, attribution: '© Mapbox © Maxar © OpenStreetMap' }).addTo(map);
  } else {
    L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      { maxZoom: 21, maxNativeZoom: 19, attribution: 'Imagery © Esri' }).addTo(map);
  }
}

// ---------- data ----------
async function loadBase() {
  if (S.saved) {
    const r = await fetch(`courses/${S.slug}.json`);
    if (r.ok) return r.json();
  }
  const cached = ls.get(`tyb-base:${S.slug}`);
  if (cached) return JSON.parse(cached);
  hint('Loading map data for this course…');
  const json = await loadCourseData(S.bbox, (m) => hint(m));
  ls.set(`tyb-base:${S.slug}`, JSON.stringify({ name: S.name, place: S.place, bbox: S.bbox, elements: json.elements }));
  return json;
}

async function loadEdits() {
  let server = null;
  try { const r = await fetch(`courses/edits/${S.slug}.json`, { cache: 'no-store' }); if (r.ok) server = await r.json(); } catch {}
  let local = null;
  try { local = JSON.parse(ls.get(`tyb-edits:${S.slug}`) || 'null'); } catch {}
  const newest = [server, local].filter(Boolean).sort((a, b) => String(b.updated).localeCompare(String(a.updated)))[0];
  return newest || emptyEdits(S.slug, S.name);
}

// ---------- drawing ----------
const ll = (g) => g.map((p) => [p.lat, p.lon]);
function drawBase() {
  base.clearLayers();
  for (const e of S.base.elements || []) {
    const t = e.tags || {};
    if (!e.geometry) continue;
    const g = ll(e.geometry);
    const opt = { interactive: false, weight: 2, opacity: 0.9 };
    if (t.golf === 'hole') {
      L.polyline(g, { ...opt, color: '#ffd166', weight: 2, dashArray: '6 6' }).addTo(base);
      if (t.ref) L.tooltip({ permanent: true, direction: 'center', className: 'hole-label' }).setLatLng(g[0]).setContent(t.ref).addTo(base);
    } else if (t.golf === 'green') L.polygon(g, { ...opt, color: COLORS.green, fillOpacity: 0.15 }).addTo(base);
    else if (t.golf === 'fairway') L.polygon(g, { ...opt, color: COLORS.fairway, fillOpacity: 0.08 }).addTo(base);
    else if (t.golf === 'bunker' || t.natural === 'sand') L.polygon(g, { ...opt, color: COLORS.bunker, fillOpacity: 0.2 }).addTo(base);
    else if (t.golf === 'tee') L.polygon(g, { ...opt, color: COLORS.tee, fillOpacity: 0.1 }).addTo(base);
    else if (t.golf === 'water_hazard' || t.golf === 'lateral_water_hazard' || t.natural === 'water') L.polygon(g, { ...opt, color: COLORS.water, fillOpacity: 0.15 }).addTo(base);
    else if (t.leisure === 'golf_course') L.polygon(g, { ...opt, color: '#ffffff', weight: 1, dashArray: '2 6', fill: false }).addTo(base);
  }
}

function drawEdits() {
  edited.clearLayers();
  for (const [ref, h] of Object.entries(S.edits.holes)) {
    if (!h.line || h.line.length < 2) continue;
    const cur = String(ref) === String(S.hole);
    L.polyline(h.line, { color: COLORS.line, weight: cur ? 5 : 3, opacity: cur ? 1 : 0.7, interactive: false }).addTo(edited);
    L.circleMarker(h.line[0], { radius: 6, color: '#fff', weight: 2, fillColor: COLORS.line, fillOpacity: 1, interactive: false }).addTo(edited);
    L.tooltip({ permanent: true, direction: 'top', offset: [0, -8], className: 'hole-label' }).setLatLng(h.line[0]).setContent(String(ref)).addTo(edited);
  }
  for (const s of S.edits.shapes) L.polygon(s.coords, { color: COLORS[s.kind], weight: 3, fillOpacity: 0.25, interactive: false }).addTo(edited);
}

function drawDraft() {
  draft.clearLayers();
  if (!S.pending.length) return;
  const col = COLORS[S.tool] || '#fff';
  if (S.tool === 'line') L.polyline(S.pending, { color: col, weight: 4, dashArray: '8 6' }).addTo(draft);
  else if (S.pending.length > 1) L.polygon(S.pending, { color: col, weight: 3, dashArray: '6 5', fillOpacity: 0.15 }).addTo(draft);
  S.pending.forEach((p, i) => L.circleMarker(p, { radius: i === 0 ? 7 : 5, color: '#000', weight: 1, fillColor: col, fillOpacity: 1, interactive: false }).addTo(draft));
}

// ---------- hole status ----------
function holeStatus(ref) {
  const merged = mergeEdits(S.base, S.edits).elements;
  const line = merged.find((e) => e.tags?.golf === 'hole' && String(e.tags.ref) === String(ref));
  if (!line) return 'none';
  const end = line.geometry[line.geometry.length - 1];
  const near = merged.some((e) => e.tags?.golf === 'green' && e.geometry.some((p) => distM([p.lat, p.lon], [end.lat, end.lon]) < 45));
  return near ? 'done' : 'part';
}
function distM(a, b) {
  const k = Math.cos((a[0] * Math.PI) / 180) * 111320;
  return Math.hypot((a[1] - b[1]) * k, (a[0] - b[0]) * 110540);
}
function lineLen(pts) { let s = 0; for (let i = 1; i < pts.length; i++) s += distM(pts[i - 1], pts[i]); return s; }

function renderHoleUI() {
  $('#holeLbl').textContent = `Hole ${S.hole}`;
  const osmPar = (S.base.elements || []).find((e) => e.tags?.golf === 'hole' && String(e.tags.ref) === String(S.hole))?.tags?.par;
  $('#par').value = String(S.edits.holes[S.hole]?.par || osmPar || 4);
  $('#chips').innerHTML = Array.from({ length: S.maxHole }, (_, i) => i + 1).map((r) =>
    `<button type="button" class="chip ${holeStatus(r)} ${r === S.hole ? 'on' : ''}" data-r="${r}" aria-label="Hole ${r}">${r}</button>`).join('');
  $('#chips').querySelectorAll('button').forEach((b) => b.addEventListener('click', () => selectHole(+b.dataset.r)));
  drawEdits();
}

function selectHole(r) {
  S.hole = Math.max(1, Math.min(S.maxHole, r));
  cancelDraft();
  renderHoleUI();
  // zoom to this hole if it has a line
  const merged = mergeEdits(S.base, S.edits).elements;
  const line = merged.find((e) => e.tags?.golf === 'hole' && String(e.tags.ref) === String(S.hole));
  if (line) map.fitBounds(L.latLngBounds(ll(line.geometry)).pad(0.25), { maxZoom: 19 });
}

// ---------- tools ----------
const HINTS = {
  line: 'Tap the back tee box, then any bends on a dogleg, then the green. It snaps to the tee box and the middle of the green, and saves when you tap the green.',
  green: 'Tap around the edge of the green, then Done.', fairway: 'Tap around the fairway edge, then Done.',
  bunker: 'Tap around the bunker, then Done.', water: 'Tap around the water, then Done.', tee: 'Tap the corners of the tee box, then Done.',
  delete: 'Tap a shape or hole line you added to delete it.',
};
function setTool(t) {
  S.tool = S.tool === t ? null : t; S.pending = [];
  document.querySelectorAll('#tools button').forEach((b) => b.classList.toggle('on', b.dataset.tool === S.tool));
  hint(S.tool ? HINTS[S.tool] : 'Pick a tool.'); drawDraft();
}
function cancelDraft() { S.pending = []; drawDraft(); }

// tee boxes and greens to snap to (map data + shapes added in the editor or by the AI)
function shapesOf(kind) {
  const out = [];
  for (const e of S.base.elements || []) if (e.tags?.golf === kind && e.geometry) out.push(e.geometry.map((q) => [q.lat, q.lon]));
  for (const sh of S.edits.shapes) if (sh.kind === kind) out.push(sh.coords);
  return out;
}
function centreOf(poly) { const n = poly.length; return [poly.reduce((a, q) => a + q[0], 0) / n, poly.reduce((a, q) => a + q[1], 0) / n]; }
function snapTo(kind, p, maxM) {
  let best = null, bd = Infinity;
  for (const poly of shapesOf(kind)) {
    const c = centreOf(poly), d = pointInPoly(p, poly) ? 0 : distM(p, c);
    if (d < bd) { bd = d; best = c; }
  }
  return bd <= maxM ? best.map((v) => +v.toFixed(7)) : null;
}

map.on('click', (ev) => {
  let p = [+ev.latlng.lat.toFixed(7), +ev.latlng.lng.toFixed(7)];
  if (!S.tool) return hint('Pick a tool first: Hole line, Green, Fairway…');
  if (S.tool === 'delete') return deleteAt(p);
  if (S.tool === 'line') {
    // first tap: onto the tee box; a tap on a green after that: the middle of the green, and the hole is done
    if (!S.pending.length) { const t = snapTo('tee', p, 25); if (t) p = t; }
    else {
      const g = snapTo('green', p, 18);
      if (g) { S.pending.push(g); drawDraft(); return finish(); }
    }
  }
  S.pending.push(p); drawDraft();
  if (S.tool === 'line') hint(`Hole ${S.hole}: ${S.pending.length} point${S.pending.length > 1 ? 's' : ''} · ${Math.round(lineLen(S.pending))} m. Last tap = middle of green. Then Done.`);
  else hint(`${S.pending.length} points. ${S.pending.length >= 3 ? 'Tap Done to finish.' : ''}`);
});

function finish() {
  if (!S.tool || S.tool === 'delete') return;
  if (S.tool === 'line') {
    if (S.pending.length < 2) return hint('A hole line needs at least the tee and the green.', 'err');
    S.edits.holes[S.hole] = { ...(S.edits.holes[S.hole] || {}), par: +$('#par').value, line: S.pending.slice() };
    persist(); hint(`Hole ${S.hole} line saved on this phone (${Math.round(lineLen(S.pending))} m).`, 'ok');
  } else {
    if (S.pending.length < 3) return hint('Tap at least 3 points around the shape.', 'err');
    S.edits.shapes.push({ id: `s${Date.now()}`, kind: S.tool, coords: S.pending.slice() });
    persist(); hint(`${S.tool[0].toUpperCase() + S.tool.slice(1)} added. Keep tapping for another, or pick a new tool.`, 'ok');
  }
  S.pending = []; drawDraft(); renderHoleUI();
}

function pointInPoly(p, poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [yi, xi] = poly[i], [yj, xj] = poly[j];
    if ((yi > p[0]) !== (yj > p[0]) && p[1] < ((xj - xi) * (p[0] - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
function deleteAt(p) {
  const i = S.edits.shapes.findIndex((s) => pointInPoly(p, s.coords));
  if (i >= 0) { const k = S.edits.shapes[i].kind; S.edits.shapes.splice(i, 1); persist(); renderHoleUI(); return hint(`Deleted ${k}.`, 'ok'); }
  for (const [ref, h] of Object.entries(S.edits.holes)) {
    if (h.line?.some((q) => distM(p, q) < 20)) { delete S.edits.holes[ref]; persist(); renderHoleUI(); return hint(`Deleted the hole ${ref} line.`, 'ok'); }
  }
  hint('Nothing you added is there. (Shapes from OpenStreetMap can’t be deleted here.)', 'err');
}

// ---------- save / preview ----------
async function save() {
  const key = ls.get('tyb-key');
  if (!key) { $('#keyRow').hidden = false; return hint('Enter your access key once to save to the course library.'); }
  hint('Saving to the course library…');
  try {
    const body = { slug: S.slug, name: S.name, place: S.place, bbox: S.bbox, edits: S.edits };
    if (!S.saved) body.base = { elements: S.base.elements };
    const r = await fetch(`api/save?key=${encodeURIComponent(key)}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (r.status === 401) { ls.set('tyb-key', ''); $('#keyRow').hidden = false; return hint('That access key was wrong. Enter it again.', 'err'); }
    if (!r.ok) return hint(`Not saved: ${j.error || r.status}. Your edits are still on this phone.`, 'err');
    S.saved = true;
    hint('Saved. The book updates for everyone in about a minute.', 'ok');
  } catch (e) { hint(`Not saved: ${e.message}. Your edits are still on this phone.`, 'err'); }
}

function preview() {
  if (!S.saved) ls.set(`tyb-base:${S.slug}`, JSON.stringify({ name: S.name, place: S.place, bbox: S.bbox, elements: S.base.elements }));
  location.href = `index.html?course=${encodeURIComponent(S.slug)}&name=${encodeURIComponent(S.name)}`;
}

// ---------- wiring ----------
document.querySelectorAll('#tools button').forEach((b) => b.addEventListener('click', () => setTool(b.dataset.tool)));
$('#done').addEventListener('click', finish);
$('#undo').addEventListener('click', () => { S.pending.pop(); drawDraft(); });
$('#cancel').addEventListener('click', () => { cancelDraft(); hint('Cancelled.'); });
$('#prevH').addEventListener('click', () => selectHole(S.hole - 1));
$('#nextH').addEventListener('click', () => selectHole(S.hole + 1));
$('#par').addEventListener('change', () => { S.edits.holes[S.hole] = { ...(S.edits.holes[S.hole] || {}), par: +$('#par').value }; persist(); });
$('#save').addEventListener('click', save);
$('#back').addEventListener('click', (ev) => { ev.preventDefault(); preview(); });
$('#keyOk').addEventListener('click', () => { const v = $('#key').value.trim(); if (!v) return; ls.set('tyb-key', v); $('#keyRow').hidden = true; save(); });

(async function start() {
  $('#title').textContent = S.name;
  document.title = `${S.name} · Course Editor`;
  if (!S.bbox.every(Number.isFinite) || S.bbox.length !== 4) { hint('Open the editor from a course in the book.', 'err'); return; }
  map.fitBounds([[S.bbox[0], S.bbox[2]], [S.bbox[1], S.bbox[3]]]);
  await addImagery();
  try {
    S.base = await loadBase();
    S.edits = await loadEdits();
    S.edits.slug = S.slug; S.edits.name = S.name;
    const refs = (S.base.elements || []).filter((e) => e.tags?.golf === 'hole').map((e) => parseInt(e.tags.ref) || 0);
    S.maxHole = Math.max(18, ...refs, ...Object.keys(S.edits.holes).map(Number));
    drawBase(); renderHoleUI();
    const pts = (S.base.elements || []).filter((e) => e.tags?.golf).flatMap((e) => (e.geometry || []).map((p) => [p.lat, p.lon]));
    if (pts.length) map.fitBounds(L.latLngBounds(pts).pad(0.05));
    hint('Pick a hole and a tool. Green chips are complete, gold ones need a green.');
  } catch (e) { hint(`Couldn't load the course: ${e.message}`, 'err'); }
})();
