import { searchCourses, loadCourseData, parseOverpass } from './osm.js';
import { buildCourse } from './course.js';
import { greenPage, holePage } from './render.js';
import { mergeEdits, slugify, hasEdits } from './edits.js';

const $ = (s) => document.querySelector(s);
const state = { course: null, name: '', group: null, hole: 0, units: localGet('units') || 'm', library: [], meta: null };

// Saved course library (fetched in the background by GitHub, loads instantly)
const libraryReady = fetch('courses/index.json', { cache: 'no-store' }).then((r) => (r.ok ? r.json() : { courses: [] })).then((j) => { state.library = j.courses || []; }).catch(() => {});

// Edits from the course editor: the library copy and any newer draft on this device
async function withEdits(json, slug) {
  let server = null, local = null;
  try { const r = await fetch(`courses/edits/${slug}.json`, { cache: 'no-store' }); if (r.ok) server = await r.json(); } catch {}
  try { local = JSON.parse(localGet(`tyb-edits:${slug}`) || 'null'); } catch {}
  const edits = [server, local].filter(Boolean).sort((x, y) => String(y.updated).localeCompare(String(x.updated)))[0];
  return { json: hasEdits(edits) ? mergeEdits(json, edits) : json, edited: hasEdits(edits) };
}

function librarySearch(q) {
  const words = q.toLowerCase().split(/\s+/).filter((w) => w.length > 1 && w !== 'golf' && w !== 'club' && w !== 'country');
  if (!words.length) return [];
  return state.library.filter((c) => { const hay = `${c.name} ${c.query} ${c.place}`.toLowerCase(); return words.every((w) => hay.includes(w)); });
}

function localGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function localSet(k, v) { try { localStorage.setItem(k, v); } catch {} }
function status(msg, kind = '') { const el = $('#status'); el.textContent = msg; el.className = 'status ' + kind; }

async function onSearch(e) {
  e.preventDefault();
  const q = $('#q').value.trim(); if (!q) return;
  status('Searching…'); $('#results').innerHTML = '';
  const saved = librarySearch(q).map((c) => ({ ...c, saved: true }));
  let live = [];
  try { live = await searchCourses(q.match(/golf/i) ? q : q + ' golf'); } catch (err) { if (!saved.length) return status(err.message, 'error'); }
  try {
    const rows = [...saved, ...live];
    if (!rows.length) return status('No courses found. Try the club name plus the town.', 'warn');
    status('');
    $('#results').innerHTML = rows.map((r, i) => `<li><button data-i="${i}"><strong>${escapeHtml(r.name)}${r.saved ? ` <em class="tag">Saved · ${r.census?.holesReady ?? '?'} holes</em>` : ''}</strong><span>${escapeHtml(r.place)}</span></button></li>`).join('');
    $('#results').querySelectorAll('button').forEach((b) => b.addEventListener('click', () => loadCourse(rows[+b.dataset.i])));
  } catch (err) { status(err.message, 'error'); }
}

async function loadCourse(row) {
  $('#results').innerHTML = '';
  if (row.saved) {
    status(`Opening ${row.name}…`);
    try {
      const j = await (await fetch(`courses/${row.slug}.json`)).json();
      const m = await withEdits(j, row.slug);
      return openCourse(m.json, row.name, { slug: row.slug, bbox: row.bbox, place: row.place, saved: true, edited: m.edited });
    }
    catch (err) { return status(`Couldn't open the saved copy: ${err.message}`, 'error'); }
  }
  let step = 'starting…';
  const t0 = Date.now();
  const tick = () => status(`Loading ${row.name}: ${step} (${Math.round((Date.now() - t0) / 1000)}s)`);
  const timer = setInterval(tick, 1000); tick();
  try {
    const json = await loadCourseData(row.bbox, (msg) => { step = msg; tick(); });
    clearInterval(timer);
    const slug = slugify(row.name);
    localSet(`tyb-base:${slug}`, JSON.stringify({ name: row.name, place: row.place, bbox: row.bbox, elements: json.elements }));
    const m = await withEdits(json, slug);
    openCourse(m.json, row.name, { slug, bbox: row.bbox, place: row.place, saved: false, edited: m.edited });
  } catch (err) { clearInterval(timer); status(`${err.message} If this keeps happening, ask Claude to add ${row.name} to the saved course library.`, 'error'); }
}

function editorUrl(meta, name) {
  return `editor.html?slug=${encodeURIComponent(meta.slug)}&name=${encodeURIComponent(name)}&place=${encodeURIComponent(meta.place || '')}&bbox=${meta.bbox.join(',')}${meta.saved ? '&saved=1' : ''}`;
}

function openCourse(json, name, meta = null) {
  state.meta = meta;
  const course = buildCourse(parseOverpass(json));
  $('#editLink').hidden = !meta;
  if (meta) $('#editLink').href = editorUrl(meta, name);
  if (!course.holes.length) {
    const c = course.census;
    status(`No holes yet. On OpenStreetMap this course has ${c.holeLines} hole lines, ${c.greens} greens and ${c.fairways} fairways.`, 'warn');
    if (meta) { $('#results').innerHTML = `<li><a class="button primary" href="${editorUrl(meta, name)}">Trace this course in the editor</a></li>`; }
    return;
  }
  state.course = course; state.name = name; state.hole = 0;
  // several courses here: pick the one whose name best matches, let the user switch
  state.group = null;
  const pick = $('#groupPick');
  if (course.groups.length) {
    const words = name.toLowerCase().split(/\s+/).filter((w) => w.length > 2 && !['golf', 'club', 'course', 'links'].includes(w));
    const score = (g) => words.filter((w) => g.name.toLowerCase().includes(w)).length * 100 + g.holes.length;
    const best = course.groups.reduce((x, y) => (score(y) > score(x) ? y : x));
    state.group = course.groups.indexOf(best);
    pick.innerHTML = course.groups.map((g, i) => `<option value="${i}">${escapeHtml(g.name)} (${g.holes.length})</option>`).join('');
    pick.value = String(state.group); pick.hidden = false;
  } else pick.hidden = true;
  const c = course.census;
  const skip = course.skipped.length ? ` Skipped: ${course.skipped.map((x) => `${x.ref} (${x.reason})`).join(', ')}.` : '';
  status(`${name}: ${course.holes.length} holes ready. On OpenStreetMap: ${c.holeLines} hole lines, ${c.greens} greens, ${c.fairways} fairways, ${c.bunkers} bunkers.${skip}${meta?.edited ? ' Includes your editor changes.' : ''}${course.holes.length < 18 || course.holes.some((h) => h.warnings.length) ? ' Tap Edit course to fill in what is missing.' : ''}`, course.holes.length >= 9 ? 'ok' : 'warn');
  renderTabs(); renderHole();
  $('#viewer').hidden = false;
}

function visibleHoles() {
  const all = state.course.holes.map((h, i) => i);
  return state.group == null ? all : state.course.groups[state.group].holes;
}
function renderTabs() {
  const vis = new Set(visibleHoles());
  if (!vis.has(state.hole)) state.hole = [...vis][0];
  $('#tabs').innerHTML = state.course.holes.map((h, i) => (!vis.has(i) ? '' :
    `<button class="${i === state.hole ? 'on' : ''}" data-i="${i}" title="Par ${h.par}">${escapeHtml(h.ref)}</button>`)).join('');
  $('#tabs').querySelectorAll('button').forEach((b) => b.addEventListener('click', () => { state.hole = +b.dataset.i; renderTabs(); renderHole(); }));
}

function renderHole() {
  const h = state.course.holes[state.hole];
  const opts = { units: state.units };
  if (!h) return;
  $('#pages').innerHTML = `<figure class="page" style="margin:0">${greenPage(h, state.course, opts)}<figcaption>Green page</figcaption></figure><figure class="page" style="margin:0">${holePage(h, state.course, opts)}<figcaption>Hole page</figcaption></figure>`;
  $('#warnings').innerHTML = h.warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join('');
  $('#title').textContent = `${state.name} · Hole ${h.ref}`;
}

function printBook(all) {
  const holes = all ? visibleHoles().map((i) => state.course.holes[i]) : [state.course.holes[state.hole]];
  const opts = { units: state.units };
  $('#print').innerHTML = holes.map((h) => `<div class="pp">${greenPage(h, state.course, opts)}</div><div class="pp">${holePage(h, state.course, opts)}</div>`).join('');
  window.print();
}

function escapeHtml(s) { return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

$('#search').addEventListener('submit', onSearch);
$('#units').value = state.units;
$('#units').addEventListener('change', (e) => { state.units = e.target.value; localSet('units', state.units); if (state.course) renderHole(); });
$('#printHole').addEventListener('click', () => printBook(false));
$('#printAll').addEventListener('click', () => printBook(true));
const step = (d) => { const v = visibleHoles(); const k = v.indexOf(state.hole) + d; if (k >= 0 && k < v.length) { state.hole = v[k]; renderTabs(); renderHole(); } };
$('#prev').addEventListener('click', () => step(-1));
$('#next').addEventListener('click', () => step(1));
$('#groupPick').addEventListener('change', (e) => { state.group = +e.target.value; state.hole = -1; renderTabs(); renderHole(); });

function loadFixture(path) {
  return fetch(path).then((r) => r.json()).then((j) => openCourse(j, j.name || 'Sample course')).catch((e) => status(e.message, 'error'));
}
// ?fixture=path.json loads saved Overpass JSON (offline testing)
const qs = new URLSearchParams(location.search);
const fx = qs.get('fixture');
if (fx) loadFixture(fx);

// ?course=slug opens a library course (or one opened earlier on this device), e.g. from the editor's Preview
const cslug = qs.get('course');
if (cslug) {
  (async () => {
    await libraryReady;
    const lib = state.library.find((c) => c.slug === cslug);
    if (lib) return loadCourse({ ...lib, saved: true });
    const local = JSON.parse(localGet(`tyb-base:${cslug}`) || 'null');
    if (!local) return status('That course is not saved yet. Search for it above.', 'warn');
    const m = await withEdits(local, cslug);
    openCourse(m.json, qs.get('name') || local.name || cslug, { slug: cslug, bbox: local.bbox, place: local.place, saved: false, edited: m.edited });
  })();
}

// Demo build (no live map access): sample courses only, no printing
const demo = document.querySelector('[data-demo]');
if (demo) {
  for (const id of ['#search', '#printHole', '#printAll']) { const el = $(id); if (el) el.hidden = true; }
  demo.querySelectorAll('[data-sample]').forEach((b) => b.addEventListener('click', () => loadFixture(b.dataset.sample)));
  loadFixture(demo.dataset.demo);
}
