// Saves editor changes into the course library by committing to GitHub.
// Needs env: ACCESS_KEY, GITHUB_TOKEN (fine-grained, Contents read/write on this repo), optional GITHUB_REPO.
export const config = { maxDuration: 30 };

const REPO = process.env.GITHUB_REPO || 'keanupest1-lab/tour-yardage-book';
const GH = 'https://api.github.com';
const slugOk = (s) => typeof s === 'string' && /^[a-z0-9-]{2,60}$/.test(s);

async function gh(path, init = {}) {
  const r = await fetch(`${GH}/repos/${REPO}/contents/${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${process.env.GITHUB_TOKEN}`, Accept: 'application/vnd.github+json', 'User-Agent': 'TourYardageBooks', ...(init.headers || {}) },
  });
  return r;
}
async function getFile(path) {
  const r = await gh(`${path}?ref=main`);
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`GitHub read ${path}: ${r.status}`);
  const j = await r.json();
  return { sha: j.sha, text: Buffer.from(j.content, 'base64').toString('utf8') };
}
async function putFile(path, text, message, sha) {
  const r = await gh(path, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, content: Buffer.from(text).toString('base64'), branch: 'main', ...(sha ? { sha } : {}) }) });
  if (!r.ok) throw new Error(`GitHub write ${path}: ${r.status} ${(await r.text()).slice(0, 120)}`);
}

export default async function handler(req, res) {
  res.setHeader('Content-Type', 'application/json');
  const send = (code, obj) => { res.statusCode = code; res.end(JSON.stringify(obj)); };
  const key = new URL(req.url, 'http://x').searchParams.get('key');
  if (!process.env.ACCESS_KEY || key !== process.env.ACCESS_KEY) return send(401, { error: 'Wrong access key' });
  if (req.method !== 'POST') return send(405, { error: 'POST only' });
  if (!process.env.GITHUB_TOKEN) return send(503, { error: 'Saving is not switched on yet: add GITHUB_TOKEN in Vercel' });
  const b = req.body || {};
  if (!slugOk(b.slug) || !b.edits || !Array.isArray(b.bbox) || b.bbox.length !== 4) return send(400, { error: 'Missing slug, bbox or edits' });
  try {
    const now = new Date().toISOString();
    const edits = { ...b.edits, slug: b.slug, name: b.name, updated: now };
    const ePath = `courses/edits/${b.slug}.json`;
    const prev = await getFile(ePath);
    await putFile(ePath, JSON.stringify(edits), `Edit course: ${b.name || b.slug}`, prev?.sha);
    // add the course itself to the library if it was opened live
    const cPath = `courses/${b.slug}.json`;
    const course = await getFile(cPath);
    if (!course && b.base?.elements) {
      await putFile(cPath, JSON.stringify({ name: b.name, place: b.place || '', bbox: b.bbox, fetched: now, elements: b.base.elements }), `Add course: ${b.name || b.slug}`);
    }
    const idx = await getFile('courses/index.json');
    const index = idx ? JSON.parse(idx.text) : { courses: [] };
    const entry = index.courses.find((c) => c.slug === b.slug);
    const editedHoles = Object.values(edits.holes || {}).filter((h) => h.line && h.line.length >= 2).length;
    if (entry) entry.edited = { updated: now, holes: editedHoles, shapes: (edits.shapes || []).length };
    else index.courses.push({ slug: b.slug, query: b.name, name: b.name, place: b.place || '', bbox: b.bbox, fetched: now, census: {}, edited: { updated: now, holes: editedHoles, shapes: (edits.shapes || []).length } });
    index.courses.sort((x, y) => x.name.localeCompare(y.name));
    await putFile('courses/index.json', JSON.stringify(index, null, 1), `Library index: ${b.name || b.slug}`, idx?.sha);
    send(200, { ok: true, updated: now });
  } catch (e) { send(502, { error: e.message }); }
}
