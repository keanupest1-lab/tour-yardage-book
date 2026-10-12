// OpenStreetMap access (runs in the browser; both services allow cross-origin requests).
// Data © OpenStreetMap contributors, ODbL.

const NOMINATIM = 'https://nominatim.openstreetmap.org/search';
// Server calls identify themselves; browsers must not add the header (it would trigger a CORS preflight).
const UA_HDR = typeof window === 'undefined' ? { 'User-Agent': 'TourYardageBooks/0.2 (+https://github.com/keanupest1-lab/tour-yardage-book)' } : {};
const OVERPASS = [
  'https://overpass-api.de/api/interpreter',
  'https://overpass.private.coffee/api/interpreter',
  'https://overpass.kumi.systems/api/interpreter',
  'https://maps.mail.ru/osm/tools/overpass/api/interpreter',
];

export async function searchCourses(q) {
  const url = `${NOMINATIM}?format=jsonv2&limit=15&q=${encodeURIComponent(q)}`;
  const res = await fetch(url, { headers: { 'Accept-Language': 'en', ...UA_HDR } });
  if (!res.ok) throw new Error(`Search failed (${res.status})`);
  const rows = await res.json();
  const golf = rows.filter((r) => r.type === 'golf_course' || /golf/i.test(r.display_name));
  return (golf.length ? golf : rows).map((r) => ({
    name: r.name || r.display_name.split(',')[0],
    place: r.display_name,
    lat: +r.lat,
    lon: +r.lon,
    // [south, north, west, east]
    bbox: r.boundingbox.map(Number),
  }));
}

export function courseQuery(bbox) {
  // Guard against a search hit that is a whole suburb or town: keep the box to about 4 km around its centre
  let [s, n, w, e] = bbox;
  const cLat = (s + n) / 2, cLon = (w + e) / 2;
  if (n - s > 0.04) { s = cLat - 0.02; n = cLat + 0.02; }
  if (e - w > 0.045) { w = cLon - 0.0225; e = cLon + 0.0225; }
  const pad = 0.003;
  const b = `${(s - pad).toFixed(6)},${(w - pad).toFixed(6)},${(n + pad).toFixed(6)},${(e + pad).toFixed(6)}`;
  return `[out:json][timeout:25];
(
  way["golf"](${b});
  relation["golf"](${b});
  way["natural"="water"](${b});
  relation["natural"="water"](${b});
  way["natural"="sand"](${b});
  way["natural"="wood"](${b});
  way["landuse"="forest"](${b});
  node["natural"="tree"](${b});
  way["natural"="tree_row"](${b});
  way["leisure"="golf_course"](${b});
);
out geom;`;
}

async function askServer(ep, i, query, timeoutMs, outerSignal) {
  const ctrl = new AbortController();
  const stop = () => ctrl.abort();
  outerSignal?.addEventListener('abort', stop);
  const timer = setTimeout(stop, timeoutMs);
  const t0 = Date.now();
  try {
    const res = await fetch(ep, { method: 'POST', body: 'data=' + encodeURIComponent(query), signal: ctrl.signal,
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', ...UA_HDR } });
    if (!res.ok) throw new Error(`answered ${res.status}${res.status === 429 ? ' (too many requests)' : res.status === 504 ? ' (overloaded)' : ''}`);
    const json = await res.json();
    if (json.remark && /runtime error|timed out|out of memory/i.test(json.remark)) throw new Error(json.remark.slice(0, 80));
    json._source = { server: i + 1, ms: Date.now() - t0 };
    return json;
  } catch (err) {
    const msg = err.name === 'AbortError' ? (outerSignal?.aborted ? 'cancelled' : `no answer in ${Math.round(timeoutMs / 1000)}s`) : err.message;
    throw new Error(`server ${i + 1}: ${msg}`);
  } finally { clearTimeout(timer); outerSignal?.removeEventListener('abort', stop); }
}

// Ask every map server at once and take the first good answer (bounded by timeoutMs).
export async function fetchCourseData(bbox, onProgress = () => {}, { timeoutMs = 25000 } = {}) {
  const query = courseQuery(bbox);
  onProgress(`Asking ${OVERPASS.length} map servers…`);
  const done = new AbortController();
  try {
    const json = await Promise.any(OVERPASS.map((ep, i) => askServer(ep, i, query, timeoutMs, done.signal)));
    return json;
  } catch (agg) {
    const reasons = (agg.errors || [agg]).map((e) => e.message).join('; ');
    throw new Error(`The map servers didn't answer (${reasons}). Try again in a minute.`);
  } finally { done.abort(); }
}

// Prefer the app's own server route (cached, not subject to browser/network quirks); fall back to direct.
export async function loadCourseData(bbox, onProgress = () => {}) {
  if (typeof window !== 'undefined' && location.protocol.startsWith('http')) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 32000);
    try {
      onProgress('Fetching course data via the app server…');
      const res = await fetch(`/api/course?bbox=${bbox.map((v) => v.toFixed(6)).join(',')}`, { signal: ctrl.signal });
      const body = await res.json().catch(() => null);
      if (res.ok && body && body.elements) return body;
      onProgress(`App server: ${body?.error || 'HTTP ' + res.status}. Trying the map servers directly…`);
    } catch (e) {
      onProgress(e.name === 'AbortError' ? 'App server took too long. Trying the map servers directly…' : 'Trying the map servers directly…');
    } finally { clearTimeout(timer); }
  }
  return fetchCourseData(bbox, onProgress);
}

// Turn Overpass JSON into simple features: {kind, tags, geom: [[lon,lat],...], closed, id}
export function parseOverpass(json) {
  const out = [];
  for (const el of json.elements || []) {
    const tags = el.tags || {};
    if (el.type === 'node') {
      out.push({ id: `n${el.id}`, tags, point: [el.lon, el.lat] });
    } else if (el.type === 'way' && el.geometry) {
      const g = el.geometry.map((p) => [p.lon, p.lat]);
      const closed = g.length > 3 && g[0][0] === g[g.length - 1][0] && g[0][1] === g[g.length - 1][1];
      out.push({ id: `w${el.id}`, tags, line: g, closed });
    } else if (el.type === 'relation' && el.members) {
      const outers = el.members.filter((m) => m.type === 'way' && m.geometry && (m.role === 'outer' || m.role === ''))
        .map((m) => m.geometry.map((p) => [p.lon, p.lat]));
      for (const ring of stitchRings(outers)) out.push({ id: `r${el.id}`, tags, line: ring, closed: true });
    }
  }
  return out;
}

function stitchRings(parts) {
  const rings = [];
  const pool = parts.map((p) => p.slice());
  const same = (a, b) => a[0] === b[0] && a[1] === b[1];
  while (pool.length) {
    let ring = pool.shift();
    let guard = 0;
    while (!same(ring[0], ring[ring.length - 1]) && guard++ < 500) {
      const end = ring[ring.length - 1];
      const i = pool.findIndex((p) => same(p[0], end) || same(p[p.length - 1], end));
      if (i < 0) break;
      const next = pool.splice(i, 1)[0];
      ring = ring.concat(same(next[0], end) ? next.slice(1) : next.reverse().slice(1));
    }
    if (ring.length > 3) rings.push(ring);
  }
  return rings;
}
