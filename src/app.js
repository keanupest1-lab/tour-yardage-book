import { searchCourses, fetchCourseData, parseOverpass } from './osm.js';
import { buildCourse } from './course.js';
import { greenPage, holePage } from './render.js';

const $ = (s) => document.querySelector(s);
const state = { course: null, name: '', hole: 0, units: localGet('units') || 'm' };

function localGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function localSet(k, v) { try { localStorage.setItem(k, v); } catch {} }
function status(msg, kind = '') { const el = $('#status'); el.textContent = msg; el.className = 'status ' + kind; }

async function onSearch(e) {
  e.preventDefault();
  const q = $('#q').value.trim(); if (!q) return;
  status('Searching…'); $('#results').innerHTML = '';
  try {
    const rows = await searchCourses(q.match(/golf/i) ? q : q + ' golf');
    if (!rows.length) return status('No courses found. Try the club name plus the town.', 'warn');
    status('');
    $('#results').innerHTML = rows.map((r, i) => `<li><button data-i="${i}"><strong>${escapeHtml(r.name)}</strong><span>${escapeHtml(r.place)}</span></button></li>`).join('');
    $('#results').querySelectorAll('button').forEach((b) => b.addEventListener('click', () => loadCourse(rows[+b.dataset.i])));
  } catch (err) { status(err.message, 'error'); }
}

async function loadCourse(row) {
  $('#results').innerHTML = '';
  status(`Loading ${row.name} from OpenStreetMap…`);
  try {
    const json = await fetchCourseData(row.bbox);
    openCourse(json, row.name);
  } catch (err) { status(err.message, 'error'); }
}

function openCourse(json, name) {
  const course = buildCourse(parseOverpass(json));
  if (!course.holes.length) {
    status('This course has no holes mapped on OpenStreetMap yet (golf=hole lines). It can be added at openstreetmap.org.', 'warn');
    return;
  }
  state.course = course; state.name = name; state.hole = 0;
  status(`${name}: ${course.holes.length} holes mapped.`, 'ok');
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
