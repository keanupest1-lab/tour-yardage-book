import { searchCourses, loadCourseData, parseOverpass } from './osm.js';
import { buildCourse } from './course.js';
import { greenPage, holePage } from './render.js';
import { mergeEdits, slugify, hasEdits } from './edits.js';
import { loadTerrain } from './terrain.js';

const $ = (s) => document.querySelector(s);
const state = { course: null, name: '', group: null, hole: 0, units: localGet('units') || 'm', library: [], meta: null, pushed: false };
const YD = 1.0936133;

// Saved course library (fetched in the background by GitHub, loads instantly)
const libraryReady = fetch('courses/index.json', { cache: 'no-store' }).then((r) => (r.ok ? r.json() : { courses: [] }))
  .then((j) => { state.library = j.courses || []; }).catch(() => {});

// Edits from the course editor: the library copy and any newer draft on this device
async function withEdits(json, slug) {
  let server = null, local = null;
  try { const r = await fetch(`courses/edits/${slug}.json`, { cache: 'no-store' }); if (r.ok) server = await r.json(); } catch {}
  try { local = JSON.parse(localGet(`tyb-edits:${slug}`) || 'null'); } catch {}
  const edits = [server, local].filter(Boolean).sort((x, y) => String(y.updated).localeCompare(String(x.updated)))[0];
  const out = hasEdits(edits) ? mergeEdits(json, edits) : json;
  // course names at clubs with several courses: { "Other holes": "East Course", "Blue Course": null (another club) }
  const groups = (server && server.groups) || (edits && edits.groups);
  if (groups) Object.defineProperty(out, '__groups', { value: groups, enumerable: false, configurable: true });
  return { json: out, edited: hasEdits(edits) };
}

function nameGroups(course, names) {
  if (!names || !course.groups.length) return course;
  course.groups = course.groups.filter((g) => names[g.name] !== null).map((g) => ({ ...g, name: names[g.name] || g.name }));
  return course;
}

function librarySearch(q) {
  const words = q.toLowerCase().split(/\s+/).filter((w) => w.length > 1 && w !== 'golf' && w !== 'club' && w !== 'country');
  if (!words.length) return [];
  return state.library.filter((c) => { const hay = `${c.name} ${c.query} ${c.place}`.toLowerCase(); return words.every((w) => hay.includes(w)); });
}

function localGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function localSet(k, v) { try { localStorage.setItem(k, v); } catch {} }
function status(msg, kind = '') { const el = $('#status'); el.textContent = msg; el.className = 'status ' + kind; }
function escapeHtml(s) { return String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

// "Irene Country Club, Main Road, Tshwane Ward 65, Irene, City of Tshwane…, Gauteng, 0157, South Africa" -> "Irene, Gauteng, South Africa"
function shortPlace(place, name) {
  const parts = String(place || '').split(',').map((s) => s.trim()).filter((p) => p && p !== name
    && !/\d/.test(p) && !/\b(road|street|avenue|drive|lane|way|rd|st|ave|ward|municipality|metropolitan)\b/i.test(p));
  const pick = [parts[0], ...parts.slice(-2)].filter((p, i, a) => p && a.indexOf(p) === i);
  return pick.join(', ');
}

// ---------- library / search list ----------
const CHEV = '<svg class="chev" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="m9 5 7 7-7 7"/></svg>';
let listRows = [];
function renderList(rows, title, sub) {
  listRows = rows;
  $('#shelfTitle').textContent = title; $('#shelfSub').textContent = sub;
  $('#results').innerHTML = rows.map((r, i) => `<li><button type="button" data-i="${i}">
      <span class="name">${escapeHtml(r.name)}</span>
      <span class="place">${escapeHtml(shortPlace(r.place, r.name))}</span>
      <span class="count" data-count="${r.saved ? escapeHtml(r.slug) : ''}">${r.saved ? '<b>&nbsp;</b><small>&nbsp;</small>' : '<small>Map live</small>'}</span>
      ${CHEV}</button></li>`).join('');
  $('#results').querySelectorAll('button').forEach((b) => b.addEventListener('click', () => loadCourse(listRows[+b.dataset.i])));
  fillCounts(rows.filter((r) => r.saved));
}

const countCache = new Map();
async function fillCounts(rows) {
  for (const r of rows) {
    try {
      if (!countCache.has(r.slug)) {
        const j = await (await fetch(`courses/${r.slug}.json`)).json();
        const m = await withEdits(j, r.slug);
        const c = nameGroups(buildCourse(parseOverpass(m.json)), m.json.__groups);
        countCache.set(r.slug, { holes: c.holes.length, groups: c.groups.length });
      }
      const { holes, groups } = countCache.get(r.slug);
      const el = document.querySelector(`[data-count="${CSS.escape(r.slug)}"]`);
      if (!el) continue;
      el.classList.toggle('part', groups < 2 && holes < 18);
      el.innerHTML = groups > 1 ? `<b>${groups}</b><small>courses</small>` : holes ? `<b>${holes}</b><small>${holes === 1 ? 'hole' : 'holes'}</small>` : '<b>0</b><small>to trace</small>';
    } catch {}
  }
}

async function showLibrary() {
  await libraryReady;
  const rows = [...state.library].sort((a, b) => a.name.localeCompare(b.name)).map((c) => ({ ...c, saved: true }));
  renderList(rows, 'Your library', rows.length ? 'Saved courses open instantly. Search above for any other course.' : 'Search above to open your first course.');
}

async function onSearch(e) {
  e.preventDefault();
  const q = $('#q').value.trim();
  if (!q) return showLibrary();
  status('Searching…', 'busy');
  await libraryReady;
  const saved = librarySearch(q).map((c) => ({ ...c, saved: true }));
  let live = [];
  try { live = await searchCourses(q.match(/golf/i) ? q : q + ' golf'); } catch (err) { if (!saved.length) return status(err.message, 'error'); }
  const seen = new Set(saved.map((s) => s.name.toLowerCase()));
  const rows = [...saved, ...live.filter((l) => !seen.has(String(l.name).toLowerCase()))];
  if (!rows.length) return status('No courses found. Try the club name with its town, for example "Irene Country Club Centurion".', 'warn');
  status('');
  renderList(rows, `Results for “${q}”`, saved.length ? 'Saved courses first, then everything else on the map.' : 'Courses found on the map. They load live, which can take a little while.');
  $('#shelfTitle').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ---------- opening a course ----------
async function loadCourse(row) {
  if (row.saved) {
    status(`Opening ${row.name}…`, 'busy');
    try {
      const j = await (await fetch(`courses/${row.slug}.json`)).json();
      const m = await withEdits(j, row.slug);
      return openCourse(m.json, row.name, { slug: row.slug, bbox: row.bbox, place: row.place, saved: true, edited: m.edited });
    } catch (err) { return status(`Couldn't open the saved copy: ${err.message}`, 'error'); }
  }
  let step = 'starting';
  const t0 = Date.now();
  const tick = () => status(`Loading ${row.name}: ${step} (${Math.round((Date.now() - t0) / 1000)}s)`, 'busy');
  const timer = setInterval(tick, 1000); tick();
  window.scrollTo({ top: 0, behavior: 'smooth' });
  try {
    const json = await loadCourseData(row.bbox, (msg) => { step = msg; tick(); });
    clearInterval(timer);
    const slug = slugify(row.name);
    localSet(`tyb-base:${slug}`, JSON.stringify({ name: row.name, place: row.place, bbox: row.bbox, elements: json.elements }));
    const m = await withEdits(json, slug);
    openCourse(m.json, row.name, { slug, bbox: row.bbox, place: row.place, saved: false, edited: m.edited });
  } catch (err) { clearInterval(timer); status(`${err.message} If this keeps happening, ask Claude to add ${row.name} to the saved library.`, 'error'); }
}

function editorUrl(meta, name) {
  return `editor.html?slug=${encodeURIComponent(meta.slug)}&name=${encodeURIComponent(name)}&place=${encodeURIComponent(meta.place || '')}&bbox=${meta.bbox.join(',')}${meta.saved ? '&saved=1' : ''}`;
}

function openCourse(json, name, meta = null, { push = true } = {}) {
  state.meta = meta;
  const course = nameGroups(buildCourse(parseOverpass(json)), json.__groups);
  for (const id of ['#editLink', '#editLink2']) { $(id).hidden = !meta; if (meta) $(id).href = editorUrl(meta, name); }
  $('#mapItem').hidden = !meta?.bbox;
  if (!course.holes.length) {
    const c = course.census;
    status(`${name} isn't mapped yet: the map has ${c.holeLines} hole lines, ${c.greens} greens and ${c.fairways} fairways.${meta ? ' Trace it in the editor to build the book.' : ''}`, 'warn');
    if (meta) {
      listRows = [];
      $('#results').innerHTML = `<li class="row-cta"><button type="button" id="ctaMap" class="btn foil">Map ${escapeHtml(name)} with AI</button> <a class="button" href="${editorUrl(meta, name)}">Trace it by hand</a></li>`;
      $('#ctaMap').addEventListener('click', () => openMap({ name, bbox: meta.bbox, place: meta.place }));
    }
    return;
  }
  status('');
  state.course = course; state.name = name; state.hole = 0;
  // several courses at one club: pick the one whose name best matches, let the user switch
  state.group = null;
  const picks = [$('#groupPick'), $('#groupPick2')];
  if (course.groups.length) {
    const words = name.toLowerCase().split(/\s+/).filter((w) => w.length > 2 && !['golf', 'club', 'course', 'links'].includes(w));
    const score = (g) => words.filter((w) => g.name.toLowerCase().includes(w)).length * 100 + g.holes.length;
    const best = course.groups.reduce((x, y) => (score(y) > score(x) ? y : x));
    state.group = course.groups.indexOf(best);
    const html = course.groups.map((g, i) => `<option value="${i}">${escapeHtml(g.name)} (${g.holes.length} holes)</option>`).join('');
    picks.forEach((p) => { p.innerHTML = html; p.value = String(state.group); });
    $('#groupPick').hidden = course.groups.length < 2; $('#groupWrap').hidden = course.groups.length < 2;
  } else { $('#groupPick').hidden = true; $('#groupWrap').hidden = true; }

  const c = course.census;
  const skip = course.skipped.length ? ` Skipped: ${course.skipped.map((x) => `${x.ref} (${x.reason})`).join(', ')}.` : '';
  $('#about').textContent = `${course.holes.length} holes ready. The map has ${c.holeLines} hole lines, ${c.greens} greens, ${c.fairways} fairways and ${c.bunkers} bunkers.${meta?.edited ? ' Includes edits made in the course editor.' : ''}${skip}`;
  $('#title').textContent = name;
  $('#place').textContent = shortPlace(meta?.place, name);
  document.title = `${name} · Tour Yardage Books`;
  renderTabs(); renderHole();
  // contours and uphill/downhill numbers, when the course has terrain heights saved
  if (meta?.slug) loadTerrain(meta.slug, course.proj).then((t) => { if (t && state.course === course) { course.terrain = t; renderHole(); } });
  $('#home').hidden = true; $('#viewer').hidden = false; window.scrollTo(0, 0);
  if (push && meta?.slug && !qs.get('fixture')) {
    history.pushState({ course: meta.slug }, '', `?course=${encodeURIComponent(meta.slug)}`); state.pushed = true;
  }
}

function showHome() {
  $('#viewer').hidden = true; $('#home').hidden = false; document.title = 'Tour Yardage Books';
  if (!listRows.length || !$('#results').children.length) showLibrary();
}

function visibleHoles() {
  const all = state.course.holes.map((h, i) => i);
  return state.group == null ? all : state.course.groups[state.group].holes;
}

function renderTabs() {
  const vis = visibleHoles();
  if (!vis.includes(state.hole)) state.hole = vis[0];
  const H = state.course.holes;
  $('#tabs').innerHTML = vis.map((i, k) => {
    const turn = k > 0 && H[vis[k - 1]].ref === '9' && H[i].ref === '10' ? '<span class="turn" aria-hidden="true"></span>' : '';
    return `${turn}<button type="button" class="${i === state.hole ? 'on' : ''}" data-i="${i}" aria-label="Hole ${escapeHtml(H[i].ref)}, par ${H[i].par}"${i === state.hole ? ' aria-current="page"' : ''}><b>${escapeHtml(H[i].ref)}</b><small>Par ${H[i].par}</small></button>`;
  }).join('');
  $('#tabs').querySelectorAll('button').forEach((b) => b.addEventListener('click', () => go(+b.dataset.i)));
  const on = $('#tabs .on'); if (on) on.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' });
}

function renderHole(dir = 0) {
  const h = state.course.holes[state.hole];
  if (!h) return;
  const opts = { units: state.units };
  const len = h.tagDist || h.toCentre || h.lengthM;
  $('#bigNum').textContent = h.ref;
  $('#par').textContent = h.par;
  $('#len').textContent = Math.round(state.units === 'yd' ? len * YD : len);
  $('#lenLbl').textContent = state.units === 'yd' ? 'Yards' : 'Metres';
  const gname = state.group != null ? state.course.groups[state.group].name : '';
  $('#holeName').textContent = [h.name, gname && gname !== state.name ? gname : ''].filter(Boolean).join(', ');
  const pages = $('#pages');
  pages.innerHTML = `<figure class="page" aria-label="Hole ${escapeHtml(h.ref)} hole page">${holePage(h, state.course, opts)}</figure><figure class="page" aria-label="Hole ${escapeHtml(h.ref)} green page">${greenPage(h, state.course, opts)}</figure>`;
  // the view from the tee, when one has been rendered for this hole
  if (state.meta?.slug) {
    const base = `courses/teeview/${state.meta.slug}-${encodeURIComponent(h.ref)}`;
    const fig = document.createElement('figure');
    fig.className = 'teeview'; fig.hidden = true;
    const CAP = { '-photo': 'AI-rendered photo of the real layout: shapes and distances are from the course, fine detail is generated.',
      '': '3D render from satellite imagery, measured trees and terrain.', '-high': 'Raised view: 3D render from satellite imagery, measured trees and terrain.' };
    fig.innerHTML = `<img alt="Hole ${escapeHtml(h.ref)} from the tee">
      <div class="seg tv-seg" role="group" aria-label="Viewpoint"><button type="button" data-tv="-photo" hidden>Photo</button><button type="button" data-tv="">3D</button><button type="button" data-tv="-high">Raised</button></div>
      <figcaption></figcaption>`;
    const img = fig.querySelector('img');
    const show = (tag) => {
      img.src = `${base}${tag}.jpg`;
      fig.querySelector('figcaption').textContent = CAP[tag];
      fig.querySelectorAll('[data-tv]').forEach((x) => x.setAttribute('aria-pressed', String(x.dataset.tv === tag)));
    };
    img.addEventListener('load', () => { fig.hidden = false; });
    img.addEventListener('error', () => { if (img.src.endsWith('-photo.jpg')) show(''); else fig.hidden = true; });
    fig.querySelectorAll('[data-tv]').forEach((b) => b.addEventListener('click', () => show(b.dataset.tv)));
    // use the photo when there is one
    const probe = new Image();
    probe.onload = () => { fig.querySelector('[data-tv="-photo"]').hidden = false; show('-photo'); };
    probe.onerror = () => show('');
    probe.src = `${base}-photo.jpg`;
    pages.appendChild(fig);
  }
  if (dir) { pages.style.setProperty('--dir', `${dir * 14}px`); pages.classList.remove('swapping'); void pages.offsetWidth; pages.classList.add('swapping'); }
  const WARN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17h.01"/></svg>';
  $('#warnings').innerHTML = h.warnings.map((w) => `<li>${WARN}<span>${escapeHtml(w)}</span></li>`).join('');
  const v = visibleHoles(), k = v.indexOf(state.hole);
  $('#prev').disabled = k <= 0; $('#next').disabled = k >= v.length - 1;
}

function go(i) {
  const v = visibleHoles(); const dir = Math.sign(v.indexOf(i) - v.indexOf(state.hole));
  state.hole = i; renderTabs(); renderHole(dir);
}
const step = (d) => { const v = visibleHoles(); const k = v.indexOf(state.hole) + d; if (k >= 0 && k < v.length) go(v[k]); };

function setUnits(u) {
  state.units = u; localSet('units', u);
  document.querySelectorAll('[data-units]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.units === u)));
  if (state.course) renderHole();
}

function printBook(all) {
  const holes = all ? visibleHoles().map((i) => state.course.holes[i]) : [state.course.holes[state.hole]];
  const opts = { units: state.units };
  $('#print').innerHTML = holes.map((h) => `<div class="pp">${greenPage(h, state.course, opts)}</div><div class="pp">${holePage(h, state.course, opts)}</div>`).join('');
  if ($('#sheet').open) $('#sheet').close();
  window.print();
}

// ---------- map with AI: scorecard -> automatic mapping job ----------
let mapTarget = null, mapUnits = 'm';
function openMap(target) {
  mapTarget = target;
  if ($('#sheet').open) $('#sheet').close();
  $('#mapName').textContent = target.name;
  $('#mapKey').value = localGet('tyb-key') || '';
  $('#mapMsg').textContent = ''; $('#mapMsg').className = 'status';
  const known = (target.holes || []).length;
  const holes = known === 9 || known === 18 ? target.holes : Array.from({ length: 18 }, () => ({ par: 4, len: null }));
  const row = (h, i) => `<span class="n">${i + 1}</span>
    <select data-p="${i}" aria-label="Hole ${i + 1} par">${[3, 4, 5].map((p) => `<option${p === h.par ? ' selected' : ''}>${p}</option>`).join('')}</select>
    <input data-l="${i}" type="number" inputmode="numeric" min="60" max="770" aria-label="Hole ${i + 1} length" value="${h.len ? Math.round(mapUnits === 'yd' ? h.len * YD : h.len) : ''}">`;
  const head = '<span class="h"></span><span class="h">Par</span><span class="h">Length</span>';
  const halves = holes.length === 18 ? [holes.slice(0, 9), holes.slice(9)] : [holes];
  $('#cardGrid').innerHTML = halves.map((hs, k) => `<div class="card-col">${head}${hs.map((h, j) => row(h, k * 9 + j)).join('')}</div>`).join('')
    + '<div class="card-tot"><span>Par <b id="totPar"></b></span><span>Length <b id="totLen"></b></span></div>';
  $('#cardGrid').oninput = cardTotals; cardTotals();
  $('#mapSheet').showModal();
}
function cardValues() {
  const pars = [...document.querySelectorAll('#cardGrid [data-p]')].map((e) => +e.value);
  const lens = [...document.querySelectorAll('#cardGrid [data-l]')].map((e) => (e.value === '' ? NaN : +e.value));
  return { pars, lens };
}
function cardTotals() {
  const { pars, lens } = cardValues();
  $('#totPar').textContent = pars.reduce((a, b) => a + b, 0);
  const t = lens.filter(Number.isFinite).reduce((a, b) => a + b, 0);
  $('#totLen').textContent = t ? `${Math.round(t)} ${mapUnits === 'yd' ? 'yd' : 'm'}` : '–';
}
document.querySelectorAll('[data-mu]').forEach((b) => b.addEventListener('click', () => {
  const to = b.dataset.mu; if (to === mapUnits) return;
  document.querySelectorAll('#cardGrid [data-l]').forEach((e) => { if (e.value !== '') e.value = Math.round(to === 'yd' ? +e.value * YD : +e.value / YD); });
  mapUnits = to; document.querySelectorAll('[data-mu]').forEach((x) => x.setAttribute('aria-pressed', String(x.dataset.mu === to))); cardTotals();
}));
$('#mapCancel').addEventListener('click', () => $('#mapSheet').close());
async function sendMap(withCard) {
  const msg = (t, k = '') => { $('#mapMsg').textContent = t; $('#mapMsg').className = 'status ' + k; };
  let pars = [], m = [];
  if (withCard) {
    const v = cardValues(); pars = v.pars;
    m = v.lens.map((l) => (mapUnits === 'yd' ? l / YD : l));
    document.querySelectorAll('#cardGrid [data-l]').forEach((el, i) => el.classList.toggle('bad', !(m[i] >= 60 && m[i] <= 700)));
    if (m.some((l) => !(l >= 60 && l <= 700))) return msg('Fill in every hole length (between 60 and 700 metres), or map without a scorecard.', 'error');
  }
  const key = $('#mapKey').value.trim();
  if (!key) return msg('Enter the access key first.', 'error');
  localSet('tyb-key', key);
  msg('Sending to the mapper…', 'busy'); $('#mapGo').disabled = $('#mapNoCard').disabled = true;
  try {
    const r = await fetch(`api/automap?key=${encodeURIComponent(key)}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: mapTarget.name, pars, lengths: m.map((l) => Math.round(l)), bbox: mapTarget.bbox }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || `The mapper answered ${r.status}`);
    msg(`Queued. ${mapTarget.name} will be traced and routed in about ten minutes; open it from your library after that.`, 'ok');
  } catch (err) { msg(err.message, 'error'); } finally { $('#mapGo').disabled = $('#mapNoCard').disabled = false; }
}
$('#mapForm').addEventListener('submit', (e) => { e.preventDefault(); sendMap(true); });
$('#mapNoCard').addEventListener('click', () => sendMap(false));
$('#mapItem').addEventListener('click', () => {
  const holes = state.course ? visibleHoles().map((i) => state.course.holes[i]).map((h) => ({ par: h.par, len: h.tagDist || h.toCentre || h.lengthM })) : [];
  openMap({ name: state.name, bbox: state.meta?.bbox, place: state.meta?.place, holes });
});

// ---------- download the book as a PDF (built on the device, vector pages) ----------
function loadScript(src) {
  return new Promise((res, rej) => { const el = document.createElement('script'); el.src = src; el.onload = res; el.onerror = () => rej(new Error(`Couldn't load ${src}`)); document.head.appendChild(el); });
}
async function downloadBook(btn) {
  if (!state.course) return;
  const label = btn?.querySelector('span')?.firstChild || btn;
  const was = label?.textContent;
  const say = (t) => { if (label) label.textContent = t; };
  try {
    say('Preparing…');
    if (!window.jspdf) await loadScript('src/vendor/jspdf.umd.min.js');
    if (!window.svg2pdf) await loadScript('src/vendor/svg2pdf.umd.min.js');
    const { jsPDF } = window.jspdf;
    const W = 101.6, H = 177.8;
    const doc = new jsPDF({ unit: 'mm', format: [W, H], orientation: 'portrait', compress: true });
    // cover
    doc.setFillColor(15, 42, 32); doc.rect(0, 0, W, H, 'F');
    doc.setTextColor(210, 174, 98); doc.setFont('helvetica', 'bold'); doc.setFontSize(22);
    doc.text(doc.splitTextToSize(state.name.toUpperCase(), W - 20), 10, 40);
    doc.setFontSize(11); doc.setTextColor(236, 233, 222); doc.setFont('helvetica', 'normal');
    const gname = state.group != null ? state.course.groups[state.group].name : '';
    doc.text([gname && gname !== state.name ? gname : '', 'Yardage book', `Distances in ${state.units === 'yd' ? 'yards' : 'metres'}`].filter(Boolean), 10, 70);
    doc.setFontSize(7); doc.setTextColor(170, 180, 172);
    doc.text(doc.splitTextToSize(`Made ${new Date().toISOString().slice(0, 10)} with Tour Yardage Books. Course data © OpenStreetMap contributors; shapes partly traced by AI from satellite imagery. Check distances on site before competition use.`, W - 20), 10, H - 22);
    const holes = visibleHoles().map((i) => state.course.holes[i]);
    const box = document.createElement('div');
    box.style.cssText = 'position:fixed;left:-20000px;top:0;width:500px;pointer-events:none';
    document.body.appendChild(box);
    const opts = { units: state.units };
    let n = 0; const total = holes.length * 2;
    // the tee shot for each hole goes across the top of its green page
    const teeView = async (h) => {
      if (!state.meta?.slug) return null;
      try {
        const r = await fetch(`courses/teeview/${state.meta.slug}-${encodeURIComponent(h.ref)}.jpg`); if (!r.ok) return null;
        const b = await r.blob();
        return await new Promise((res) => { const fr = new FileReader(); fr.onload = () => res(fr.result); fr.onerror = () => res(null); fr.readAsDataURL(b); });
      } catch { return null; }
    };
    for (const h of holes) {
      const tv = await teeView(h);
      for (const page of [holePage(h, state.course, opts), greenPage(h, state.course, { ...opts, teeView: tv })]) {
        say(`Page ${++n} of ${total}…`);
        box.innerHTML = page;
        const svg = box.querySelector('svg');
        // PDF built-in fonts: Helvetica regular and bold only
        svg.querySelectorAll('[font-weight]').forEach((el) => el.setAttribute('font-weight', parseInt(el.getAttribute('font-weight')) >= 600 || el.getAttribute('font-weight') === 'bold' ? 'bold' : 'normal'));
        svg.querySelectorAll('[font-family]').forEach((el) => el.setAttribute('font-family', 'helvetica'));
        // the PDF library doesn't scale outline widths on text to the page: do it here (keeps the white halos thin)
        const vbW = svg.viewBox?.baseVal?.width || parseFloat(svg.getAttribute('width')) || 1000;
        svg.querySelectorAll('text[stroke]').forEach((el) => el.setAttribute('stroke-width', String(parseFloat(el.getAttribute('stroke-width') || '1') * (W / vbW))));
        doc.addPage([W, H], 'portrait');
        await doc.svg(svg, { x: 0, y: 0, width: W, height: H });
        await new Promise((r) => setTimeout(r, 0));     // keep the page responsive
      }
    }
    box.remove();
    const file = `${slugify(state.name)}${gname && gname !== state.name ? '-' + slugify(gname) : ''}-yardage-book.pdf`;
    doc.save(file);
    say('Downloaded');
    setTimeout(() => say(was), 2500);
  } catch (err) {
    say(was); alert(`Couldn't build the PDF: ${err.message}`);
  }
}

// ---------- wiring ----------
$('#search').addEventListener('submit', onSearch);
document.querySelectorAll('[data-units]').forEach((b) => b.addEventListener('click', () => setUnits(b.dataset.units)));
setUnits(state.units);
$('#pdfAll').addEventListener('click', (e) => downloadBook(e.currentTarget));
$('#pdfAll2').addEventListener('click', (e) => downloadBook(e.currentTarget));
$('#printAll').addEventListener('click', () => printBook(true));
$('#printHole2').addEventListener('click', () => printBook(false));
$('#printAll2').addEventListener('click', () => printBook(true));
$('#prev').addEventListener('click', () => step(-1));
$('#next').addEventListener('click', () => step(1));
const pickGroup = (e) => { state.group = +e.target.value; $('#groupPick').value = $('#groupPick2').value = e.target.value; state.hole = -1; renderTabs(); renderHole(); };
$('#groupPick').addEventListener('change', pickGroup);
$('#groupPick2').addEventListener('change', pickGroup);
$('#more').addEventListener('click', () => $('#sheet').showModal());
$('#sheetClose').addEventListener('click', () => $('#sheet').close());
$('#sheet').addEventListener('click', (e) => { if (e.target === $('#sheet')) $('#sheet').close(); });
$('#back').addEventListener('click', () => { if (state.pushed) history.back(); else { history.replaceState(null, '', location.pathname); showHome(); } });
window.addEventListener('popstate', () => { if (!new URLSearchParams(location.search).get('course')) { state.pushed = false; showHome(); } });
document.addEventListener('keydown', (e) => {
  if ($('#viewer').hidden || e.target.closest('input, select, textarea') || $('#sheet').open) return;
  if (e.key === 'ArrowRight') step(1); else if (e.key === 'ArrowLeft') step(-1);
});
// swipe across the pages to turn holes
let touch = null;
$('#pages').addEventListener('touchstart', (e) => { const t = e.touches[0]; touch = { x: t.clientX, y: t.clientY, t: Date.now() }; }, { passive: true });
$('#pages').addEventListener('touchend', (e) => {
  if (!touch) return; const t = e.changedTouches[0]; const dx = t.clientX - touch.x, dy = t.clientY - touch.y;
  if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.6 && Date.now() - touch.t < 700) step(dx < 0 ? 1 : -1);
  touch = null;
}, { passive: true });

function loadFixture(path) {
  return fetch(path).then((r) => r.json()).then((j) => openCourse(j, j.name || 'Sample course', null, { push: false })).catch((e) => status(e.message, 'error'));
}
const qs = new URLSearchParams(location.search);
// ?fixture=path.json loads saved Overpass JSON (offline testing)
const fx = qs.get('fixture');
if (fx) loadFixture(fx);

// ?course=slug opens a library course (or one opened earlier on this device), e.g. from the editor's Preview
const cslug = qs.get('course');
if (cslug) {
  (async () => {
    await libraryReady;
    const lib = state.library.find((c) => c.slug === cslug);
    const reopen = (json, name, meta) => { openCourse(json, name, meta, { push: false }); history.replaceState({ course: cslug }, '', location.href); };
    if (lib) {
      try {
        const j = await (await fetch(`courses/${lib.slug}.json`)).json(); const m = await withEdits(j, lib.slug);
        return reopen(m.json, lib.name, { slug: lib.slug, bbox: lib.bbox, place: lib.place, saved: true, edited: m.edited });
      } catch (err) { return status(`Couldn't open the saved copy: ${err.message}`, 'error'); }
    }
    const local = JSON.parse(localGet(`tyb-base:${cslug}`) || 'null');
    if (!local) { showLibrary(); return status('That course is not saved yet. Search for it above.', 'warn'); }
    const m = await withEdits(local, cslug);
    reopen(m.json, qs.get('name') || local.name || cslug, { slug: cslug, bbox: local.bbox, place: local.place, saved: false, edited: m.edited });
  })();
}

// Demo build (no live map access): sample courses only, no printing
const demo = document.querySelector('[data-demo]');
if (demo) {
  for (const id of ['#search', '#printAll', '#printHole2', '#printAll2']) { const el = $(id); if (el) el.hidden = true; }
  demo.querySelectorAll('[data-sample]').forEach((b) => b.addEventListener('click', () => loadFixture(b.dataset.sample)));
  loadFixture(demo.dataset.demo);
} else if (!fx && !cslug) showLibrary();
