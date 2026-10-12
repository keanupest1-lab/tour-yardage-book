// Health check: tests every upstream service from the server and reports timings.
// Open /api/health in a browser when something seems stuck.
import { courseQuery } from '../src/osm.js';

const UA = { 'User-Agent': 'TourYardageBooks/0.3 (+https://github.com/keanupest1-lab/tour-yardage-book)' };
const OVERPASS = [
  'https://overpass-api.de/api/interpreter',
  'https://overpass.private.coffee/api/interpreter',
  'https://overpass.kumi.systems/api/interpreter',
  'https://maps.mail.ru/osm/tools/overpass/api/interpreter',
];

async function probe(name, url, init = {}, timeoutMs = 20000, check = (r) => r.ok) {
  const t0 = Date.now(); const ctrl = new AbortController(); const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const r = await fetch(url, { ...init, signal: ctrl.signal, headers: { ...UA, ...(init.headers || {}) } });
    const text = await r.text();
    const ok = check(r, text);
    return { name, ok, status: r.status, ms: Date.now() - t0, note: ok ? undefined : text.slice(0, 160) };
  } catch (e) {
    return { name, ok: false, ms: Date.now() - t0, note: e.name === 'AbortError' ? `no answer in ${timeoutMs / 1000}s` : e.message };
  } finally { clearTimeout(timer); }
}

export const config = { maxDuration: 30 };

export default async function handler(req, res) {
  // Dainfern area: a small real query
  const q = courseQuery([-26.012, -25.995, 27.985, 28.005]);
  const token = process.env.MAPBOX_TOKEN;
  const checks = await Promise.all([
    probe('nominatim (course search)', 'https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&q=Dainfern%20golf'),
    ...OVERPASS.map((ep, i) => probe(`overpass server ${i + 1} (${new URL(ep).host})`, ep,
      { method: 'POST', body: 'data=' + encodeURIComponent(q), headers: { 'Content-Type': 'application/x-www-form-urlencoded' } }, 25000,
      (r, t) => r.ok && t.startsWith('{') && !/runtime error/.test(t))),
    token ? probe('mapbox satellite', `https://api.mapbox.com/styles/v1/mapbox/satellite-v9/static/[27.99,-26.0,28.0,-25.99]/64x64?access_token=${token}`)
          : Promise.resolve({ name: 'mapbox satellite', ok: false, note: 'MAPBOX_TOKEN not set' }),
  ]);
  res.setHeader('Content-Type', 'application/json');
  res.setHeader('Cache-Control', 'no-store');
  res.end(JSON.stringify({
    time: new Date().toISOString(),
    access_key_set: Boolean(process.env.ACCESS_KEY),
    mapbox_token_set: Boolean(token),
    checks,
  }, null, 2));
}
