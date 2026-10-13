// Queues a course for automatic mapping: appends a line to courses/automap-requests.txt,
// which starts the "Map courses automatically" GitHub job (satellite -> AI shapes -> routing -> library).
// Needs env: ACCESS_KEY, GITHUB_TOKEN (Contents read/write on this repo), optional GITHUB_REPO.
export const config = { maxDuration: 30 };

const REPO = process.env.GITHUB_REPO || 'keanupest1-lab/tour-yardage-book';
const PATH = 'courses/automap-requests.txt';

async function gh(path, init = {}) {
  return fetch(`https://api.github.com/repos/${REPO}/contents/${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${process.env.GITHUB_TOKEN}`, Accept: 'application/vnd.github+json', 'User-Agent': 'TourYardageBooks', ...(init.headers || {}) },
  });
}

export default async function handler(req, res) {
  res.setHeader('Content-Type', 'application/json');
  const send = (code, obj) => { res.statusCode = code; res.end(JSON.stringify(obj)); };
  const key = new URL(req.url, 'http://x').searchParams.get('key');
  if (!process.env.ACCESS_KEY || key !== process.env.ACCESS_KEY) return send(401, { error: 'Wrong access key' });
  if (req.method !== 'POST') return send(405, { error: 'POST only' });
  if (!process.env.GITHUB_TOKEN) return send(503, { error: 'Automatic mapping is not switched on yet: add GITHUB_TOKEN in Vercel' });
  const b = req.body || {};
  const name = String(b.name || '').replace(/[|\n\r]/g, ' ').trim().slice(0, 90);
  const pars = (b.pars || []).map(Number), lens = (b.lengths || []).map(Number);
  if (!name) return send(400, { error: 'Missing course name' });
  if (![9, 18].includes(pars.length) || pars.length !== lens.length) return send(400, { error: 'Enter a par and a length for every hole' });
  if (pars.some((p) => ![3, 4, 5, 6].includes(p))) return send(400, { error: 'Pars must be 3, 4, 5 or 6' });
  if (lens.some((l) => !(l >= 60 && l <= 700))) return send(400, { error: 'Hole lengths must be between 60 and 700 metres' });
  const club = Array.isArray(b.clubhouse) && b.clubhouse.length === 2 && b.clubhouse.every(Number.isFinite) ? b.clubhouse.map((v) => v.toFixed(6)).join(',') : '';
  // bbox arrives as the app keeps it: [south, north, west, east]; the request line wants south,west,north,east
  const bb = Array.isArray(b.bbox) && b.bbox.length === 4 && b.bbox.every(Number.isFinite) ? [b.bbox[0], b.bbox[2], b.bbox[1], b.bbox[3]].join(',') : '';
  const line = `${name} | ${pars.join(' ')} | ${lens.map((l) => Math.round(l)).join(' ')} | ${club} | ${bb}`;
  try {
    const r = await gh(`${PATH}?ref=main`);
    let sha, text = '';
    if (r.ok) { const j = await r.json(); sha = j.sha; text = Buffer.from(j.content, 'base64').toString('utf8'); }
    else if (r.status !== 404) throw new Error(`GitHub read: ${r.status}`);
    // replace an earlier request for the same course
    const kept = text.split('\n').filter((l) => l.trim() && !l.startsWith(`${name} |`));
    const body = [...kept, line].join('\n') + '\n';
    const w = await gh(PATH, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: `Map automatically: ${name}`, content: Buffer.from(body).toString('base64'), branch: 'main', ...(sha ? { sha } : {}) }) });
    if (!w.ok) throw new Error(`GitHub write: ${w.status} ${(await w.text()).slice(0, 120)}`);
    send(200, { ok: true, queued: line });
  } catch (e) { send(502, { error: e.message }); }
}
