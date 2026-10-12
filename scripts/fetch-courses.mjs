// Fetches requested courses from OpenStreetMap patiently (retries across servers) and saves them
// into courses/ so the app and the Claude connector can load them instantly.
// Runs in GitHub Actions whenever courses/requests.txt changes.
import fs from 'node:fs';
import { courseQuery, parseOverpass } from '../src/osm.js';
import { buildCourse } from '../src/course.js';

const UA = { 'User-Agent': 'TourYardageBooks/0.3 (+https://github.com/keanupest1-lab/tour-yardage-book)' };
const SERVERS = [
  'https://overpass-api.de/api/interpreter',
  'https://overpass.private.coffee/api/interpreter',
  'https://overpass.kumi.systems/api/interpreter',
  'https://maps.mail.ru/osm/tools/overpass/api/interpreter',
];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const slug = (s) => s.toLowerCase().normalize('NFKD').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60);
const log = [];
const say = (m) => { console.log(m); log.push(`${new Date().toISOString()} ${m}`); };

async function timed(url, init, ms) {
  const c = new AbortController(); const t = setTimeout(() => c.abort(), ms);
  try { return await fetch(url, { ...init, signal: c.signal, headers: { ...UA, ...(init?.headers || {}) } }); } finally { clearTimeout(t); }
}

async function search(q) {
  const res = await timed(`https://nominatim.openstreetmap.org/search?format=jsonv2&limit=10&q=${encodeURIComponent(q)}`, {}, 30000);
  const rows = await res.json();
  const golf = rows.filter((r) => r.type === 'golf_course');
  const r = golf[0] || rows.find((x) => /golf/i.test(x.display_name)) || rows[0];
  if (!r) throw new Error('not found on OpenStreetMap search');
  return { name: r.name || r.display_name.split(',')[0], place: r.display_name, bbox: r.boundingbox.map(Number) };
}

async function overpass(bbox) {
  const q = courseQuery(bbox).replace('[timeout:25]', '[timeout:120]');
  for (let attempt = 0; attempt < 12; attempt++) {
    const ep = SERVERS[attempt % SERVERS.length];
    try {
      const res = await timed(ep, { method: 'POST', body: 'data=' + encodeURIComponent(q), headers: { 'Content-Type': 'application/x-www-form-urlencoded' } }, 150000);
      const text = await res.text();
      if (!res.ok || !text.startsWith('{')) throw new Error(`HTTP ${res.status} ${text.slice(0, 80)}`);
      const json = JSON.parse(text);
      if (json.remark && /error|timed out/i.test(json.remark)) throw new Error(json.remark.slice(0, 100));
      say(`  overpass ok via ${new URL(ep).host} on attempt ${attempt + 1}`);
      return json;
    } catch (e) {
      say(`  attempt ${attempt + 1} ${new URL(ep).host}: ${e.name === 'AbortError' ? 'timeout' : e.message}`);
      await sleep(Math.min(60000, 8000 * (attempt + 1)));
    }
  }
  throw new Error('all attempts failed');
}

const indexPath = 'courses/index.json';
const index = fs.existsSync(indexPath) ? JSON.parse(fs.readFileSync(indexPath, 'utf8')) : { courses: [] };
const lines = fs.readFileSync('courses/requests.txt', 'utf8').split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith('#'));
const force = process.env.FORCE === '1';

for (const line of lines) {
  // "Name" or "Name | south,north,west,east"
  const [qName, bboxStr] = line.split('|').map((s) => s.trim());
  const id = slug(qName);
  if (!force && index.courses.some((c) => c.slug === id)) { say(`skip ${qName} (already saved)`); continue; }
  try {
    say(`fetch ${qName}`);
    const meta = bboxStr ? { name: qName, place: qName, bbox: bboxStr.split(',').map(Number) } : await search(qName);
    const json = await overpass(meta.bbox);
    const course = buildCourse(parseOverpass(json));
    const census = { ...course.census, holesReady: course.holes.length, skipped: course.skipped };
    fs.writeFileSync(`courses/${id}.json`, JSON.stringify({ name: meta.name, place: meta.place, bbox: meta.bbox, fetched: new Date().toISOString(), elements: json.elements }));
    index.courses = index.courses.filter((c) => c.slug !== id);
    index.courses.push({ slug: id, query: qName, name: meta.name, place: meta.place, bbox: meta.bbox, fetched: new Date().toISOString(), census });
    say(`  saved ${id}: ${course.holes.length} holes ready; OSM has ${census.holeLines} hole lines, ${census.greens} greens, ${census.fairways} fairways`);
  } catch (e) {
    say(`  FAILED ${qName}: ${e.message}`);
  }
  await sleep(3000);
}
index.courses.sort((a, b) => a.name.localeCompare(b.name));
fs.writeFileSync(indexPath, JSON.stringify(index, null, 1));
fs.writeFileSync('courses/_last_run.log', log.join('\n') + '\n');
