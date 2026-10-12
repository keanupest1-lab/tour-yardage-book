import { searchCourses, loadCourseData, parseOverpass } from './osm.js';
import { buildCourse } from './course.js';
import { greenPage, holePage } from './render.js';

const $ = (s) => document.querySelector(s);
const state = { course: null, name: '', hole: 0, units: localGet('units') || 'm', library: [] };

// Saved course library (fetched in the background by GitHub, loads instantly)
fetch('courses/index.json').then((r) => (r.ok ? r.json() : { courses: [] })).then((j) => { state.library = j.courses || []; }).catch(() => {});

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
    try { const j = await (await fetch(`courses/${row.slug}.json`)).json(); return openCourse(j, row.name); }
    catch (err) { return status(`Couldn't open the saved copy: ${err.message}`, 'error'); }
  }
  let step = 'starting…';
  const t0 = Date.now();
  const tick = () => status(`Loading ${row.name}: ${step} (${Math.round((Date.now() - t0) / 1000)}s)`);
  const timer = setInterval(tick, 1000); tick();
  try {
    const json = await loadCourseData(row.bbox, (msg) => { step = msg; tick(); });
    clearInterval(timer);
    openCourse(json, row.name);
  } catch (err) { clearInterval(timer); status(`${err.message} If this keeps happening, ask Claude to add ${row.name} to the saved course library.`, 'error'); }
}

function openCourse(json, name) {
  const course = buildCourse(parseOverpass(json));
  if (!course.holes.length) {
    const c = course.census;
    status(`No holes could be built. On OpenStreetMap this area has ${c.holeLines} hole lines, ${c.greens} greens and ${c.fairways} fairways.`, 'warn');
    return;
  }
  state.course = course; state.name = name; state.hole = 0;
  const c = course.census;
  const skip = course.skipped.length ? ` Skipped: ${course.skipped.map((x) => `${x.ref} (${x.reason})`).join(', ')}.` : '';
  status(`${name}: ${course.holes.length} holes ready. On OpenStreetMap: ${c.holeLines} hole lines, ${c.greens} greens, ${c.fairways} fairways, ${c.bunkers} bunkers.${skip}${c.holeLines < 18 ? ' Holes without a hole line can be added in the course editor (coming next).' : ''}`, course.holes.length >= 9 ? 'ok' : 'warn');
  renderTabs(); renderHole();
  $('#viewer').hidden = false;
}

function renderTabs() {
  $('#tabs').innerHTML = state.course.holes.map((h, i) =>
    `<button class="${i === state.hole ? 'on' : ''}" data-i="${i}" title="Par ${h.par}">${escapeHtml(h.ref)}</button>`).join('');
  $('#tabs').querySelectorAll('button').forEach((b) => b.addEventListener('click', () => { state.hole = +b.dataset.i; renderTabs(); renderHole(); }));
}

function renderHole() {
  const h = state.course.holes[state.hole];
  const opts = { units: state.units };
  $('#pages').innerHTML = `<figure class="page" style="margin:0">${greenPage(h, state.course, opts)}<figcaption>Green page</figcaption></figure><figure class="page" style="margin:0">${holePage(h, state.course, opts)}<figcaption>Hole page</figcaption></figure>`;
  $('#warnings').innerHTML = h.warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join('');
  $('#title').textContent = `${state.name} · Hole ${h.ref}`;
}

function printBook(all) {
  const holes = all ? state.course.holes : [state.course.holes[state.hole]];
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
$('#prev').addEventListener('click', () => { if (state.hole > 0) { state.hole--; renderTabs(); renderHole(); } });
$('#next').addEventListener('click', () => { if (state.hole < state.course.holes.length - 1) { state.hole++; renderTabs(); renderHole(); } });

function loadFixture(path) {
  return fetch(path).then((r) => r.json()).then((j) => openCourse(j, j.name || 'Sample course')).catch((e) => status(e.message, 'error'));
}
// ?fixture=path.json loads saved Overpass JSON (offline testing)
const fx = new URLSearchParams(location.search).get('fixture');
if (fx) loadFixture(fx);

// Demo build (no live map access): sample courses only, no printing
const demo = document.querySelector('[data-demo]');
if (demo) {
  for (const id of ['#search', '#printHole', '#printAll']) { const el = $(id); if (el) el.hidden = true; }
  demo.querySelectorAll('[data-sample]').forEach((b) => b.addEventListener('click', () => loadFixture(b.dataset.sample)));
  loadFixture(demo.dataset.demo);
}
